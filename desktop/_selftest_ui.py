"""Headless smoke test for the window skeleton (offscreen Qt, no real window).
Verifies: full app bootstrap constructs; both themes render; a queue row is
added and the id-routed status/progress slots update the right widgets.
"""
import os
import sys
import tempfile
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

results = []


def check(name, ok, detail=""):
    """``ok`` may be a boolean OR a callable.

    A callable is CALLED and its return value becomes the detail; an assertion or
    any exception inside it is a FAIL with the message. Passing a function object
    to a truthiness test made seven checks in this file report PASS without ever
    running - see ROADMAP.
    """
    if callable(ok):
        try:
            detail = str(ok() or "")
            ok = True
        except AssertionError as exc:
            detail, ok = f"assert: {exc}", False
        except Exception as exc:  # noqa: BLE001 - a crash is a failure, not a skip
            import traceback
            detail, ok = f"{type(exc).__name__}: {exc}", False
            results.append("      " + traceback.format_exc()
                           .replace(chr(10), chr(10) + "      "))
    results.append((f"PASS  {name}  {detail}" if ok else f"FAIL  {name}  {detail}").rstrip())


try:
    from app.main import build_app
    from app import config as app_config
    from app.ui.main_window import MainWindow
    from app.ui.trim_dialog import TrimDialog
    from app.ui.theme import build_stylesheet
    from app.core.history import HistoryStore
    from app.core.models import JobStatus, main_label
    from PySide6.QtWidgets import QScrollArea, QPushButton

    class _Sig:
        def connect(self, *args, **kwargs):
            pass

    class FakeQueue:
        status_changed = _Sig()
        progress = _Sig()
        job_finished = _Sig()
        speakers_needed = _Sig()
        active_changed = _Sig()
        stage_done = _Sig()

        def __init__(self):
            self.max_concurrency = 4
            self.enqueued = []
            self.cancelled = []

        def enqueue(self, job_id, path):
            self.enqueued.append((job_id, path))

        def active_count(self):
            return 0

        def pending_count(self):
            return len(self.pending_ids())

        def pending_ids(self):
            return {job_id for job_id, _ in self.enqueued
                    if job_id not in self.cancelled}

        def runner(self, job_id):
            return None

        def set_max_concurrency(self, value):
            self.max_concurrency = value

        def cancel(self, job_id):
            if job_id not in self.pending_ids():
                return False
            self.cancelled.append(job_id)
            return True

    app, window = build_app([])
    check("bootstrap_constructs", window is not None and window.table.columnCount() == 5)

    dark = build_stylesheet("dark")
    light = build_stylesheet("light")
    check("themes_render", "#007acc" in dark and "#0078d4" in light and len(dark) > 500)

    # Use a temp store so the real config/history.json is never touched.
    tmp = tempfile.TemporaryDirectory()
    store = HistoryStore(path=Path(tmp.name) / "history.json",
                         transcripts_root=Path(tmp.name) / "transcripts")
    settings = {"parallelWorkers": "2", "language": "ru", "theme": "dark"}
    win = MainWindow(settings, store, queue=None, language="ru", theme="dark")

    eid = store.add("C:/x/meeting.mkv", "5m", "18 MB")
    entry = store.get(eid)
    win.add_job_row(entry)
    check("row_added", win.table.rowCount() == 1
          and win.table.item(0, win.COL_ID).text() == str(eid))

    # The queue table lives in a scrollable column, so without an explicit floor
    # it collapses to a single visible row no matter how much is queued.
    _row_h = win.table.verticalHeader().defaultSectionSize() or 30
    _visible = win.table.minimumHeight() // _row_h
    check("queue_shows_several_rows_when_almost_empty",
          _visible >= win.QUEUE_MIN_ROWS, f"{_visible} rows fit")
    for _i in range(2, 9):
        win.add_job_row(store.get(store.add(f"C:/x/m{_i}.mkv")))
    _grown = win.table.minimumHeight() // _row_h
    check("queue_grows_with_the_backlog", _grown > _visible,
          f"{_visible} -> {_grown} rows")
    for _i in range(9, 30):
        win.add_job_row(store.get(store.add(f"C:/x/m{_i}.mkv")))
    _capped = win.table.minimumHeight() // _row_h
    check("queue_height_is_capped", _capped <= win.QUEUE_MAX_ROWS + 1,
          f"{win.table.rowCount()} rows queued -> {_capped} rows tall")

    # Switching the interface language must retranslate EVERYTHING, including
    # panels with no data yet. Gating the analysis panel on "an analysis is
    # loaded" left its placeholder in the previous language on every meeting
    # that has not been analysed.
    from PySide6.QtWidgets import QPushButton as _Btn, QLabel as _Lbl

    def _visible_text(w):
        out = []
        for kind in (_Btn, _Lbl):
            out += [c.text() for c in w.findChildren(kind) if c.text()]
        return " ".join(out)

    def _has_cyrillic(s):
        return any("Ѐ" <= ch <= "ӿ" for ch in s)

    _lang_win = MainWindow(settings, store, queue=None, language="ru", theme="dark")
    check("ru_interface_is_russian", _has_cyrillic(_visible_text(_lang_win)))
    _lang_win.toggle_language()
    _leftovers = [c.text() for kind in (_Btn, _Lbl)
                  for c in _lang_win.findChildren(kind)
                  if c.text() and _has_cyrillic(c.text())]
    check("nothing_stays_russian_after_switching_to_english", not _leftovers,
          "; ".join(_leftovers)[:120])
    _lang_win.toggle_language()
    check("switching_back_restores_russian", _has_cyrillic(_visible_text(_lang_win)))

    # A fresh window restores persisted meetings, selects the newest one and
    # does not implicitly enqueue/reprocess it.
    restored_store = HistoryStore(
        path=Path(tmp.name) / "restored-history.json",
        transcripts_root=Path(tmp.name) / "restored-transcripts")
    old_id = restored_store.add("C:/x/old.mkv")
    new_id = restored_store.add("C:/x/new.mkv")
    restored_store.set_status(new_id, JobStatus.DONE)
    restored_win = MainWindow(
        settings, restored_store, queue=None, language="ru", theme="dark")
    check("history_restored_newest_first",
          restored_win.table.rowCount() == 2
          and restored_win.table.item(0, restored_win.COL_ID).text() == str(new_id)
          and restored_win._selected_job_id() == new_id)
    check("restored_done_progress_is_complete",
          restored_win._bars[new_id].value() == 100)
    check("restored_status_panel_matches_selected_row",
          "new.mkv" in restored_win.lbl_status.text()
          and main_label(JobStatus.DONE, "ru") in restored_win.lbl_status.text(),
          restored_win.lbl_status.text())
    restored_win.close()

    win.on_status_changed(eid, JobStatus.TRANSCRIBING)
    check("status_slot_updates_row",
          win.table.item(0, win.COL_STATUS).text() == main_label(JobStatus.TRANSCRIBING, "ru"),
          win.table.item(0, win.COL_STATUS).text())

    # Backend details arrive in English; the RU UI must show them translated.
    win.on_progress(eid, 42, "Transcribing chunk 2/5...")
    bar = win._bars[eid]
    detail_cell = win.table.item(0, win.COL_DETAILS).text()
    check("progress_slot_updates_widgets",
          bar.value() == 42 and win.progress.value() == 42
          and detail_cell == "Транскрибация фрагмента 2/5...",
          detail_cell)

    prev_header = win.table.horizontalHeaderItem(win.COL_FILE).text()
    saved_preferences = []
    original_save_settings = app_config.save_settings
    app_config.save_settings = lambda settings: saved_preferences.append(dict(settings))
    win._persist_ui_preferences = True
    win.toggle_language()
    new_header = win.table.horizontalHeaderItem(win.COL_FILE).text()
    check("language_toggle_retranslates", prev_header != new_header,
          f"{prev_header} -> {new_header}")
    check("language_toggle_retranslates_url_controls",
          win.btn_url.text() == "Add by URL"
          and "YouTube or a video link" in win.ed_url.placeholderText(),
          f"{win.btn_url.text()} / {win.ed_url.placeholderText()}")

    win.toggle_theme()
    check("theme_toggle", win.theme == "light")
    check("ui_preferences_persist",
          len(saved_preferences) == 2
          and saved_preferences[-1].get("language") == "en"
          and saved_preferences[-1].get("theme") == "light",
          str(saved_preferences))
    app_config.save_settings = original_save_settings
    win._persist_ui_preferences = False

    # Adding files auto-enqueues them after the trim decision; no extra click on
    # "Process" is required.  Bypass the modal itself in this wiring test.
    q = FakeQueue()
    queue_store = HistoryStore(
        path=Path(tmp.name) / "queue-history.json",
        transcripts_root=Path(tmp.name) / "queue-transcripts")
    win2 = MainWindow(
        {"parallelWorkers": "auto"}, queue_store, queue=q, language="ru")
    win2._resolve_segments = lambda path: [path]
    win2._add_files(["C:/x/first.mkv", "C:/x/second.mkv"])
    check("add_files_autostarts_all", len(q.enqueued) == 2, str(q.enqueued))

    # The selected row owns the shared status panel.  Later progress from another
    # concurrently running job must update only that row, not steal the panel.
    first_id, second_id = (item[0] for item in q.enqueued)
    win2.on_status_changed(first_id, JobStatus.TRANSCRIBING)
    win2.on_status_changed(second_id, JobStatus.SUMMARIZING)
    win2.table.selectRow(1)
    win2.on_progress(second_id, 12, "second")
    win2.on_progress(first_id, 67, "first")
    check("selected_job_keeps_global_progress", win2.progress.value() == 12,
          str(win2.progress.value()))
    check("selected_job_keeps_status_title", "second.mkv" in win2.lbl_status.text(),
          win2.lbl_status.text())
    win2.on_finished(first_id, False, "background failure")
    check("background_error_does_not_steal_status",
          "second.mkv" in win2.lbl_status.text()
          and "background failure" not in win2.lbl_status.text(),
          win2.lbl_status.text())
    old_stage_t0 = win2._live_by_job[second_id]["stage_t0"]
    win2.on_stage_done(second_id, "Анализ: задачи", 4.0)
    check("fine_stage_restarts_live_timer",
          win2._live_by_job[second_id]["stage_t0"] >= old_stage_t0)

    # Cancel is row-scoped: cancelling the selected second job must not touch the
    # first active/queued row.
    win2._cancel_selected()
    check("cancel_targets_only_selected_job",
          q.cancelled == [second_id]
          and queue_store.get(second_id).status == JobStatus.CANCELLED.value
          and queue_store.get(first_id).status != JobStatus.CANCELLED.value,
          str(q.cancelled))

    # CUDA auto mode deliberately serialises GPU-heavy jobs, and the label must
    # show the real cap instead of the pre-probe CPU value.
    win2._on_device_detected(True, "Test GPU")
    check("worker_label_matches_real_gpu_cap",
          q.max_concurrency == 1 and "1" in win2.lbl_workers.text(),
          win2.lbl_workers.text())
    check("status_panel_fixed_compact", win2.status_timeline.height() == 112)

    # Partial analysis failures still leave useful persisted artifacts.  The
    # selected meeting must refresh them instead of continuing to show an older
    # summary/analysis version.
    loaded_after_error = []
    original_load_results = win2._load_results
    win2._load_results = loaded_after_error.append
    win2._active_job = second_id
    win2.lbl_export_status.setText(win2._t("regen_running"))
    win2.on_finished(second_id, False, "partial analysis")
    win2._load_results = original_load_results
    check("partial_failure_reloads_persisted_results",
          loaded_after_error == [second_id], str(loaded_after_error))
    check("regeneration_indicator_finishes_on_error",
          win2.lbl_export_status.text() == win2._t("regen_failed"),
          win2.lbl_export_status.text())

    # The editor scrolls on short displays, while both processing actions remain
    # fixed below it and therefore never disappear beyond the work area.
    trim = TrimDialog("C:/missing/test.mkv", language="ru")
    trim_scroll = trim.findChild(QScrollArea, "trimScroll")
    trim_buttons = {
        button.text(): button for button in trim.findChildren(QPushButton)
    }
    check("trim_dialog_scrolls_on_short_screens",
          trim_scroll is not None
          and "Обработать файл целиком" in trim_buttons
          and "Обработать фрагменты (0)" in trim_buttons
          and not trim_scroll.isAncestorOf(
              trim_buttons["Обработать файл целиком"])
          and not trim_scroll.isAncestorOf(
              trim_buttons["Обработать фрагменты (0)"]))
    trim.close()

    tmp.cleanup()
