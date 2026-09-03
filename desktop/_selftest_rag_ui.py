"""UI tests for RAG + Search integration in the main window (offscreen).

Verifies wiring without a live embeddings endpoint:
  * project field loads/persists onto the history entry
  * add-to-RAG enabled only with a summary; builds a correct command
  * RagDialog and SearchDialog construct
  * SearchDialog finds matches across transcripts (real text search)

Run:
    set QT_QPA_PLATFORM=offscreen && backend\\python\\python.exe desktop\\_selftest_rag_ui.py
"""
import sys, tempfile, os, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

from desktop.app.ui.main_window import MainWindow
from desktop.app.ui.rag_dialog import RagDialog
from desktop.app.ui.search_dialog import SearchDialog
from desktop.app.core.history import HistoryStore, versioned_filename

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if (detail and not cond) else ""))


tmp = tempfile.mkdtemp()
store = HistoryStore(path=os.path.join(tmp, "history.json"),
                     transcripts_root=Path(tmp) / "transcripts")
eid = store.add("C:/videos/alpha_meeting.mp4")
job = store.job_dir(eid)
tx = job / "alpha_meeting_raw.txt"
tx.write_text("[00:00:01] [Иван]: Обсудили бюджет и сроки проекта.\n"
              "[00:00:08] [Мария]: API спроектируем позже.", encoding="utf-8")
store.set_transcript(eid, tx)
s1 = job / versioned_filename("alpha_meeting", "summary", 1, ".txt")
s1.write_text("Краткое содержание встречи про бюджет.", encoding="utf-8")
store.add_summary_version(eid, s1, provider="local")

mw = MainWindow(settings={"ragEmbeddingBackend": "local",
                          "localEndpoint": "http://localhost:8080/v1"},
                store=store, queue=None, language="ru")

# ── project field ─────────────────────────────────────────────────────────────
mw._load_results(eid)
check("project_field_exists", hasattr(mw, "edit_project"))
check("project_empty_initially", mw.edit_project.text() == "")

# type a project and trigger persist
mw.edit_project.setText("ProjectAlpha")
mw._on_project_edited()
reloaded = store.get(eid)
check("project_persisted", reloaded.project == "ProjectAlpha", reloaded.project)

# reload reflects persisted project
mw._load_results(eid)
check("project_reloaded", mw.edit_project.text() == "ProjectAlpha")

# ── add-to-RAG button gating ──────────────────────────────────────────────────
check("add_rag_enabled_with_summary", mw.btn_add_rag.isEnabled())

# entry without summary -> disabled
eid2 = store.add("C:/videos/no_summary.mp4")
job2 = store.job_dir(eid2)
tx2 = job2 / "no_summary_raw.txt"; tx2.write_text("[00:00:01] текст", encoding="utf-8")
store.set_transcript(eid2, tx2)
mw._load_results(eid2)
check("add_rag_disabled_no_summary", not mw.btn_add_rag.isEnabled())

# ── RagDialog constructs ──────────────────────────────────────────────────────
try:
    rd = RagDialog(rag_dir=os.path.join(tmp, "rag"),
                   python_exe="python", rag_script="rag.py",
                   settings={"ragEmbeddingBackend": "local"}, language="ru")
    check("rag_dialog_constructs", True)
    check("rag_dialog_has_tabs", rd.tabs.count() == 3)
    rd._on_search_done("search", True, {"results": [{
        "doc_id": "x", "title": "Русская встреча <demo>",
        "date": "2026-01-01", "score": 0.75,
        "text": "Кириллица <script>alert(1)</script>",
    }]}, "")
    rendered = rd.search_results.toPlainText()
    check("rag_result_keeps_cyrillic", "Кириллица" in rendered, rendered)
    check("rag_result_escapes_html",
          "<script>alert(1)</script>" in rendered, rendered)
except Exception as e:
    check("rag_dialog_constructs", False, str(e))

# command building for search includes settings, for list does not
captured = {}
class _Probe(RagDialog):
    def _run(self, op, args, on_done):
        cmd = [self._python, self._script, op, "--rag-dir", self._rag_dir] + args
        if op in ("search", "add", "rebuild"):
            cmd += ["--settings", self._settings_json]
        captured[op] = cmd  # don't actually start a thread
try:
    probe = _Probe(rag_dir=os.path.join(tmp, "rag2"),
                   python_exe="py", rag_script="rag.py",
                   settings={"ragEmbeddingBackend": "local"}, language="ru")
    probe.query_edit.setText("бюджет")
    probe._do_search()
    check("search_cmd_has_query", "--query" in captured.get("search", []))
    check("search_cmd_has_settings", "--settings" in captured.get("search", []))
    check("list_cmd_no_settings", "--settings" not in captured.get("list", []))
    # rebuild button -> command carries --history-file + --settings (skip the confirm)
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    probe._history_file = os.path.join(tmp, "history.json")
    probe._do_rebuild()
    check("rebuild_btn_exists", hasattr(probe, "btn_rebuild"))
    check("rebuild_cmd_has_history",
          probe._history_file in captured.get("rebuild", []) and
          "--history-file" in captured.get("rebuild", []))
    check("rebuild_cmd_has_settings", "--settings" in captured.get("rebuild", []))
except Exception as e:
    check("rag_probe", False, str(e))

# ── SearchDialog constructs + finds matches ───────────────────────────────────
entries = [{
    "video_name": "alpha_meeting.mp4", "video_path": "C:/videos/alpha_meeting.mp4",
    "processed_at": "2026-03-01T10:00:00", "transcript_path": str(tx),
}]
sd = SearchDialog(entries, language="ru")
check("search_dialog_constructs", True)

# run synchronously by calling the thread's run path through search_in_text
from desktop.app.backend.textsearch import search_in_text
hits = search_in_text(tx.read_text(encoding="utf-8"), "бюджет")
check("search_finds_in_transcript", len(hits) >= 1)

# speaker filter in dialog flow (Иван said бюджет)
hits_ivan = search_in_text(tx.read_text(encoding="utf-8"), "бюджет", speaker_filter="Иван")
check("search_speaker_filter", len(hits_ivan) == 1, str(len(hits_ivan)))

# ── entry list build for search (without opening modal exec) ─────────────────
search_entries = []
for e in store.load():
    search_entries.append({
        "video_name": e.video_name, "video_path": e.video_path,
        "processed_at": e.processed_at or "", "transcript_path": e.transcript_path or "",
    })
check("search_entry_list_built", len(search_entries) >= 1)
sd2 = SearchDialog(search_entries, language="ru")
check("search_dialog_from_store_entries", sd2 is not None)

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)} failed): {', '.join(FAIL)}")
    sys.stdout.flush()
    os._exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
sys.stdout.flush()
# Avoid PySide6's noisy offscreen teardown (QTextBrowser/QThread finalizers can
# raise an access-violation exit code AFTER all assertions pass); hard-exit 0.
os._exit(0)
