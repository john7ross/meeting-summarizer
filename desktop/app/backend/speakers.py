"""Speaker management utilities: transcript parsing and speaker extraction.

Used by both SpeakersDialog (UI) and JobRunner (pipeline gating).

Real WhisperX diarised output format (one line per utterance, chronological)::

    [00:01:23] [SPEAKER_00]: Добро пожаловать на встречу.
    [00:01:45] [SPEAKER_01]: Спасибо, я готов.
    [00:02:10] [SPEAKER_00]: Начнём с первого пункта.

Each line carries a [HH:MM:SS] timestamp, then [SPEAKER_NN]: then the text.
Lines are already time-ordered (segments come from WhisperX in order), so the
dialogue sequence is preserved — we only rename the [SPEAKER_NN] label, never
reorder.

API
---
``extract_speakers(text)`` -> sorted unique speaker labels (e.g. ["SPEAKER_00"])
``parse_utterances(text)`` -> ordered list of Utterance(timestamp, speaker, text, raw)
``speaker_stats(utterances)`` -> {speaker: {"segments", "words"}}
``rename_in_transcript(text, name_map)`` -> transcript with [SPEAKER_NN] labels
    replaced by [DisplayName], timestamps and order preserved.
``apply_edited_utterances(utterances, name_map)`` -> rebuild from (edited)
    utterance list, used when the user has edited individual lines.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# A diarised line: optional [HH:MM:SS] timestamp, then [SPEAKER label]: text
# Speaker label is captured loosely so renamed labels survive a second parse.
_LINE_RE = re.compile(
    r"^\s*"
    r"(?:\[(?P<ts>\d{1,2}:\d{2}:\d{2}(?:\.\d+)?)\]\s*)?"   # optional timestamp
    r"\[(?P<speaker>[^\]]+)\]\s*:\s*"                        # [label]:
    r"(?P<text>.*)$"                                         # the utterance text
)

# A label that looks like a raw WhisperX speaker id, e.g. SPEAKER_00 / Speaker 1
_SPEAKER_ID_RE = re.compile(r"^(?:SPEAKER|Speaker)[_\s]?(\d+)$")


@dataclass
class Utterance:
    """One diarised line."""
    timestamp: str        # "" if none
    speaker: str          # raw label as found, e.g. "SPEAKER_00"
    text: str             # utterance text (no tags)
    raw: str              # the original full line, verbatim

    def is_diarised(self) -> bool:
        return _SPEAKER_ID_RE.match(self.speaker) is not None


def _is_speaker_label(label: str) -> bool:
    """True if *label* looks like a diarisation speaker id (not a timestamp)."""
    return bool(_SPEAKER_ID_RE.match(label.strip()))


def parse_utterances(transcript: str) -> list[Utterance]:
    """Parse *transcript* into an ordered list of Utterance.

    Only lines matching the ``[ts] [SPEAKER]: text`` shape are treated as
    utterances. Lines that do not match (blank lines, stray text) are skipped
    for the structured view but never lost — callers that need verbatim
    reconstruction use ``rename_in_transcript`` on the raw text instead.
    """
    utterances: list[Utterance] = []
    for line in transcript.splitlines():
        if not line.strip():
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        speaker = m.group("speaker").strip()
        # Guard: a bare "[00:00:00]" with no speaker would put the timestamp in
        # the speaker group — reject labels that are themselves timestamps.
        if re.match(r"^\d{1,2}:\d{2}:\d{2}", speaker):
            continue
        utterances.append(Utterance(
            timestamp=(m.group("ts") or "").strip(),
            speaker=speaker,
            text=m.group("text").strip(),
            raw=line,
        ))
    return utterances


def extract_speakers(transcript: str) -> list[str]:
    """Return unique speaker labels in first-appearance order.

    Returns [] when no diarisation markers are present (e.g. faster-whisper
    output, which is plain ``[HH:MM:SS] text`` with no speaker label). Human
    names saved by the speaker editor remain valid labels, so the dialog and
    per-speaker export keep working after a rename.
    """
    found: list[str] = []
    for utt in parse_utterances(transcript):
        if utt.speaker not in found:
            found.append(utt.speaker)
    return found


def speaker_stats(utterances: list[Utterance]) -> dict[str, dict]:
    """Compute per-speaker segment and word counts from parsed utterances."""
    stats: dict[str, dict] = {}
    for utt in utterances:
        s = stats.setdefault(utt.speaker, {"segments": 0, "words": 0})
        s["segments"] += 1
        s["words"] += len(utt.text.split()) if utt.text else 0
    return stats


def utterances_for_speaker(
    utterances: list[Utterance], speaker: str
) -> list[Utterance]:
    """All utterances by *speaker*, in chronological (document) order."""
    return [u for u in utterances if u.speaker == speaker]


def _format_line(timestamp: str, display: str, text: str) -> str:
    """Render one line in the canonical ``[ts] [Name]: text`` form."""
    prefix = f"[{timestamp}] " if timestamp else ""
    return f"{prefix}[{display}]: {text}"


def rename_in_transcript(transcript: str, name_map: dict[str, str]) -> str:
    """Return *transcript* with diarisation labels renamed per *name_map*.

    Timestamps, order, and any non-utterance lines are preserved verbatim.
    Only the ``[SPEAKER_NN]`` token inside matched lines is swapped for the
    mapped display name. Unmapped speakers are left unchanged.
    """
    out_lines: list[str] = []
    for line in transcript.splitlines():
        m = _LINE_RE.match(line)
        if not m:
            out_lines.append(line)
            continue
        speaker = m.group("speaker").strip()
        if re.match(r"^\d{1,2}:\d{2}:\d{2}", speaker):
            out_lines.append(line)
            continue
        display = name_map.get(speaker, speaker)
        if display == speaker:
            out_lines.append(line)
        else:
            out_lines.append(_format_line(
                (m.group("ts") or "").strip(), display, m.group("text").strip()))
    return "\n".join(out_lines)


def apply_edited_utterances(
    utterances: list[Utterance],
    name_map: dict[str, str],
    edited_text: Optional[dict[int, str]] = None,
) -> str:
    """Rebuild a transcript from a (possibly edited) utterance list.

    *utterances* is the full ordered list (chronological). *name_map* renames
    speaker labels. *edited_text* maps an utterance index -> new text for that
    single line; absent indices keep their original text. Timestamps and the
    chronological order are always preserved.
    """
    out_lines: list[str] = []
    for idx, utt in enumerate(utterances):
        display = name_map.get(utt.speaker, utt.speaker)
        text = edited_text.get(idx, utt.text) if edited_text else utt.text
        out_lines.append(_format_line(utt.timestamp, display, text))
    return "\n".join(out_lines)


def export_by_speaker(transcript: str, out_dir, base_name: str,
                      name_map: "dict[str, str] | None" = None) -> list:
    """Write ONE .txt per speaker into *out_dir*: ``<base_name>_<display>.txt`` with
    that speaker's lines (``[ts] text``, chronological). Honours renamed speakers via
    *name_map*. Returns the list of written Paths (empty if no diarised speakers)."""
    import re
    from pathlib import Path
    name_map = name_map or {}
    utterances = parse_utterances(transcript)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for speaker in extract_speakers(transcript):
        lines = utterances_for_speaker(utterances, speaker)
        content = "\n".join(
            (f"[{u.timestamp}] {u.text}" if u.timestamp else u.text)
            for u in lines if u.text.strip()).strip()
        if not content:
            continue
        display = (name_map.get(speaker) or speaker).strip()
        safe = re.sub(r"[^\w.\- ]", "_", display).strip() or speaker
        path = out / f"{base_name}_{safe}.txt"
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written
