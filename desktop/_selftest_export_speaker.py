"""TODO #19 — Export by speaker: one .txt per speaker (grouped, renamed).

Run: backend\\python\\python.exe desktop\\_selftest_export_speaker.py
"""
import os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desktop.app.backend.speakers import export_by_speaker

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if (detail and not cond) else ""))

TX = ("[00:00:01] [SPEAKER_00]: Привет, начнём встречу.\n"
      "[00:00:05] [SPEAKER_01]: Да, готов.\n"
      "[00:00:09] [SPEAKER_00]: Первый пункт — бюджет.\n"
      "[00:00:14] [SPEAKER_01]: Согласен по бюджету.")

tmp = tempfile.mkdtemp()
written = export_by_speaker(TX, tmp, "meeting")
names = sorted(p.name for p in written)
check("two_files", names == ["meeting_SPEAKER_00.txt", "meeting_SPEAKER_01.txt"], str(names))
s0 = (Path(tmp) / "meeting_SPEAKER_00.txt").read_text(encoding="utf-8")
s1 = (Path(tmp) / "meeting_SPEAKER_01.txt").read_text(encoding="utf-8")
check("spk0_only_own", "бюджет" in s0 and "начнём встречу" in s0 and "готов" not in s0, s0[:60])
check("spk1_only_own", "готов" in s1 and "Согласен" in s1 and "бюджет — " not in s1)
check("spk0_has_timestamps", "[00:00:01]" in s0)

# renamed speakers -> filename uses display name
tmp2 = tempfile.mkdtemp()
w2 = export_by_speaker(TX, tmp2, "meeting", name_map={"SPEAKER_00": "Иван", "SPEAKER_01": "Мария"})
n2 = sorted(p.name for p in w2)
check("renamed_files", n2 == ["meeting_Иван.txt", "meeting_Мария.txt"], str(n2))

# non-diarised transcript -> nothing
plain = export_by_speaker("[00:00:01] просто текст без спикеров", tempfile.mkdtemp(), "m")
check("no_speakers_empty", plain == [], str(plain))

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)}): {', '.join(FAIL)}"); sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)"); sys.exit(0)
