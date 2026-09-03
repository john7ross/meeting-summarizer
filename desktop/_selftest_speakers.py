"""Smoke tests for backend/speakers.py — REAL WhisperX format.

Real diarised line: ``[HH:MM:SS] [SPEAKER_NN]: text``

Run:
    backend\\python\\python.exe desktop\\_selftest_speakers.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desktop.app.backend.speakers import (
    extract_speakers, parse_utterances, speaker_stats,
    rename_in_transcript, apply_edited_utterances, utterances_for_speaker,
    export_by_speaker,
)

PASS, FAIL = [], []

def check(name, cond, detail=""):
    if cond:
        PASS.append(name); print(f"PASS  {name}")
    else:
        FAIL.append(name); print(f"FAIL  {name}" + (f"  ({detail})" if detail else ""))

# ── fixtures (real WhisperX format) ───────────────────────────────────────────
DIARISED = """\
[00:00:01] [SPEAKER_00]: Добро пожаловать на встречу.
[00:00:08] [SPEAKER_01]: Спасибо, я готов.
[00:00:15] [SPEAKER_00]: Начнём с первого пункта.
[00:00:30] [SPEAKER_02]: Можно добавить вопрос?
[00:00:42] [SPEAKER_01]: Конечно.
"""

# faster-whisper format: timestamp but NO speaker label
NO_SPEAKERS = """\
[00:00:01] Добро пожаловать на встречу.
[00:00:08] Сегодня обсудим план.
"""

# ── extract_speakers ──────────────────────────────────────────────────────────
spks = extract_speakers(DIARISED)
check("extract_finds_3", spks == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"], str(spks))
check("extract_sorted_numerically", spks[0] == "SPEAKER_00" and spks[-1] == "SPEAKER_02")
check("extract_empty_for_plain", extract_speakers(NO_SPEAKERS) == [], str(extract_speakers(NO_SPEAKERS)))

# ── parse_utterances ──────────────────────────────────────────────────────────
utts = parse_utterances(DIARISED)
check("parse_5_utterances", len(utts) == 5, str(len(utts)))
check("utt0_timestamp", utts[0].timestamp == "00:00:01")
check("utt0_speaker", utts[0].speaker == "SPEAKER_00")
check("utt0_text", utts[0].text == "Добро пожаловать на встречу.")
check("utt0_is_diarised", utts[0].is_diarised())

# Order preserved (chronological): SPEAKER_00, 01, 00, 02, 01
order = [u.speaker for u in utts]
check("order_preserved", order == ["SPEAKER_00","SPEAKER_01","SPEAKER_00","SPEAKER_02","SPEAKER_01"], str(order))

# plain transcript yields no utterances (no speaker labels)
check("plain_no_utterances", parse_utterances(NO_SPEAKERS) == [])

# ── utterances_for_speaker (chronological) ────────────────────────────────────
sp00 = utterances_for_speaker(utts, "SPEAKER_00")
check("sp00_has_2", len(sp00) == 2)
check("sp00_chrono", sp00[0].timestamp == "00:00:01" and sp00[1].timestamp == "00:00:15")

# ── speaker_stats ─────────────────────────────────────────────────────────────
stats = speaker_stats(utts)
check("stats_3_speakers", len(stats) == 3)
check("stats_sp00_segments", stats["SPEAKER_00"]["segments"] == 2)
check("stats_sp01_segments", stats["SPEAKER_01"]["segments"] == 2)
check("stats_sp02_segments", stats["SPEAKER_02"]["segments"] == 1)
check("stats_words_positive", all(s["words"] > 0 for s in stats.values()))

# ── rename_in_transcript ──────────────────────────────────────────────────────
name_map = {"SPEAKER_00": "Иван", "SPEAKER_01": "Мария", "SPEAKER_02": "Алексей"}
renamed = rename_in_transcript(DIARISED, name_map)
check("rename_has_Ivan",   "[Иван]"    in renamed)
check("rename_has_Maria",  "[Мария]"   in renamed)
check("rename_has_Alexey", "[Алексей]" in renamed)
check("rename_no_SPEAKER", "SPEAKER_" not in renamed)
# timestamps preserved
check("rename_keeps_ts", "[00:00:01]" in renamed and "[00:00:42]" in renamed)
# order preserved: first line still Ivan's welcome
check("rename_order_first", renamed.splitlines()[0] == "[00:00:01] [Иван]: Добро пожаловать на встречу.", renamed.splitlines()[0])

# partial rename leaves others untouched
partial = rename_in_transcript(DIARISED, {"SPEAKER_00": "Иван"})
check("partial_has_Ivan", "[Иван]" in partial)
check("partial_keeps_SPEAKER_01", "[SPEAKER_01]" in partial)

# ── apply_edited_utterances ───────────────────────────────────────────────────
# Edit utterance index 0 text, rename all
edited = {0: "Здравствуйте, коллеги!"}
rebuilt = apply_edited_utterances(utts, name_map, edited)
check("edited_text_applied", "Здравствуйте, коллеги!" in rebuilt)
check("edited_keeps_ts", rebuilt.splitlines()[0].startswith("[00:00:01] [Иван]:"))
check("edited_other_lines_intact", "Начнём с первого пункта." in rebuilt)
check("edited_order_preserved",
      [l.split("]: ")[0].split("[")[-1] for l in rebuilt.splitlines()] ==
      ["Иван","Мария","Иван","Алексей","Мария"],
      str([l for l in rebuilt.splitlines()]))

# no edits -> just rename, equivalent to rename_in_transcript content
rebuilt_no_edit = apply_edited_utterances(utts, name_map, {})
check("no_edit_matches_rename_lines",
      rebuilt_no_edit.splitlines() == renamed.splitlines(),
      "mismatch")

# round-trip: renamed speakers remain editable/exportable
spks_after = extract_speakers(renamed)
check("roundtrip_named_speakers",
      spks_after == ["Иван", "Мария", "Алексей"], str(spks_after))
renamed_stats = speaker_stats(parse_utterances(renamed))
check("roundtrip_named_stats",
      renamed_stats["Иван"]["segments"] == 2
      and renamed_stats["Мария"]["segments"] == 2
      and renamed_stats["Алексей"]["segments"] == 1,
      str(renamed_stats))
speaker_export_dir = Path(tempfile.mkdtemp())
speaker_files = export_by_speaker(renamed, speaker_export_dir, "meeting")
check("roundtrip_named_export_count", len(speaker_files) == 3, str(speaker_files))
check("roundtrip_named_export_content",
      any(p.name == "meeting_Иван.txt"
          and "Добро пожаловать" in p.read_text(encoding="utf-8")
          for p in speaker_files))

# ── edge: empty transcript ────────────────────────────────────────────────────
check("empty_extract", extract_speakers("") == [])
check("empty_parse", parse_utterances("") == [])

# ── summary ───────────────────────────────────────────────────────────────────
print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)} failed): {', '.join(FAIL)}")
    sys.exit(1)
else:
    print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
    sys.exit(0)
