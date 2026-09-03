"""Self-test for the rewritten Obsidian export. Verifies the EXACT layout the
user's vault uses: Meetings/<stem>/<stem>_summary.md + <stem>_analysis.md,
the rich analysis format (# 📊 Meeting Analysis, characteristics table, emoji
sections), summary frontmatter, _index/By Date.md entry, the 4 static _queries
files, and People/Topics notes. Checks no data loss + word count. Pure (no Qt).
"""
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import py_compile  # noqa: E402
py_compile.compile(str(Path(__file__).resolve().parent / "app/backend/obsidian.py"), doraise=True)

from app.backend import obsidian as O  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((f"PASS  {name}  {detail}" if ok else f"FAIL  {name}  {detail}").rstrip())


STEM = "2026-03-25 14-32-25"
SUMMARY = "# x\n\n## Тема: Процесс оформления (ВЛК)\n\n- пункт\n\n## Тема: Стратегия"
TRANSCRIPT = "слово " * 1234
ANALYSIS = {
    "characteristics": {"keyTopics": ["KT_TOPIC_SENT", "Вторая тема"]},
    "actionItems": [{"task": "AI_TASK_SENT", "assignee": "Сережа", "priority": "high",
                     "deadline": "спринт"}],
    "sentiment": {"overall": "neutral", "engagement": "high", "hasConflict": False,
                  "emotions": ["усталость"], "description": "SENT_DESC_SENTINEL",
                  "interruptionIndex": 65, "emotionalBalance": 45, "empathyIndex": 75,
                  "speechSpeedVariability": "high", "questionsToAnswersRatio": 0.35,
                  "dominanceDistribution": {"Speaker_1": 65, "Speaker_2": 35}},
    "category": {"category": "CAT_SENTINEL", "tags": ["бизнес-процессы"], "description": "CAT_DESC"},
    "risks": [{"description": "RISK_SENT", "severity": "high", "impact": "business",
               "status": "identified"}],
    "quotes": [{"text": "QUOTE_SENT", "speaker": "Мария", "context": "QCTX"}],
    "technologies": [{"name": "TECH_SENT", "category": "platform", "context": "current use"}],
    "questions": [{"question": "Q_SENT", "category": "business", "priority": "medium",
                   "owner": "Юристы"}],
    "recommendations": [{"recommendation": "REC_SENT", "category": "process",
                         "priority": "high", "impact": "high"}],
}
SENT = ["KT_TOPIC_SENT", "AI_TASK_SENT", "SENT_DESC_SENTINEL", "CAT_SENTINEL", "RISK_SENT",
        "QUOTE_SENT", "TECH_SENT", "Q_SENT", "REC_SENT"]

