"""RAG knowledge-base dialog: semantic search over past meetings + management.

Talks to backend/rag.py through RagWorker (QThread + subprocess), so embedding
and chromadb work never blocks the UI. Search is by meaning and can be scoped to
a project. Tabs:

  * Search   — query + optional project filter + top-k; ranked hits with score
  * Library  — list of indexed documents (optionally per project); delete
  * Stats    — counts, projects, active embedding provider/model; clear all

Adding meetings to the KB is done from the main window's "Add to RAG" button,
not here, so this dialog is read/manage only.
"""
from __future__ import annotations

import html
import json
from urllib.parse import quote, unquote

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QSpinBox, QTabWidget, QTextBrowser,
    QVBoxLayout, QWidget,
)

from . import theme
from ..core.worker import RagWorker

_L = {
    "ru": {
        "title": "База знаний (RAG)",
        "tab_search": "Поиск по смыслу",
        "tab_library": "Библиотека",
        "tab_stats": "Статистика",
        "query_ph": "О чём была речь? Поиск по смыслу…",
        "project_ph": "Проект (необязательно)",
        "topk": "Результатов:",
        "search": "Искать",
        "searching": "Поиск по векторной базе…",
        "no_results": "Ничего не найдено.",
        "score": "релевантность",
        "refresh": "Обновить",
        "delete": "Удалить",
        "del_confirm_t": "Удаление",
        "del_confirm": "Удалить «{title}» из базы знаний?",
        "clear": "Очистить базу",
        "clear_confirm_t": "Очистка базы",
        "clear_confirm": "Удалить ВСЕ документы из базы знаний? Это нельзя отменить.",
        "rebuild": "Переиндексировать",
        "rebuild_confirm_t": "Переиндексация базы",
        "rebuild_confirm": "Заново пересчитать эмбеддинги для всех встреч текущей моделью? Нужно после смены модели эмбеддингов. Может занять время.",
        "rebuilding": "Переиндексация…",
        "rebuilt": "Переиндексировано: {n}, пропущено: {s}",
        "empty_lib": "База знаний пуста.",
        "stat_docs": "Документов",
        "stat_chunks": "Фрагментов (векторов)",
        "stat_provider": "Провайдер эмбеддингов",
        "stat_model": "Модель",
        "stat_dim": "Размерность",
        "stat_projects": "Проекты",
        "loading": "Загрузка…",
        "error": "Ошибка: {err}",
        "no_query": "Введите запрос.",
        "from": "из проекта",
    },
    "en": {
        "title": "Knowledge base (RAG)",
        "tab_search": "Semantic search",
        "tab_library": "Library",
        "tab_stats": "Statistics",
        "query_ph": "What was discussed? Search by meaning…",
        "project_ph": "Project (optional)",
        "topk": "Results:",
        "search": "Search",
        "searching": "Searching the vector store…",
        "no_results": "Nothing found.",
        "score": "relevance",
        "refresh": "Refresh",
        "delete": "Delete",
        "del_confirm_t": "Delete",
        "del_confirm": "Remove “{title}” from the knowledge base?",
        "clear": "Clear database",
        "clear_confirm_t": "Clear database",
        "clear_confirm": "Delete ALL documents from the knowledge base? This cannot be undone.",
        "rebuild": "Re-index",
        "rebuild_confirm_t": "Rebuild the knowledge base",
        "rebuild_confirm": "Re-embed every meeting with the current model? Needed after changing the embedding model. May take a while.",
        "rebuilding": "Re-indexing…",
        "rebuilt": "Re-indexed: {n}, skipped: {s}",
        "empty_lib": "The knowledge base is empty.",
        "stat_docs": "Documents",
        "stat_chunks": "Chunks (vectors)",
        "stat_provider": "Embedding provider",
        "stat_model": "Model",
        "stat_dim": "Dimension",
        "stat_projects": "Projects",
        "loading": "Loading…",
        "error": "Error: {err}",
        "no_query": "Enter a query.",
        "from": "in project",
    },
}