except Exception as exc:  # noqa: BLE001
    results.append(f"FAIL  harness  {exc!r}")
    results.append("      " + traceback.format_exc().replace("\n", "\n      "))


# -- queue: clearing it must not be a one-row-at-a-time chore -----------------
def _clear_queue_button():
    from app.ui.main_window import LABELS
    src = (Path(__file__).resolve().parent / "app" / "ui" / "main_window.py").read_text(encoding="utf-8")
    assert "self.btn_clear = QPushButton" in src, "no clear-queue button"
    assert "def _clear_queue(self)" in src, "no handler"
    assert 'self.btn_clear.setEnabled(self.table.rowCount() > 0)' in src, "not state-driven"
    # a row being processed must survive the clear, or its subprocess writes into
    # an entry the table no longer knows about
    handler = src[src.index("def _clear_queue(self)"):src.index("def _remove_selected(self)")]
    assert "self._q_runner(job_id) is not None" in handler, "clear would drop a running job"
    for lang in ("ru", "en"):
        for key in ("clear_queue", "clear_tip", "clear_kept"):
            assert LABELS[lang].get(key), f"{lang}/{key} missing"
    assert "{n}" in LABELS["ru"]["clear_kept"] and "{n}" in LABELS["en"]["clear_kept"]
    return "button + handler + both languages"


