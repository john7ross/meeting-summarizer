"""Processing journal + scoped regeneration + error rendering (offscreen Qt).

Covers the three defects this suite exists for:

* Regenerate could only redo summary AND analysis, so a run that died on one
  analysis feature had to redo the summary too. Scopes 'summary'/'analysis'/'both'
  must each produce exactly what they say.
* A long failure reason made the main window scroll HORIZONTALLY (the status label
  demanded ~3800 px). It must wrap and grow the window in height only.
* The queue was the only history: removing a meeting from it erased every trace of
  the processing. The journal is a separate file and must survive that.

Run:
    set QT_QPA_PLATFORM=offscreen && backend\\python\\python.exe desktop\\_selftest_run_history.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from PySide6.QtCore import QTimer                       # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from app import paths                                   # noqa: E402
from app.core.history import HistoryStore               # noqa: E402
from app.core.models import JobStatus                   # noqa: E402
from app.core.pipeline import JobRunner, PipelineQueue  # noqa: E402
from app.core.run_history import INTERRUPTED, RunHistoryStore  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"PASS  {name}  {detail}".rstrip())
    else:
        FAIL.append(name)
        print(f"FAIL  {name}  {detail}".rstrip())


PY = str(paths.python_executable())
FAKE_PROC = str(HERE / "_fake_processor_cli.py")
FAKE_AI = str(HERE / "_fake_ai_cli.py")

tmp = tempfile.TemporaryDirectory()
root = Path(tmp.name)
video = root / "meeting.mkv"
video.write_bytes(b"not a real video")

store = HistoryStore(path=root / "history.json", transcripts_root=root / "transcripts")
runs = RunHistoryStore(path=root / "processing_history.json")
settings = {
    "transcriptionEngine": "faster-whisper", "whisperModel": "medium",
    "transcriptionLanguage": "ru", "whisperDevice": "auto",
    "analysisSource": "transcript",
    "aiProvider": "local", "apiKey": "", "localEndpoint": "http://localhost:1234/v1",
    "prompt": "Сделай структурированное саммари встречи по транскрипции.",
    "extractActionItems": True, "categorizeAutomatically": True,
}


def factory(entry_id, video_path):
    return JobRunner(entry_id, video_path, settings, store, run_store=runs,
                     python_exe=PY, processor_script=FAKE_PROC, ai_client_script=FAKE_AI)


queue = PipelineQueue(max_concurrency=1, runner_factory=factory)
timed_out = {"v": False}
queue.all_done.connect(lambda: QTimer.singleShot(0, app.quit))


def pump(start) -> bool:
    """Start one piece of work and spin the loop until the queue drains."""
    timed_out["v"] = False

    def _timeout():
        timed_out["v"] = True
        app.quit()

    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(_timeout)
    guard.start(180000)
    start()
    app.exec()
    guard.stop()
    return not timed_out["v"]


entry_id = store.add(str(video), "5m 49s", "18.2 MB")

# ── 1. a full run is journalled ───────────────────────────────────────────────
ok = pump(lambda: queue.enqueue(entry_id, str(video)))
check("full_run_completed", ok, "safety timeout fired - machine too slow, not a bug")
log = runs.load()
check("full_run_recorded", len(log) == 1, f"{len(log)} records")
full = log[0] if log else {}
check("full_run_kind", full.get("kind") == "full", str(full.get("kind")))
check("full_run_status_done", full.get("status") == JobStatus.DONE.value,
      str(full.get("status")))
check("full_run_has_finished_at", bool(full.get("finishedAt")))
check("full_run_has_duration", float(full.get("durationSec") or 0) > 0,
      str(full.get("durationSec")))
statuses = [e.get("status") for e in full.get("events", [])]
check("full_run_status_history",
      statuses == [JobStatus.EXTRACTING.value, JobStatus.TRANSCRIBING.value,
                   JobStatus.SUMMARIZING.value, JobStatus.ANALYZING.value,
                   JobStatus.DONE.value], str(statuses))
check("full_run_events_are_timestamped",
      all(e.get("at") for e in full.get("events", [])))
check("full_run_has_stage_timings", len(full.get("stages", [])) > 0,
      str(full.get("stages")))
arts = {(a.get("kind"), a.get("version")) for a in full.get("artifacts", [])}
check("full_run_artifacts", arts == {("summary", 1), ("analysis", 1)}, str(arts))

entry = store.get(entry_id)
transcript_path = entry.transcript_path

# ── 2. regenerate: summary ONLY ───────────────────────────────────────────────
ok = pump(lambda: queue.enqueue_regenerate(
    entry_id, str(video), transcript_path, "summary"))
entry = store.get(entry_id)
check("summary_scope_completed", ok)
check("summary_scope_added_a_summary", len(entry.summary_versions) == 2,
      f"{len(entry.summary_versions)} summary versions")
check("summary_scope_left_the_analysis_alone", len(entry.analysis_versions) == 1,
      f"{len(entry.analysis_versions)} analysis versions")
log = runs.load()
check("summary_scope_journalled", len(log) == 2 and log[-1].get("kind") == "summary",
      str([r.get("kind") for r in log]))
check("summary_scope_never_reached_analysis",
      JobStatus.ANALYZING.value not in [e.get("status") for e in log[-1].get("events", [])],
      str([e.get("status") for e in log[-1].get("events", [])]))
check("summary_scope_artifacts",
      [(a.get("kind"), a.get("version")) for a in log[-1].get("artifacts", [])]
      == [("summary", 2)], str(log[-1].get("artifacts")))

# ── 3. regenerate: analysis ONLY ──────────────────────────────────────────────
ok = pump(lambda: queue.enqueue_regenerate(
    entry_id, str(video), transcript_path, "analysis"))
entry = store.get(entry_id)
check("analysis_scope_completed", ok)
check("analysis_scope_added_an_analysis", len(entry.analysis_versions) == 2,
      f"{len(entry.analysis_versions)} analysis versions")
check("analysis_scope_left_the_summary_alone", len(entry.summary_versions) == 2,
      f"{len(entry.summary_versions)} summary versions")
log = runs.load()
check("analysis_scope_journalled", len(log) == 3 and log[-1].get("kind") == "analysis",
      str([r.get("kind") for r in log]))
check("analysis_scope_skipped_the_summary_stage",
      JobStatus.SUMMARIZING.value not in [e.get("status") for e in log[-1].get("events", [])],
      str([e.get("status") for e in log[-1].get("events", [])]))

# ── 3b. regenerate: both, and a regeneration is never logged as "full" ───────
ok = pump(lambda: queue.enqueue_regenerate(
    entry_id, str(video), transcript_path, "both"))
entry = store.get(entry_id)
check("both_scope_completed", ok)
check("both_scope_added_one_of_each",
      len(entry.summary_versions) == 3 and len(entry.analysis_versions) == 3,
      f"{len(entry.summary_versions)} summaries / {len(entry.analysis_versions)} analyses")
log = runs.load()
check("both_scope_is_not_journalled_as_full_processing",
      log[-1].get("kind") == "summary+analysis", str(log[-1].get("kind")))
check("both_scope_never_transcribed",
      JobStatus.TRANSCRIBING.value not in [e.get("status") for e in log[-1].get("events", [])],
      str([e.get("status") for e in log[-1].get("events", [])]))

# ── 4. an analysis-only run with every feature switched off fails loudly ──────
quiet = dict(settings)
quiet["extractActionItems"] = False
quiet["categorizeAutomatically"] = False
quiet_runs = RunHistoryStore(path=root / "quiet_history.json")
quiet_queue = PipelineQueue(
    max_concurrency=1,
    runner_factory=lambda i, v: JobRunner(i, v, quiet, store, run_store=quiet_runs,
                                          python_exe=PY, processor_script=FAKE_PROC,
                                          ai_client_script=FAKE_AI))
outcome = {}
quiet_queue.job_finished.connect(lambda jid, k, err: outcome.update(ok=k, err=err))
quiet_queue.all_done.connect(lambda: QTimer.singleShot(0, app.quit))
quiet_queue.enqueue_regenerate(entry_id, str(video), transcript_path, "analysis")
QTimer.singleShot(30000, app.quit)
app.exec()
check("no_enabled_feature_is_an_error_not_a_silent_done",
      outcome.get("ok") is False and "анализ" in (outcome.get("err") or "").lower(),
      str(outcome))
check("no_enabled_feature_added_no_version",
      len(store.get(entry_id).analysis_versions) == 3,
      f"{len(store.get(entry_id).analysis_versions)} analysis versions")

# ── 4b. a scoped run exports the meeting's REAL state, not its own half ──────
# A summary-only run holds no analysis in memory. Exporting that emptiness wrote a
# Google Sheets row with every analysis column blank for a meeting that has one.
vault = root / "vault"
vault.mkdir()
settings["obsidianIntegration"] = True
settings["obsidianVaultPath"] = str(vault)
ok = pump(lambda: queue.enqueue_regenerate(
    entry_id, str(video), transcript_path, "summary"))
check("obsidian_scope_run_completed", ok)
notes = sorted(p.name for p in (vault / "Meetings" / "meeting").glob("*.md"))
check("summary_only_run_still_exports_an_analysis_note",
      any(n.startswith("meeting_analysis") for n in notes), str(notes))
an_note = next((p for p in (vault / "Meetings" / "meeting").glob("meeting_analysis*.md")), None)
check("that_analysis_note_is_not_empty",
      an_note is not None and len(an_note.read_text(encoding="utf-8")) > 120,
      str(an_note))
check("summary_note_is_named_after_the_new_version",
      any(n.startswith("meeting_summary_v4") for n in notes), str(notes))
settings["obsidianIntegration"] = False

# ── 5. the journal survives removing the meeting from the queue ──────────────
store.remove(entry_id)
check("meeting_gone_from_the_archive", store.get(entry_id) is None)
log = runs.load()
check("journal_survives_queue_removal", len(log) == 5, f"{len(log)} records")
check("journal_still_names_the_file",
      all(r.get("videoName") == "meeting.mkv" for r in log),
      str([r.get("videoName") for r in log]))

# ── 6. a run killed mid-flight is closed as interrupted on the next start ────
orphan = RunHistoryStore(path=root / "orphan.json")
record = orphan.new_run(entry_id=1, video_name="dead.mkv", kind="full")
check("open_run_has_no_finish", not orphan.load()[0].get("finishedAt"))
check("mark_interrupted_closes_it", orphan.mark_interrupted() == 1)
check("interrupted_status", orphan.load()[0].get("status") == INTERRUPTED,
      str(orphan.load()[0].get("status")))
check("mark_interrupted_is_idempotent", orphan.mark_interrupted() == 0)

# ── 7. UI: the regenerate menu offers exactly the three scopes ───────────────
from app.ui.main_window import MainWindow                # noqa: E402

ui_store = HistoryStore(path=root / "ui_history.json",
                        transcripts_root=root / "ui_transcripts")
ui_id = ui_store.add(str(video), "1m", "1 MB")
ui_job = ui_store.job_dir(ui_id)
ui_tx = ui_job / "meeting_raw.txt"
ui_tx.write_text("[00:00:01] Привет.", encoding="utf-8")
ui_store.set_transcript(ui_id, ui_tx)


class FakeQueue:
    class _Sig:
        def connect(self, *a, **k):
            pass

    status_changed = _Sig()
    progress = _Sig()
    job_finished = _Sig()
    speakers_needed = _Sig()
    active_changed = _Sig()
    stage_done = _Sig()
    max_concurrency = 1

    def __init__(self):
        self.calls = []

    def enqueue_regenerate(self, entry_id, video_path, transcript_path, scope="both"):
        self.calls.append(scope)


fake = FakeQueue()
window = MainWindow(settings={}, store=ui_store, queue=fake, language="ru",
                    theme="dark", run_store=runs)
window.resize(1200, 860)
window.show()
for _ in range(5):
    app.processEvents()
window.table.selectRow(0)
for _ in range(3):
    app.processEvents()

menu = window.btn_regenerate.menu()
check("regenerate_is_a_menu", menu is not None and len(menu.actions()) == 3,
      str(menu and [a.text() for a in menu.actions()]))
check("regenerate_menu_is_localised",
      bool(menu) and [a.text() for a in menu.actions()]
      == ["Только саммари", "Только анализ", "Саммари + анализ"],
      str(menu and [a.text() for a in menu.actions()]))

from PySide6.QtWidgets import QMessageBox                # noqa: E402

original = QMessageBox.question
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
try:
    for action in menu.actions():
        window.btn_regenerate.setEnabled(True)
        action.trigger()
        for _ in range(2):
            app.processEvents()
finally:
    QMessageBox.question = original
check("each_menu_item_enqueues_its_own_scope",
      fake.calls == ["summary", "analysis", "both"], str(fake.calls))

# ── 8. UI: a long error wraps instead of forcing a horizontal scrollbar ──────
LONG_ERROR = (
    "Анализ выполнен не полностью: ошибок 3 из 11. Проверьте журнал и повторите "
    "генерацию анализа.\nриски: AI returned invalid JSON/schema; response starts "
    "with: <think> Okay, the user wants me to analyse the risks in this meeting "
    "transcript and return strict JSON, but the model kept reasoning out loud and "
    "nothing valid ever came back\nцитаты: HTTP 503 from "
    "http://127.0.0.1:8080/v1/chat/completions after 3 retries (model loading)")
before_height = window.lbl_status.parentWidget().height()
window.on_finished(ui_id, False, LONG_ERROR)
for _ in range(5):
    app.processEvents()
scroll = window.centralWidget()
check("no_horizontal_scrollbar_after_a_long_error",
      scroll.horizontalScrollBar().maximum() == 0,
      f"needs {scroll.horizontalScrollBar().maximum()}px more width")
check("status_label_wraps", window.lbl_status.wordWrap())
check("status_section_grew_in_height",
      window.lbl_status.parentWidget().height() > before_height,
      f"{before_height} -> {window.lbl_status.parentWidget().height()}")
check("full_error_kept_in_the_tooltip",
      "127.0.0.1:8080" in window.lbl_status.toolTip())
check("queue_details_cell_keeps_the_reason",
      "не полностью" in window.table.item(0, window.COL_DETAILS).toolTip())
check("timeline_escapes_model_output",
      any("&lt;think&gt;" in line for line in window._stages_by_job.get(ui_id, [])),
      str(window._stages_by_job.get(ui_id, []))[:160])

# ── 9. UI: the history window lists the journalled runs ─────────────────────
from app.ui.history_dialog import HistoryDialog          # noqa: E402

dialog = HistoryDialog(runs, language="ru", parent=window)
check("history_lists_every_run", dialog.table.rowCount() == 5,
      f"{dialog.table.rowCount()} rows")
check("history_is_newest_first",
      dialog.table.item(0, dialog.COL_KIND).text() == "саммари",
      dialog.table.item(0, dialog.COL_KIND).text())
check("history_names_every_kind_it_recorded",
      {dialog.table.item(r, dialog.COL_KIND).text() for r in range(5)}
      == {"полная обработка", "саммари", "анализ", "саммари + анализ"},
      str({dialog.table.item(r, dialog.COL_KIND).text() for r in range(5)}))
dialog.table.selectRow(0)
for _ in range(2):
    app.processEvents()
detail = dialog.details.toPlainText()
check("history_detail_shows_the_stages", "Этапы" in detail, detail[:120])
check("history_detail_shows_the_status_history", "История статусов" in detail)
check("history_filter_error_empties_a_clean_journal",
      (dialog.cb_filter.setCurrentIndex(1) or dialog.table.rowCount()) == 0,
      f"{dialog.table.rowCount()} rows")
dialog.cb_filter.setCurrentIndex(0)
dialog.close()
window.close()

# ── 10. the two version counters diverge — nothing may pair them by index ────
# Scoped regeneration makes "summary v2 + analysis v3" (or "3 summaries + 1
# analysis") normal. Every consumer must clamp per list, and an export must be
# named after the version it really wrote.
import json as _json                                     # noqa: E402
from app.core.history import versioned_filename          # noqa: E402


def _entry_with(summaries: int, analyses: int, name: str):
    store2 = HistoryStore(path=root / f"{name}.json",
                          transcripts_root=root / f"{name}_transcripts")
    eid = store2.add(str(video), "1m", "1 MB")
    job = store2.job_dir(eid)
    tx = job / "meeting_raw.txt"
    tx.write_text("[00:00:01] Привет.", encoding="utf-8")
    store2.set_transcript(eid, tx)
    for n in range(1, summaries + 1):
        path = job / versioned_filename("meeting", "summary", n, ".txt")
        path.write_text(f"SUMMARY {n}", encoding="utf-8")
        store2.add_summary_version(eid, path, provider="local")
    for n in range(1, analyses + 1):
        path = job / versioned_filename("meeting", "analysis", n, ".json")
        path.write_text(_json.dumps({"keyTopics": [f"topic {n}"]}), encoding="utf-8")
        # every analysis of this meeting was built from the newest summary there was
        store2.add_analysis_version(eid, path, provider="local",
                                    source_summary_version=summaries)
    return store2, eid, job


for summaries, analyses, tag in ((2, 3, "s2a3"), (3, 1, "s3a1")):
    store2, eid2, job2 = _entry_with(summaries, analyses, tag)
    win = MainWindow(settings={}, store=store2, queue=FakeQueue(), language="ru",
                     theme="dark", run_store=runs)
    win.resize(1200, 860)
    win.show()
    for _ in range(4):
        app.processEvents()
    win.table.selectRow(0)
    for _ in range(3):
        app.processEvents()
    check(f"{tag}_both_pickers_list_their_own_versions",
          win.cb_sum_version.count() == summaries and win.cb_an_version.count() == analyses,
          f"{win.cb_sum_version.count()} summaries / {win.cb_an_version.count()} analyses")
    check(f"{tag}_each_picker_defaults_to_its_own_latest",
          win._sel_summary_idx == summaries - 1 and win._sel_analysis_idx == analyses - 1,
          f"{win._sel_summary_idx} / {win._sel_analysis_idx}")
    check(f"{tag}_shows_the_latest_of_each",
          win.txt_summary.toPlainText() == f"SUMMARY {summaries}"
          and win._current_analysis == {"keyTopics": [f"topic {analyses}"]},
          f"{win.txt_summary.toPlainText()} / {win._current_analysis}")
    # the shorter list must not be reached through the longer list's index
    win.cb_sum_version.setCurrentIndex(0)
    win.cb_an_version.setCurrentIndex(0)
    for _ in range(2):
        app.processEvents()
    check(f"{tag}_picking_v1_on_one_side_leaves_the_other_alone",
          win.txt_summary.toPlainText() == "SUMMARY 1"
          and win._current_analysis == {"keyTopics": ["topic 1"]},
          f"{win.txt_summary.toPlainText()} / {win._current_analysis}")
    # export each kind at its own latest version: each file is named after the
    # version of ITS OWN kind, so the two never collide or borrow each other's n
    import time as _time                                  # noqa: E402
    kinds = [win.cb_export_kind.itemData(i) for i in range(win.cb_export_kind.count())]
    fmts = [win.cb_export_fmt.itemData(i) for i in range(win.cb_export_fmt.count())]
    win.cb_export_fmt.setCurrentIndex(fmts.index("txt"))
    win.cb_sum_version.setCurrentIndex(summaries - 1)
    win.cb_an_version.setCurrentIndex(analyses - 1)
    saved = {}
    for kind, count in (("summary", summaries), ("analysis", analyses)):
        win.cb_export_kind.setCurrentIndex(kinds.index(kind))
        win.lbl_export_status.setText("")
        for _ in range(2):
            app.processEvents()
        win._do_export()
        for _ in range(80):
            app.processEvents()
            text = win.lbl_export_status.text()
            if text and "…" not in text:
                break
            _time.sleep(0.05)
        saved[kind] = win.lbl_export_status.text()
        expected = f"meeting_{kind}.txt" if count == 1 else f"meeting_{kind}_v{count}.txt"
        check(f"{tag}_{kind}_export_is_named_after_its_own_version",
              expected in saved[kind], f"{saved[kind]} (expected {expected})")
    check(f"{tag}_the_two_exports_are_different_files",
          saved["summary"] != saved["analysis"], str(saved))
    win.close()

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)} failed): {', '.join(FAIL)}")
    sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
sys.exit(0)
