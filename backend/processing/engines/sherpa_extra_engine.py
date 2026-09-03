"""Extra (optional, download-only) sherpa-onnx model adapter (TODO #14g).

Runs OPTIONAL community models on the ALREADY-installed sherpa-onnx runtime,
dispatching the loader by ``model_type``:
  - ``nemo_ctc``  -> ``from_nemo_ctc``  (GigaAM Russian; single model onnx)
  - ``moonshine`` -> ``from_moonshine`` (Moonshine English; 4-file bundle)
These are NOT bundled in any build variant — the user downloads them on demand.
Same raw contract as the other engines: one ``[HH:MM:SS] text`` line per chunk.

NOTE: requires the ``sherpa-onnx`` package (already used by the sherpa-onnx and
funasr engines); a missing package raises the standard dependency error.
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


def _resolve_extra_model(model, language, res_dir=None):
    """On-disk dir + type for an extra model. Honour a known name; else fall back
    to a language-appropriate default; raise if a known model is not downloaded.
    Returns ``(path, name, model_type)``."""
    models = reg.ENGINES["sherpa-extra"]["models"]
    name = model if model in models else None
    if name is None:
        name = next((mid for mid, m in models.items() if m.get("lang") == language),
                    reg.default_model("sherpa-extra"))
    path = reg.resolve_model_path("sherpa-extra", name, res_dir=res_dir)
    mtype = models.get(name, {}).get("model_type", "")
    if path:
        return path, name, mtype
    raise Exception(
        f"Extra model '{name}' is not downloaded. It is an optional model, not part "
        f"of the distribution — download it in Settings. Expected at "
        f"resources/sherpa_extra_models/{name}.")


def _pick(model_dir, prefix):
    files = sorted(glob.glob(os.path.join(model_dir, prefix + "*.onnx")))
    if not files:
        raise Exception(f"extra model is missing {prefix}*.onnx in {model_dir}")
    non_int8 = [f for f in files if "int8" not in os.path.basename(f)]
    return (non_int8 or files)[0]


def _build_recognizer(sherpa_onnx, model_dir, mtype, provider):
    tokens = os.path.join(model_dir, "tokens.txt")
    if mtype == "moonshine":
        return sherpa_onnx.OfflineRecognizer.from_moonshine(
            preprocessor=_pick(model_dir, "preprocess"),
            encoder=_pick(model_dir, "encode"),
            uncached_decoder=_pick(model_dir, "uncached_decode"),
            cached_decoder=_pick(model_dir, "cached_decode"),
            tokens=tokens, num_threads=2, provider=provider)
    if mtype == "nemo_ctc":
        return sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
            model=_pick(model_dir, "model"), tokens=tokens, num_threads=2,
            decoding_method="greedy_search", provider=provider)
    raise Exception(f"unsupported extra model_type: {mtype}")


def _read_wave(path):
    import wave
    import numpy as np
    with wave.open(path, "rb") as f:
        if f.getnchannels() != 1 or f.getsampwidth() != 2:
            raise Exception(f"Expected 16-bit mono WAV: {os.path.basename(path)}")
        frames = f.readframes(f.getnframes())
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        return samples, f.getframerate()


def transcribe_audio_sherpa_extra(audio_chunks, model_name, language, device,
                                  output_dir, base_name, missing_dependency_error):
    """Transcribe audio using an optional extra model (GigaAM / Moonshine)."""
    try:
        import sherpa_onnx
    except ImportError as import_error:
        raise Exception(missing_dependency_error("extra models (sherpa-onnx runtime)",
                                                 ["sherpa-onnx"], import_error))

    log_progress("status.transcribing", 30, "Loading extra model")
    try:
        model_dir, name, mtype = _resolve_extra_model(model_name, language)
        provider = "cuda" if str(device).lower() in ("cuda", "gpu") else "cpu"
        recognizer = _build_recognizer(sherpa_onnx, model_dir, mtype, provider)
        log_progress("status.transcribing", 35, f"Extra model loaded: {name} ({mtype})")

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
                     f"Transcription completed in {total_time:.1f}s total (extra/{mtype}). "
                     f"Saved to {output_path}")
        return output_path

    except Exception as e:
        log_progress("status.error", 0, f"Extra-model transcription failed: {str(e)}")
        raise