# -- settings must never need horizontal scrolling ---------------------------
def _settings_fits_horizontally():
    from PySide6.QtWidgets import QScrollArea
    from app import config
    from app.ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(config.load_settings(), language="ru")
    dlg.resize(dlg.minimumWidth(), 600)
    dlg.show()
    app.processEvents()
    area = dlg.findChild(QScrollArea)
    body_w = area.widget().minimumSizeHint().width()
    viewport_w = area.viewport().width()
    dlg.close()
    # A combo sized to a full agent command line once forced 2662 px of content,
    # so the dialog ALWAYS had a horizontal scrollbar whatever its size.
    assert body_w <= viewport_w, f"content {body_w}px > viewport {viewport_w}px"
    assert dlg.minimumWidth() >= body_w - 40, "minimum width narrower than the form"
    return f"content {body_w}px fits {viewport_w}px viewport"


def _no_window_demands_a_wide_screen():
    """No window may demand more width than a normal display gives it.

    A single long item in a combo box or an unwrappable checkbox label silently
    stretches the whole window: settings once needed 2662 px and diagnostics
    2380, so both always showed a horizontal scrollbar.
    """
    import tempfile
    from app import config, paths
    from app.ui.settings_dialog import SettingsDialog
    from app.ui.diagnostics_dialog import DiagnosticsDialog
    from app.ui.stats_dialog import StatsDialog
    from app.ui.local_ai_dialog import LocalAiDialog
    from app.ui.recorder_dialog import RecorderDialog

    settings = config.load_settings()
    tmp = Path(tempfile.mkdtemp())
    store = HistoryStore(path=tmp / "history.json", transcripts_root=tmp / "transcripts")
    LIMIT = 1000
    windows = [
        ("settings", SettingsDialog(settings, language="ru")),
        ("diagnostics", DiagnosticsDialog(store, language="ru")),
        ("stats", StatsDialog(store, language="ru")),
        ("local_ai", LocalAiDialog(settings, language="ru")),
        ("recorder", RecorderDialog(str(tmp), language="ru")),
    ]
    too_wide = []
    for name, dlg in windows:
        dlg.show()
        app.processEvents()
        width = dlg.minimumSizeHint().width()
        if width > LIMIT:
            too_wide.append(f"{name}={width}px")
        dlg.close()
    assert not too_wide, "windows wider than a 1000px budget: " + ", ".join(too_wide)
    return f"every window fits {LIMIT}px"


