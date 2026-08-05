"""whisper.cpp offline transcription adapter (TODO #14f).

Loads a whisper.cpp ggml model (single ``.bin``) through the ``pywhispercpp``
binding and decodes each audio chunk. Same multilingual whisper weights as the
torch engines, but a CPU-efficient C++ runtime — offered so the user can compare.
The model name comes from ``whisperModel`` (a size: tiny/base/small/medium/large)
and is resolved via the engine registry (clear error if a known size is not
downloaded). Same raw-output contract as the other engines: per-segment
``[HH:MM:SS] text`` lines (offset by chunk index) written to ``<base>_raw.txt``.

NOTE: end-to-end transcription requires the ``pywhispercpp`` package; a missing
package raises the standard dependency error.
"""
import os
import time as time_module

from ..progress import log_progress

try:
    import engines_registry as reg
except ImportError:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    import engines_registry as reg

CHUNK_SECONDS = 600  # processor splits audio into 10-minute chunks


def _resolve_whispercpp_model(model, language, res_dir=None):
    """On-disk ggml .bin for the configured size. Honour a known size; else fall
    back to the engine default; raise if a known size is not downloaded. Multi-
    lingual, so ``language`` does not change the model. Returns ``(path, size)``."""
    size = model if reg.intended_path("whisper-cpp", model or "") else None
    if size is None:
        size = reg.default_model("whisper-cpp")
    path = reg.resolve_model_path("whisper-cpp", size, res_dir=res_dir)
    if path:
        return path, size
    raise Exception(
        f"whisper.cpp model '{size}' is not downloaded. Download it in Settings "
        f"(or choose an available model). Expected at resources/whispercpp_models/"
        f"{reg.ENGINES['whisper-cpp']['models'][size]['file']}.")


def transcribe_audio_whispercpp(audio_chunks, model_size, language, device,
                                output_dir, base_name, missing_dependency_error):
    """Transcribe audio using whisper.cpp (pywhispercpp)."""
    try:
        from pywhispercpp.model import Model
    except ImportError as import_error:
        raise Exception(missing_dependency_error("whisper.cpp", ["pywhispercpp"], import_error))

    log_progress("status.transcribing", 30, "Loading whisper.cpp model")
    try:
        model_path, size = _resolve_whispercpp_model(model_size, language)
        use_gpu = str(device).lower() in ("cuda", "gpu", "auto")
        model = Model(
            model_path,
            n_threads=max(2, (os.cpu_count() or 4) // 2),
            language=(language or "auto"),
            print_progress=False,
            print_realtime=False,
            context_params={"use_gpu": use_gpu},
        )
        log_progress("status.transcribing", 35, f"whisper.cpp model loaded: {size}")

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
                segments = model.transcribe(chunk_path)
                offset = idx * CHUNK_SECONDS
                for seg in segments:
                    text = (getattr(seg, "text", "") or "").strip()
                    if not text:
                        continue
                    start = offset + getattr(seg, "t0", 0) / 100.0  # t0 is centiseconds
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
                     f"Transcription completed in {total_time:.1f}s total (whisper.cpp). "
                     f"Saved to {output_path}")
        return output_path

    except Exception as e:
        log_progress("status.error", 0, f"whisper.cpp transcription failed: {str(e)}")
        raise
