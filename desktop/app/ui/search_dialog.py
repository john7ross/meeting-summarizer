"""Plain-text search across all transcripts in history.

Port of the Electron search panel: a literal/regex grep over every meeting's
raw transcript, with date and speaker filters and highlighted context. This is
NOT semantic search (that's the RAG dialog) — it finds exact words/patterns.

Runs the actual file reads on a background QThread so a large history doesn't
freeze the UI.
"""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QTextBrowser, QVBoxLayout, QWidget,
)

from . import theme
from ..backend.textsearch import (
    highlight_html, passes_date_filter, search_in_text,
)

_L = {
    "ru": {
        "title": "Поиск по транскриптам",
        "query_ph": "Введите запрос…",
        "regex": "Регулярное выражение",
        "case": "С учётом регистра",
        "date": "Период:",
        "date_all": "Все", "date_today": "Сегодня",
        "date_week": "Неделя", "date_month": "Месяц",
        "speaker_ph": "Фильтр по спикеру (необязательно)",
        "search": "Искать",
        "clear": "Очистить",
        "export": "Экспорт результатов",
        "empty": "Введите запрос для поиска.",
        "no_results": "Ничего не найдено.",
        "count": "{m} совпадений в {f} файлах",
        "bad_regex": "Неверное регулярное выражение: {err}",
        "line": "Строка {n}",
        "searching": "Поиск…",
    },
    "en": {
        "title": "Search transcripts",
        "query_ph": "Enter a query…",
        "regex": "Regular expression",
        "case": "Case sensitive",
        "date": "Period:",
        "date_all": "All", "date_today": "Today",
        "date_week": "Week", "date_month": "Month",
        "speaker_ph": "Speaker filter (optional)",
        "search": "Search",
        "clear": "Clear",
        "export": "Export results",
        "empty": "Enter a query to search.",
        "no_results": "Nothing found.",
        "count": "{m} matches in {f} files",
        "bad_regex": "Invalid regular expression: {err}",
        "line": "Line {n}",
        "searching": "Searching…",
    },
}


def _t(key: str, lang: str) -> str:
    return _L.get(lang, _L["en"]).get(key, key)


