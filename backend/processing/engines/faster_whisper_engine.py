"""Faster-Whisper (CTranslate2) transcription adapter — the default engine.

2-4x faster than openai-whisper at the same quality; the registry picks it by
default for every language.
"""
import os
import time as time_module

from ..progress import log_progress


def transcribe_audio_faster_whisper(
    audio_chunks,
    model_size,
    language,
    device,
    output_dir,
    base_name,
    tracer,
    missing_dependency_error,
    get_torch_cuda_status,
    initial_prompt=""
):
    """Transcribe audio using Faster-Whisper."""
    tracer.start_span("transcribe_faster_whisper", {
        "model": model_size,
        "language": language,
        "device": device,
        "chunks": len(audio_chunks)
    })

    try:
        from faster_whisper import WhisperModel
    except ImportError as import_error:
        raise Exception(missing_dependency_error("Faster-Whisper", ["faster-whisper"], import_error))

    log_progress("status.transcribing", 30, f"Loading Faster-Whisper model: {model_size}")

    try:
        cuda_available, torch_module = get_torch_cuda_status()
        log_progress("status.transcribing", 28, f"CUDA available: {cuda_available}")

        if cuda_available and torch_module:
            log_progress("status.transcribing", 28, f"GPU device: {torch_module.cuda.get_device_name(0)}")

        if device == 'auto':
            actual_device = 'cuda' if cuda_available else 'cpu'
            log_progress("status.transcribing", 29, f"Auto-selected device: {actual_device}")
        elif device == 'cuda':
            if not cuda_available:
                log_progress("status.transcribing", 29, "CUDA requested but not available, falling back to CPU")
                actual_device = 'cpu'
            else:
                actual_device = 'cuda'
                log_progress("status.transcribing", 29, f"Using CUDA device: {torch_module.cuda.get_device_name(0)}")
        else:
            actual_device = 'cpu'
            log_progress("status.transcribing", 29, "Using CPU device")

        compute_type = 'float16' if actual_device == 'cuda' else 'int8'
        log_progress("status.transcribing", 30, f"Compute type: {compute_type}")

        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        possible_paths = [
            os.path.join(backend_dir, '..', 'resources', 'whisper_models'),
            os.path.join(backend_dir, '..', '..', 'resources', 'whisper_models'),
        ]

        models_dir = None
        for path in possible_paths:
            if os.path.exists(path):
                models_dir = os.path.abspath(path)
                break

        if not models_dir:
            models_dir = os.path.abspath(possible_paths[0])
            os.makedirs(models_dir, exist_ok=True)

        log_progress("status.transcribing", 32, f"Using models directory: {models_dir}")

        model_exists = False
        if os.path.exists(models_dir):
            possible_model_paths = [
                os.path.join(models_dir, f"faster-whisper-{model_size}"),
                os.path.join(models_dir, f"models--Systran--faster-whisper-{model_size}")
            ]

            for model_path in possible_model_paths:
                if os.path.exists(model_path):
                    model_exists = True
                    log_progress("status.transcribing", 33, f"Found local model at {model_path}")
                    break

            if not model_exists:
                log_progress("status.transcribing", 33, "No local model found, will download from HuggingFace")

        try:
            model = WhisperModel(
                model_size,
                device=actual_device,
                compute_type=compute_type,
                download_root=models_dir,
                local_files_only=model_exists
            )
            log_progress("status.transcribing", 35, f"Model loaded on {actual_device}")
        except Exception as model_error:
            error_msg = str(model_error)
            if "proxy" in error_msg.lower() or "connection" in error_msg.lower():
                raise Exception(
                    "Failed to download model from HuggingFace. "
                    "Please check your internet connection and firewall settings. "
                    "If you're behind a corporate proxy, you may need to configure it. "
                    f"Original error: {error_msg}"
                )
            raise

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

                segments, info = model.transcribe(
                    chunk_path,
                    language=language,
                    beam_size=5,
                    vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=500),
                    initial_prompt=(initial_prompt or None)
                )

                chunk_text = []
                for segment in segments:
                    start_time = segment.start + (idx * 600)
                    hours = int(start_time // 3600)
                    minutes = int((start_time % 3600) // 60)
                    seconds = int(start_time % 60)
                    timestamp = f"[{hours:02d}:{minutes:02d}:{seconds:02d}]"
                    chunk_text.append(f"{timestamp} {segment.text}")

                elapsed = time_module.time() - chunk_start_time
                total_transcription_time += elapsed
                log_progress("status.transcribing", progress, f"Chunk {idx + 1}/{len(audio_chunks)} done in {elapsed:.1f}s")
            except Exception as e:
                raise Exception(f"Failed to transcribe {os.path.basename(chunk_path)}: {str(e)}")

            if chunk_text:
                full_transcript.append('\n'.join(chunk_text))

            try:
                os.remove(chunk_path)
            except Exception:
                pass

        output_path = os.path.join(output_dir, f"{base_name}_raw.txt")
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(full_transcript))

        log_progress("status.transcribing", 75, f"Transcription completed in {total_transcription_time:.1f}s total (Faster-Whisper). Saved to {output_path}")

        try:
            del model
        except Exception:
            pass

        tracer.end_span()
        return output_path

    except Exception as e:
        tracer.end_span()
        log_progress("status.error", 0, f"Faster-Whisper transcription failed: {str(e)}")
        raise
