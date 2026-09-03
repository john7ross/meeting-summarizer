#!/usr/bin/env python3
"""
Обработчик видео: извлечение аудио и транскрибация
"""
import argparse
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import re

from processing.audio import extract_audio, peak_dbfs, split_audio_chunks
from processing.engines.faster_whisper_engine import transcribe_audio_faster_whisper
from processing.engines.openai_whisper_engine import transcribe_audio_openai_whisper
from processing.engines.vosk_engine import transcribe_audio_vosk
from processing.engines.whisperx_engine import transcribe_audio_whisperx
from processing.engines.sherpa_onnx_engine import transcribe_audio_sherpa_onnx
from processing.engines.whispercpp_engine import transcribe_audio_whispercpp
from processing.engines.funasr_engine import transcribe_audio_funasr
from processing.engines.sherpa_extra_engine import transcribe_audio_sherpa_extra
from processing.progress import log_progress
from processing.tracing import PerformanceTracer

import engines_registry as reg

# КРИТИЧЕСКИ ВАЖНО: Отключаем прокси ДО импорта библиотек
# Это решает проблему MaxRetryError при загрузке моделей с HuggingFace
os.environ['NO_PROXY'] = '*'
os.environ['no_proxy'] = '*'
if 'HTTP_PROXY' in os.environ:
    del os.environ['HTTP_PROXY']
if 'HTTPS_PROXY' in os.environ:
    del os.environ['HTTPS_PROXY']
if 'http_proxy' in os.environ:
    del os.environ['http_proxy']
if 'https_proxy' in os.environ:
    del os.environ['https_proxy']


def _missing_engine_dependency(engine_name, packages, import_error):
    package_list = ', '.join(packages)
    missing_name = getattr(import_error, 'name', None)
    suffix = f" Missing import: {missing_name}." if missing_name else ""
    return (
        f"{engine_name} transcription engine is unavailable. "
        f"Required Python package(s): {package_list}. "
        f"Install the required dependencies or choose another transcription engine in settings."
        f"{suffix}"
    )


def _get_torch_cuda_status():
    try:
        import torch
    except ImportError:
        return False, None

    return torch.cuda.is_available(), torch


def _adapter(fn, *trailing):
    """Wrap a transcribe function so it takes only the 6 common args
    (chunks, model, language, device, output_dir, base_name); each engine's
    extra trailing args (tracer / missing-dep / cuda-status) are bound here."""
    def call(chunks, model, language, device, output_dir, base_name):
        return fn(chunks, model, language, device, output_dir, base_name, *trailing)
    return call


def _build_adapters(tracer, missing_dependency, cuda_status, initial_prompt="",
                    diarization_backend="sherpa", hf_token=""):
    """engine id -> adapter callable. Adding a new engine is a registry entry
    (engines_registry) + one line here + its transcribe module — no dispatch
    branching to touch. ``initial_prompt`` is an optional transcription hint
    (vocabulary/terms) fed only to the whisper-family engines that accept one."""
    return {
        "whisper":        _adapter(transcribe_audio_openai_whisper, tracer, missing_dependency, initial_prompt),
        "faster-whisper": _adapter(transcribe_audio_faster_whisper, tracer, missing_dependency, cuda_status, initial_prompt),
        "whisperx":       _adapter(transcribe_audio_whisperx, tracer, missing_dependency, diarization_backend, hf_token),
        "vosk":           _adapter(transcribe_audio_vosk, missing_dependency),
        "sherpa-onnx":    _adapter(transcribe_audio_sherpa_onnx, missing_dependency),
        "whisper-cpp":    _adapter(transcribe_audio_whispercpp, missing_dependency),
        "funasr":         _adapter(transcribe_audio_funasr, missing_dependency),
        "sherpa-extra":   _adapter(transcribe_audio_sherpa_extra, missing_dependency),
    }


def resolve_engine(engine, available):
    """Pick which engine to dispatch to. A known engine with an adapter -> itself.
    An engine declared in the registry but not implemented (implemented=False) ->
    a clear error (never silently usable — TODO #14d). Anything else -> the
    default whisper engine (backward compatible with the old else-branch)."""
    if engine in available:
        return engine
    if engine in reg.ENGINES and not reg.is_implemented(engine):
        raise RuntimeError(
            f"Transcription engine '{engine}' is declared but has no adapter yet "
            f"(see TODO #14d). Choose another engine in settings.")
    return "whisper"


# A transcript is "empty" once the timestamp markers the engines write are gone:
# a run that recognised nothing still produces "[00:00:00]" lines on some engines.
_TIMESTAMP = re.compile(r"\[\d{2}:\d{2}:\d{2}(?:\.\d+)?\]")
# Anything at or below this is a track with no signal in it at all. Digital
# silence measures about -91 dBFS (the noise floor of 16-bit PCM); a real room
# with nobody talking still sits far above it.
SILENCE_DBFS = -60.0