def _empty_queue_clears_the_panels():
    """An empty queue must leave no results on screen.

    The transcript, summary, analysis, project field, version pickers and the
    actions all belong to the SELECTED row. With the queue emptied there is no
    selection, and the panels kept showing a meeting the queue no longer held.
    """
    src = (Path(__file__).resolve().parent / "app" / "ui" / "main_window.py").read_text(encoding="utf-8")
    assert "def _clear_results(self)" in src, "no reset for the result panels"
    body = src[src.index("def _clear_results(self)"):]
    body = body[:body.index("def ", 40)]
    for surface in ("self.txt_raw.setPlainText(\"\")", "self.txt_summary.setPlainText(\"\")",
                    "self.analysis_widget.clear()", "self.edit_project.setText(\"\")",
                    "self._current_transcript = \"\"", "self._current_analysis = None"):
        assert surface in body, f"_clear_results does not reset {surface}"
    assert "self.btn_regenerate" in body and "self.btn_add_rag" in body,         "actions stay enabled with nothing loaded"
    # both ways of emptying the queue must trigger it
    for handler in ("def _clear_queue(self)", "def _remove_selected(self)"):
        chunk = src[src.index(handler):]
        chunk = chunk[:chunk.index("def ", 40)]
        assert "self._clear_results()" in chunk, f"{handler} leaves the panels populated"
    return "transcript, summary, analysis, project, versions and actions all reset"


