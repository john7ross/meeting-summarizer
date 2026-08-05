"""TODO #18 — Session/meeting statistics: aggregate + dialog (offscreen).

Run: set QT_QPA_PLATFORM=offscreen && backend\\python\\python.exe desktop\\_selftest_stats.py
"""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

from desktop.app.core.history import HistoryStore, versioned_filename
from desktop.app.ui.stats_dialog import aggregate, StatsDialog

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if (detail and not cond) else ""))

tmp = tempfile.mkdtemp()
store = HistoryStore(path=os.path.join(tmp, "history.json"),
                     transcripts_root=Path(tmp) / "transcripts")

# m1: transcript(3 words) + summary + project Alpha
e1 = store.add("C:/v/m1.mp4"); store.set_project(e1, "Alpha")
j1 = store.job_dir(e1); tx1 = j1 / "m1_raw.txt"; tx1.write_text("один два три", encoding="utf-8")
store.set_transcript(e1, tx1)
s1 = j1 / versioned_filename("m1", "summary", 1, ".txt"); s1.write_text("S", encoding="utf-8")
store.add_summary_version(e1, s1, provider="local")
# m2: transcript(2 words) + project Alpha
e2 = store.add("C:/v/m2.mp4"); store.set_project(e2, "Alpha")
j2 = store.job_dir(e2); tx2 = j2 / "m2_raw.txt"; tx2.write_text("привет мир", encoding="utf-8")
store.set_transcript(e2, tx2)
# m3: nothing, no project
store.add("C:/v/m3.mp4")

s = aggregate(store)
check("total_3", s["total"] == 3, str(s["total"]))
check("with_tx_2", s["with_tx"] == 2, str(s["with_tx"]))
check("with_sum_1", s["with_sum"] == 1, str(s["with_sum"]))
check("words_5", s["words"] == 5, str(s["words"]))
check("project_alpha_2", s["by_project"].get("Alpha") == 2, str(s["by_project"]))
check("project_empty_1", s["by_project"].get("") == 1, str(s["by_project"]))

dlg = StatsDialog(store, language="ru")
html = dlg.view.toHtml()
check("dialog_renders_total", "Всего встреч" in html)
check("dialog_shows_alpha", "Alpha" in html)

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)}): {', '.join(FAIL)}"); sys.stdout.flush(); os._exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)"); sys.stdout.flush(); os._exit(0)
