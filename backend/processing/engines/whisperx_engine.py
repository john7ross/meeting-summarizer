"""WhisperX transcription adapter — fastest, and the only one that labels speakers.

Diarization runs through the bundled sherpa-onnx backend by default (no token)
or through gated pyannote when a HuggingFace token is configured.
"""
import os
import sys
import time as time_module

from ..progress import log_progress


def transcribe_audio_whisperx(
    audio_chunks,
    model_size,
    language,
    device,
    output_dir,
    base_name,
    tracer,
    missing_dependency_error,
    diarization_backend="sherpa",
    hf_token=""
):
    """Transcribe audio using WhisperX with optional speaker diarization."""
    tracer.start_span("transcribe_whisperx", {
        "model": model_size,
        "language": language,
        "device": device,
        "chunks": len(audio_chunks)
    })

    try:
        import whisperx
        import torch
    except ImportError as import_error:
        raise Exception(missing_dependency_error("WhisperX", ["whisperx", "torch"], import_error))

    try:
        from whisperx_patch import patch_whisperx
        patch_whisperx()
    except Exception as patch_error:
        print(f"Warning: Failed to apply WhisperX compatibility patch: {patch_error}", file=sys.stderr)

    log_progress("status.transcribing", 30, f"Loading WhisperX model: {model_size}")

    try:
        if device == 'auto':
            actual_device = 'cuda' if torch.cuda.is_available() else 'cpu'
        else:
            actual_device = device

        compute_type = 'float16' if actual_device == 'cuda' else 'int8'

        model = whisperx.load_model(
            model_size,
            actual_device,
            compute_type=compute_type,
            language=language
        )

        log_progress("status.transcribing", 35, f"Model loaded on {actual_device}")

        align_model = None
        align_metadata = None
        try:
            log_progress("status.transcribing", 37, "Loading alignment model...")
            align_model, align_metadata = whisperx.load_align_model(
                language_code=language,
                device=actual_device
            )
            log_progress("status.transcribing", 38, "Alignment model loaded")
        except Exception as e:
            log_progress("status.transcribing", 38, f"Alignment unavailable for {language}, using basic timestamps: {str(e)}")

        # Diarization backend: 'sherpa' (offline, ungated ONNX — the default, works
        # with NO HuggingFace token) | 'pyannote' (needs a gated HF token) | 'off'.
        diar_mode = None            # resolved backend actually available
        diarize_model = None        # pyannote pipeline (only for the pyannote path)
        backend = (diarization_backend or "sherpa").lower()
        if backend == "off":
            log_progress("status.transcribing", 40, "Speaker diarization disabled")
        elif backend == "pyannote":
            try:
                log_progress("status.transcribing", 39, "Loading pyannote diarization (HF token)...")
                token = hf_token or True   # True => use the cached HF token
                from whisperx_patch import whisperx_vad_safe_globals
                safe_globals = getattr(torch.serialization, "safe_globals", None)
                if safe_globals is None:
                    from contextlib import nullcontext
                    load_scope = nullcontext()
                else:
                    load_scope = safe_globals(whisperx_vad_safe_globals())
                with load_scope:
                    diarize_model = whisperx.DiarizationPipeline(
                        use_auth_token=token, device=actual_device)
                diar_mode = "pyannote"
                log_progress("status.transcribing", 40, "Speaker diarization enabled (pyannote)")
            except Exception as e:
                print(f"DEBUG: pyannote diarization failed: {str(e)}", file=sys.stderr)
                raise RuntimeError(
                    "Pyannote diarization could not be loaded. Check the HF "
                    f"token and accepted model terms: {e}") from e
        else:  # 'sherpa' (default)
            try:
                from processing import diarization as _diar
                if _diar.is_available():
                    diar_mode = "sherpa"
                    log_progress("status.transcribing", 40, "Speaker diarization enabled (offline sherpa-onnx)")
                else:
                    log_progress("status.transcribing", 40,
                                 "Offline diarization models not downloaded — continuing without speakers")
            except Exception as e:
                print(f"DEBUG: sherpa diarization unavailable: {str(e)}", file=sys.stderr)
                log_progress("status.transcribing", 40, f"Diarization unavailable: {str(e)}")

        full_transcript = []
        total_transcription_time = 0

        for idx, chunk_path in enumerate(audio_chunks):
            progress = 40 + (idx / len(audio_chunks)) * 35

            chunk_path = str(chunk_path)
            if not os.path.exists(chunk_path):
                raise Exception(f"Chunk file not found: {chunk_path}")

            log_progress("status.transcribing", progress, f"Transcribing chunk {idx + 1}/{len(audio_chunks)}...")

            if idx == 0:
                time_module.sleep(1.0)

            try:
                chunk_start_time = time_module.time()

                audio = whisperx.load_audio(chunk_path)

                result = model.transcribe(
                    audio,
                    batch_size=16,
                    language=language
                )

                if align_model and align_metadata:
                    result = whisperx.align(
                        result["segments"],
                        align_model,
                        align_metadata,
                        audio,
                        actual_device,
                        return_char_alignments=False
                    )

                speakers_detected = False
                if diar_mode == "pyannote" and diarize_model:
                    try:
                        diarize_segments = diarize_model(audio)
                        result = whisperx.assign_word_speakers(diarize_segments, result)
                        speakers_detected = True
                    except Exception as e:
                        log_progress("status.transcribing", progress, f"Speaker detection failed for chunk {idx + 1}: {str(e)}")
                elif diar_mode == "sherpa":
                    try:
                        import numpy as np
                        from processing import diarization as _diar
                        samples = np.asarray(audio, dtype=np.float32)
                        sherpa_segs = _diar.diarize(samples, 16000)
                        _assign_sherpa_speakers(result.get("segments", []), sherpa_segs)
                        speakers_detected = bool(sherpa_segs)
                    except Exception as e:
                        log_progress("status.transcribing", progress, f"Offline speaker detection failed for chunk {idx + 1}: {str(e)}")

                if speakers_detected:
                    chunk_text = _format_whisperx_with_speakers(result["segments"], chunk_offset=idx * 600)
                else:
                    chunk_text = []
                    for segment in result["segments"]:
                        start_time = segment.get("start", 0) + (idx * 600)
                        hours = int(start_time // 3600)
                        minutes = int((start_time % 3600) // 60)
                        seconds = int(start_time % 60)
                        timestamp = f"[{hours:02d}:{minutes:02d}:{seconds:02d}]"
                        text = segment.get("text", "").strip()
                        if text:
                            chunk_text.append(f"{timestamp} {text}")
                    chunk_text = '\n'.join(chunk_text)

                elapsed = time_module.time() - chunk_start_time
                total_transcription_time += elapsed
                log_progress("status.transcribing", progress, f"Chunk {idx + 1}/{len(audio_chunks)} done in {elapsed:.1f}s")
            except Exception as e:
                raise Exception(f"Failed to transcribe {os.path.basename(chunk_path)}: {str(e)}")

            if chunk_text:
                full_transcript.append(chunk_text)

            try:
                os.remove(chunk_path)
            except Exception:
                pass

        output_path = os.path.join(output_dir, f"{base_name}_raw.txt")
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n\n'.join(full_transcript))

        log_progress("status.transcribing", 75, f"Transcription completed in {total_transcription_time:.1f}s total (WhisperX). Saved to {output_path}")

        tracer.end_span()
        return output_path

    except Exception as e:
        tracer.end_span()
        log_progress("status.error", 0, f"WhisperX transcription failed: {str(e)}")
        raise


def _assign_sherpa_speakers(segments, sherpa_segs):
    """Assign each transcript segment the sherpa-onnx speaker whose time range it
    overlaps most (both are chunk-relative seconds). Sets segment['speaker']."""
    for seg in segments:
        s0 = seg.get("start", 0) or 0
        s1 = seg.get("end", s0) or s0
        best, best_ov = None, 0.0
        for d0, d1, spk in sherpa_segs:
            ov = min(s1, d1) - max(s0, d0)
            if ov > best_ov:
                best_ov, best = ov, spk
        if best:
            seg["speaker"] = best
    return segments


def _format_whisperx_with_speakers(segments, chunk_offset=0):
    """Format WhisperX segments with speaker labels and timestamps."""
    formatted_lines = []
    current_speaker = None
    current_text = []
    current_start_time = None

    for segment in segments:
        speaker = _get_whisperx_segment_speaker(segment)
        start_time = segment.get("start", 0) + chunk_offset

        if speaker != current_speaker and current_text:
            hours = int(current_start_time // 3600)
            minutes = int((current_start_time % 3600) // 60)
            seconds = int(current_start_time % 60)
            timestamp = f"[{hours:02d}:{minutes:02d}:{seconds:02d}]"
            speaker_label = current_speaker if current_speaker else "Unknown"
            formatted_lines.append(f"{timestamp} [{speaker_label}]: {' '.join(current_text).strip()}")
            current_text = []
            current_start_time = None

        if current_start_time is None:
            current_start_time = start_time

        current_speaker = speaker
        text = segment.get("text", "").strip()
        if text:
            current_text.append(text)

    if current_text and current_start_time is not None:
        hours = int(current_start_time // 3600)
        minutes = int((current_start_time % 3600) // 60)
        seconds = int(current_start_time % 60)
        timestamp = f"[{hours:02d}:{minutes:02d}:{seconds:02d}]"
        speaker_label = current_speaker if current_speaker else "Unknown"
        formatted_lines.append(f"{timestamp} [{speaker_label}]: {' '.join(current_text).strip()}")

    return '\n'.join(formatted_lines)


def _get_whisperx_segment_speaker(segment):
    """Return the primary speaker for a WhisperX segment."""
    if "speaker" in segment:
        return segment["speaker"]

    if "words" in segment:
        speakers = {}
        for word in segment["words"]:
            if "speaker" in word:
                speaker = word["speaker"]
                speakers[speaker] = speakers.get(speaker, 0) + 1

        if speakers:
            return max(speakers.items(), key=lambda x: x[1])[0]

    return None