def _nothing_is_clipped():
    """No placeholder may be cut off, and no control squeezed below its own hint.

    Both were live defects: a hard-coded 260 px cap turned the hint into
    "Проект (необяз…" at the real UI font, and the diagnostics comparison tab had
    no scroll area, so on a normal screen Qt compressed the buttons, the file
    field and the table header until their text was clipped.
    """
    import tempfile
    from PySide6.QtWidgets import (QLineEdit, QPushButton, QComboBox, QTabWidget,
                                   QScrollArea)
    from app import config, paths
    from app.ui.rag_dialog import RagDialog
    from app.ui.diagnostics_dialog import DiagnosticsDialog

    from app.ui.main_window import MainWindow

    settings = config.load_settings()
    tmp = Path(tempfile.mkdtemp())
    store = HistoryStore(path=tmp / "history.json", transcripts_root=tmp / "transcripts")
    rag = RagDialog(str(tmp / "rag"), str(paths.python_executable()),
                    str(Path(__file__).resolve().parent.parent / "backend" / "rag.py"),
                    settings, language="ru")
    diag = DiagnosticsDialog(store, language="ru")
    # The MAIN WINDOW belongs in this sweep. Checking only the dialogs is how the
    # export selector shipped reading "Транскри": Qt caches a combo's size hint
    # from the font at construction time, and the stylesheet enlarges it after.
    main = MainWindow(settings, store, queue=None, language="ru", theme="dark")
    problems = []
    for dlg, name in ((rag, "rag"), (diag, "diagnostics"), (main, "main window")):
        dlg.resize(900, 560)
        dlg.show()
        for _ in range(3):
            app.processEvents()
        for edit in dlg.findChildren(QLineEdit):
            hint = edit.placeholderText()
            if hint and edit.isVisible():
                need = edit.fontMetrics().horizontalAdvance(hint) + 20
                if edit.width() < need:
                    problems.append(f"{name}: placeholder cut «{hint[:24]}»")
        for ctl in dlg.findChildren(QPushButton) + dlg.findChildren(QComboBox):
            if ctl.isVisible() and ctl.height() < ctl.minimumSizeHint().height():
                problems.append(f"{name}: {type(ctl).__name__} squeezed to {ctl.height()}px")
        # A combo must fit its own entries - UNLESS it was deliberately capped by
        # theme.cap_combo_width, whose entries are user content (file names, agent
        # command lines) and are meant to elide rather than widen the window.
        capped = QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        for combo in dlg.findChildren(QComboBox):
            if not combo.isVisible() or combo.sizeAdjustPolicy() == capped:
                continue
            texts = [combo.itemText(i) for i in range(combo.count())]
            widest = max((combo.fontMetrics().horizontalAdvance(t) for t in texts),
                         default=0)
            if widest and combo.width() < widest + 24:
                problems.append(f"{name}: combo cuts «{max(texts, key=len)[:20]}» "
                                f"({combo.width()}px < {widest + 24}px)")
        dlg.close()
    # the tall comparison tab must live in a scroll area, or it compresses again
    tabs = diag.findChild(QTabWidget)
    compare = tabs.widget(2)
    assert compare.findChild(QScrollArea) is not None, "comparison tab does not scroll"
    assert not problems, "; ".join(problems)
    return "placeholders fit, controls keep their height, tall tab scrolls"


