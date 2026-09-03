"""Headless integration test for the export UI wiring. Builds a MainWindow with
a temp HistoryStore, fabricates a finished job (two summary versions + one
analysis version), and drives _do_export through the real ExportWorker thread.
Verifies version-aware naming (latest version is exported, not v1), no data loss
through the worker, and the footer. Offscreen Qt; hard-exits to avoid teardown.
"""
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

results = []


def check(name, ok, detail=""):
    results.append((f"PASS  {name}  {detail}" if ok else f"FAIL  {name}  {detail}").rstrip())


try:
    from PySide6.QtWidgets import QApplication

    from app.backend import exporter
    from app.core.history import HistoryStore
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = tempfile.mkdtemp()
    store = HistoryStore(path=Path(tmp) / "history.json",
                         transcripts_root=Path(tmp) / "transcripts")
    win = MainWindow({"parallelWorkers": "2", "language": "ru", "theme": "dark"},
                     store, queue=None, language="ru", theme="dark")

    eid = store.add("C:/x/meeting.mkv", "5m", "18 MB")
    jobdir = Path(store.job_dir(eid))
    jobdir.mkdir(parents=True, exist_ok=True)

    # A transcript, so the "Transcript" export kind has something to write.
    raw = jobdir / "meeting_raw.txt"
    raw.write_text("[00:00:01] RAW_SENTINEL" + chr(10), encoding="utf-8")
    store.set_transcript(eid, raw)

    s1 = jobdir / "meeting_summary.txt"
    s1.write_text("# T\n\nv1 text", encoding="utf-8")
    store.add_summary_version(eid, s1, provider="local")
    s2 = jobdir / "meeting_summary_v2.txt"
    s2.write_text("# T\n\nSUMMARY_V2_SENTINEL", encoding="utf-8")
    store.add_summary_version(eid, s2, provider="local")

    adict = {"actionItems": [{"task": "UI_AI_SENTINEL", "assignee": "X", "priority": "high"}],
             "characteristics": {"keyTopics": ["KT_UI"]}}
    aj = jobdir / "meeting_analysis.json"
    aj.write_text(json.dumps(adict, ensure_ascii=False), encoding="utf-8")
    store.add_analysis_version(eid, aj, provider="local")

    win._load_results(eid)
    check("export_bar_exists", win.cb_export_kind.count() == 3
          and win.cb_export_fmt.count() == len(exporter.FORMATS))
    check("current_id_set", win._current_result_id == eid)

    def run_export(kind, fmt):
        win.cb_export_kind.setCurrentIndex(win.cb_export_kind.findData(kind))
        win.cb_export_fmt.setCurrentIndex(win.cb_export_fmt.findData(fmt))
        win._do_export()
        if win._ew is not None:
            win._ew.wait(20000)
        app.processEvents()

    # summary -> html: must use the LATEST (v2) name + v2 content + footer
    run_export("summary", "html")
    exp_sum = jobdir / "meeting_summary_v2.html"
    stext = exp_sum.read_text(encoding="utf-8") if exp_sum.exists() else ""
    check("summary_export_v2_named", exp_sum.exists(), exp_sum.name)
    check("summary_export_content",
          "SUMMARY_V2_SENTINEL" in stext and "Meeting Summarizer v" in stext)

    # analysis -> docx: v1 name, sentinel present, footer present (no loss via worker)
    run_export("analysis", "docx")
    exp_an = jobdir / "meeting_analysis.docx"
    check("analysis_export_named", exp_an.exists(), exp_an.name)
    from docx import Document
    dtext = "\n".join(p.text for p in Document(str(exp_an)).paragraphs) if exp_an.exists() else ""
    check("analysis_export_content",
          "UI_AI_SENTINEL" in dtext and "Meeting Summarizer v" in dtext)

    check("status_saved", "Сохранено" in win.lbl_export_status.text(),
          win.lbl_export_status.text())

    # manual Obsidian export through ObsidianWorker
    vault = Path(tmp) / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    win.settings["obsidianIntegration"] = True
    win.settings["obsidianVaultPath"] = str(vault)
    win._do_obsidian()
    if win._ow is not None:
        win._ow.wait(20000)
    app.processEvents()
    # One press = the ONE kind selected next to the button. It used to write a
    # summary note whatever the kind was (and reported nothing for the others).
    notes = sorted(p.name for p in vault.rglob("*.md"))
    kind_now = win.cb_export_kind.currentData()
    expected = {"raw": "transcript", "summary": "summary", "analysis": "analysis"}[kind_now]
    check("obsidian_note_written_for_the_selected_kind",
          len(notes) == 1 and expected in notes[0], f"{kind_now} -> {notes}")
    check("obsidian_status", "Obsidian" in win.lbl_export_status.text(),
          win.lbl_export_status.text())
    # every kind must produce its own note
    kinds = [win.cb_export_kind.itemData(i) for i in range(win.cb_export_kind.count())]
    for kind in kinds:
        win.cb_export_kind.setCurrentIndex(kinds.index(kind))
        app.processEvents()
        win._do_obsidian()
        if win._ow is not None:
            win._ow.wait(20000)
        app.processEvents()
    notes = sorted(p.name for p in vault.rglob("*.md"))
    check("obsidian_covers_every_kind",
          any("transcript" in n for n in notes) and any("summary" in n for n in notes)
          and any("analysis" in n for n in notes), str(notes))
except Exception as exc:  # noqa: BLE001
    results.append(f"FAIL  harness  {exc!r}")
    results.append("      " + traceback.format_exc().replace("\n", "\n      "))

print("\n".join(results))
ok_all = bool(results) and all(r.startswith("PASS") for r in results)
print("SUMMARY " + ("ALL_PASS" if ok_all else "HAS_FAILURES"))
sys.stdout.flush()
sys.stderr.flush()
os._exit(0 if ok_all else 1)
