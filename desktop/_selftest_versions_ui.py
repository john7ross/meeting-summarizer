"""UI tests for version switching + regenerate wiring (offscreen).

Run:
    set QT_QPA_PLATFORM=offscreen && backend\\python\\python.exe desktop\\_selftest_versions_ui.py
"""
import sys, tempfile, os, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

from desktop.app.ui.main_window import MainWindow
from desktop.app.core.history import HistoryStore, versioned_filename

PASS, FAIL = [], []

def check(name, cond, detail=""):
    if cond:
        PASS.append(name); print(f"PASS  {name}")
    else:
        FAIL.append(name); print(f"FAIL  {name}" + (f"  ({detail})" if detail else ""))


class FakeQueue:
    """Minimal stand-in capturing enqueue_regenerate calls."""
    def __init__(self):
        self.regen_calls = []
        self.max_concurrency = 1
    # signals used by _connect_queue are accessed only if present; MainWindow
    # guards on `if not self.queue`. We provide the connect-only attributes via
    # a no-op signal shim.
    class _Sig:
        def connect(self, *a, **k): pass
    status_changed = _Sig()
    progress = _Sig()
    job_finished = _Sig()
    speakers_needed = _Sig()
    def enqueue_regenerate(self, entry_id, video_path, transcript_path, scope="both"):
        self.regen_calls.append((entry_id, video_path, transcript_path, scope))


tmp = tempfile.mkdtemp()
store = HistoryStore(path=os.path.join(tmp, "history.json"),
                     transcripts_root=Path(tmp) / "transcripts")
eid = store.add("C:/videos/meeting.mp4")
job = store.job_dir(eid)

# transcript
tx = job / "meeting_raw.txt"
tx.write_text("[00:00:01] Привет.\n[00:00:05] Как дела?", encoding="utf-8")
store.set_transcript(eid, tx)

# 2 summary versions
s1 = job / versioned_filename("meeting","summary",1,".txt"); s1.write_text("SUMMARY ONE", encoding="utf-8")
store.add_summary_version(eid, s1, provider="local")
s2 = job / versioned_filename("meeting","summary",2,".txt"); s2.write_text("SUMMARY TWO", encoding="utf-8")
store.add_summary_version(eid, s2, provider="openai")

# 2 analysis versions, linked
a1 = job / versioned_filename("meeting","analysis",1,".json")
a1.write_text(json.dumps({"category":{"category":"Cat A","tags":[]}}), encoding="utf-8")
store.add_analysis_version(eid, a1, provider="local", source_summary_version=1)
a2 = job / versioned_filename("meeting","analysis",2,".json")
a2.write_text(json.dumps({"category":{"category":"Cat B","tags":[]}}), encoding="utf-8")
store.add_analysis_version(eid, a2, provider="openai", source_summary_version=2)

fake_q = FakeQueue()
mw = MainWindow(settings={}, store=store, queue=fake_q, language="ru")

# ── load results ──────────────────────────────────────────────────────────────
mw._load_results(eid)

check("summary_combo_2_items", mw.cb_sum_version.count() == 2)
check("analysis_combo_2_items", mw.cb_an_version.count() == 2)

# default selection = latest (index 1)
check("default_summary_latest", mw._sel_summary_idx == 1)
check("default_analysis_latest", mw._sel_analysis_idx == 1)

# summary text shows v2 (latest)
check("summary_shows_v2", mw.txt_summary.toPlainText() == "SUMMARY TWO",
      mw.txt_summary.toPlainText())

# analysis shows v2 (Cat B)
check("analysis_shows_v2", mw._current_analysis.get("category",{}).get("category") == "Cat B",
      str(mw._current_analysis))

# version label contains linkage hint for analysis
lbl = mw.cb_an_version.itemText(0)
check("analysis_label_has_source", "v1" in lbl, lbl)

# ── step summary back to v1 ───────────────────────────────────────────────────
mw._step_summary_version(-1)
check("stepped_to_summary_v1", mw._sel_summary_idx == 0)
check("summary_shows_v1", mw.txt_summary.toPlainText() == "SUMMARY ONE",
      mw.txt_summary.toPlainText())

