"""FunASR-family offline transcription adapter (TODO #14f, EN-only).

Runs SenseVoice / Paraformer (the FunASR models) through the ALREADY installed
sherpa-onnx runtime — ``OfflineRecognizer.from_sense_voice`` /
``from_paraformer`` — so NO heavy ``funasr``/modelscope package is pulled in and
the torch/whisperx stack is untouched (owner decision). These models cover
en/zh/ja/ko but have NO Russian; the registry gates them to language=en. Same raw
contract as the other engines: one ``[HH:MM:SS] text`` line per chunk in
``<base>_raw.txt``.

NOTE: requires the ``sherpa-onnx`` package (already used by the sherpa-onnx
engine); a missing package raises the standard dependency error.
"""
import glob
import os
import time as time_module

from ..progress import log_progress

try:
    import engines_registry as reg
except ImportError:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    import engines_registry as reg


def _resolve_funasr_model(model, language, res_dir=None):
    """On-disk FunASR model dir + its type. Honour a known name; else fall back to
    the engine default; raise if a known model is not downloaded. Returns
    ``(path, name, model_type)``."""
    name = model if reg.intended_path("funasr", model or "") else None
    if name is None:
        name = reg.default_model("funasr")
    path = reg.resolve_model_path("funasr", name, res_dir=res_dir)
    mtype = reg.ENGINES["funasr"]["models"].get(name, {}).get("model_type", "sense_voice")
    if path:
        return path, name, mtype
    raise Exception(
        f"FunASR model '{name}' is not downloaded. Download it in Settings "
        f"(or choose an available model). Expected at resources/funasr_models/{name}.")


def _pick_onnx(model_dir):
    """The single model onnx, preferring the full-precision file over int8."""
    files = sorted(glob.glob(os.path.join(model_dir, "model*.onnx")))
    if not files:
        raise Exception(f"FunASR model is missing model*.onnx in {model_dir}")
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


def transcribe_audio_funasr(audio_chunks, model_name, language, device,
                            output_dir, base_name, missing_dependency_error):
    """Transcribe audio using a FunASR model (SenseVoice/Paraformer via sherpa-onnx)."""
    try:
        import sherpa_onnx
    except ImportError as import_error:
        raise Exception(missing_dependency_error("FunASR (sherpa-onnx runtime)",
                                                 ["sherpa-onnx"], import_error))

    log_progress("status.transcribing", 30, "Loading FunASR model")
    try:
        model_dir, name, mtype = _resolve_funasr_model(model_name, language)
        provider = "cuda" if str(device).lower() in ("cuda", "gpu") else "cpu"
        onnx = _pick_onnx(model_dir)
        tokens = os.path.join(model_dir, "tokens.txt")
        if mtype == "paraformer":
            recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
                paraformer=onnx, tokens=tokens, num_threads=2,
                decoding_method="greedy_search", provider=provider)
        else:  # sense_voice
            recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=onnx, tokens=tokens, num_threads=2,
                decoding_method="greedy_search", provider=provider,
                language="en", use_itn=True)
        log_progress("status.transcribing", 35, f"FunASR model loaded: {name} ({mtype})")

        full_transcript = []
        total_time = 0.0
        for idx, chunk_path in enumerate(audio_chunks):
            chunk_path = str(chunk_path)
            if not os.path.exists(chunk_path):
                raise Exception(f"Chunk file not found: {chunk_path}")
            progress = 35 + (idx / len(audio_chunks)) * 40
            log_progress("status.transcribing", progress,
                         f"Transcribing chunk {idx + 1}/{len(audio_chunks)}...")
            try:
                t0 = time_module.time()
                samples, sample_rate = _read_wave(chunk_path)
                stream = recognizer.create_stream()
                stream.accept_waveform(sample_rate, samples)
                recognizer.decode_streams([stream])
                text = (getattr(stream.result, "text", "") or "").strip()
                if text:
                    start = idx * 600
                    hh, mm, ss = int(start // 3600), int((start % 3600) // 60), int(start % 60)
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
                     f"Transcription completed in {total_time:.1f}s total (FunASR/{mtype}). "
                     f"Saved to {output_path}")
        return output_path

    except Exception as e:
        log_progress("status.error", 0, f"FunASR transcription failed: {str(e)}")
        raise
