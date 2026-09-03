#!/usr/bin/env python3
"""Live (streaming) transcription worker.

    <python> backend/live_stt.py --engine faster-whisper --model medium \
        --language ru --channels 2 --transcript-file <path>

Reads raw PCM (16-bit little-endian, ``--sample-rate``, ``--channels``
interleaved) from **stdin** and writes one JSON object per line to **stdout** —
the same "front end assembles argv and reads JSON lines" contract every other
backend module uses, so the desktop and the web cabinet drive it identically and
neither of them holds a model in its own process.

Why a separate module and not a flag on ``processor.py``: the batch processor is
file-in / file-out and loads the model per run. Live is stream-in / event-out and
must hold ONE loaded model for the whole meeting. Sharing a module would mean two
lifecycles in one file; sharing the *registry* and the *engine adapters*, which is
what actually matters, already happens.

Decoding runs on a worker thread behind a bounded queue. If it did not, a slow
model would block the read from stdin, the pipe would fill, and the recorder —
which is on the UI thread — would stall. Recording must never pay for
transcription being slow: the WAV on disk stays the source of truth.

Output lines::

    {"type":"ready","engine":...,"model":...,"device":...}
    {"type":"segment","index":1,"start":12.3,"source":"mic","text":"..."}
    {"type":"lag","queued":6}
    {"type":"error","message":"..."}
    {"type":"done","segments":42,"audio_seconds":1830.2}
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from processing import live_engines
from processing.live_vad import Segmenter

READ_BLOCK = 8192          # bytes per stdin read (~0.25 s of 16 kHz mono)
QUEUE_LIMIT = 64           # utterances; ~16 min of backlog before we shed load
LAG_WARN_AT = 4            # queued utterances that mean "recognition is behind"

_stdout_lock = threading.Lock()


def _force_utf8_streams() -> None:
    """Recognised text is Cyrillic far more often than not, and the Windows
    console default (cp1251/cp866) turns it into question marks before the
    caller ever sees it. The reader on the other end of the pipe decodes UTF-8,
    so the writer has to produce it."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):        # pragma: no cover
            pass


def emit(payload: dict) -> None:
    """One JSON object per line, flushed — the caller reads this line by line."""
    with _stdout_lock:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"[{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}]"


SOURCE_LABEL = {"mic": "MIC", "system": "SYSTEM"}


def transcript_line(timestamp: str, source: str, text: str) -> str:
    """One transcript line in the project's own format.

    Deliberately the SAME shape the batch engines produce, because this file is
    not a live-mode curiosity — it can be fed straight into the normal
    summary/analysis pipeline, and everything downstream (the speakers dialog,
    the speaker-aware prompt, the exporters) already parses that shape:

        two sources  ->  ``[HH:MM:SS] [MIC]: text``   (a diarised transcript)
        one source   ->  ``[HH:MM:SS] text``          (a plain transcript)

    A mono recording has nobody to attribute to, so it gets no label rather than
    a meaningless one; a stereo recording labels the channel the speech came
    from, exactly where a diarised transcript puts ``[SPEAKER_00]``.
    """
    label = SOURCE_LABEL.get(source)
    return f"{timestamp} [{label}]: {text}" if label else f"{timestamp} {text}"


class TranscriptFile:
    """Append-only live transcript next to the recording.

    Mic Recorder keeps its live transcript in renderer memory only, so closing
    the window loses it. Here every line is on disk the moment it is recognised:
    if the recording itself is ever lost, the live text is still a usable record
    of the meeting.
    """

    def __init__(self, path: str = ""):
        self.path = path or ""
        self._handle = None
        if not self.path:
            return
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a", encoding="utf-8")

    def append(self, timestamp: str, source: str, text: str) -> None:
        if self._handle is None:
            return
        self._handle.write(transcript_line(timestamp, source, text) + "\n")
        self._handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            finally:
                self._handle = None


