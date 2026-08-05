"""TODO #10 — Diagnostics: metrics + trace + dialog + engine-compare (offscreen).

Real: metrics.sample() reads this machine's CPU/RAM/GPU; trace.layout normalises a
real span trace; the dialog builds tabs + engine checkboxes from an injected
catalog; CompareWorker runs the fake processor over N engines and aggregates.

Run:
    set QT_QPA_PLATFORM=offscreen && backend\\python\\python.exe desktop\\_selftest_diagnostics.py
"""
import json, os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

from desktop.app.core import metrics, trace
from desktop.app.core.history import HistoryStore
from desktop.app.core.worker import CompareWorker
from desktop.app.ui.diagnostics_dialog import DiagnosticsDialog

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if (detail and not cond) else ""))

# ── metrics (real) ───────────────────────────────────────────────────────────
metrics.sample()
s = metrics.sample()
check("metrics_has_keys", {"cpu_percent", "ram_percent", "gpu", "psutil"}.issubset(s))
check("metrics_ram_positive", (s.get("ram_total_mb") or 0) > 0)
check("metrics_cpu_is_num", isinstance(s.get("cpu_percent"), (int, float)))

# ── trace layout ─────────────────────────────────────────────────────────────
fake_trace = {"name": "video_processing", "timestamp": "2026-07-01T10:00:00",
              "startTime": 1000.0, "endTime": 1010.0, "spans": [
                  {"name": "extract_audio", "start": 1000.0, "end": 1002.0, "duration": 2000.0},
                  {"name": "transcribe", "start": 1002.0, "end": 1010.0, "duration": 8000.0}]}
lay = trace.layout(fake_trace)
check("trace_total_ms", abs(lay["total_ms"] - 10000.0) < 1)
check("trace_bar1_offset", abs(lay["bars"][1]["offset"] - 0.2) < 1e-6)
check("trace_bar1_width", abs(lay["bars"][1]["width"] - 0.8) < 1e-6)

# ── dialog with injected catalog (no subprocess) ─────────────────────────────
CATALOG = {"engines": [
    {"id": "faster-whisper", "label": {"ru": "Faster", "en": "Faster"}, "implemented": True,
     "default_model": "medium", "models": [{"id": "medium", "lang": None, "available": True}]},
    {"id": "vosk", "label": {"ru": "Vosk", "en": "Vosk"}, "implemented": True,
     "default_model": "vosk-model-small-ru-0.22", "models": [
         {"id": "vosk-model-small-ru-0.22", "lang": "ru", "available": True},
         {"id": "vosk-model-small-en-us-0.15", "lang": "en", "available": False}]},
    {"id": "not-impl", "label": {"ru": "X", "en": "X"}, "implemented": False,
     "default_model": None, "models": []},
]}

tmp = tempfile.mkdtemp()
store = HistoryStore(path=os.path.join(tmp, "history.json"),
                     transcripts_root=Path(tmp) / "transcripts")
eid = store.add("C:/videos/demo.mp4")
(store.job_dir(eid) / "demo_trace.json").write_text(json.dumps(fake_trace), encoding="utf-8")
store.set_status(eid, "error")
eid_latest = store.add("C:/videos/demo.mp4")
(store.job_dir(eid_latest) / "demo_trace.json").write_text(
    json.dumps(fake_trace), encoding="utf-8")
store.set_status(eid_latest, "done")

dlg = DiagnosticsDialog(store, language="ru", catalog=CATALOG)
check("dialog_four_tabs", dlg.tabs.count() == 4, str(dlg.tabs.count()))
check("dialog_newest_run_first", dlg.cb_meeting.currentData() == eid_latest,
      str(dlg.cb_meeting.currentData()))
check("dialog_duplicate_runs_distinguishable",
      dlg.cb_meeting.itemText(0) != dlg.cb_meeting.itemText(1)
      and "Готово" in dlg.cb_meeting.itemText(0)
      and "(2)" in dlg.cb_meeting.itemText(0),
      str([dlg.cb_meeting.itemText(i) for i in range(dlg.cb_meeting.count())]))
check("dialog_trace_renders",
      any(b["name"] == "extract_audio" for b in dlg.flame._rows),
      str([b["name"] for b in dlg.flame._rows]))
dlg._refresh_system()
check("dialog_system_ok", dlg._vals["cpu"].text() != "")

# compare tab: only implemented engines get a checkbox; runnable model resolves
check("cmp_two_engines", set(dlg._cmp_checks) == {"faster-whisper", "vosk"}, str(set(dlg._cmp_checks)))
check("cmp_faster_model", dlg._cmp_checks["faster-whisper"]["model"] == "medium")
check("cmp_faster_checked", dlg._cmp_checks["faster-whisper"]["cb"].isChecked())
check("cmp_file_path_editable", not dlg.cmp_file.isReadOnly())
dlg.cmp_file.setText(str(Path(tmp) / "missing.mkv"))
dlg._do_compare()
check("cmp_missing_file_rejected", dlg.cmp_status.text() == "Файл не найден.")
# switch to EN: vosk en model is unavailable -> resolved but unchecked
dlg.cmp_lang.setCurrentIndex(1)
check("cmp_vosk_en_unavail", dlg._cmp_checks["vosk"]["available"] is False)
check("cmp_vosk_en_unchecked", not dlg._cmp_checks["vosk"]["cb"].isChecked())
dlg.done(0)

# ── CompareWorker over the fake processor (real orchestration) ────────────────
fake_proc = str(ROOT / "desktop" / "_fake_processor_cli.py")
out_root = tempfile.mkdtemp(prefix="cmp_")
worker = CompareWorker(sys.executable, fake_proc, "C:/videos/demo.mp4",
                       [("faster-whisper", "medium"), ("vosk", "vosk-model-small-ru-0.22")],
                       "ru", "auto", out_root)
seen = []
worker.engine_done.connect(lambda e, r: seen.append(e))
results = {}
worker.finished_all.connect(lambda rs: results.update({r["engine"]: r for r in rs}))
worker.run()   # synchronous: emits inline
check("cmp_ran_two", len(results) == 2, str(list(results)))
check("cmp_faster_ok", results.get("faster-whisper", {}).get("ok") is True, str(results.get("faster-whisper")))
check("cmp_has_text", results.get("vosk", {}).get("chars", 0) > 0)
check("cmp_time_recorded", isinstance(results.get("faster-whisper", {}).get("seconds"), (int, float)))

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)}): {', '.join(FAIL)}")
    sys.stdout.flush(); os._exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
sys.stdout.flush(); os._exit(0)
