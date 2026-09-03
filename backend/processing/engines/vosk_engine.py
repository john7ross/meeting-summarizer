"""Vosk offline transcription adapter — lightweight, CPU-only, no torch.

Per-language models (the registry refuses a model whose language does not
match the chosen one), so it is the fallback for machines without a GPU.
"""
import json
import os
import time as time_module

from ..progress import log_progress

try:
    import engines_registry as reg
except ImportError:  # make backend/ importable when this module is used standalone
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    import engines_registry as reg


def _resolve_vosk_model(model, language, res_dir=None):
    """Locate the on-disk Vosk model directory for the configured model.

    For Vosk, ``whisperModel`` holds a concrete model NAME (e.g.
    ``vosk-model-small-ru-0.22``); honour it via the engine registry. Legacy
    callers that pass a whisper size or a blank value fall back to a small model
    for the language. Raises with guidance if the chosen model is known but not
    downloaded. Returns ``(path, name)``."""
    name = model if reg.intended_path("vosk", model or "") else None
    if name is None:
        name = "vosk-model-small-ru-0.22" if language == "ru" else "vosk-model-small-en-us-0.15"
    path = reg.resolve_model_path("vosk", name, res_dir=res_dir)
    if path:
        return path, name
    raise Exception(
        f"Vosk model '{name}' is not downloaded. Download it in Settings "
        f"(or choose an available model). Expected at resources/vosk_models/{name}.")


def transcribe_audio_vosk(audio_chunks, model_size, language, device, output_dir, base_name, missing_dependency_error):
    """Transcribe audio using Vosk."""
    try:
        from vosk import Model, KaldiRecognizer
        import wave
    except ImportError as import_error:
        raise Exception(missing_dependency_error("Vosk", ["vosk"], import_error))

    log_progress("status.transcribing", 30, "Loading Vosk model")

    try:
        model_path, model_name = _resolve_vosk_model(model_size, language)
        log_progress("status.transcribing", 32, f"Loading Vosk model: {model_name}")

        model = Model(model_path)
        log_progress("status.transcribing", 35, "Vosk model loaded")

        full_transcript = []
        total_transcription_time = 0

        for idx, chunk_path in enumerate(audio_chunks):
            progress = 35 + (idx / len(audio_chunks)) * 40

            chunk_path = str(chunk_path)
            if not os.path.exists(chunk_path):
                raise Exception(f"Chunk file not found: {chunk_path}")

            log_progress("status.transcribing", progress, f"Transcribing chunk {idx + 1}/{len(audio_chunks)}...")

            if idx == 0:
                time_module.sleep(1.0)

            try:
                chunk_start_time = time_module.time()

                wf = wave.open(chunk_path, "rb")
                rec = KaldiRecognizer(model, wf.getframerate())
                rec.SetWords(True)

                # Each recognised segment becomes one timestamped line. Vosk
                # returns per-word timing in result['result']; the segment's
                # start time is the first word's 'start' (plus the chunk's
                # 600s offset so multi-chunk audio stays monotonic). This keeps
                # the raw file format consistent with the whisper engines:
                #     [HH:MM:SS] text
                chunk_offset = idx * 600

                def _format_segment(seg_result):
                    text = (seg_result.get('text') or '').strip()
                    if not text:
                        return None
                    words = seg_result.get('result') or []
                    start_time = (words[0].get('start', 0.0)
                                  if words else 0.0) + chunk_offset
                    hours = int(start_time // 3600)
                    minutes = int((start_time % 3600) // 60)
                    seconds = int(start_time % 60)
                    timestamp = f"[{hours:02d}:{minutes:02d}:{seconds:02d}]"
                    return f"{timestamp} {text}"

                results = []
                while True:
                    data = wf.readframes(4000)
                    if len(data) == 0:
                        break
                    if rec.AcceptWaveform(data):
                        line = _format_segment(json.loads(rec.Result()))
                        if line:
                            results.append(line)

                final_line = _format_segment(json.loads(rec.FinalResult()))
                if final_line:
                    results.append(final_line)

                wf.close()

                elapsed = time_module.time() - chunk_start_time
                total_transcription_time += elapsed
                log_progress("status.transcribing", progress, f"Chunk {idx + 1}/{len(audio_chunks)} done in {elapsed:.1f}s")
            except Exception as e:
                raise Exception(f"Failed to transcribe {os.path.basename(chunk_path)}: {str(e)}")

            if results:
                # results already are individual "[HH:MM:SS] text" lines
                full_transcript.append('\n'.join(results))

            try:
                os.remove(chunk_path)
            except Exception:
                pass

        output_path = os.path.join(output_dir, f"{base_name}_raw.txt")
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(full_transcript))

        log_progress("status.transcribing", 75, f"Transcription completed in {total_transcription_time:.1f}s total (Vosk). Saved to {output_path}")

        return output_path

    except Exception as e:
        log_progress("status.error", 0, f"Vosk transcription failed: {str(e)}")
        raise