# next disabled at end? go to v2 then check next-at-end
mw._step_summary_version(1)
check("back_to_summary_v2", mw._sel_summary_idx == 1)
check("next_disabled_at_end", not mw.btn_sum_next.isEnabled())
mw._step_summary_version(-1)
check("prev_disabled_at_start", not mw.btn_sum_prev.isEnabled() or mw._sel_summary_idx == 0)

# ── dropdown pick analysis v1 ─────────────────────────────────────────────────
mw.cb_an_version.setCurrentIndex(0)
check("analysis_picked_v1", mw._sel_analysis_idx == 0)
check("analysis_shows_catA", mw._current_analysis.get("category",{}).get("category") == "Cat A")

# ── regenerate button enabled (transcript present) ────────────────────────────
check("regen_enabled", mw.btn_regenerate.isEnabled())

# ── regenerate flow: monkeypatch QMessageBox.question to auto-Yes ─────────────
from PySide6.QtWidgets import QMessageBox
orig_q = QMessageBox.question
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
try:
    # edit transcript in UI before regenerate
    mw.txt_raw.setPlainText("[00:00:01] ОТРЕДАКТИРОВАНО.")
    mw._do_regenerate()
finally:
    QMessageBox.question = orig_q

check("regen_enqueued", len(fake_q.regen_calls) == 1, str(fake_q.regen_calls))
if fake_q.regen_calls:
    rid, vpath, tpath, scope = fake_q.regen_calls[0]
    check("regen_correct_id", rid == eid)
    check("regen_default_scope_is_both", scope == "both", scope)
    # transcript file was overwritten with the edited text
    saved = Path(tpath).read_text(encoding="utf-8")
    check("regen_wrote_edited_transcript", "ОТРЕДАКТИРОВАНО" in saved, saved)
    check("regen_button_disabled_during", not mw.btn_regenerate.isEnabled())

# ── empty-transcript guard ────────────────────────────────────────────────────
fake_q.regen_calls.clear()
mw.txt_raw.setPlainText("   ")
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
try:
    mw._do_regenerate()
finally:
    QMessageBox.question = orig_q
check("regen_blocked_empty_transcript", len(fake_q.regen_calls) == 0)

# ── entry with single version: nav hidden/disabled ────────────────────────────
eid2 = store.add("C:/videos/solo.mp4")
job2 = store.job_dir(eid2)
tx2 = job2 / "solo_raw.txt"; tx2.write_text("[00:00:01] one liner", encoding="utf-8")
store.set_transcript(eid2, tx2)
s = job2 / versioned_filename("solo","summary",1,".txt"); s.write_text("only", encoding="utf-8")
store.add_summary_version(eid2, s, provider="local")
mw._load_results(eid2)
check("single_summary_combo_1", mw.cb_sum_version.count() == 1)
check("single_summary_nav_disabled",
      not mw.btn_sum_prev.isEnabled() and not mw.btn_sum_next.isEnabled())
check("no_analysis_combo_empty", mw.cb_an_version.count() == 0)

# ── summary ───────────────────────────────────────────────────────────────────
print()

# -- exporting must follow the version PICKER, not the newest version ---------
# Choosing v2 and pressing Export silently wrote v3: the handler read
# ``versions[-1]`` and numbered the file with len(versions), so every selection
# also collided on one file name.
def _export_uses_the_selected_version():
    import tempfile
    import time
    from desktop.app import config
    from desktop.app.core.pipeline import PipelineQueue, JobRunner

    tmp = Path(tempfile.mkdtemp())
    store = HistoryStore(path=tmp / "history.json", transcripts_root=tmp / "transcripts")
    video = tmp / "meeting.mkv"
    video.write_bytes(b"x")
    entry_id = store.add(str(video))
    job = store.job_dir(entry_id)
    job.mkdir(parents=True, exist_ok=True)
    transcript = job / "meeting_raw.txt"
    transcript.write_text("[00:00:01] text" + chr(10), encoding="utf-8")
    store.set_transcript(entry_id, str(transcript))
    for n in (1, 2, 3):
        src = job / f"s{n}.txt"
        src.write_text(f"BODY OF VERSION {n}", encoding="utf-8")
        store.add_summary_version(entry_id, str(src), provider=f"p{n}")

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
    window.cb_sum_version.setCurrentIndex(1)          # v2, not the newest
    for _ in range(2):
        app.processEvents()
    kinds = [window.cb_export_kind.itemData(i) for i in range(window.cb_export_kind.count())]
    fmts = [window.cb_export_fmt.itemData(i) for i in range(window.cb_export_fmt.count())]
    window.cb_export_kind.setCurrentIndex(kinds.index("summary"))
    window.cb_export_fmt.setCurrentIndex(fmts.index("txt"))
    window._do_export()
    for _ in range(60):
        app.processEvents()
        time.sleep(0.05)
    written = sorted(job.glob("meeting_summary*.txt"))
    window.close()
    return written


