"""Reference OpenAI Whisper transcription adapter.

The slowest engine, kept because it is the baseline every other adapter is
compared against in the Diagnostics engine comparison.
"""
import os
import time as time_module

from ..progress import log_progress


def transcribe_audio_openai_whisper(
    audio_chunks,
    model_name,
    language,
    device_type,
    output_dir,
    base_name,
    tracer,
    missing_dependency_error,
    initial_prompt=""
):
    """Transcribe audio with OpenAI Whisper."""
    tracer.start_span("transcribe_openai_whisper", {
        "model": model_name,
        "language": language,
        "device": device_type,
        "chunks": len(audio_chunks)
    })

    log_progress("status.transcribing", 45, f"Loading Whisper model: {model_name}")

    try:
        try:
            import whisper
            import torch
            import numpy as np
            import scipy.io.wavfile as wavfile
        except ImportError as import_error:
            raise Exception(missing_dependency_error(
                "OpenAI Whisper",
                ["openai-whisper", "torch", "numpy", "scipy"],
                import_error
            ))

        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        model_path = os.path.join(backend_dir, '..', 'resources', 'whisper_models')
        os.makedirs(model_path, exist_ok=True)

        cuda_available = torch.cuda.is_available()
        log_progress("status.transcribing", 45, f"CUDA available: {cuda_available}")

        if cuda_available:
            log_progress("status.transcribing", 45, f"GPU device: {torch.cuda.get_device_name(0)}")

        if device_type == 'auto':
            device = "cuda" if cuda_available else "cpu"
            log_progress("status.transcribing", 46, f"Auto-selected device: {device}")
        elif device_type == 'cuda':
            if not cuda_available:
                log_progress("status.transcribing", 46, "CUDA requested but not available, falling back to CPU")
                device = "cpu"
            else:
                device = "cuda"
                log_progress("status.transcribing", 46, f"Using CUDA device: {torch.cuda.get_device_name(0)}")
        else:
            device = "cpu"
            log_progress("status.transcribing", 46, "Using CPU device")

        log_progress("status.transcribing", 47, f"Loading model on {device}...")

        model = whisper.load_model(model_name, download_root=model_path, device=device)

        log_progress("status.transcribing", 50, "Transcribing audio...")

        full_transcript = []
        time_offset = 0
        total_transcription_time = 0

        for idx, chunk_path in enumerate(audio_chunks):
            progress = 50 + (idx / len(audio_chunks)) * 25

            chunk_path = str(chunk_path)
            if not os.path.exists(chunk_path):
                raise Exception(f"Chunk file not found: {chunk_path}")

            log_progress("status.transcribing", progress, f"Transcribing chunk {idx + 1}/{len(audio_chunks)}...")

            if idx == 0:
                time_module.sleep(1.0)

            try:
                chunk_start_time = time_module.time()

                sample_rate, audio_data = wavfile.read(chunk_path)
                audio_data = audio_data.astype(np.float32) / 32768.0

                result = model.transcribe(
                    audio_data,
                    language=language,
                    verbose=False,
                    word_timestamps=False,
                    initial_prompt=(initial_prompt or None)
                )

                elapsed = time_module.time() - chunk_start_time
                total_transcription_time += elapsed
                log_progress("status.transcribing", progress, f"Chunk {idx + 1}/{len(audio_chunks)} done in {elapsed:.1f}s")
            except Exception as e:
                raise Exception(f"Failed to transcribe {os.path.basename(chunk_path)}: {str(e)}")

            for segment in result['segments']:
                start_time = segment['start'] + time_offset
                text = segment['text'].strip()

                hours = int(start_time // 3600)
                minutes = int((start_time % 3600) // 60)
                seconds = int(start_time % 60)

                timestamp = f"[{hours:02d}:{minutes:02d}:{seconds:02d}]"
                full_transcript.append(f"{timestamp} {text}")

            time_offset += 600

            if len(audio_chunks) > 18:
                checkpoint_path = os.path.join(output_dir, f"{base_name}_checkpoint.txt")
                with open(checkpoint_path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(full_transcript))
                log_progress("status.transcribing", progress, f"Checkpoint saved (chunk {idx + 1}/{len(audio_chunks)})")

            if len(audio_chunks) > 1:
                try:
                    os.remove(chunk_path)
                except Exception:
                    pass

        output_path = os.path.join(output_dir, f"{base_name}_raw.txt")
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(full_transcript))

        checkpoint_path = os.path.join(output_dir, f"{base_name}_checkpoint.txt")
        if os.path.exists(checkpoint_path):
            try:
                os.remove(checkpoint_path)
            except Exception:
                pass

        log_progress("status.transcribing", 75, f"Transcription completed in {total_transcription_time:.1f}s total. Saved to {output_path}")

        try:
            del model
        except Exception:
            pass

        tracer.end_span()
        return output_path

    except Exception as e:
        tracer.end_span()
        log_progress("status.error", 0, f"Transcription failed: {str(e)}")
        raise