try:
    vault = Path(tempfile.mkdtemp())
    settings = {"obsidianVaultPath": str(vault), "createPeopleNotes": True,
                "createTopicNotes": True, "updateMeetingIndex": True,
                "createDataviewQueries": True}
    res = O.export_to_obsidian(vault, stem=STEM, video_name=STEM + ".mkv",
                               summary_text=SUMMARY, analysis=ANALYSIS, settings=settings,
                               duration="1h 5m 28s", summary_version=1, analysis_version=1,
                               transcript_text=TRANSCRIPT, language="ru")

    folder = vault / "Meetings" / STEM
    sum_path = folder / f"{STEM}_summary.md"
    an_path = folder / f"{STEM}_analysis.md"
    check("folder_named_by_stem", folder.is_dir())
    check("summary_named", sum_path.exists() and res["summary"] == str(sum_path), sum_path.name)
    check("analysis_named", an_path.exists() and res["analysis"] == str(an_path), an_path.name)

    stext = sum_path.read_text(encoding="utf-8") if sum_path.exists() else ""
    check("summary_frontmatter", stext.startswith("---\ntype: meeting-summary")
          and f"title: {STEM}" in stext and f"# {STEM}" in stext)
    check("summary_footer", stext.rstrip().endswith(O.FOOTER), O.APP_VERSION)

    a = an_path.read_text(encoding="utf-8") if an_path.exists() else ""
    check("analysis_header", a.startswith("# 📊 Meeting Analysis")
          and f"**File:** {STEM}" in a and "**Generated:**" in a)
    check("analysis_characteristics_table", "## 📋 Характеристики встречи" in a
          and "| ⏱️ **Длительность** | 1h 5m 28s |" in a)
    check("analysis_word_count", "| 📝 **Количество слов** | 1 234 |" in a, "1 234")
    check("analysis_sections", all(h in a for h in [
        "## ✅ Задачи и Action Items", "## 😊 Анализ тональности", "## 📂 Категория встречи",
        "## 🔴 Риски и блокеры", "## 💬 Ключевые цитаты", "## 💻 Технологии и системы",
        "## ❓ Нерешенные вопросы", "## 💡 Рекомендации"]))
    check("analysis_priority_ru", "🔴 **Priority:** ВЫСОКИЙ" in a)
    check("analysis_dominance", "- **Speaker_1:** 65%" in a)
    alost = [s for s in SENT if s not in a]
    check("analysis_no_loss", not alost, "ok" if not alost else f"missing {alost}")
    check("analysis_footer", a.rstrip().endswith(O.FOOTER), O.APP_VERSION)

    index = vault / "Meetings" / "_index" / "By Date.md"
    itext = index.read_text(encoding="utf-8") if index.exists() else ""
    check("index_entry", "## 2026-03-25" in itext
          and f"[[{STEM}/{STEM}_summary|{STEM}]]" in itext)

    qdir = vault / "Meetings" / "_queries"
    qfiles = {"Action Items.md", "By Person.md", "By Topic.md", "Recent Meetings.md"}
    have = {p.name for p in qdir.glob("*.md")} if qdir.exists() else set()
    check("queries_4_files", qfiles <= have, f"{sorted(have)}")

    people = list((vault / "People").glob("*.md")) if (vault / "People").exists() else []
    ptext = people[0].read_text(encoding="utf-8") if people else ""
    check("people_notes", len(people) == 2 and "speaking time" in ptext
          and f"[[{STEM}/{STEM}_summary|{STEM}]]" in ptext, f"{[p.name for p in people]}")
    topics = list((vault / "Topics").glob("*.md")) if (vault / "Topics").exists() else []
    check("topic_notes", len(topics) >= 2, f"{len(topics)} files")

    # versioning: v2 appends _v2
    check("versioned_name", O._versioned(f"{STEM}_summary", 2) == f"{STEM}_summary_v2")
except Exception as exc:  # noqa: BLE001
    results.append(f"FAIL  harness  {exc!r}")
    results.append("      " + traceback.format_exc().replace("\n", "\n      "))


# -- the export must write the KIND that was asked for -----------------------
# The vault only ever got summary + analysis notes: choosing "Transcript" in the
# UI still produced "<stem>_summary_vN.md", and a kind whose note was not written
# reported an empty path, so the button looked dead.
def _kinds_are_honoured():
    import json
    import tempfile
    vault = Path(tempfile.mkdtemp()) / "vault"
    vault.mkdir()
    common = dict(stem="встреча", video_name="встреча.mkv",
                  summary_text="САММАРИ", analysis={"keyTopics": ["тема"]},
                  settings={}, duration="5m", transcript_text="[00:00:01] текст",
                  language="ru")
    only_raw = O.export_to_obsidian(vault, kinds=("raw",), **common)
    assert only_raw["transcript"] and not only_raw["summary"], only_raw
    assert Path(only_raw["transcript"]).name == "встреча_transcript.md", only_raw
    body = Path(only_raw["transcript"]).read_text(encoding="utf-8")
    assert "type: meeting-transcript" in body and "[00:00:01] текст" in body, body[:120]

    only_an = O.export_to_obsidian(vault, kinds=("analysis",), **common)
    assert only_an["analysis"] and not only_an["summary"], only_an

    only_sum = O.export_to_obsidian(vault, kinds=("summary",), summary_version=2,
                                    **common)
    assert Path(only_sum["summary"]).name == "встреча_summary_v2.md", only_sum
    assert not only_sum["analysis"], only_sum

    both = O.export_to_obsidian(vault, **common)     # default = historic behaviour
    assert both["summary"] and both["analysis"], both
    return "raw / summary / analysis written separately, default unchanged"


try:
    detail = _kinds_are_honoured()
    results.append(f"PASS  obsidian_writes_the_requested_kind  {detail}")
except Exception as exc:  # noqa: BLE001
    results.append(f"FAIL  obsidian_writes_the_requested_kind  {exc!r}")

print("\n".join(results))
print("SUMMARY " + ("ALL_PASS" if results and all(r.startswith("PASS") for r in results)
                    else "HAS_FAILURES"))
sys.exit(0 if results and not any(r.startswith("FAIL") for r in results) else 1)