def _history_row_shows_its_status():
    """Selecting a meeting must restore its STATUS, not only its artifacts.

    The stage timeline lived in memory only, so clicking a meeting from a previous
    session loaded the transcript, summary and analysis while the status panel
    stayed blank. Stage timings are on disk in the meeting's *_trace.json and the
    rest on the entry itself.
    """
    import json
    import tempfile
    from app import config
    from app.core.pipeline import PipelineQueue, JobRunner
    from app.ui.main_window import MainWindow

    tmp = Path(tempfile.mkdtemp())
    store = HistoryStore(path=tmp / "history.json", transcripts_root=tmp / "transcripts")
    video = tmp / "планёрка.mkv"
    video.write_bytes(b"x")
    entry_id = store.add(str(video))
    job = store.job_dir(entry_id)
    job.mkdir(parents=True, exist_ok=True)
    transcript = job / "планёрка_raw.txt"
    transcript.write_text("[00:00:01] привет" + chr(10), encoding="utf-8")
    store.set_transcript(entry_id, str(transcript))
    (job / "планёрка_trace.json").write_text(json.dumps({
        "name": "video_processing", "duration": 12000.0,
        "spans": [{"name": "extract_audio", "duration": 1500.0},
                  {"name": "transcribe_vosk", "duration": 9000.0},
                  # A stage that finished instantly: 0.0 is a measurement, not a
                  # missing value - it must still render with its time.
                  {"name": "Загрузка локальной LLM", "duration": 0.0}],
    }), encoding="utf-8")
    summary = job / "планёрка_summary.txt"
    summary.write_text("итог", encoding="utf-8")
    store.add_summary_version(entry_id, str(summary), provider="local")
    store.set_status(entry_id, "error", error="Cannot connect to local API at :8080")

    settings = config.load_settings()
    window = MainWindow(settings, store,
                        PipelineQueue(1, lambda i, v: JobRunner(i, v, settings, store)),
                        language="ru", theme="dark")
    window.show()
    for _ in range(3):
        app.processEvents()
    window.table.selectRow(0)
    for _ in range(2):
        app.processEvents()
    timeline = window.lbl_stages.text()
    status = window.lbl_status.text()
    details = window.table.item(0, window.COL_DETAILS).text()
    window.close()

    assert "Извлечение аудио" in timeline, f"trace stages missing: {timeline!r}"
    assert "Транскрибация (Vosk)" in timeline, f"engine stage missing: {timeline!r}"
    assert "Саммари v1" in timeline, f"summary version missing: {timeline!r}"
    assert "Обработано" in timeline, f"no processed-at header: {timeline!r}"
    assert "Cannot connect" in timeline, f"failure reason missing: {timeline!r}"
    assert "Cannot connect" in status, f"status line hides the reason: {status!r}"
    assert "Cannot connect" in details, f"Details column empty: {details!r}"
    zero = [l for l in timeline.split("<br>") if "Загрузка локальной LLM" in l]
    assert zero and "—" in zero[0], f"instant stage rendered without a time: {zero!r}"
    return "stages (including instant ones), versions and the reason all restored"



def _deleting_a_row_clears_its_status():
    """Removing a meeting must take its STATUS TIMELINE with it.

    The decisive case is the owner's: ONE meeting in history, deleted. With more
    than one row the selection moves to a neighbour and the timeline is redrawn as
    a side effect, which hides the defect - so this asserts the single-row case
    first. Source checks missed it entirely: `_clear_results` was called and reset
    every artifact surface, but the timeline is rendered HTML built from
    `_stages_by_job[_active_job]`, so "✔ Обработка — 10м 18с" of a deleted meeting
    stayed on screen beside an empty queue and a 0% bar.
    """
    import tempfile
    from app import config
    from app.ui.main_window import MainWindow

    settings = config.load_settings()

    def _window_with(count):
        root = Path(tempfile.mkdtemp())
        st = HistoryStore(path=root / "history.json",
                          transcripts_root=root / "transcripts")
        ids = [st.add(str(root / f"m{i}.mkv")) for i in range(count)]
        w = MainWindow(settings, st, queue=None, language="ru", theme="dark")
        for job_id in ids:
            if job_id not in w._rows:
                w.add_job_row(st.get(job_id))
        return w, ids

    def _stage(w, job_id):
        w._active_job = job_id
        w._current_result_id = job_id
        w._stages_by_job[job_id] = ["✔ Обработка — 10м 18с", "✔ Извлечение аудио — 2с"]
        w._render_stages()
        assert w.lbl_stages.text(), "the fixture failed to render a timeline"

    # 0. The unit that owned the defect: _clear_results must blank the timeline.
    #    Asserting only through the delete handler is not enough - with rows left
    #    over, the selection change redraws the timeline anyway and hides the bug.
    win0, ids0 = _window_with(1)
    _stage(win0, ids0[0])
    win0._clear_results()
    assert win0.lbl_stages.text() == "", (
        f"_clear_results left the timeline on screen: {win0.lbl_stages.text()!r}")
    win0.close()

    # 1. the only meeting in history
    win, ids = _window_with(1)
    _stage(win, ids[0])
    win.table.selectRow(win._rows[ids[0]])
    win._remove_selected()
    assert win.table.rowCount() == 0, "row not removed"
    assert win.lbl_stages.text() == "", (
        f"timeline survived deleting the only meeting: {win.lbl_stages.text()!r}")
    assert win.progress.value() == 0 and win.txt_summary.toPlainText() == ""
    assert ids[0] not in win._stages_by_job, "stage lines kept for a deleted id"
    win.close()

    # 2. the SHOWN meeting deleted while another remains
    win2, ids2 = _window_with(2)
    _stage(win2, ids2[1])
    win2.table.selectRow(win2._rows[ids2[1]])
    win2._remove_selected()
    assert win2.table.rowCount() == 1, "the other meeting must stay"
    assert ids2[1] not in win2._stages_by_job
    win2.close()

    # 3. Clear-the-queue must behave the same way
    win3, ids3 = _window_with(1)
    _stage(win3, ids3[0])
    win3._clear_queue()
    assert win3.lbl_stages.text() == "", "timeline survived a queue clear"
    win3.close()
    return "timeline, artifacts and progress reset on delete and on clear"


