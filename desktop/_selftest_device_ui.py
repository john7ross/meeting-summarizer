"""UI test for the CUDA device indicator + concurrency adjustment (TODO #9).

Offscreen; the real probe (QTimer/DeviceWorker) never fires without an event
loop, so we drive ``_on_device_detected`` directly and assert the header label
and the queue concurrency update.

Run:
    set QT_QPA_PLATFORM=offscreen && backend\\python\\python.exe desktop\\_selftest_device_ui.py
"""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

from desktop.app.ui.main_window import MainWindow
from desktop.app.core.history import HistoryStore
from desktop.app.core.queue_manager import resolve_workers

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if (detail and not cond) else ""))


class FakeQueue:
    def __init__(self):
        self.max_concurrency = 4
        self.cap_calls = []
    class _Sig:
        def connect(self, *a, **k): pass
    status_changed = _Sig(); progress = _Sig(); job_finished = _Sig(); speakers_needed = _Sig()
    def set_max_concurrency(self, n):
        self.max_concurrency = n
        self.cap_calls.append(n)


tmp = tempfile.mkdtemp()
store = HistoryStore(path=os.path.join(tmp, "history.json"),
                     transcripts_root=Path(tmp) / "transcripts")
q = FakeQueue()
w = MainWindow({"parallelWorkers": "auto", "transcriptionEngine": "faster-whisper",
                "whisperModel": "medium"}, store, q, language="ru", theme="dark")

# initial: indicator present and in the "probing" state
check("indicator_exists", hasattr(w, "lbl_device"))
check("initial_probing", w.lbl_device.text() == w._t("device_probing"), w.lbl_device.text())

# GPU detected -> label shows GPU, tooltip = name, queue caps to 1 (auto+cuda)
w._on_device_detected(True, "NVIDIA GeForce RTX 4060 Ti")
check("gpu_label", "GPU" in w.lbl_device.text(), w.lbl_device.text())
check("gpu_tooltip", w.lbl_device.toolTip() == "NVIDIA GeForce RTX 4060 Ti")
check("gpu_caps_to_1", q.max_concurrency == resolve_workers("auto", cuda=True) == 1, str(q.max_concurrency))

# CPU detected -> CPU label, queue uses the cpu-auto count
w._on_device_detected(False, "")
check("cpu_label", w.lbl_device.text().endswith(w._t("device_cpu")), w.lbl_device.text())
check("cpu_caps", q.max_concurrency == resolve_workers("auto", cuda=False), str(q.max_concurrency))

# language switch re-renders the indicator (english CPU label)
w.toggle_language()
check("relabel_en_cpu", w.lbl_device.text().endswith("CPU"), w.lbl_device.text())

# explicit worker count ignores cuda (stays as set)
q2 = FakeQueue()
w2 = MainWindow({"parallelWorkers": 6, "transcriptionEngine": "faster-whisper",
                 "whisperModel": "medium"}, store, q2, language="en", theme="dark")
w2._on_device_detected(True, "GPU")
check("explicit_workers_kept", q2.max_concurrency == 6, str(q2.max_concurrency))

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)}): {', '.join(FAIL)}")
    sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
sys.exit(0)