written = _export_uses_the_selected_version()
check("export_writes_the_selected_version_only", len(written) == 1, str([p.name for p in written]))
check("export_file_is_named_after_that_version",
      bool(written) and written[0].name.endswith("_v2.txt"), str([p.name for p in written]))
def _obsidian_follows_both_selectors():
    """The Obsidian button obeys the kind AND the version shown in the UI."""
    import json
    import tempfile
    import time
    from desktop.app import config
    from desktop.app.core.pipeline import PipelineQueue, JobRunner

    tmp = Path(tempfile.mkdtemp())
    vault = tmp / "vault"
    vault.mkdir()
    store = HistoryStore(path=tmp / "history.json", transcripts_root=tmp / "transcripts")
    video = tmp / "meeting.mkv"
    video.write_bytes(b"x")
    entry_id = store.add(str(video))
    job = store.job_dir(entry_id)
    job.mkdir(parents=True, exist_ok=True)
    transcript = job / "raw.txt"
    transcript.write_text("[00:00:01] spoken words" + chr(10), encoding="utf-8")
    store.set_transcript(entry_id, str(transcript))
    for n in (1, 2, 3):
        src = job / f"s{n}.txt"
        src.write_text(f"SUMMARY BODY {n}", encoding="utf-8")
        store.add_summary_version(entry_id, str(src), provider="p")
    an = job / "a1.json"
    an.write_text(json.dumps({"keyTopics": ["t"]}), encoding="utf-8")
    store.add_analysis_version(entry_id, str(an), provider="p")

    settings = dict(config.load_settings())
    settings["obsidianIntegration"] = True
    settings["obsidianVaultPath"] = str(vault)
    window = MainWindow(settings, store,
                        PipelineQueue(1, lambda i, v: JobRunner(i, v, settings, store)),
                        language="ru", theme="dark")
    window.show()
    for _ in range(3):
        app.processEvents()
    window.table.selectRow(0)
    for _ in range(2):
        app.processEvents()
    kinds = [window.cb_export_kind.itemData(i) for i in range(window.cb_export_kind.count())]
    out = {}
    for kind in ("raw", "analysis", "summary"):
        window.cb_export_kind.setCurrentIndex(kinds.index(kind))
        if kind == "summary":
            window.cb_sum_version.setCurrentIndex(1)      # v2 of three
        for _ in range(2):
            app.processEvents()
        window._do_obsidian()
        for _ in range(60):
            app.processEvents()
            time.sleep(0.05)
        out[kind] = window.lbl_export_status.text()
    written = sorted(p.name for p in (vault / "Meetings" / "meeting").glob("*.md"))
    body = (vault / "Meetings" / "meeting" / "meeting_summary_v2.md")
    window.close()
    return out, written, body


statuses, notes, v2_note = _obsidian_follows_both_selectors()
check("obsidian_exports_the_transcript_kind", "transcript" in statuses["raw"], statuses["raw"])
check("obsidian_exports_the_analysis_kind", "analysis" in statuses["analysis"],
      statuses["analysis"])
check("obsidian_names_the_selected_summary_version",
      "summary_v2" in statuses["summary"], statuses["summary"])
check("obsidian_wrote_one_note_per_kind",
      notes == ["meeting_analysis.md", "meeting_summary_v2.md", "meeting_transcript.md"],
      str(notes))
check("obsidian_note_holds_the_selected_version",
      v2_note.exists() and "SUMMARY BODY 2" in v2_note.read_text(encoding="utf-8"),
      v2_note.read_text(encoding="utf-8")[:60] if v2_note.exists() else "missing")

check("export_contains_that_version_content",
      bool(written) and "BODY OF VERSION 2" in written[0].read_text(encoding="utf-8"),
      written[0].read_text(encoding="utf-8")[:60] if written else "nothing written")

if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)} failed): {', '.join(FAIL)}")
    sys.exit(1)
else:
    print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
    sys.exit(0)
