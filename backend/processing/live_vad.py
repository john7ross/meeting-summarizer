"""Utterance segmentation for the live (streaming) transcription worker.

The streaming worker gets a continuous PCM stream and has to decide WHEN a piece
of it is worth handing to a recognition engine. Sending fixed 5-second windows
cuts words in half; sending everything at once is not "live" at all. So this
module carves the stream into *utterances*: speech bounded by silence.

Deliberately stdlib-only (``audioop``, same as ``core/recorder.py``) — no numpy,
no torch, no webrtcvad. It therefore imports in any environment and is unit
testable without a microphone or a model.

Energy VAD with two refinements that matter in a real room:

* **Adaptive noise floor.** A fan, an open window or a noisy headset raises the
  baseline; a fixed threshold either never triggers or never stops. The floor is
  an exponential moving average of the quiet frames, so it follows the room.
* **Hysteresis.** Speech STARTS at a higher threshold than it STOPS at. With one
  threshold the tail of every sentence — which is quieter than its middle — gets
  clipped, and the engine receives "давайте перенесём" without "на пятницу".

Pre-roll matters for the same reason from the other side: by the time the level
crosses the start threshold the first consonant is already gone, so a short
ring buffer of the frames *before* the trigger is prepended to the utterance.
"""
from __future__ import annotations

import audioop
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

SAMPLE_WIDTH = 2          # bytes (Int16) — the only width the pipeline uses


@dataclass
class Utterance:
    """One speech piece ready for recognition.

    ``pcm`` is always MONO int16 at the stream's sample rate — engines want mono,
    and the stereo layout (mic left / system right) survives only as
    ``channel_rms``, which is what the source label is derived from.
    """
    pcm: bytes
    start: float                       # seconds from the start of the stream
    duration: float
    channel_rms: Tuple[float, ...] = field(default_factory=tuple)
    forced: bool = False               # cut by the length cap, speech continues

    @property
    def source(self) -> str:
        """'mic' / 'system' / 'mix' — who was louder over this utterance.

        Two capture sources mixed into two channels give speaker attribution for
        free: the microphone is the local participant, the loopback channel is
        everyone else. It is a heuristic, not diarisation — when both channels
        are within a few dB of each other it honestly says 'mix' instead of
        guessing.
        """
        if len(self.channel_rms) != 2:
            return "mix"
        left, right = self.channel_rms
        louder, quieter = (left, right) if left >= right else (right, left)
        if louder <= 0 or louder < quieter * 1.6:
            return "mix"
        return "mic" if left > right else "system"


