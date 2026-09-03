"""Smoke tests for the AnalysisWidget (offscreen).

Run:
    set QT_QPA_PLATFORM=offscreen && backend\\python\\python.exe desktop\\_selftest_analysis_ui.py
"""
import sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from desktop.app.ui.analysis_widget import AnalysisWidget

PASS = []
FAIL = []

def check(name: str, cond: bool) -> None:
    if cond:
        PASS.append(name)
        print(f"PASS  {name}")
    else:
        FAIL.append(name)
        print(f"FAIL  {name}")

# ── fixture ──────────────────────────────────────────────────────────
ANALYSIS = {
    "characteristics": {"keyTopics": ["Сроки", "Бюджет", "Архитектура"]},
    "actionItems": [
        {"task": "Обновить план", "assignee": "Сергей", "priority": "high", "deadline": "2026-06-15"},
        {"task": "Провести ревью кода", "assignee": "Unassigned", "priority": "medium", "deadline": "Not specified"},
    ],
    "sentiment": {
        "overall": "positive",
        "engagement": "high",
        "hasConflict": False,
        "emotions": ["энтузиазм", "сосредоточенность"],
        "description": "Продуктивная встреча с высокой вовлечённостью.",
        "interruptionIndex": 25,
        "emotionalBalance": 75,
        "empathyIndex": 80,
        "speechSpeedVariability": "medium",
        "questionsToAnswersRatio": 1.2,
        "dominanceDistribution": {"Иван": 55, "Мария": 30, "Алексей": 15},
    },
    "category": {
        "category": "Планирование/Стратегия",
        "tags": ["Q2", "архитектура", "спринт"],
        "description": "Стратегическое планирование следующего спринта.",
    },
    "risks": [
        {"description": "Задержка интеграции", "severity": "high", "impact": "timeline", "status": "identified"},
    ],
    "quotes": [
        {"text": "Нам нужно ускориться.", "speaker": "Иван", "context": "Ключевое решение"},
    ],
    "technologies": [
        {"name": "Python", "category": "programming language", "context": "current use"},
        {"name": "PostgreSQL", "category": "database", "context": "planned"},
    ],
    "questions": [
        {"question": "Кто отвечает за деплой?", "category": "technical", "priority": "high", "owner": "Мария"},
    ],
    "recommendations": [
        {"recommendation": "Автоматизировать тесты.", "category": "technical", "priority": "high", "impact": "high"},
    ],
    "followupQuestions": [
        {"question": "Каков прогресс по задаче?", "category": "progress", "priority": "medium", "context": "Из предыдущей встречи"},
    ],
    "formalProtocol": {
        "protocolNumber": "П-2026-01",
        "date": "2026-06-08",
        "time": "10:00",
        "location": "Online",
        "participants": ["Иван Петров", "Мария Сидорова"],
        "chairman": "Иван Петров",
        "secretary": "Мария Сидорова",
        "agenda": ["Статус проекта", "Планирование Q2"],
        "decisions": [{"number": 1, "text": "Принять план Q2", "votingResult": "Единогласно"}],
        "actionItems": [{"task": "Обновить дорожную карту", "assignee": "Сергей", "deadline": "2026-06-20"}],
        "nextMeeting": "2026-06-22 10:00",
        "protocolText": "Протокол совещания от 2026-06-08.",
    },
}

# ── tests ────────────────────────────────────────────────────────────

# 1. Widget constructs without error
try:
    w = AnalysisWidget(language="ru")
    check("widget_constructs", True)
except Exception as e:
    check("widget_constructs", False)
    print(f"  ERR: {e}")
    sys.exit(1)

# 2. All 11 panels exist
check("has_11_panels", len(w._panels) == 11)

# 3. Before load: all panels hidden
check("panels_hidden_before_load", all(not getattr(p, "_has_data", False) for p in w._panels.values()))

# 4. Load full analysis: active panels become visible
w.load(ANALYSIS)
visible = [k for k, p in w._panels.items() if getattr(p, "_has_data", False)]
check("panels_visible_after_load", len(visible) == 11)

# 5. Each expected panel is visible
for key in ("characteristics", "actionItems", "sentiment", "category", "risks",
            "quotes", "technologies", "questions", "recommendations",
            "followupQuestions", "formalProtocol"):
    check(f"panel_{key}_visible", getattr(w._panels[key], "_has_data", False))

# 6. Panel titles contain emoji / text
for key in ("actionItems", "sentiment"):
    title = w._panels[key].title()
    check(f"panel_{key}_has_title", len(title) > 3)

# 7. Clear hides all
w.clear()
check("panels_hidden_after_clear", all(not getattr(p, "_has_data", False) for p in w._panels.values()))

