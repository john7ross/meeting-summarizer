"""Self-test for the unified exporter. Verifies: all kinds x all formats write
non-empty files; NO DATA LOSS (a unique sentinel from every one of the 11
analysis features appears in txt/md/html and in docx); the app+version footer is
present in every text-readable format; version-aware naming; analysis JSON
round-trips exactly. Pure (no Qt). Run with the embedded Python.
"""
import json
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import py_compile  # noqa: E402
py_compile.compile(str(Path(__file__).resolve().parent / "app/backend/exporter.py"), doraise=True)

from app.backend import exporter as E  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((f"PASS  {name}  {detail}" if ok else f"FAIL  {name}  {detail}").rstrip())


ANALYSIS = {
    "characteristics": {"keyTopics": ["KT_SENTINEL", "topic2"],
                        "frequentWords": [["wordX", 7], "plainword"]},
    "actionItems": [{"task": "AI_TASK_SENTINEL", "assignee": "AI_WHO",
                     "priority": "high", "deadline": "2026-07-01"}],
    "sentiment": {"overall": "positive", "engagement": "high", "hasConflict": True,
                  "emotions": ["joy", "focus"], "description": "SENT_DESC",
                  "interruptionIndex": 10, "emotionalBalance": 80, "empathyIndex": 70,
                  "speechSpeedVariability": "low", "questionsToAnswersRatio": 1.5,
                  "dominanceDistribution": {"Speaker_1": 60, "Speaker_2": 40}},
    "category": {"category": "CAT_SENTINEL", "tags": ["t1", "t2"], "description": "CAT_DESC"},
    "risks": [{"description": "RISK_SENTINEL", "severity": "high", "impact": "timeline",
               "status": "identified"}],
    "quotes": [{"text": "QUOTE_SENTINEL", "speaker": "Bob", "context": "QCTX"}],
    "technologies": [{"name": "TECH_SENTINEL", "category": "tool", "context": "current use"}],
    "questions": [{"question": "Q_SENTINEL.", "category": "technical", "priority": "low",
                   "owner": "Ann"}],
    "recommendations": [{"recommendation": "REC_SENTINEL", "category": "process",
                         "priority": "medium", "impact": "high"}],
    "followupQuestions": [{"question": "FQ_SENTINEL.", "category": "clarification",
                           "priority": "high", "context": "FCTX"}],
    "formalProtocol": {"protocolNumber": "FP_NUM", "date": "2026-06-05", "time": "10:00",
                       "location": "Online", "participants": ["P1", "P2"], "chairman": "Chair",
                       "secretary": "Sec", "agenda": ["AG1", "AG2"],
                       "decisions": [{"number": 1, "text": "DEC_SENTINEL",
                                      "votingResult": "Unanimous"}],
                       "actionItems": [{"task": "FPAI_SENTINEL", "assignee": "Who",
                                        "deadline": "soon"}],
                       "nextMeeting": "next week", "protocolText": "PROTO_TEXT_SENTINEL"},
}
SENTINELS = ["KT_SENTINEL", "AI_TASK_SENTINEL", "SENT_DESC", "CAT_SENTINEL", "RISK_SENTINEL",
             "QUOTE_SENTINEL", "TECH_SENTINEL", "Q_SENTINEL.", "REC_SENTINEL", "FQ_SENTINEL.",
             "DEC_SENTINEL", "FPAI_SENTINEL", "PROTO_TEXT_SENTINEL"]
SUMMARY = "# Заголовок\n\nВступление.\n\n## Тема 1\n\n- пункт один\n- пункт два\n\n## Тема 2\n\nТекст."
RAW = "Спикер 1: привет.\n\nСпикер 2: здравствуйте."

DATA = {"raw": RAW, "summary": SUMMARY, "analysis": ANALYSIS}

