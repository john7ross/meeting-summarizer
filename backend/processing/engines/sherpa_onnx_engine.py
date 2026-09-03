"""sherpa-onnx offline transcription adapter (TODO #14d).

Loads a zipformer *transducer* model (tokens.txt + encoder/decoder/joiner .onnx)
with ``OfflineRecognizer.from_transducer`` and decodes each audio chunk. The
model name comes from ``whisperModel`` and is resolved via the engine registry
(honoured if known; otherwise a language small default; clear error if a known
model is not downloaded). Same raw-output contract as the other engines:
one ``[HH:MM:SS] text`` line per chunk written to ``<base>_raw.txt``.

The Russian fixed-shape Zipformer encoder cannot accept the processor's normal
10-minute chunks directly. Long chunks are therefore split near the quietest
point in 14–18 second windows before decoding. This follows sherpa-onnx's
documented long-audio pattern (segment first, then run the offline recognizer)
without cutting every window at an arbitrary fixed boundary.
"""
import glob
import json  # noqa: F401  (kept parallel to the other engines)
import os
import time as time_module

from ..progress import log_progress

try:
    import engines_registry as reg
except ImportError:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    import engines_registry as reg


def _resolve_sherpa_model(model, language, res_dir=None):
    """On-disk sherpa model dir for the configured model name. Honour a known
    name; else fall back to a small model for the language; raise if a known
    model is not downloaded. Returns ``(path, name)``."""
    name = model if reg.intended_path("sherpa-onnx", model or "") else None
    if name is None:
        name = ("sherpa-onnx-small-zipformer-ru-2024-09-18" if language == "ru"
                else "sherpa-onnx-zipformer-small-en-2023-06-26")
    path = reg.resolve_model_path("sherpa-onnx", name, res_dir=res_dir)
    if path:
        return path, name
    raise Exception(
        f"sherpa-onnx model '{name}' is not downloaded. Download it in Settings "
        f"(or choose an available model). Expected at resources/sherpa_models/{name}.")


def _pick(model_dir, prefix):
    """First matching .onnx for a role, preferring the non-int8 file."""
    files = sorted(glob.glob(os.path.join(model_dir, prefix + "*.onnx")))
    if not files:
        raise Exception(f"sherpa model is missing {prefix}*.onnx in {model_dir}")
    non_int8 = [f for f in files if "int8" not in os.path.basename(f)]
    return (non_int8 or files)[0]


def _read_wave(path):
    """Return (float32 mono samples in [-1,1], sample_rate) — sherpa's input."""
    import wave
    import numpy as np
    with wave.open(path, "rb") as f:
        if f.getnchannels() != 1 or f.getsampwidth() != 2:
            raise Exception(f"Expected 16-bit mono WAV: {os.path.basename(path)}")
        frames = f.readframes(f.getnframes())
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        return samples, f.getframerate()


def _sample_windows(samples, sample_rate, min_seconds=14.0, max_seconds=18.0):
    """Yield ``(start_sample, window)`` pieces accepted by fixed-shape encoders.

    A boundary is selected at the quietest 80-ms frame in the final four
    seconds of each window. Natural pauses are therefore preferred while every
    piece remains below the model's observed ~20-second limit.
    """
    import numpy as np

    total = len(samples)
    max_size = max(1, int(sample_rate * max_seconds))
    min_size = max(1, int(sample_rate * min_seconds))
    frame = max(1, int(sample_rate * 0.08))
    hop = max(1, int(sample_rate * 0.04))
    start = 0
    while total - start > max_size:
        search_start = min(start + min_size, total)
        search_end = min(start + max_size, total)
        candidates = range(
            search_start, max(search_start + 1, search_end - frame + 1), hop)
        cut_frame = min(
            candidates,
            key=lambda pos: float(np.mean(np.abs(samples[pos:pos + frame]))),
        )
        end = min(search_end, cut_frame + frame // 2)
        if end <= start:
            end = min(total, start + max_size)
        yield start, samples[start:end]
        start = end
    if start < total:
        yield start, samples[start:]


def transcribe_audio_sherpa_onnx(audio_chunks, model_size, language, device,
                                 output_dir, base_name, missing_dependency_error):
    """Transcribe audio using sherpa-onnx (offline zipformer transducer)."""
    try:
        import sherpa_onnx
    except ImportError as import_error:
        raise Exception(missing_dependency_error("sherpa-onnx", ["sherpa-onnx"], import_error))

    log_progress("status.transcribing", 30, "Loading sherpa-onnx model")
    try:
        model_dir, model_name = _resolve_sherpa_model(model_size, language)
        provider = "cuda" if str(device).lower() in ("cuda", "gpu") else "cpu"
        recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=_pick(model_dir, "encoder"),
            decoder=_pick(model_dir, "decoder"),
            joiner=_pick(model_dir, "joiner"),
            tokens=os.path.join(model_dir, "tokens.txt"),
            num_threads=2,
            decoding_method="greedy_search",
            provider=provider,
        )
        log_progress("status.transcribing", 35, f"sherpa-onnx model loaded: {model_name}")

        full_transcript = []
        total_time = 0.0
        for idx, chunk_path in enumerate(audio_chunks):
            chunk_path = str(chunk_path)
            if not os.path.exists(chunk_path):
                raise Exception(f"Chunk file not found: {chunk_path}")
            progress = 35 + (idx / len(audio_chunks)) * 40
            try:
                t0 = time_module.time()
                samples, sample_rate = _read_wave(chunk_path)
                windows = list(_sample_windows(samples, sample_rate))
                for part_idx, (sample_start, window) in enumerate(windows, 1):
                    log_progress(
                        "status.transcribing", progress,
                        f"Transcribing chunk {idx + 1}/{len(audio_chunks)}, "
                        f"segment {part_idx}/{len(windows)}...")
                    stream = recognizer.create_stream()
                    stream.accept_waveform(sample_rate, window)
                    recognizer.decode_streams([stream])
                    result = stream.result
                    text = (getattr(result, "text", "") or "").strip()
                    if text:
                        stamps = getattr(result, "timestamps", None) or []
                        start = (
                            (stamps[0] if stamps else 0.0)
                            + idx * 600
                            + sample_start / sample_rate
                        )
                        hh, mm, ss = (
                            int(start // 3600),
                            int((start % 3600) // 60),
                            int(start % 60),
                        )
                        full_transcript.append(f"[{hh:02d}:{mm:02d}:{ss:02d}] {text}")
                elapsed = time_module.time() - t0
                total_time += elapsed
                log_progress("status.transcribing", progress,
                             f"Chunk {idx + 1}/{len(audio_chunks)} done in {elapsed:.1f}s")
            except Exception as e:
                raise Exception(f"Failed to transcribe {os.path.basename(chunk_path)}: {str(e)}")
            try:
                os.remove(chunk_path)
            except OSError:
                pass

        output_path = os.path.join(output_dir, f"{base_name}_raw.txt")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(full_transcript))
        log_progress("status.transcribing", 75,
                     f"Transcription completed in {total_time:.1f}s total (sherpa-onnx). "
                     f"Saved to {output_path}")
        return output_path

    except Exception as e:
        log_progress("status.error", 0, f"sherpa-onnx transcription failed: {str(e)}")
        raise
