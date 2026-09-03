"""Cross-process Windows regression for history.json atomic updates."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desktop.app.core.history import HistoryStore

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(("PASS  " if condition else "FAIL  ") + name +
          (f"  ({detail})" if detail and not condition else ""))


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    history = root / "history.json"
    store = HistoryStore(history, root / "transcripts")
    ids = [store.add("C:/x/a.mkv"), store.add("C:/x/b.mkv")]

    code = r"""
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from desktop.app.core.history import HistoryStore
store = HistoryStore(Path(sys.argv[2]), Path(sys.argv[3]))
jid = int(sys.argv[4])
prefix = sys.argv[5]
for i in range(35):
    store.set_project(jid, f"{prefix}-{i}")
    time.sleep(0.002)
"""
    procs = [
        subprocess.Popen([
            sys.executable, "-c", code, str(ROOT), str(history),
            str(root / "transcripts"), str(ids[0]), "A"]),
        subprocess.Popen([
            sys.executable, "-c", code, str(ROOT), str(history),
            str(root / "transcripts"), str(ids[1]), "B"]),
    ]
    exits = [p.wait(timeout=30) for p in procs]
    check("writers_exit_cleanly", exits == [0, 0], str(exits))

    raw = json.loads(history.read_text(encoding="utf-8"))
    by_id = {int(row["id"]): row for row in raw}
    check("json_stays_valid", len(raw) == 2, str(raw))
    check("writer_a_not_lost", by_id[ids[0]].get("project") == "A-34",
          str(by_id[ids[0]].get("project")))
    check("writer_b_not_lost", by_id[ids[1]].get("project") == "B-34",
          str(by_id[ids[1]].get("project")))
    check("no_temp_files_left", not list(root.glob("history.json.*.tmp")))

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)}): {', '.join(FAIL)}")
    raise SystemExit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