try:
    tmp = Path(tempfile.mkdtemp())
    meta = {"video_name": "meeting.mkv", "duration": "5m", "version": 3, "language": "ru",
            "export_date": "2026-07-25 12:34", "participants": "Alice, Bob",
            "wordCount": 4242}

    # 1) all kinds x all formats produce non-empty files
    all_files = {}
    for kind in E.KINDS:
        for fmt in E.FORMATS:
            p = E.default_export_path(tmp, "meeting", kind, meta["version"], fmt)
            E.export(kind, DATA[kind], fmt, p, meta)
            all_files[(kind, fmt)] = p
    missing = [(k, f) for (k, f), p in all_files.items() if not p.exists() or p.stat().st_size == 0]
    check("all_outputs_written", not missing, f"{len(all_files)} files" if not missing else str(missing))

    # 2) NO DATA LOSS — values from every analysis branch and the common
    # metadata header are present in every directly readable format.
    visible_values = SENTINELS + [
        "topic2", "wordX", "plainword", "AI_WHO", "2026-07-01", "joy", "focus",
        "Speaker_1", "Speaker_2", "CAT_DESC", "timeline", "identified", "Bob",
        "QCTX", "tool", "current use", "technical", "Ann", "process",
        "clarification", "FCTX", "FP_NUM", "Online", "P1", "P2", "Chair", "Sec",
        "AG1", "AG2", "Unanimous", "Who", "soon", "next week",
        "meeting.mkv", "2026-07-25 12:34", "5m", "Alice, Bob", "4 242",
    ]
    for fmt in ("txt", "md", "html"):
        text = all_files[("analysis", fmt)].read_text(encoding="utf-8")
        lost = [s for s in visible_values if s not in text]
        check(f"analysis_no_loss_{fmt}", not lost, "ok" if not lost else f"missing {lost}")

    # 3) footer present in text-readable formats
    for fmt in ("txt", "md", "html"):
        text = all_files[("analysis", fmt)].read_text(encoding="utf-8")
        check(f"footer_{fmt}", "Meeting Summarizer v" in text)

    # 4) docx: read back -> sentinels + footer present (no data loss in docx)
    from docx import Document
    doc = Document(str(all_files[("analysis", "docx")]))
    dtext = "\n".join(p.text for p in doc.paragraphs)
    dlost = [s for s in visible_values if s not in dtext]
    check("analysis_no_loss_docx", not dlost, "ok" if not dlost else f"missing {dlost}")
    check("footer_docx", "Meeting Summarizer v" in dtext)

    # 5) pdf built and reasonably sized (content correctness covered by shared blocks)
    pdf = all_files[("analysis", "pdf")]
    check("pdf_nontrivial", pdf.stat().st_size > 1500, f"{pdf.stat().st_size} bytes")

    # 6) version-aware naming
    n_sum = E.default_export_path(tmp, "meeting", "summary", 3, "pdf").name
    n_an = E.default_export_path(tmp, "meeting", "analysis", 2, "html").name
    n_raw = E.default_export_path(tmp, "meeting", "raw", 5, "txt").name
    n_sum1 = E.default_export_path(tmp, "meeting", "summary", 1, "md").name
    check("naming", n_sum == "meeting_summary_v3.pdf" and n_an == "meeting_analysis_v2.html"
          and n_raw == "meeting_raw.txt" and n_sum1 == "meeting_summary.md",
          f"{n_sum} | {n_an} | {n_raw} | {n_sum1}")

    # 7) analysis JSON round-trips exactly + version + generator
    aj = json.loads(all_files[("analysis", "json")].read_text(encoding="utf-8"))
    check("json_roundtrip", aj["analysis"] == ANALYSIS, "exact" if aj["analysis"] == ANALYSIS else "MISMATCH")
    check("json_meta", aj["metadata"]["version"] == 3
          and aj["_generator"].startswith("Meeting Summarizer v"))

    # 8) summary json preserves full text + sectioned structure
    sj = json.loads(all_files[("summary", "json")].read_text(encoding="utf-8"))
    check("summary_json_struct", sj["summary"]["full_text"] == SUMMARY
          and any(sec["title"] == "Тема 1" for sec in sj["summary"]["sections"]))
except Exception as exc:  # noqa: BLE001
    results.append(f"FAIL  harness  {exc!r}")
    results.append("      " + traceback.format_exc().replace("\n", "\n      "))

print("\n".join(results))
print("SUMMARY " + ("ALL_PASS" if results and all(r.startswith("PASS") for r in results)
                    else "HAS_FAILURES"))
sys.exit(0 if results and not any(r.startswith("FAIL") for r in results) else 1)