def verify_transcript_has_speech(transcript_path, audio_path):
    """Fail loudly when transcription produced nothing, and say WHY.

    Without this the empty transcript travelled on: every transcription step was
    reported as successful, and the run died two stages later on "No text
    provided or text is empty" — which points at the AI provider while the real
    cause is upstream. Reported by the owner on a 15-minute screen recording
    whose audio track turned out to be digital silence.
    """
    try:
        with open(transcript_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise RuntimeError(f"Transcript file could not be read: {exc}")

    if _TIMESTAMP.sub("", text).strip():
        return

    peak = peak_dbfs(audio_path)
    if peak is not None and peak <= SILENCE_DBFS:
        raise RuntimeError(
            f"SILENT_AUDIO: no speech recognised because the audio track is "
            f"silent (peak {peak:.1f} dBFS). The recording has a sound track, "
            f"but nothing was captured into it — check that the right input was "
            f"selected when recording.")
    level = f" (peak {peak:.1f} dBFS)" if peak is not None else ""
    raise RuntimeError(
        f"NO_SPEECH: the audio was read but no speech was recognised{level}. "
        f"Check the transcription language matches the recording, and that the "
        f"file actually contains speech.")


# Глобальный tracer
tracer = PerformanceTracer()


def main():
    parser = argparse.ArgumentParser(description='Process video and transcribe')
    parser.add_argument('--video', required=True, help='Path to video file')
    parser.add_argument('--language', default='ru', help='Transcription language')
    parser.add_argument('--model', default='small', help='Whisper model size')
    parser.add_argument(
        '--engine', default='whisper',
        help=('Transcription engine: whisper, faster-whisper, whisperx, vosk, '
              'sherpa-onnx, whisper-cpp, funasr, or sherpa-extra'))
    parser.add_argument('--device', default='auto', help='Device to use: auto, cuda, or cpu')
    parser.add_argument('--initial-prompt', dest='initial_prompt', default='',
                        help='Optional transcription hint (vocabulary/terms) for whisper-family engines')
    parser.add_argument('--diarization', default='sherpa',
                        help='Speaker diarization backend (whisperx): sherpa | pyannote | off')
    parser.add_argument('--hf-token', dest='hf_token',
                        default=os.environ.get('MEETING_SUMMARIZER_HF_TOKEN', ''),
                        help='HuggingFace token for the pyannote diarization backend')
    parser.add_argument('--output-dir', required=True, help='Output directory')

    args = parser.parse_args()

    try:
        # Создаем выходную директорию
        os.makedirs(args.output_dir, exist_ok=True)

        # Получаем базовое имя файла
        base_name = Path(args.video).stem

        # Путь для временного аудио
        temp_audio = os.path.join(args.output_dir, f"{base_name}_temp.wav")

        # Отправляем путь к временному WAV файлу вызывающей стороне СРАЗУ
        log_progress("status.extracting", 0, f"WAV_PATH:{temp_audio}")

        # Извлекаем аудио
        extract_audio(args.video, temp_audio, tracer)

        # Разбиваем на чанки
        audio_chunks = split_audio_chunks(temp_audio)

        # Транскрибируем с выбранным движком (диспетчер управляется реестром)
        adapters = _build_adapters(tracer, _missing_engine_dependency, _get_torch_cuda_status,
                                   args.initial_prompt, args.diarization, args.hf_token)
        engine = resolve_engine(args.engine, set(adapters))
        output_path = adapters[engine](
            audio_chunks, args.model, args.language, args.device,
            args.output_dir, base_name)

        # Before the temp audio is deleted — it is what the diagnosis measures.
        verify_transcript_has_speech(output_path, temp_audio)

        # Удаляем временный полный аудиофайл и чанки ПЕРЕД выводом результата
        try:
            if os.path.exists(temp_audio):
                os.remove(temp_audio)
                log_progress("status.complete", 79, f"Cleaned up temporary audio file")

            # Удаляем все чанки
            for chunk_path in audio_chunks:
                if os.path.exists(chunk_path) and chunk_path != temp_audio:
                    try:
                        os.remove(chunk_path)
                    except Exception as chunk_error:
                        log_progress("status.complete", 79, f"Warning: Could not delete chunk {chunk_path}: {chunk_error}")

            log_progress("status.complete", 79, f"Cleaned up {len(audio_chunks)} temporary chunk files")
        except Exception as cleanup_error:
            log_progress("status.complete", 79, f"Warning: Could not delete temp files: {cleanup_error}")

        log_progress("status.complete", 80, f"Processing complete: {output_path}")

        # Сохраняем trace данные для flame graph
        trace_file = tracer.save_trace(args.output_dir, base_name)
        log_progress("status.complete", 85, f"Сохранен trace производительности: {trace_file}")

        # Явно выводим результат и сбрасываем все буферы
        result = json.dumps({"success": True, "output": output_path, "trace": trace_file})
        print(result, flush=True)
        sys.stdout.flush()
        sys.stderr.flush()

    except Exception as e:
        # Пытаемся удалить temp файл даже при ошибке — ВМЕСТЕ С ЧАНКАМИ.
        # Раньше на ошибке удалялся только `_temp.wav`, а `_temp_chunk_*.wav`
        # оставались: один прерванный прогон 12-минутной встречи оставил на диске
        # 328 МБ (171 МБ полного WAV и девять чанков).
        try:
            if 'temp_audio' in locals():
                for leftover in glob.glob(
                        os.path.join(args.output_dir, f"{base_name}_temp*.wav")):
                    try:
                        os.remove(leftover)
                    except Exception:
                        pass
        except Exception:
            pass

        error_msg = str(e)
        log_progress("status.error", 0, error_msg)
        print(json.dumps({"success": False, "error": error_msg}))
        sys.exit(1)


if __name__ == '__main__':
    main()
