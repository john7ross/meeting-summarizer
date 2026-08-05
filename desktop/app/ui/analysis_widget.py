"""Advanced Analysis Widget — 11 collapsible panels that render the analysis JSON.

Port of the Electron renderer's render* functions (renderActionItems,
renderSentiment, renderCategory, renderRisks, renderQuotes, renderTechnologies,
renderQuestions, renderRecommendations, renderFollowupQuestions,
renderFormalProtocol) plus a characteristics/key-topics panel.

Each panel is a QGroupBox that hides itself when the data is absent/empty.
The widget is a QScrollArea wrapping a vertical layout of all panels.

Usage::

    widget = AnalysisWidget(language="ru", parent=self)
    widget.load(analysis_dict)   # from <id>/..._analysis.json
    widget.clear()
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLayout,
    QProgressBar, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)


class FlowLayout(QLayout):
    """A left-to-right layout that wraps to the next row when it runs out of
    width — for tag rows (key topics, emotions) that must not clip or overflow.
    (A plain QHBoxLayout can't wrap, which is why long topic lists were cut off.)"""

    def __init__(self, parent=None, spacing: int = 4) -> None:
        super().__init__(parent)
        self._items: list = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect, test_only: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        sp = self.spacing()
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() and line_h > 0:
                x = rect.x()
                y += line_h + sp
                line_h = 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + sp
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()

# ──────────────────────────────────────────────────────────────────
# i18n labels (minimal; mirrors what renderer.js translate() does)
# ──────────────────────────────────────────────────────────────────
_L: dict[str, dict[str, str]] = {
    "ru": {
        # panel titles
        "panel.characteristics":   "📊 Характеристики",
        "panel.actionItems":       "✅ Задачи и решения",
        "panel.sentiment":         "😊 Тональность",
        "panel.category":          "📂 Категория",
        "panel.risks":             "🔴 Риски и блокеры",
        "panel.quotes":            "💬 Ключевые цитаты",
        "panel.technologies":      "💻 Технологии",
        "panel.questions":         "❓ Открытые вопросы",
        "panel.recommendations":   "💡 Рекомендации",
        "panel.followupQuestions": "🔄 Вопросы для следующей встречи",
        "panel.formalProtocol":    "📜 Формальный протокол",
        # field labels
        "assignee": "Ответственный",
        "deadline": "Срок",
        "impact":   "Влияние",
        "owner":    "Ответственный",
        "context":  "Контекст",
        # sentiment
        "sentiment.overall.positive": "Позитивная встреча",
        "sentiment.overall.neutral":  "Нейтральная встреча",
        "sentiment.overall.negative": "Негативная встреча",
        "sentiment.engagement":         "Вовлечённость",
        "sentiment.conflict":           "Конфликт",
        "sentiment.emotions":           "Эмоции",
        "sentiment.interruptionIndex":  "Индекс прерываний",
        "sentiment.emotionalBalance":   "Эмоциональный баланс",
        "sentiment.empathyIndex":       "Индекс эмпатии",
        "sentiment.speechSpeed":        "Скорость речи",
        "sentiment.qRatio":             "Соотн. вопросы/ответы",
        "sentiment.dominance":          "Распределение доминирования",
        "yes": "Да", "no": "Нет",
        # priority
        "priority.high":   "Высокий",
        "priority.medium": "Средний",
        "priority.low":    "Низкий",
        # risk status
        "risk.identified":  "Выявлен",
        "risk.in-progress": "В работе",
        "risk.resolved":    "Решён",
        # tech category
        "tech.programming language": "Языки программирования",
        "tech.framework":  "Фреймворки",
        "tech.tool":       "Инструменты",
        "tech.platform":   "Платформы",
        "tech.service":    "Сервисы",
        "tech.database":   "Базы данных",
        "tech.other":      "Прочее",
        # tech context
        "ctx.current use": "Текущее", "ctx.planned": "Планируется",
        "ctx.problem":     "Проблема", "ctx.alternative": "Альтернатива",
        # question category
        "qcat.technical": "Техническая", "qcat.business": "Бизнес",
        "qcat.process":   "Процесс",     "qcat.resource":  "Ресурсы",
        # recommendation category
        "rcat.process":        "Процесс",      "rcat.communication": "Коммуникация",
        "rcat.technical":      "Техническая",  "rcat.planning":      "Планирование",
        # followup category
        "fcat.clarification":    "Уточнение",       "fcat.progress":       "Прогресс",
        "fcat.deep-dive":        "Углублённый анализ", "fcat.decision-review": "Пересмотр решения",
        # protocol
        "proto.number":       "Номер протокола",
        "proto.date":         "Дата",
        "proto.time":         "Время",
        "proto.location":     "Место",
        "proto.chairman":     "Председатель",
        "proto.secretary":    "Секретарь",
        "proto.participants": "Участники",
        "proto.agenda":       "Повестка",
        "proto.decisions":    "Решения",
        "proto.actionItems":  "Задачи",
        "proto.nextMeeting":  "Следующая встреча",
        "proto.text":         "Текст протокола",
        "proto.responsible":  "Ответственный",
        "proto.deadline":     "Срок",
        "proto.votingResult": "Голосование",
        # misc
        "key.topics":  "Ключевые темы",
        "char.duration": "Длительность", "char.participants": "Участники",
        "char.words": "Количество слов",
        "no.data":     "Нет данных",
    },
    "en": {
        "panel.characteristics":   "📊 Characteristics",
        "panel.actionItems":       "✅ Action Items",
        "panel.sentiment":         "😊 Sentiment",
        "panel.category":          "📂 Category",
        "panel.risks":             "🔴 Risks & Blockers",
        "panel.quotes":            "💬 Key Quotes",
        "panel.technologies":      "💻 Technologies",
        "panel.questions":         "❓ Open Questions",
        "panel.recommendations":   "💡 Recommendations",
        "panel.followupQuestions": "🔄 Follow-up Questions",
        "panel.formalProtocol":    "📜 Formal Protocol",
        "assignee": "Assignee", "deadline": "Deadline",
        "impact": "Impact",     "owner": "Owner",
        "context": "Context",
        "sentiment.overall.positive": "Positive meeting",
        "sentiment.overall.neutral":  "Neutral meeting",
        "sentiment.overall.negative": "Negative meeting",
        "sentiment.engagement":        "Engagement",
        "sentiment.conflict":          "Conflict",
        "sentiment.emotions":          "Emotions",
        "sentiment.interruptionIndex": "Interruption index",
        "sentiment.emotionalBalance":  "Emotional balance",
        "sentiment.empathyIndex":      "Empathy index",
        "sentiment.speechSpeed":       "Speech speed variability",
        "sentiment.qRatio":            "Questions/answers ratio",
        "sentiment.dominance":         "Dominance distribution",
        "yes": "Yes", "no": "No",
        "priority.high": "High", "priority.medium": "Medium", "priority.low": "Low",
        "risk.identified": "Identified", "risk.in-progress": "In progress",
        "risk.resolved":   "Resolved",
        "tech.programming language": "Programming languages",
        "tech.framework": "Frameworks", "tech.tool": "Tools",
        "tech.platform":  "Platforms",  "tech.service": "Services",
        "tech.database":  "Databases",  "tech.other": "Other",
        "ctx.current use": "Current use", "ctx.planned": "Planned",
        "ctx.problem": "Problem",         "ctx.alternative": "Alternative",
        "qcat.technical": "Technical", "qcat.business": "Business",
        "qcat.process":   "Process",   "qcat.resource":  "Resource",
        "rcat.process": "Process",        "rcat.communication": "Communication",
        "rcat.technical": "Technical",    "rcat.planning":      "Planning",
        "fcat.clarification": "Clarification", "fcat.progress": "Progress",
        "fcat.deep-dive":     "Deep-dive",     "fcat.decision-review": "Decision review",
        "proto.number":       "Protocol number",
        "proto.date":         "Date",         "proto.time":         "Time",
        "proto.location":     "Location",     "proto.chairman":     "Chairman",
        "proto.secretary":    "Secretary",    "proto.participants": "Participants",
        "proto.agenda":       "Agenda",       "proto.decisions":    "Decisions",
        "proto.actionItems":  "Action items", "proto.nextMeeting":  "Next meeting",
        "proto.text":         "Protocol text","proto.responsible":  "Responsible",
        "proto.deadline":     "Deadline",     "proto.votingResult": "Voting result",
        "key.topics": "Key Topics", "no.data": "No data",
        "char.duration": "Duration", "char.participants": "Participants",
        "char.words": "Word count",
    },
}


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

def _t(key: str, lang: str) -> str:
    tbl = _L.get(lang, _L["en"])
    return tbl.get(key) or _L["en"].get(key, key)


_PRIORITY_COLORS = {"high": "#f44747", "medium": "#ff9800", "low": "#4ec9b0"}
_SEVERITY_ICONS  = {"high": "🔴", "medium": "🟡", "low": "🟢"}
_STATUS_ICONS    = {"identified": "🆕", "in-progress": "⏳", "resolved": "✅"}
_CATEGORY_ICONS  = {"technical": "⚙️", "business": "💼", "process": "🔄", "resource": "👥"}
_RCAT_ICONS      = {"process": "🔄", "communication": "💬", "technical": "⚙️", "planning": "📋"}
_FCAT_ICONS      = {"clarification": "❓", "progress": "📈", "deep-dive": "🔍", "decision-review": "🔁"}
_TECH_ICONS      = {
    "programming language": "💻", "framework": "🏗️", "tool": "🔧",
    "platform": "☁️", "service": "🌐", "database": "🗄️", "other": "📦",
}


def _lbl(text: str, obj_name: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    if obj_name:
        lbl.setObjectName(obj_name)
    return lbl


def _badge(text: str, color: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("analysisBadge")
    lbl.setStyleSheet(f"color: {color}; font-weight: 600;")
    return lbl


def _hsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setObjectName("analysisSep")
    return f


def _bar_widget(value: int, color: str) -> QWidget:
    """A label + progress bar in a row, value 0-100."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setValue(value)
    bar.setFixedHeight(8)
    bar.setTextVisible(False)
    bar.setObjectName("analysisBar")
    bar.setStyleSheet(
        f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}"
        "QProgressBar { background: rgba(255,255,255,.1); border-radius: 3px; border: none; }"
    )
    lbl = QLabel(f"{value}/100")
    lbl.setObjectName("hint")
    h.addWidget(bar, 1)
    h.addWidget(lbl)
    return w


# ──────────────────────────────────────────────────────────────────
# Panel base
# ──────────────────────────────────────────────────────────────────

class _Panel(QGroupBox):
    """A collapsible analysis panel (title bar = QGroupBox title)."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self.setCheckable(False)
        self.setObjectName("analysisPanel")
        self._body = QVBoxLayout(self)
        self._body.setContentsMargins(12, 8, 12, 12)
        self._body.setSpacing(6)

    @staticmethod
    def _purge(layout) -> None:
        """Empty a layout INCLUDING its nested layouts.

        Panels that build a QGridLayout/QHBoxLayout and ``addLayout`` it are not
        cleared by walking top-level items only: taking the nested layout out of
        the parent leaves its labels parented to the panel, so they keep painting
        at their old geometry and the next render draws on top of them (that is
        the 'overlapping rows' in Characteristics/Sentiment after switching
        analysis version, meeting or language). ``setParent(None)`` detaches them
        at once - ``deleteLater`` alone only frees them on the next event loop
        pass, which is too late for the repaint that follows immediately.
        """
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
                continue
            nested = item.layout()
            if nested is not None:
                _Panel._purge(nested)
                nested.deleteLater()

    def _clear(self) -> None:
        self._has_data = False
        self._purge(self._body)

    def render(self, data: Any, lang: str) -> None:
        """Subclasses implement this to populate _body. Call _clear() first."""
        raise NotImplementedError

    def load(self, data: Any, lang: str) -> None:
        self._clear()
        has_data = bool(data)
        self._has_data = has_data
        if has_data:
            self.render(data, lang)
        self.setVisible(has_data)


# ──────────────────────────────────────────────────────────────────
# Characteristics (keyTopics + word count)
# ──────────────────────────────────────────────────────────────────

class _CharacteristicsPanel(_Panel):
    def render(self, data: Any, lang: str) -> None:
        # data = analysis["characteristics"] enriched with meta (duration/participants/words)
        d = data or {}
        rows = []
        if d.get("duration"):
            rows.append((_t("char.duration", lang), str(d["duration"])))
        if d.get("participants"):
            rows.append((_t("char.participants", lang), str(d["participants"])))
        if d.get("wordCount") is not None:
            rows.append((_t("char.words", lang),
                         f"{int(d['wordCount']):,}".replace(",", " ")))
        if rows:
            grid = QGridLayout()
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(4)
            for i, (k, v) in enumerate(rows):
                lbl_k = _lbl(f"<b>{k}</b>"); lbl_k.setMinimumWidth(140)
                grid.addWidget(lbl_k, i, 0, Qt.AlignTop)
                vlbl = _lbl(v); vlbl.setWordWrap(True)
                grid.addWidget(vlbl, i, 1)
            grid.setColumnStretch(1, 1)
            self._body.addLayout(grid)
        topics: list[str] = d.get("keyTopics") or []
        if not topics:
            return
        self._body.addWidget(_lbl(f"<b>{_t('key.topics', lang)}</b>"))
        flow = QWidget()
        fl = FlowLayout(flow, spacing=4)   # wraps — long topic lists no longer clip
        for topic in topics:
            tag = QLabel(topic)
            tag.setObjectName("categoryTag")
            tag.setWordWrap(True)
            fl.addWidget(tag)
        self._body.addWidget(flow)


def _as_dict(item: Any, key: str) -> dict:
    """An analysis entry as a dict, whatever the model returned.

    ``analysis.is_valid_feature_result`` accepts a list of plain strings for every
    list feature, so ["мало ресурсов"] is a legitimately stored result - and the
    exporters and Google Sheets already render it. This panel assumed dicts and
    raised AttributeError, blanking the whole analysis view. A bare string becomes
    the entry's main field instead.
    """
    if isinstance(item, dict):
        return item
    return {key: str(item)}


# ──────────────────────────────────────────────────────────────────
# Action Items
# ──────────────────────────────────────────────────────────────────

class _ActionItemsPanel(_Panel):
    def render(self, data: Any, lang: str) -> None:
        items = [_as_dict(i, "task") for i in (data or [])]
        for item in items:
            card = QFrame()
            card.setObjectName("analysisCard")
            cv = QVBoxLayout(card)
            cv.setContentsMargins(10, 8, 10, 8)
            cv.setSpacing(4)
            cv.addWidget(_lbl(item.get("task", "—"), "analysisCardTitle"))
            meta = QHBoxLayout()
            meta.setSpacing(8)
            prio = item.get("priority", "medium")
            meta.addWidget(_badge(f"● {_t(f'priority.{prio}', lang)}", _PRIORITY_COLORS.get(prio, "#888")))
            if item.get("assignee") and item["assignee"] not in ("Unassigned", "Не назначен"):
                meta.addWidget(_lbl(f"👤 {item['assignee']}", "hint"))
            if item.get("deadline") and item["deadline"] not in ("Not specified", "Не указан"):
                meta.addWidget(_lbl(f"📅 {item['deadline']}", "hint"))
            meta.addStretch(1)
            cv.addLayout(meta)
            self._body.addWidget(card)


# ──────────────────────────────────────────────────────────────────
# Sentiment
# ──────────────────────────────────────────────────────────────────

class _SentimentPanel(_Panel):
    def render(self, data: Any, lang: str) -> None:
        s = data or {}
        overall = s.get("overall", "neutral")
        icons = {"positive": "😊", "neutral": "😐", "negative": "😟"}
        # Header row
        hrow = QHBoxLayout()
        hrow.setSpacing(8)
        icon_lbl = QLabel(icons.get(overall, "😐"))
        icon_lbl.setStyleSheet("font-size: 28px;")
        hrow.addWidget(icon_lbl)
        desc_col = QVBoxLayout()
        desc_col.addWidget(_lbl(f"<b>{_t(f'sentiment.overall.{overall}', lang)}</b>"))
        if s.get("description"):
            desc_col.addWidget(_lbl(s["description"], "hint"))
        hrow.addLayout(desc_col, 1)
        self._body.addLayout(hrow)
        self._body.addWidget(_hsep())

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        row = 0

        def _metric(label: str, value_widget: QWidget) -> None:
            nonlocal row
            grid.addWidget(_lbl(f"<b>{label}</b>"), row, 0)
            grid.addWidget(value_widget, row, 1)
            row += 1

        engagement = s.get("engagement", "")
        if engagement:
            eng_colors = {"high": "#4ec9b0", "medium": "#ff9800", "low": "#f44747"}
            _metric(_t("sentiment.engagement", lang),
                    _badge(_t(f"priority.{engagement}", lang),
                           eng_colors.get(engagement, "#888")))

        conflict = s.get("hasConflict")
        if conflict is not None:
            clr = "#f44747" if conflict else "#4ec9b0"
            _metric(_t("sentiment.conflict", lang),
                    _badge(_t("yes" if conflict else "no", lang), clr))

        emotions = s.get("emotions")
        if emotions:
            _metric(_t("sentiment.emotions", lang), _lbl(", ".join(emotions)))

        for key, label, color in [
            ("interruptionIndex",  "sentiment.interruptionIndex",  "#ff9800"),
            ("emotionalBalance",   "sentiment.emotionalBalance",   "#4ec9b0"),
            ("empathyIndex",       "sentiment.empathyIndex",       "#4ec9b0"),
        ]:
            val = s.get(key)
            if val is not None:
                _metric(_t(label, lang), _bar_widget(int(val), color))

        speed = s.get("speechSpeedVariability")
        if speed:
            _metric(_t("sentiment.speechSpeed", lang), _lbl(_t(f"priority.{speed}", lang)))

        ratio = s.get("questionsToAnswersRatio")
        if ratio is not None:
            _metric(_t("sentiment.qRatio", lang), _lbl(f"{float(ratio):.2f}"))

        self._body.addLayout(grid)

        dom = s.get("dominanceDistribution")
        if dom and isinstance(dom, dict):
            self._body.addWidget(_hsep())
            self._body.addWidget(_lbl(f"<b>{_t('sentiment.dominance', lang)}</b>"))
            for speaker, pct in dom.items():
                row_w = QWidget()
                rh = QHBoxLayout(row_w)
                rh.setContentsMargins(0, 0, 0, 0)
                rh.setSpacing(8)
                name_lbl = QLabel(speaker)
                name_lbl.setFixedWidth(120)
                rh.addWidget(name_lbl)
                bar = QProgressBar()
                bar.setRange(0, 100)
                bar.setValue(int(pct))
                bar.setFixedHeight(10)
                bar.setTextVisible(False)
                bar.setObjectName("analysisBar")
                # Qt QSS has no CSS var() — a var() fill silently renders nothing,
                # which is why these bars were empty. Use a literal accent colour.
                bar.setStyleSheet(
                    "QProgressBar::chunk { background: #0e7ad4; border-radius: 4px; }"
                    "QProgressBar { background: rgba(255,255,255,.1); border-radius: 4px; border: none; }"
                )
                rh.addWidget(bar, 1)
                pct_lbl = QLabel(f"{pct}%")
                pct_lbl.setObjectName("hint")
                pct_lbl.setFixedWidth(38)
                rh.addWidget(pct_lbl)
                self._body.addWidget(row_w)


# ──────────────────────────────────────────────────────────────────
# Category
# ──────────────────────────────────────────────────────────────────

class _CategoryPanel(_Panel):
    def render(self, data: Any, lang: str) -> None:
        c = data or {}
        badge = QLabel(f"📋 {c.get('category', '—')}")
        badge.setObjectName("categoryBadge")
        self._body.addWidget(badge)
        tags = c.get("tags") or []
        if tags:
            flow = QWidget()
            fl = QHBoxLayout(flow)
            fl.setContentsMargins(0, 0, 0, 0)
            fl.setSpacing(4)
            fl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            for tag in tags:
                tl = QLabel(tag)
                tl.setObjectName("categoryTag")
                fl.addWidget(tl)
            fl.addStretch(1)
            self._body.addWidget(flow)
        if c.get("description"):
            self._body.addWidget(_lbl(c["description"], "hint"))


# ──────────────────────────────────────────────────────────────────
# Risks
# ──────────────────────────────────────────────────────────────────

class _RisksPanel(_Panel):
    def render(self, data: Any, lang: str) -> None:
        for risk in [_as_dict(i, "description") for i in (data or [])]:
            sev = risk.get("severity", "medium")
            card = QFrame()
            card.setObjectName(f"riskCard_{sev}")
            cv = QVBoxLayout(card)
            cv.setContentsMargins(10, 8, 10, 8)
            cv.setSpacing(4)
            header = QHBoxLayout()
            header.addWidget(_badge(
                f"{_SEVERITY_ICONS.get(sev, '🟡')} {_t(f'priority.{sev}', lang)}",
                _PRIORITY_COLORS.get(sev, "#888"),
            ))
            header.addStretch(1)
            status = risk.get("status", "identified")
            header.addWidget(_lbl(f"{_STATUS_ICONS.get(status, '🆕')} {_t(f'risk.{status}', lang)}", "hint"))
            cv.addLayout(header)
            cv.addWidget(_lbl(risk.get("description", "—"), "analysisCardTitle"))
            impact = risk.get("impact", "")
            if impact:
                cv.addWidget(_lbl(f"{_t('impact', lang)}: {impact}", "hint"))
            self._body.addWidget(card)


# ──────────────────────────────────────────────────────────────────
# Quotes
# ──────────────────────────────────────────────────────────────────

class _QuotesPanel(_Panel):
    def render(self, data: Any, lang: str) -> None:
        for q in [_as_dict(i, "text") for i in (data or [])]:
            card = QFrame()
            card.setObjectName("quoteCard")
            cv = QVBoxLayout(card)
            cv.setContentsMargins(14, 10, 14, 10)
            cv.setSpacing(4)
            quote_lbl = QLabel(f'"{q.get("text", "")}"')
            quote_lbl.setObjectName("quoteText")
            quote_lbl.setWordWrap(True)
            cv.addWidget(quote_lbl)
            meta = QHBoxLayout()
            meta.addWidget(_lbl(f"— {q.get('speaker', '?')}", "quoteAuthor"))
            ctx = q.get("context")
            if ctx:
                meta.addWidget(_lbl(ctx, "hint"))
            meta.addStretch(1)
            cv.addLayout(meta)
            self._body.addWidget(card)


# ──────────────────────────────────────────────────────────────────
# Technologies
# ──────────────────────────────────────────────────────────────────

class _TechnologiesPanel(_Panel):
    def render(self, data: Any, lang: str) -> None:
        grouped: dict[str, list] = {}
        for tech in [_as_dict(i, "name") for i in (data or [])]:
            cat = (tech.get("category") or "other").lower()
            grouped.setdefault(cat, []).append(tech)
        for cat, techs in grouped.items():
            icon = _TECH_ICONS.get(cat, "📦")
            self._body.addWidget(_lbl(f"<b>{icon} {_t(f'tech.{cat}', lang)}</b>"))
            for tech in techs:
                ctx = (tech.get("context") or "").lower().replace(" ", "_")
                ctx_label = _t(f"ctx.{tech.get('context', '')}", lang)
                row_w = QWidget()
                rh = QHBoxLayout(row_w)
                rh.setContentsMargins(8, 2, 0, 2)
                rh.setSpacing(8)
                rh.addWidget(_lbl(f"<b>{tech.get('name', '?')}</b>"))
                ctx_badge = QLabel(ctx_label)
                ctx_badge.setObjectName("techContext")
                rh.addWidget(ctx_badge)
                rh.addStretch(1)
                self._body.addWidget(row_w)


# ──────────────────────────────────────────────────────────────────
# Questions
# ──────────────────────────────────────────────────────────────────

class _QuestionsPanel(_Panel):
    def render(self, data: Any, lang: str) -> None:
        for q in [_as_dict(i, "question") for i in (data or [])]:
            card = QFrame()
            card.setObjectName("analysisCard")
            cv = QVBoxLayout(card)
            cv.setContentsMargins(10, 8, 10, 8)
            cv.setSpacing(4)
            cat = (q.get("category") or "technical").lower()
            prio = (q.get("priority") or "medium").lower()
            header = QHBoxLayout()
            header.addWidget(_lbl(
                f"{_CATEGORY_ICONS.get(cat, '❓')} {_t(f'qcat.{cat}', lang)}", "hint"))
            header.addStretch(1)
            header.addWidget(_badge(_t(f"priority.{prio}", lang),
                                    _PRIORITY_COLORS.get(prio, "#888")))
            cv.addLayout(header)
            cv.addWidget(_lbl(q.get("question", "—"), "analysisCardTitle"))
            owner = q.get("owner", "")
            if owner and owner not in ("Unassigned", "Не назначен"):
                cv.addWidget(_lbl(f"👤 {owner}", "hint"))
            self._body.addWidget(card)


# ──────────────────────────────────────────────────────────────────
# Recommendations
# ──────────────────────────────────────────────────────────────────

class _RecommendationsPanel(_Panel):
    def render(self, data: Any, lang: str) -> None:
        for rec in [_as_dict(i, "recommendation") for i in (data or [])]:
            card = QFrame()
            card.setObjectName("analysisCard")
            cv = QVBoxLayout(card)
            cv.setContentsMargins(10, 8, 10, 8)
            cv.setSpacing(4)
            cat  = (rec.get("category") or "process").lower()
            prio = (rec.get("priority") or "medium").lower()
            imp  = (rec.get("impact")   or "medium").lower()
            header = QHBoxLayout()
            header.addWidget(_lbl(
                f"{_RCAT_ICONS.get(cat, '💡')} {_t(f'rcat.{cat}', lang)}", "hint"))
            header.addStretch(1)
            header.addWidget(_badge(_t(f"priority.{prio}", lang),
                                    _PRIORITY_COLORS.get(prio, "#888")))
            imp_badge = QLabel(f"{_t('impact', lang)}: {_t(f'priority.{imp}', lang)}")
            imp_badge.setObjectName("hint")
            header.addWidget(imp_badge)
            cv.addLayout(header)
            cv.addWidget(_lbl(rec.get("recommendation", "—"), "analysisCardTitle"))
            self._body.addWidget(card)


# ──────────────────────────────────────────────────────────────────
# Follow-up Questions
# ──────────────────────────────────────────────────────────────────

class _FollowupPanel(_Panel):
    def render(self, data: Any, lang: str) -> None:
        for q in [_as_dict(i, "question") for i in (data or [])]:
            card = QFrame()
            card.setObjectName("analysisCard")
            cv = QVBoxLayout(card)
            cv.setContentsMargins(10, 8, 10, 8)
            cv.setSpacing(4)
            cat  = (q.get("category") or "clarification").lower()
            prio = (q.get("priority") or "medium").lower()
            header = QHBoxLayout()
            header.addWidget(_lbl(
                f"{_FCAT_ICONS.get(cat, '💭')} {_t(f'fcat.{cat}', lang)}", "hint"))
            header.addStretch(1)
            header.addWidget(_badge(_t(f"priority.{prio}", lang),
                                    _PRIORITY_COLORS.get(prio, "#888")))
            cv.addLayout(header)
            cv.addWidget(_lbl(q.get("question", "—"), "analysisCardTitle"))
            ctx = q.get("context")
            if ctx:
                cv.addWidget(_lbl(f"{_t('context', lang)}: {ctx}", "hint"))
            self._body.addWidget(card)


# ──────────────────────────────────────────────────────────────────
# Formal Protocol
# ──────────────────────────────────────────────────────────────────

class _ProtocolPanel(_Panel):
    def render(self, data: Any, lang: str) -> None:
        p = data or {}

        def _kv(key_token: str, value: str) -> None:
            if not value:
                return
            row_w = QWidget()
            rh = QHBoxLayout(row_w)
            rh.setContentsMargins(0, 1, 0, 1)
            rh.setSpacing(8)
            k = QLabel(f"<b>{_t(key_token, lang)}:</b>")
            k.setFixedWidth(170)
            k.setAlignment(Qt.AlignTop)
            rh.addWidget(k)
            rh.addWidget(_lbl(value), 1)
            self._body.addWidget(row_w)

        # Metadata
        _kv("proto.number",   str(p.get("protocolNumber") or ""))
        _kv("proto.date",     str(p.get("date") or ""))
        _kv("proto.time",     str(p.get("time") or ""))
        _kv("proto.location", str(p.get("location") or ""))
        _kv("proto.chairman", str(p.get("chairman") or ""))
        _kv("proto.secretary",str(p.get("secretary") or ""))

        # Participants
        participants = p.get("participants") or []
        if participants:
            self._body.addWidget(_hsep())
            self._body.addWidget(_lbl(f"<b>{_t('proto.participants', lang)}</b>"))
            for name in participants:
                self._body.addWidget(_lbl(f"• {name}"))

        # Agenda
        agenda = p.get("agenda") or []
        if agenda:
            self._body.addWidget(_hsep())
            self._body.addWidget(_lbl(f"<b>{_t('proto.agenda', lang)}</b>"))
            for i, item in enumerate(agenda, 1):
                self._body.addWidget(_lbl(f"{i}. {item}"))

        # Decisions
        decisions = p.get("decisions") or []
        if decisions:
            self._body.addWidget(_hsep())
            self._body.addWidget(_lbl(f"<b>{_t('proto.decisions', lang)}</b>"))
            for d in decisions:
                text = d.get("text") or d.get("decision") or str(d)
                voting = d.get("votingResult", "")
                row_w = QWidget()
                rh = QVBoxLayout(row_w)
                rh.setContentsMargins(8, 4, 0, 4)
                rh.addWidget(_lbl(text, "analysisCardTitle"))
                if voting:
                    rh.addWidget(_lbl(f"{_t('proto.votingResult', lang)}: {voting}", "hint"))
                self._body.addWidget(row_w)

        # Action items
        action_items = p.get("actionItems") or []
        if action_items:
            self._body.addWidget(_hsep())
            self._body.addWidget(_lbl(f"<b>{_t('proto.actionItems', lang)}</b>"))
            for i, a in enumerate(action_items, 1):
                task = a.get("task") or a.get("action") or a.get("text") or str(a)
                card = QFrame()
                card.setObjectName("analysisCard")
                cv = QVBoxLayout(card)
                cv.setContentsMargins(10, 6, 10, 6)
                cv.setSpacing(2)
                cv.addWidget(_lbl(f"{i}. {task}", "analysisCardTitle"))
                meta = QHBoxLayout()
                if a.get("assignee") or a.get("responsible"):
                    meta.addWidget(_lbl(
                        f"👤 {a.get('assignee') or a.get('responsible')}", "hint"))
                if a.get("deadline"):
                    meta.addWidget(_lbl(f"📅 {a['deadline']}", "hint"))
                meta.addStretch(1)
                cv.addLayout(meta)
                self._body.addWidget(card)

        next_meeting = p.get("nextMeeting") or ""
        if next_meeting:
            self._body.addWidget(_hsep())
            _kv("proto.nextMeeting", next_meeting)

        protocol_text = p.get("protocolText") or ""
        if protocol_text:
            self._body.addWidget(_hsep())
            self._body.addWidget(_lbl(f"<b>{_t('proto.text', lang)}</b>"))
            txt_lbl = QLabel(protocol_text)
            txt_lbl.setWordWrap(True)
            txt_lbl.setObjectName("protocolText")
            txt_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._body.addWidget(txt_lbl)


# ──────────────────────────────────────────────────────────────────
# Main widget
# ──────────────────────────────────────────────────────────────────

class AnalysisWidget(QScrollArea):
    """Scrollable container of 11 analysis panels.

    Call ``load(analysis_dict)`` to populate.  Call ``clear()`` to reset.
    Language can be changed by calling ``set_language(lang)`` then ``load()``.
    """

    def __init__(self, language: str = "ru", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.language = language if language in _L else "ru"
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setObjectName("analysisScrollArea")

        container = QWidget()
        container.setObjectName("analysisContainer")
        root = QVBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self._root_layout = root

        self.empty_label = QLabel(
            "Анализ появится здесь после обработки"
            if self.language == "ru" else
            "Analysis will appear here after processing")
        self.empty_label.setObjectName("analysisEmpty")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        root.addWidget(self.empty_label, 1)

        # Build panels in Electron display order
        self._panels: dict[str, _Panel] = {}
        specs = [
            ("characteristics",   _CharacteristicsPanel),
            ("actionItems",       _ActionItemsPanel),
            ("sentiment",         _SentimentPanel),
            ("category",          _CategoryPanel),
            ("risks",             _RisksPanel),
            ("quotes",            _QuotesPanel),
            ("technologies",      _TechnologiesPanel),
            ("questions",         _QuestionsPanel),
            ("recommendations",   _RecommendationsPanel),
            ("followupQuestions", _FollowupPanel),
            ("formalProtocol",    _ProtocolPanel),
        ]
        for key, cls in specs:
            panel = cls(_t(f"panel.{key}", language))
            panel.setVisible(False)
            self._panels[key] = panel
            root.addWidget(panel)

        root.addStretch(1)
        self.setWidget(container)

    def set_language(self, lang: str) -> None:
        self.language = lang if lang in _L else "ru"
        self.empty_label.setText(
            "Анализ появится здесь после обработки"
            if self.language == "ru" else
            "Analysis will appear here after processing")
        for key, panel in self._panels.items():
            panel.setTitle(_t(f"panel.{key}", self.language))

    def load(self, analysis: dict | None, meta: dict | None = None) -> None:
        """Render all panels from an analysis JSON dict (may be None/empty).
        ``meta`` carries characteristics not in the analysis JSON — meeting
        duration, participants, word count — to mirror the export."""
        if not analysis:
            self.clear()
            return
        self.empty_label.setVisible(False)
        lang = self.language
        meta = meta or {}
        char = analysis.get("characteristics") or {}
        topics = char.get("keyTopics") or []
        char_data = {"keyTopics": topics, "duration": meta.get("duration"),
                     "participants": meta.get("participants"),
                     "wordCount": meta.get("wordCount")}
        has_char = topics or any(meta.get(k) not in (None, "")
                                 for k in ("duration", "participants", "wordCount"))
        self._panels["characteristics"].load(char_data if has_char else None, lang)

        for key in ("actionItems", "risks", "quotes", "technologies",
                    "questions", "recommendations", "followupQuestions"):
            lst = analysis.get(key) or []
            self._panels[key].load(lst if lst else None, lang)

        for key in ("sentiment", "category", "formalProtocol"):
            obj = analysis.get(key)
            self._panels[key].load(obj, lang)

    def clear(self) -> None:
        for panel in self._panels.values():
            panel._clear()   # resets _has_data
            panel.setVisible(False)
        self.empty_label.setVisible(True)
