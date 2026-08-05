"""TODO #16 — Contextual Memory: JobRunner injects prior project meetings' summaries.

Verifies _contextual_memory_block: builds context from prior same-project meetings,
and returns '' when disabled / no project / no priors.

Run: backend\\python\\python.exe desktop\\_selftest_contextual_memory.py
"""
import os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication
_app = QCoreApplication.instance() or QCoreApplication([])

from desktop.app.core.history import HistoryStore, versioned_filename
from desktop.app.core.pipeline import JobRunner

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if (detail and not cond) else ""))

tmp = tempfile.mkdtemp()
store = HistoryStore(path=os.path.join(tmp, "history.json"),
                     transcripts_root=Path(tmp) / "transcripts")

def add_meeting(name, project, summary_text):
    eid = store.add(f"C:/videos/{name}.mp4")
    store.set_project(eid, project)
    job = store.job_dir(eid)
    s = job / versioned_filename(name, "summary", 1, ".txt")
    s.write_text(summary_text, encoding="utf-8")
    store.add_summary_version(eid, s, provider="local")
    return eid

# two prior meetings in project "Alpha", one in "Beta"
add_meeting("m1", "Alpha", "Обсудили бюджет проекта Alpha и сроки.")
add_meeting("m2", "Alpha", "Договорились по архитектуре Alpha.")
add_meeting("m3", "Beta", "Встреча по проекту Beta.")
cur = store.add("C:/videos/current.mp4")   # current meeting, no summary yet

def runner(settings):
    return JobRunner(cur, "C:/videos/current.mp4", settings, store)

# enabled + project Alpha -> context has both Alpha summaries, not Beta
block = runner({"useContextualMemory": True, "projectId": "Alpha"})._contextual_memory_block()
check("ctx_has_alpha1", "бюджет проекта Alpha" in block, block[:80])
check("ctx_has_alpha2", "архитектуре Alpha" in block)
check("ctx_excludes_beta", "проекту Beta" not in block)
check("ctx_has_header", "предыдущих встреч проекта" in block and "Alpha" in block)

# disabled -> empty
check("ctx_off_empty", runner({"useContextualMemory": False, "projectId": "Alpha"})._contextual_memory_block() == "")
# no project -> empty
check("ctx_noproject_empty", runner({"useContextualMemory": True, "projectId": ""})._contextual_memory_block() == "")
# project with no priors -> empty
check("ctx_noprior_empty", runner({"useContextualMemory": True, "projectId": "Gamma"})._contextual_memory_block() == "")

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)}): {', '.join(FAIL)}"); sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)"); sys.exit(0)