def _t(key: str, lang: str) -> str:
    return _L.get(lang, _L["en"]).get(key, key)


class RagDialog(QDialog):
    def __init__(self, rag_dir: str, python_exe: str, rag_script: str,
                 settings: dict, language: str = "ru", parent=None,
                 history_file: str = ""):
        super().__init__(parent)
        self._rag_dir = str(rag_dir)
        self._python = str(python_exe)
        self._script = str(rag_script)
        self._history_file = str(history_file)
        self._settings_json = json.dumps(settings or {})
        self._lang = language
        self._workers = []  # keep refs so QThreads aren't GC'd mid-run

        self.setWindowTitle(_t("title", language))
        self.setMinimumSize(720, 560)
        self.resize(900, 680)
        self._build_ui()
        # Initial loads
        self._refresh_library()
        self._refresh_stats()

    # -- worker plumbing ----------------------------------------------
    def _run(self, op: str, args: list, on_done) -> None:
        cmd = [self._python, self._script, op, "--rag-dir", self._rag_dir]
        cmd += args
        # search/add need settings (embedding provider); list/stats/delete/clear don't
        if op in ("search", "add", "rebuild"):
            cmd += ["--settings", self._settings_json]
        worker = RagWorker(op, cmd, parent=self)
        worker.done.connect(on_done)
        worker.done.connect(lambda *a, w=worker: self._drop_worker(w))
        self._workers.append(worker)
        worker.start()

    def _drop_worker(self, worker) -> None:
        try:
            self._workers.remove(worker)
        except ValueError:
            pass

    # -- ui ------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        self.tabs.addTab(self._build_search_tab(), _t("tab_search", self._lang))
        self.tabs.addTab(self._build_library_tab(), _t("tab_library", self._lang))
        self.tabs.addTab(self._build_stats_tab(), _t("tab_stats", self._lang))

    def _build_search_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        qrow = QHBoxLayout()
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText(_t("query_ph", self._lang))
        self.query_edit.returnPressed.connect(self._do_search)
        self.btn_search = QPushButton(_t("search", self._lang))
        self.btn_search.setProperty("variant", "primary")
        self.btn_search.clicked.connect(self._do_search)
        qrow.addWidget(self.query_edit, 1)
        qrow.addWidget(self.btn_search)
        v.addLayout(qrow)

        opts = QHBoxLayout()
        self.search_project = QLineEdit()
        self.search_project.setPlaceholderText(_t("project_ph", self._lang))
        self.search_project.setMaximumWidth(260)
        theme.fit_placeholder(self.search_project)
        opts.addWidget(self.search_project)
        opts.addWidget(QLabel(_t("topk", self._lang)))
        self.topk = QSpinBox()
        self.topk.setRange(1, 50)
        self.topk.setValue(5)
        opts.addWidget(self.topk)
        opts.addStretch(1)
        v.addLayout(opts)

        self.search_status = QLabel("")
        self.search_status.setObjectName("hint")
        v.addWidget(self.search_status)
        self.search_results = QTextBrowser()
        self.search_results.setObjectName("ragResults")
        v.addWidget(self.search_results, 1)
        return w

    def _build_library_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        top = QHBoxLayout()
        self.lib_project = QLineEdit()
        self.lib_project.setPlaceholderText(_t("project_ph", self._lang))
        self.lib_project.setMaximumWidth(260)
        theme.fit_placeholder(self.lib_project)
        self.lib_project.returnPressed.connect(self._refresh_library)
        btn_refresh = QPushButton(_t("refresh", self._lang))
        btn_refresh.clicked.connect(self._refresh_library)
        top.addWidget(self.lib_project)
        top.addWidget(btn_refresh)
        top.addStretch(1)
        v.addLayout(top)
        self.lib_status = QLabel("")
        self.lib_status.setObjectName("hint")
        v.addWidget(self.lib_status)
        self.lib_view = QTextBrowser()
        self.lib_view.setObjectName("ragLibrary")
        # anchor clicks -> delete:docid
        self.lib_view.setOpenLinks(False)
        self.lib_view.anchorClicked.connect(self._on_lib_anchor)
        v.addWidget(self.lib_view, 1)
        return w

    def _build_stats_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.stats_view = QTextBrowser()
        self.stats_view.setObjectName("ragStats")
        v.addWidget(self.stats_view, 1)
        btnrow = QHBoxLayout()
        btnrow.addStretch(1)
        self.btn_rebuild = QPushButton(_t("rebuild", self._lang))
        self.btn_rebuild.clicked.connect(self._do_rebuild)
        btnrow.addWidget(self.btn_rebuild)
        self.btn_clear = QPushButton(_t("clear", self._lang))
        self.btn_clear.clicked.connect(self._do_clear)
        btnrow.addWidget(self.btn_clear)
        v.addLayout(btnrow)
        return w

    # -- search --------------------------------------------------------
    def _do_search(self) -> None:
        query = self.query_edit.text().strip()
        if not query:
            self.search_status.setText(_t("no_query", self._lang))
            return
        self.btn_search.setEnabled(False)
        self.search_status.setText(_t("searching", self._lang))
        args = ["--query", query, "--top-k", str(self.topk.value())]
        proj = self.search_project.text().strip()
        if proj:
            args += ["--project", proj]
        self._run("search", args, self._on_search_done)

    def _on_search_done(self, op, ok, data, error) -> None:
        self.btn_search.setEnabled(True)
        if not ok:
            self.search_status.setText(_t("error", self._lang).format(err=error))
            return
        results = (data or {}).get("results", [])
        if not results:
            self.search_status.setText(_t("no_results", self._lang))
            self.search_results.setHtml("")
            return
        self.search_status.setText(f"{len(results)}")
        parts = []
        for r in results:
            score_pct = int(round(float(r.get("score", 0)) * 100))
            proj = html.escape(str(r.get("project", "") or ""))
            proj_html = (f"<span style='opacity:.6'>· {_t('from', self._lang)} "
                         f"{proj}</span>" if proj else "")
            title = html.escape(str(r.get("title") or r.get("doc_id", "?")))
            date = html.escape(str(r.get("date", "") or ""))
            text = html.escape(str(r.get("text", "") or "")).replace("\n", "<br>")
            parts.append(
                f"<div style='margin:10px 0 4px;font-weight:600'>📄 {title}"
                f"<span style='opacity:.6;font-weight:400'>  {date}</span> "
                f"{proj_html}</div>"
                f"<div style='opacity:.5;font-size:11px'>"
                f"{_t('score', self._lang)}: {score_pct}%</div>"
                f"<div style='margin:4px 0 10px;padding:6px 10px;"
                f"border-left:2px solid #007acc;font-size:12px'>{text}</div>")
        self.search_results.setHtml("".join(parts))

    # -- library -------------------------------------------------------
    def _refresh_library(self) -> None:
        self.lib_status.setText(_t("loading", self._lang))
        args = []
        proj = self.lib_project.text().strip()
        if proj:
            args += ["--project", proj]
        self._run("list", args, self._on_library_done)

    def _on_library_done(self, op, ok, data, error) -> None:
        if not ok:
            self.lib_status.setText(_t("error", self._lang).format(err=error))
            return
        docs = (data or {}).get("documents", [])
        if not docs:
            self.lib_status.setText(_t("empty_lib", self._lang))
            self.lib_view.setHtml("")
            return
        self.lib_status.setText(f"{len(docs)}")
        parts = []
        for d in docs:
            did = str(d.get("doc_id", "") or "")
            title = html.escape(str(d.get("title") or did))
            proj = html.escape(str(d.get("project", "") or ""))
            date = html.escape(str(d.get("date", "") or ""))
            proj_html = (f"<span style='opacity:.6'>· {proj}</span>"
                         if proj else "")
            parts.append(
                "<div style='margin:8px 0;padding:6px 10px;"
                "border-bottom:1px solid rgba(128,128,128,.2)'>"
                f"<div style='font-weight:600'>📄 {title} {proj_html}</div>"
                f"<div style='opacity:.5;font-size:11px'>{date} · "
                f"{d.get('chunks',0)} chunks · "
                f"<a href='delete:{quote(did, safe='')}' style='color:#e06c75'>"
                f"{_t('delete', self._lang)}</a></div></div>")
        self.lib_view.setHtml("".join(parts))

    def _on_lib_anchor(self, url) -> None:
        s = url.toString()
        if not s.startswith("delete:"):
            return
        doc_id = unquote(s[len("delete:"):])
        title = doc_id
        if QMessageBox.question(
                self, _t("del_confirm_t", self._lang),
                _t("del_confirm", self._lang).format(title=title),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._run("delete", ["--doc-id", doc_id], self._on_delete_done)

    def _on_delete_done(self, op, ok, data, error) -> None:
        if not ok:
            self.lib_status.setText(_t("error", self._lang).format(err=error))
            return
        self._refresh_library()
        self._refresh_stats()

    # -- stats ---------------------------------------------------------
    def _refresh_stats(self) -> None:
        self.stats_view.setHtml(f"<p>{_t('loading', self._lang)}</p>")
        self._run("stats", [], self._on_stats_done)

    def _on_stats_done(self, op, ok, data, error) -> None:
        if not ok:
            self.stats_view.setHtml(
                f"<p>{_t('error', self._lang).format(err=error)}</p>")
            return
        d = data or {}
        projects = d.get("projects", {}) or {}
        proj_rows = "".join(
            f"<tr><td style='padding:2px 12px 2px 0'>{p or '—'}</td>"
            f"<td>{c}</td></tr>" for p, c in projects.items())
        rows = [
            (_t("stat_docs", self._lang), d.get("documents", 0)),
            (_t("stat_chunks", self._lang), d.get("chunks", 0)),
            (_t("stat_provider", self._lang), d.get("provider", "") or "—"),
            (_t("stat_model", self._lang), d.get("model", "") or "—"),
            (_t("stat_dim", self._lang), d.get("dimension", 0) or "—"),
        ]
        main_rows = "".join(
            f"<tr><td style='padding:3px 16px 3px 0;font-weight:600'>{k}</td>"
            f"<td>{v}</td></tr>" for k, v in rows)
        html = (f"<table>{main_rows}</table>"
                f"<h4 style='margin:14px 0 4px'>{_t('stat_projects', self._lang)}</h4>"
                f"<table>{proj_rows or '<tr><td>—</td></tr>'}</table>")
        self.stats_view.setHtml(html)

    def _do_clear(self) -> None:
        if QMessageBox.question(
                self, _t("clear_confirm_t", self._lang),
                _t("clear_confirm", self._lang),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._run("clear", [], self._on_clear_done)

    def _on_clear_done(self, op, ok, data, error) -> None:
        if not ok:
            self.stats_view.setHtml(
                f"<p>{_t('error', self._lang).format(err=error)}</p>")
            return
        self._refresh_library()
        self._refresh_stats()
        self.search_results.setHtml("")

    def _do_rebuild(self) -> None:
        if QMessageBox.question(
                self, _t("rebuild_confirm_t", self._lang),
                _t("rebuild_confirm", self._lang),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.btn_rebuild.setEnabled(False)
        self.stats_view.setHtml(f"<p>{_t('rebuilding', self._lang)}</p>")
        self._run("rebuild", ["--history-file", self._history_file], self._on_rebuild_done)

    def _on_rebuild_done(self, op, ok, data, error) -> None:
        self.btn_rebuild.setEnabled(True)
        if not ok:
            self.stats_view.setHtml(
                f"<p>{_t('error', self._lang).format(err=error)}</p>")
            return
        n = (data or {}).get("rebuilt", 0)
        skipped = (data or {}).get("skipped", []) or []
        # brief flash of the outcome, then refresh the real stats
        self.stats_view.setHtml(
            f"<p>{_t('rebuilt', self._lang).format(n=n, s=len(skipped))}</p>")
        self._refresh_library()
        self._refresh_stats()