class Segmenter:
    """Feed PCM in, get complete utterances out.

    All durations are milliseconds. The defaults are tuned for meeting speech:
    ``silence_ms`` shorter than 500 splits mid-sentence on a breath, longer than
    ~1000 makes the live panel feel dead.
    """

    def __init__(self, sample_rate: int = 16000, channels: int = 1,
                 frame_ms: int = 20, silence_ms: int = 700,
                 min_speech_ms: int = 400, max_utterance_ms: int = 15000,
                 preroll_ms: int = 240, start_factor: float = 3.0,
                 stop_factor: float = 1.5, min_rms: int = 120):
        if channels not in (1, 2):
            raise ValueError("Segmenter supports 1 or 2 channels")
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.frame_bytes = max(
            SAMPLE_WIDTH * self.channels,
            int(self.sample_rate * frame_ms / 1000) * SAMPLE_WIDTH * self.channels)
        self.frame_seconds = self.frame_bytes / float(
            self.sample_rate * SAMPLE_WIDTH * self.channels)
        self.silence_frames = max(1, int(silence_ms / frame_ms))
        self.min_speech_frames = max(1, int(min_speech_ms / frame_ms))
        self.max_frames = max(self.min_speech_frames, int(max_utterance_ms / frame_ms))
        self.preroll_frames = max(0, int(preroll_ms / frame_ms))
        self.start_factor = float(start_factor)
        self.stop_factor = float(stop_factor)
        self.min_rms = float(min_rms)

        self._tail = b""                    # bytes left over from the last feed
        self._floor = float(min_rms)        # adaptive noise floor (RMS units)
        self._preroll: List[bytes] = []     # ring buffer of pre-trigger frames
        self._speech: List[bytes] = []      # frames of the utterance being built
        self._silence_run = 0
        self._in_speech = False
        self._frames_seen = 0               # total frames consumed (stream clock)
        self._start_frame = 0               # frame index the utterance started at
        self._left_sq = 0.0                 # per-channel energy accumulators
        self._right_sq = 0.0
        self._energy_frames = 0

    # -- public API ------------------------------------------------------
    @property
    def position(self) -> float:
        """Seconds of audio consumed so far — the stream clock."""
        return self._frames_seen * self.frame_seconds

    def feed(self, pcm: bytes) -> List[Utterance]:
        """Consume PCM (any length) and return the utterances it completed."""
        if not pcm:
            return []
        data = self._tail + pcm
        out: List[Utterance] = []
        offset = 0
        while offset + self.frame_bytes <= len(data):
            frame = data[offset:offset + self.frame_bytes]
            offset += self.frame_bytes
            done = self._consume(frame)
            if done is not None:
                out.append(done)
        self._tail = data[offset:]
        return out

    def flush(self) -> List[Utterance]:
        """End of stream: emit whatever speech is still buffered.

        The tail (a partial frame) is dropped — at 20 ms it cannot carry a word,
        and padding it would only feed the engine silence.
        """
        self._tail = b""
        if not self._in_speech or len(self._speech) < self.min_speech_frames:
            self._reset_utterance()
            return []
        return [self._emit(forced=False)]

    # -- internals -------------------------------------------------------
    def _consume(self, frame: bytes) -> Optional[Utterance]:
        self._frames_seen += 1
        mono = self._to_mono(frame)
        level = float(audioop.rms(mono, SAMPLE_WIDTH))

        start_threshold = max(self.min_rms, self._floor * self.start_factor)
        stop_threshold = max(self.min_rms * 0.6, self._floor * self.stop_factor)

        if not self._in_speech:
            # Track the room only while nobody is talking, otherwise speech
            # itself would raise the floor until the VAD goes deaf.
            self._floor = self._floor * 0.95 + level * 0.05
            self._preroll.append(frame)
            if len(self._preroll) > self.preroll_frames:
                self._preroll.pop(0)
            if level >= start_threshold:
                self._in_speech = True
                self._speech = list(self._preroll)
                self._start_frame = self._frames_seen - len(self._speech)
                self._preroll = []
                self._silence_run = 0
                self._accumulate_energy(self._speech)
            return None

        self._speech.append(frame)
        self._accumulate_energy([frame])
        self._silence_run = 0 if level >= stop_threshold else self._silence_run + 1

        if self._silence_run >= self.silence_frames:
            if len(self._speech) - self._silence_run >= self.min_speech_frames:
                return self._emit(forced=False)
            # Too short to be a word: drop it, but let the floor learn from it.
            self._reset_utterance()
            return None

        if len(self._speech) >= self.max_frames:
            # A monologue never pauses long enough. Cut it so the panel keeps
            # updating; ``forced`` tells the caller the thought continues.
            return self._emit(forced=True)
        return None

    def _to_mono(self, frame: bytes) -> bytes:
        if self.channels == 1:
            return frame
        return audioop.tomono(frame, SAMPLE_WIDTH, 0.5, 0.5)

    def _accumulate_energy(self, frames: List[bytes]) -> None:
        if self.channels != 2:
            return
        for frame in frames:
            left = audioop.tomono(frame, SAMPLE_WIDTH, 1, 0)
            right = audioop.tomono(frame, SAMPLE_WIDTH, 0, 1)
            self._left_sq += float(audioop.rms(left, SAMPLE_WIDTH)) ** 2
            self._right_sq += float(audioop.rms(right, SAMPLE_WIDTH)) ** 2
            self._energy_frames += 1

    def _emit(self, forced: bool) -> Utterance:
        frames = self._speech
        pcm = self._to_mono(b"".join(frames))
        start = self._start_frame * self.frame_seconds
        duration = len(frames) * self.frame_seconds
        rms: Tuple[float, ...] = ()
        if self.channels == 2 and self._energy_frames:
            rms = ((self._left_sq / self._energy_frames) ** 0.5,
                   (self._right_sq / self._energy_frames) ** 0.5)
        self._reset_utterance()
        if forced:
            # Speech did not stop — stay in-speech so the next frames continue
            # the same stretch instead of waiting for a fresh trigger.
            self._in_speech = True
            self._start_frame = self._frames_seen
        return Utterance(pcm=pcm, start=start, duration=duration,
                         channel_rms=rms, forced=forced)

    def _reset_utterance(self) -> None:
        self._speech = []
        self._preroll = []
        self._silence_run = 0
        self._in_speech = False
        self._left_sq = 0.0
        self._right_sq = 0.0
        self._energy_frames = 0