# 8. Reload works
w.load(ANALYSIS)
check("reload_works", getattr(w._panels["sentiment"], "_has_data", False))

# 9. Empty analysis hides all
w.load({})
check("empty_analysis_hides_all", all(not getattr(p, "_has_data", False) for p in w._panels.values()))

# 10. None analysis hides all
w.load(None)
check("none_analysis_hides_all", all(not getattr(p, "_has_data", False) for p in w._panels.values()))

# 11. Language toggle
w.load(ANALYSIS)
w.set_language("en")
check("language_switch_en", "Action" in w._panels["actionItems"].title())
w.set_language("ru")
check("language_switch_ru", "Задачи" in w._panels["actionItems"].title())

# 12. Partial data: only sentiment, no actionItems
partial = {"sentiment": ANALYSIS["sentiment"]}
w.load(partial)
check("partial_sentiment_visible", getattr(w._panels["sentiment"], "_has_data", False))
check("partial_actionItems_hidden", not getattr(w._panels["actionItems"], "_has_data", False))

# 12b. Re-rendering must not leave the previous render alive underneath.
# Panels that build a nested QGridLayout/QHBoxLayout used to keep their labels
# parented to the panel after a clear, so switching analysis version/meeting
# painted the new rows ON TOP of the old ones (visible as doubled, overlapping
# "Длительность"/"Количество слов" rows).
from PySide6.QtWidgets import QLabel

_rr = AnalysisWidget()
_rr.load({"characteristics": {"keyTopics": ["A"]},
          "sentiment": {"overall": "positive", "score": 8,
                        "speakerDominance": {"Ivan": 65}}},
         {"duration": "30m49s", "participants": "Ivan", "wordCount": 7972})
app.processEvents()
_rr.load({"characteristics": {"keyTopics": ["C"]},
          "sentiment": {"overall": "neutral", "score": 5,
                        "speakerDominance": {"Petr": 40}}},
         {"duration": "12m01s", "participants": "Petr", "wordCount": 827})
app.processEvents()

_char_texts = [w.text() for w in _rr._panels["characteristics"].findChildren(QLabel)]
_sent_texts = [w.text() for w in _rr._panels["sentiment"].findChildren(QLabel)]
check("rerender_drops_previous_characteristics",
      not any("30m49s" in t or "7 972" in t or "Ivan" in t for t in _char_texts))
check("rerender_keeps_current_characteristics",
      any("12m01s" in t for t in _char_texts) and any("827" in t for t in _char_texts))
check("rerender_drops_previous_sentiment",
      not any("Ivan" in t for t in _sent_texts))
check("rerender_purges_nested_layouts",
      len(_char_texts) == len(set(_char_texts)))

# 13. MainWindow integrates analysis_widget
from desktop.app.ui.main_window import MainWindow
from desktop.app.core.history import HistoryStore
import tempfile, os

tmpdir = tempfile.mkdtemp()
store = HistoryStore(os.path.join(tmpdir, "history.json"))
mw = MainWindow(settings={}, store=store, queue=None, language="ru")
check("mainwindow_has_analysis_widget", hasattr(mw, "analysis_widget"))
check("mainwindow_analysis_widget_present_initially",
      not mw.analysis_widget.isHidden() and not mw.analysis_widget.empty_label.isHidden())

# ── summary ──────────────────────────────────────────────────────────
print()
# ── a list of plain strings must render, not crash ──────────────────────────
# analysis.is_valid_feature_result accepts ["мало ресурсов"] for every list
# feature, so the pipeline stores it; this panel used to raise AttributeError on
# it and blank the entire analysis view, while the exporters rendered it fine.
_string_shapes = {
    "actionItems": ["Запустить пилот"], "risks": ["Мало ресурсов"],
    "technologies": ["Kubernetes"], "quotes": ["цитата"],
    "questions": ["Когда релиз?"], "recommendations": ["Зафиксировать дату"],
    "followupQuestions": ["Кто отвечает?"], "keyTopics": ["Пилот"],
}
for _feature, _value in _string_shapes.items():
    _w = AnalysisWidget()
    try:
        _w.load({_feature: _value})
        _ok = True
    except Exception:  # noqa: BLE001
        _ok = False
    check(f"{_feature}_as_plain_strings_renders", _ok)
    _w.close()

_w = AnalysisWidget()
try:
    _w.load(_string_shapes)
    _mixed_ok = True
except Exception:  # noqa: BLE001
    _mixed_ok = False
check("every_list_feature_as_strings_renders_together", _mixed_ok)
_w.close()

if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)} failed): {', '.join(FAIL)}")
    sys.exit(1)
else:
    print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
    sys.exit(0)
