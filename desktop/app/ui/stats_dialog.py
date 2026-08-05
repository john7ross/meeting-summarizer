"""Session / meeting statistics modal (TODO #18).

The Electron app kept an in-memory per-session counter (files processed/succeeded/
failed, avg time). The port instead aggregates the PERSISTENT history (more useful,
survives restarts): totals, how many reached transcript/summary/analysis, breakdown
by status and by project, and total transcribed words. ``aggregate`` is Qt-free so
it is unit-tested; the dialog just renders it.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QTextBrowser, QVBoxLayout

from ..core.models import JobStatus, main_label

_L = {
    "ru": {"title": "Статистика встреч", "refresh": "Обновить", "close": "Закрыть",
           "total": "Всего встреч", "with_tx": "С транскриптом", "with_sum": "С саммари",
           "with_an": "С анализом", "words": "Всего слов (транскрипты)",
           "by_status": "По статусу", "by_project": "По проектам", "none": "Нет данных",
           "no_project": "(без проекта)"},
    "en": {"title": "Meeting statistics", "refresh": "Refresh", "close": "Close",
           "total": "Total meetings", "with_tx": "With transcript", "with_sum": "With summary",
           "with_an": "With analysis", "words": "Total words (transcripts)",
           "by_status": "By status", "by_project": "By project", "none": "No data",
           "no_project": "(no project)"},
}


def aggregate(store) -> dict:
    """Aggregate persistent history into stats. Never raises on a bad entry/file."""
    try:
        entries = list(store.load())
    except Exception:
        entries = []
    total = len(entries)
    with_tx = with_sum = with_an = words = 0
    by_status: dict = {}
    by_project: dict = {}
    for e in entries:
        if getattr(e, "transcript_path", None):
            with_tx += 1
            try:
                p = Path(e.transcript_path)
                if p.exists():
                    words += len(p.read_text(encoding="utf-8", errors="replace").split())
            except OSError:
                pass
        if getattr(e, "summary_versions", None):
            with_sum += 1
        if getattr(e, "analysis_versions", None):
            with_an += 1
        st = getattr(e, "status", "") or "—"
        by_status[st] = by_status.get(st, 0) + 1
        proj = getattr(e, "project", "") or ""
        by_project[proj] = by_project.get(proj, 0) + 1
    return {"total": total, "with_tx": with_tx, "with_sum": with_sum,
            "with_an": with_an, "words": words, "by_status": by_status,
            "by_project": by_project}


class StatsDialog(QDialog):
    def __init__(self, store, language: str = "ru", parent=None):
        super().__init__(parent)
        self._store = store
        self._lang = language
        self.setWindowTitle(self._t("title"))
        self.resize(560, 480)
        v = QVBoxLayout(self)
        self.view = QTextBrowser()
        self.view.setObjectName("statsView")
        v.addWidget(self.view, 1)
        row = QHBoxLayout(); row.addStretch(1)
        b_ref = QPushButton(self._t("refresh")); b_ref.clicked.connect(self._render)
        b_close = QPushButton(self._t("close")); b_close.clicked.connect(self.accept)
        row.addWidget(b_ref); row.addWidget(b_close)
        v.addLayout(row)
        self._render()

    def _t(self, key):
        return _L.get(self._lang, _L["ru"]).get(key, key)

    def _status_label(self, raw: str) -> str:
        """Translate a raw status value (e.g. 'queued') to the UI language."""
        if raw in JobStatus._value2member_map_:
            return main_label(JobStatus(raw), self._lang)
        return raw

    def _render(self):
        s = aggregate(self._store)
        rows = [(self._t("total"), s["total"]), (self._t("with_tx"), s["with_tx"]),
                (self._t("with_sum"), s["with_sum"]), (self._t("with_an"), s["with_an"]),
                (self._t("words"), f"{s['words']:,}".replace(",", " "))]
        main = "".join(f"<tr><td style='padding:2px 14px 2px 0'>{k}</td>"
                       f"<td><b>{val}</b></td></tr>" for k, val in rows)

        def _breakdown(d, label=None):
            if not d:
                return f"<i>{self._t('none')}</i>"
            items = sorted(d.items(), key=lambda kv: kv[1], reverse=True)
            return "<table>" + "".join(
                f"<tr><td style='padding:2px 14px 2px 0'>"
                f"{(label(k) if label else (k or self._t('no_project')))}</td>"
                f"<td>{v}</td></tr>" for k, v in items) + "</table>"

        html = (f"<table>{main}</table>"
                f"<h4 style='margin:14px 0 4px'>{self._t('by_status')}</h4>"
                f"{_breakdown(s['by_status'], label=self._status_label)}"
                f"<h4 style='margin:14px 0 4px'>{self._t('by_project')}</h4>{_breakdown(s['by_project'])}")
        self.view.setHtml(html)
