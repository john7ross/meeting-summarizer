"""Smoke tests for SpeakersDialog + main_window integration (offscreen).

Real WhisperX format: ``[HH:MM:SS] [SPEAKER_NN]: text``

Run:
    set QT_QPA_PLATFORM=offscreen && backend\\python\\python.exe desktop\\_selftest_speakers_ui.py
"""
import sys, tempfile, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

from desktop.app.ui.speakers_dialog import SpeakersDialog
from desktop.app.backend.speakers import extract_speakers

PASS, FAIL = [], []

def check(name, cond, detail=""):
    if cond:
        PASS.append(name); print(f"PASS  {name}")
    else:
        FAIL.append(name); print(f"FAIL  {name}" + (f"  ({detail})" if detail else ""))

DIARISED = """\
[00:00:01] [SPEAKER_00]: Добро пожаловать на встречу.
[00:00:08] [SPEAKER_01]: Спасибо, я готов.
[00:00:15] [SPEAKER_00]: Начнём с первого пункта.
[00:00:30] [SPEAKER_02]: Можно добавить вопрос?
"""
PLAIN = "[00:00:01] Просто текст без спикеров."

# ── dialog constructs ─────────────────────────────────────────────────────────
try:
    dlg = SpeakersDialog(DIARISED, language="ru")
    check("dialog_constructs", True)
except Exception as e:
    check("dialog_constructs", False, str(e)); sys.exit(1)

check("has_3_cards", len(dlg._cards) == 3)
check("has_3_speakers", len(dlg._speakers) == 3)

# Default names = raw labels
check("card0_default", dlg._cards[0].speaker_name() == "SPEAKER_00")
check("card1_default", dlg._cards[1].speaker_name() == "SPEAKER_01")
check("card2_default", dlg._cards[2].speaker_name() == "SPEAKER_02")

# SPEAKER_00 card has 2 utterance rows (chronological), both with their text
sp00_rows = dlg._cards[0]._rows
check("card0_has_2_rows", len(sp00_rows) == 2)
check("card0_row0_text", "Добро пожаловать" in sp00_rows[0].text())
check("card0_row1_text", "Начнём с первого пункта" in sp00_rows[1].text())
# global indices map to chronological positions 0 and 2
check("card0_global_indices", [r.global_index for r in sp00_rows] == [0, 2],
      str([r.global_index for r in sp00_rows]))

# SPEAKER_01 card has 1 row
check("card1_has_1_row", len(dlg._cards[1]._rows) == 1)
check("card1_row_text", "Спасибо" in dlg._cards[1]._rows[0].text())

# ── name assignment + save ────────────────────────────────────────────────────
dlg._cards[0].name_edit.setText("Иван")
dlg._cards[1].name_edit.setText("Мария")
# SPEAKER_02 left default

received = {}
dlg.accepted_data.connect(lambda txt, parts: received.update(t=txt, p=parts))
dlg._on_save()

check("accepted_emitted", "t" in received)
t = received.get("t", "")
check("transcript_has_Ivan",  "[Иван]"  in t)
check("transcript_has_Maria", "[Мария]" in t)
check("transcript_no_SPEAKER_00", "SPEAKER_00" not in t)
check("transcript_no_SPEAKER_01", "SPEAKER_01" not in t)
# SPEAKER_02 not renamed -> still present
check("transcript_keeps_SPEAKER_02", "SPEAKER_02" in t)
# timestamps + order preserved
check("transcript_keeps_ts", "[00:00:01]" in t and "[00:00:30]" in t)
lines = t.splitlines()
check("first_line_ivan", lines[0] == "[00:00:01] [Иван]: Добро пожаловать на встречу.", lines[0])
check("third_line_ivan", lines[2] == "[00:00:15] [Иван]: Начнём с первого пункта.", lines[2])

# participants
check("participants_ivan",  "Иван"  in received.get("p", []))
check("participants_maria", "Мария" in received.get("p", []))
check("participants_sp02_default", "SPEAKER_02" in received.get("p", []))

# ── edit a line, then save ────────────────────────────────────────────────────
dlg2 = SpeakersDialog(DIARISED, language="ru")
dlg2._cards[0].name_edit.setText("Иван")
dlg2._cards[0]._rows[0].edit.setPlainText("Здравствуйте, коллеги!")
rec2 = {}
dlg2.accepted_data.connect(lambda txt, parts: rec2.update(t=txt))
dlg2._on_save()
check("edit_applied", "Здравствуйте, коллеги!" in rec2.get("t", ""))
check("edit_keeps_ts", rec2.get("t","").splitlines()[0].startswith("[00:00:01] [Иван]:"))

# ── cancel ────────────────────────────────────────────────────────────────────
dlg3 = SpeakersDialog(DIARISED, language="ru")
cancelled = [False]
dlg3.cancelled.connect(lambda: cancelled.__setitem__(0, True))
dlg3._on_cancel()
check("cancel_emitted", cancelled[0])

# ── plain transcript: no cards, cancel on save ────────────────────────────────
dlg4 = SpeakersDialog(PLAIN, language="ru")
check("plain_no_speakers", len(dlg4._speakers) == 0)
cancelled4 = [False]
dlg4.cancelled.connect(lambda: cancelled4.__setitem__(0, True))
dlg4._on_save()
check("plain_save_cancels", cancelled4[0])

# ── english ───────────────────────────────────────────────────────────────────
check("en_title", "Speaker" in SpeakersDialog(DIARISED, language="en").windowTitle())

# ── MainWindow integration ────────────────────────────────────────────────────
from desktop.app.ui.main_window import MainWindow
from desktop.app.core.history import HistoryStore

tmpdir = tempfile.mkdtemp()
store = HistoryStore(os.path.join(tmpdir, "history.json"))
mw = MainWindow(settings={}, store=store, queue=None, language="ru")

check("mw_has_btn", hasattr(mw, "btn_speakers"))
check("mw_disabled_initial", not mw.btn_speakers.isEnabled())

mw._current_transcript = DIARISED
mw._update_speakers_button()
check("mw_enabled_diarised", mw.btn_speakers.isEnabled())

mw._current_transcript = PLAIN
mw._update_speakers_button()
check("mw_disabled_plain", not mw.btn_speakers.isEnabled())

# ── summary ───────────────────────────────────────────────────────────────────
print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)} failed): {', '.join(FAIL)}")
    sys.exit(1)
else:
    print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
    sys.exit(0)