class _SearchThread(QThread):
    """Reads each transcript and collects matches off the UI thread."""
    finished_results = Signal(object, str)  # results list, error

    def __init__(self, entries, query, use_regex, case_sensitive,
                 date_filter, speaker_filter, parent=None):
        super().__init__(parent)
        self._entries = entries
        self._query = query
        self._use_regex = use_regex
        self._case = case_sensitive
        self._date = date_filter
        self._speaker = speaker_filter

    def run(self) -> None:
        results = []
        try:
            for e in self._entries:
                if not passes_date_filter(e.get("processed_at", ""), self._date):
                    continue
                path = e.get("transcript_path")
                if not path or not Path(path).exists():
                    continue
                try:
                    text = Path(path).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                matches = search_in_text(
                    text, self._query, self._use_regex, self._case, self._speaker)
                if matches:
                    results.append({
                        "file": e.get("video_name", "?"),
                        "date": e.get("processed_at", ""),
                        "video_path": e.get("video_path", ""),
                        "matches": matches,
                    })
        except re.error as exc:
            self.finished_results.emit(None, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.finished_results.emit(None, str(exc))
            return
        self.finished_results.emit(results, "")


class SearchDialog(QDialog):
    def __init__(self, entries, language: str = "ru", parent=None):
        """*entries* is a list of dicts with keys: video_name, video_path,
        processed_at, transcript_path."""
        super().__init__(parent)
        self._lang = language
        self._entries = entries
        self._results = []
        self._thread = None

        self.setWindowTitle(_t("title", language))
        self.setMinimumSize(760, 600)
        self.resize(960, 720)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # Query row
        qrow = QHBoxLayout()
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText(_t("query_ph", self._lang))
        self.query_edit.returnPressed.connect(self._do_search)
        self.btn_search = QPushButton(_t("search", self._lang))
        self.btn_search.setProperty("variant", "primary")
        self.btn_search.clicked.connect(self._do_search)
        qrow.addWidget(self.query_edit, 1)
        qrow.addWidget(self.btn_search)
        root.addLayout(qrow)

        # Options row
        opts = QHBoxLayout()
        self.cb_regex = QCheckBox(_t("regex", self._lang))
        self.cb_case = QCheckBox(_t("case", self._lang))
        opts.addWidget(self.cb_regex)
        opts.addWidget(self.cb_case)
        opts.addWidget(QLabel(_t("date", self._lang)))
        self.cb_date = QComboBox()
        for val, key in (("all", "date_all"), ("today", "date_today"),
                         ("week", "date_week"), ("month", "date_month")):
            self.cb_date.addItem(_t(key, self._lang), val)
        opts.addWidget(self.cb_date)
        self.speaker_edit = QLineEdit()
        self.speaker_edit.setPlaceholderText(_t("speaker_ph", self._lang))
        self.speaker_edit.setMaximumWidth(240)
        theme.fit_placeholder(self.speaker_edit)
        opts.addWidget(self.speaker_edit)
        opts.addStretch(1)
        root.addLayout(opts)

        # Count + actions
        meta = QHBoxLayout()
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("hint")
        meta.addWidget(self.lbl_count)
        meta.addStretch(1)
        self.btn_export = QPushButton(_t("export", self._lang))
        self.btn_export.clicked.connect(self._export)
        self.btn_clear = QPushButton(_t("clear", self._lang))
        self.btn_clear.clicked.connect(self._clear)
        meta.addWidget(self.btn_export)
        meta.addWidget(self.btn_clear)
        root.addLayout(meta)

        # Results
        self.results_view = QTextBrowser()
        self.results_view.setObjectName("searchResults")
        self.results_view.setOpenExternalLinks(False)
        root.addWidget(self.results_view, 1)

    # -- search --------------------------------------------------------
    def _do_search(self) -> None:
        query = self.query_edit.text().strip()
        if not query:
            self.lbl_count.setText(_t("empty", self._lang))
            return
        # Validate regex early for a friendly message.
        if self.cb_regex.isChecked():
            try:
                re.compile(query)
            except re.error as exc:
                self.lbl_count.setText(_t("bad_regex", self._lang).format(err=exc))
                return
        self.btn_search.setEnabled(False)
        self.lbl_count.setText(_t("searching", self._lang))
        self._thread = _SearchThread(
            self._entries, query, self.cb_regex.isChecked(),
            self.cb_case.isChecked(), self.cb_date.currentData(),
            self.speaker_edit.text().strip(), parent=self)
        self._thread.finished_results.connect(self._on_results)
        self._thread.start()

    def _on_results(self, results, error: str) -> None:
        self.btn_search.setEnabled(True)
        if error:
            self.lbl_count.setText(_t("bad_regex", self._lang).format(err=error))
            return
        self._results = results or []
        total = sum(len(r["matches"]) for r in self._results)
        if not self._results:
            self.lbl_count.setText(_t("no_results", self._lang))
            self.results_view.setHtml("")
            return
        self.lbl_count.setText(_t("count", self._lang).format(
            m=total, f=len(self._results)))
        self.results_view.setHtml(self._render_html())

    def _render_html(self) -> str:
        query = self.query_edit.text()
        use_regex = self.cb_regex.isChecked()
        case = self.cb_case.isChecked()
        parts = []
        for r in self._results:
            parts.append(
                f"<div style='margin:10px 0 4px;font-weight:600'>📄 {r['file']}"
                f"<span style='opacity:.6;font-weight:400'>  {r['date']}</span></div>")
            for m in r["matches"]:
                ctx = highlight_html(m["context"], query, use_regex, case)
                ctx = ctx.replace("\n", "<br>")
                parts.append(
                    "<div style='margin:4px 0 8px;padding:6px 10px;"
                    "border-left:2px solid #007acc'>"
                    f"<div style='opacity:.5;font-size:11px'>"
                    f"{_t('line', self._lang).format(n=m['line_number'])}</div>"
                    f"<div style='font-family:Consolas,monospace;font-size:12px'>{ctx}</div>"
                    "</div>")
        return "".join(parts)

    def _clear(self) -> None:
        self.query_edit.clear()
        self.speaker_edit.clear()
        self.cb_regex.setChecked(False)
        self.cb_case.setChecked(False)
        self.cb_date.setCurrentIndex(0)
        self._results = []
        self.results_view.setHtml("")
        self.lbl_count.setText("")

    def _export(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        if not self._results:
            return
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default = f"search_results_{ts}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self, _t("export", self._lang), default, "Text Files (*.txt)")
        if not path:
            return
        query = self.query_edit.text()
        total = sum(len(r["matches"]) for r in self._results)
        lines = [
            f"Search Query: {query}",
            f"Date: {datetime.now().isoformat(timespec='seconds')}",
            f"Total Results: {total}",
            f"Files: {len(self._results)}",
            "", "=" * 80, "",
        ]
        for r in self._results:
            lines.append(f"File: {r['file']}")
            lines.append(f"Date: {r['date']}")
            lines.append(f"Matches: {len(r['matches'])}")
            lines.append("")
            for m in r["matches"]:
                lines.append(f"Line {m['line_number']}:")
                lines.append(m["context"])
                lines.append("-" * 80)
            lines.append("=" * 80)
            lines.append("")
        try:
            Path(path).write_text("\n".join(lines), encoding="utf-8")
            self.lbl_count.setText(f"→ {Path(path).name}")
        except OSError as exc:
            self.lbl_count.setText(str(exc))