check("history_row_restores_its_status", _history_row_shows_its_status)
check("nothing_is_clipped_or_squeezed", _nothing_is_clipped)
check("empty_queue_clears_the_result_panels", _empty_queue_clears_the_panels)
check("deleting_a_row_clears_its_status", _deleting_a_row_clears_its_status)
check("no_window_demands_a_wide_screen", _no_window_demands_a_wide_screen)
check("queue_has_a_clear_button", _clear_queue_button)
check("settings_need_no_horizontal_scroll", _settings_fits_horizontally)


# ── every field the details header renders must have a PRODUCER ─────────────
# _render_history_head() prints (processed_at, duration, size) and drops empties,
# so a field nobody fills is invisible forever rather than obviously broken.
# _add_files() called store.add(path) with neither, so the length and size of
# every recording were blank on the desktop - the same "displayed field with no
# producer" class that was already fixed for the web cabinet's duration column.
def _duration_and_size_have_producers():
    real = Path(__file__).resolve().parent.parent / "tests" / "2026-05-19 13-04-45.mkv"
    if not real.exists():
        return "no sample recording in tests/ - skipped"
    root = Path(tempfile.mkdtemp())
    st = HistoryStore(path=root / "history.json",
                      transcripts_root=root / "transcripts")
    w = MainWindow(settings, st, queue=None, language="ru", theme="dark")
    # answer the modal Trim dialog the way "process the whole file" does
    w._resolve_segments = lambda p: [p]
    w._add_files([str(real)])
    loaded = st.load()               # ids are timestamps, so read the list back
    assert loaded, "the file was not added at all"
    entry = loaded[-1]
    assert entry.duration, "duration is empty - nothing produces it"
    assert entry.size, "size is empty - nothing produces it"
    assert "м" in entry.duration or "с" in entry.duration, entry.duration
    assert "MB" in entry.size or "KB" in entry.size or "GB" in entry.size, entry.size
    # and they must survive into the rendered header
    head = [p for p in ("", entry.duration, entry.size) if p]
    assert len(head) == 2, head
    # an unreadable file must never block adding it
    assert w._probe_duration_label(str(root / "missing.mkv")) == ""
    assert w._file_size_label(str(root / "missing.mkv")) == ""
    w.close()
    return f"duration={entry.duration!r} size={entry.size!r}"


check("history_entry_records_duration_and_size", _duration_and_size_have_producers)

print("\n".join(results))
print("SUMMARY " + ("ALL_PASS" if all(r.startswith("PASS") for r in results)
                    else "HAS_FAILURES"))
_code = 0 if results and not any(r.startswith("FAIL") for r in results) else 1
# Leave through os._exit: constructing any of the dialogs makes CPython terminate
# abnormally (127) while unwinding Qt AFTER this point, which turned an all-green
# run into a reported failure. The verdict is already printed and computed; the
# teardown crash is tracked separately in the ROADMAP.
sys.stdout.flush()
sys.stderr.flush()
os._exit(_code)