def decode_loop(engine, work: "queue.Queue", transcript: TranscriptFile,
                sample_rate: int, state: dict) -> None:
    """Drain the queue: one utterance -> one recognised line."""
    while True:
        item = work.get()
        if item is None:
            work.task_done()
            return
        utterance = item
        started = time.time()
        try:
            text = engine.transcribe(utterance.pcm, sample_rate)
        except Exception as exc:                       # noqa: BLE001
            # One bad utterance must not end the meeting's live transcription.
            emit({"type": "warning", "message": f"decode failed: {exc}"})
            work.task_done()
            continue
        if text:
            state["segments"] += 1
            stamp = format_timestamp(utterance.start)
            transcript.append(stamp, utterance.source, text)
            emit({
                "type": "segment",
                "index": state["segments"],
                "start": round(utterance.start, 2),
                "duration": round(utterance.duration, 2),
                "timestamp": stamp,
                "source": utterance.source,
                "forced": utterance.forced,
                "text": text,
                "latency": round(time.time() - started, 2),
                "queued": work.qsize(),
            })
        work.task_done()


def main() -> int:
    parser = argparse.ArgumentParser(description="Live streaming transcription")
    parser.add_argument("--engine", default="faster-whisper",
                        help=f"one of: {', '.join(live_engines.SUPPORTED)}")
    parser.add_argument("--model", default="", help="model id for the engine")
    parser.add_argument("--language", default="ru")
    parser.add_argument("--device", default="auto", help="auto | cuda | cpu")
    parser.add_argument("--initial-prompt", dest="initial_prompt", default="",
                        help="vocabulary hint for the whisper family")
    parser.add_argument("--sample-rate", dest="sample_rate", type=int, default=16000)
    parser.add_argument("--channels", type=int, default=1,
                        help="1 = microphone only; 2 = mic left / system right")
    parser.add_argument("--transcript-file", dest="transcript_file", default="",
                        help="append recognised lines here as they arrive")
    parser.add_argument("--silence-ms", dest="silence_ms", type=int, default=700)
    parser.add_argument("--max-utterance-ms", dest="max_utterance_ms",
                        type=int, default=15000)
    parser.add_argument("--min-speech-ms", dest="min_speech_ms", type=int, default=400)
    args = parser.parse_args()
    _force_utf8_streams()

    # HuggingFace downloads fail behind a corporate proxy exactly as they do for
    # the batch processor; keep the same escape hatch.
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(var, None)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"

    emit({"type": "loading", "engine": args.engine, "model": args.model})
    try:
        engine = live_engines.load(
            args.engine, args.model, args.language, args.device,
            args.initial_prompt)
    except Exception as exc:                           # noqa: BLE001
        emit({"type": "error", "message": str(exc)})
        return 1

    emit({
        "type": "ready",
        "engine": engine.name,
        "model": getattr(engine, "model_id", args.model),
        "device": getattr(engine, "device", "cpu"),
        "sample_rate": args.sample_rate,
        "channels": args.channels,
    })

    segmenter = Segmenter(sample_rate=args.sample_rate, channels=args.channels,
                          silence_ms=args.silence_ms,
                          min_speech_ms=args.min_speech_ms,
                          max_utterance_ms=args.max_utterance_ms)
    transcript = TranscriptFile(args.transcript_file)
    work: "queue.Queue" = queue.Queue(maxsize=QUEUE_LIMIT)
    state = {"segments": 0}
    worker = threading.Thread(
        target=decode_loop,
        args=(engine, work, transcript, args.sample_rate, state),
        daemon=True)
    worker.start()

    lag_reported = False
    stream = sys.stdin.buffer
    try:
        while True:
            block = stream.read(READ_BLOCK)
            if not block:
                break
            for utterance in segmenter.feed(block):
                try:
                    work.put_nowait(utterance)
                except queue.Full:
                    # The model cannot keep up with the room. Say so instead of
                    # growing the backlog until memory runs out: the recording
                    # is unaffected and the batch pass will transcribe it fully.
                    emit({"type": "warning",
                          "message": "recognition is too far behind; "
                                     "utterance dropped from the live panel"})
                if work.qsize() >= LAG_WARN_AT and not lag_reported:
                    lag_reported = True
                    emit({"type": "lag", "queued": work.qsize()})
                elif work.qsize() < LAG_WARN_AT:
                    lag_reported = False
        for utterance in segmenter.flush():
            try:
                work.put_nowait(utterance)
            except queue.Full:
                pass
    except KeyboardInterrupt:
        pass
    except Exception as exc:                           # noqa: BLE001
        emit({"type": "error", "message": f"stream read failed: {exc}"})

    work.put(None)
    worker.join(timeout=120)
    transcript.close()
    engine.close()
    emit({"type": "done", "segments": state["segments"],
          "audio_seconds": round(segmenter.position, 2)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
