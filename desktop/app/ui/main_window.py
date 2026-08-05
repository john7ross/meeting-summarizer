"""Main application window: header toolbar + scrollable sections, reproducing
the Electron layout (Upload, Queue, Status, Results). It binds to the proven
PipelineQueue signals and the id-keyed HistoryStore; every per-row status comes
from one authoritative source, routed by id.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QProgressDialog,
    QPushButton, QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem, QToolBar, QVBoxLayout, QWidget,
)

from . import theme
from .. import config
from ..backend import exporter
from ..backend.speakers import extract_speakers, export_by_speaker
from ..core.history import HistoryStore
from ..core.models import JobStatus, main_label
from ..core.worker import DeviceWorker, ExportWorker, ModelsWorker, ObsidianWorker, RagWorker
from ..core.queue_manager import resolve_workers
from .. import paths
from .analysis_widget import AnalysisWidget
from .rag_dialog import RagDialog
from .search_dialog import SearchDialog
from .settings_dialog import SettingsDialog
from .speakers_dialog import SpeakersDialog
from .theme import build_stylesheet

VIDEO_FILTER = "Video files (*.mp4 *.avi *.mov *.mkv *.webm);;All files (*)"

LABELS = {
    "ru": {
        "title": "Meeting Summarizer", "drop": "Перетащите видеофайлы сюда",
        "select": "Выбрать видеофайлы", "hint": "Поддержка: MP4, AVI, MOV, MKV, WebM",
        "add_url": "Добавить по ссылке", "url_ph": "https://… (YouTube или ссылка на видео)",
        "url_bad": "Введите корректную ссылку http:// или https://",
        "downloading": "Загрузка видео…", "dl_failed": "Не удалось скачать видео",
        "cutting_title": "Нарезка фрагментов",
        "cutting": "Вырезаю фрагмент…",
        "cut_failed": "Не удалось вырезать фрагмент: {err}\nНичего не поставлено в очередь.",
        "device_probing": "⏳ Проверка GPU…", "device_gpu": "GPU (CUDA)",
        "device_cpu": "CPU", "device_cpu_tip": "GPU не найден — используется CPU",
        "queue": "Очередь обработки", "process": "Обработать", "cancel": "Отменить",
        "remove": "Удалить из очереди",
        "remove_tip": "Убрать выделенные встречи из списка навсегда — файлы результатов "
                      "останутся на диске (обрабатываемый сейчас файл "
                      "нужно сначала отменить). Клавиша Delete.",
        "remove_running": "Файл в обработке — сначала нажмите «Отменить».",
        "clear_queue": "Очистить очередь",
        "clear_tip": "Убрать из списка все встречи разом, навсегда — файлы "
                     "результатов останутся на диске (обрабатываемые сейчас "
                     "останутся в списке, их сначала нужно отменить).",
        "clear_kept": "Очередь очищена; в работе осталось: {n}.",
        "hist_processed": "🕘 Обработано: {v}",
        "hist_summary": "✔ Саммари v{n} · {p}",
        "hist_analysis": "✔ Анализ v{n} · {p}",
        "hist_empty": "Для этой встречи нет сохранённых этапов обработки.",
        "status": "Статус", "idle": "Готово", "elapsed": "· прошло {t}",
        "tip_record": "Запись с микрофона", "tip_diag": "Диагностика", "tip_stats": "Статистика", "tip_settings": "Настройки",
        "tip_theme": "Сменить тему", "tip_lang": "Сменить язык",
        "raw": "Транскрипция", "summary": "Саммари",
        "col_id": "ID", "col_file": "Файл", "col_status": "Статус",
        "col_progress": "Прогресс", "col_details": "Детали",
        "workers": "Параллельно: {n}{mode}",
        "workers_auto_gpu": " (авто, 1 GPU)",
        "export": "Экспорт:", "exp_raw": "Транскрипция", "exp_summary": "Саммари",
        "exp_analysis": "Анализ", "do_export": "Экспортировать",
        "exporting": "Экспорт…", "exported": "Сохранено: {name}",
        "export_err": "Ошибка экспорта: {err}", "no_result": "Нет данных для экспорта",
        "obsidian": "→ Obsidian", "obs_no_vault": "Не задан путь к Obsidian vault",
        "obs_done": "В Obsidian: {name}", "obs_err": "Obsidian: {err}",
        "analysis": "Анализ встречи",
        "speakers": "👥 Спикеры",
        "export_speakers": "📤 По спикерам",
        "exp_spk_done": "Экспортировано файлов по спикерам: {n}",
        "transcript_save_err": "Не удалось сохранить транскрипцию: {err}",
        "regenerate": "🔄 Перегенерировать",
        "regen_tip": "Отредактируйте транскрипт слева (он редактируемый) и нажмите — "
                     "приложение сохранит правки и создаст НОВУЮ версию саммари и анализа "
                     "по исправленному тексту.",
        "regen_confirm_title": "Перегенерация",
        "regen_confirm": "Создать новую версию саммари и анализа из текущего транскрипта?",
        "regen_running": "Перегенерация…",
        "regen_done": "Перегенерация завершена",
        "regen_failed": "Перегенерация завершена с ошибкой",
        "regen_no_transcript": "Нет транскрипта для перегенерации",
        "ver_summary": "Версия саммари",
        "ver_analysis": "Версия анализа",
        "ver_of": "из",
        "ver_from_summary": "← из саммари v{n}",
        "ver_prov": "{prov}",
        "project": "Проект:",
        "project_ph": "ID проекта для группировки и RAG",
        "add_to_rag": "➕ В базу знаний",
        "rag": "🧠 База знаний",
        "search_btn": "🔎 Поиск",
        "rag_adding": "Добавление в базу знаний…",
        "rag_added": "Добавлено в базу знаний: {n} фрагментов",
        "rag_add_err": "Ошибка RAG: {err}",
        "rag_need_summary": "Нет саммари для добавления в базу знаний",
        "model_missing_title": "Модель транскрибации не найдена",
        "model_missing_msg": "Выбранная модель «{model}» для движка «{engine}» не скачана.\n\nОткрыть настройки, чтобы скачать её?",
    },
    "en": {
        "title": "Meeting Summarizer", "drop": "Drag & drop video files here",
        "select": "Select video files", "hint": "Supports: MP4, AVI, MOV, MKV, WebM",
        "add_url": "Add by URL", "url_ph": "https://… (YouTube or a video link)",
        "url_bad": "Enter a valid http:// or https:// link",
        "downloading": "Downloading video…", "dl_failed": "Failed to download the video",
        "cutting_title": "Cutting segments",
        "cutting": "Cutting segment…",
        "cut_failed": "Could not cut the segment: {err}\nNothing was queued.",
        "device_probing": "⏳ Checking GPU…", "device_gpu": "GPU (CUDA)",
        "device_cpu": "CPU", "device_cpu_tip": "No GPU found — using CPU",
        "queue": "Processing Queue", "process": "Process", "cancel": "Cancel",
        "remove": "Remove from queue",
        "remove_tip": "Remove the selected meetings from the list for good - produced "
                      "files stay on disk (a file being processed "
                      "must be cancelled first). Delete key.",
        "remove_running": "That file is being processed — press Cancel first.",
        "clear_queue": "Clear queue",
        "clear_tip": "Remove every meeting from the list at once, for good - "
                     "produced files stay on disk (anything being processed "
                     "stays in the list, cancel it first).",
        "clear_kept": "Queue cleared; still running: {n}.",
        "hist_processed": "🕘 Processed: {v}",
        "hist_summary": "✔ Summary v{n} · {p}",
        "hist_analysis": "✔ Analysis v{n} · {p}",
        "hist_empty": "No processing stages were recorded for this meeting.",
        "status": "Status", "idle": "Ready", "elapsed": "· elapsed {t}",
        "tip_record": "Record from microphone", "tip_diag": "Diagnostics", "tip_stats": "Statistics", "tip_settings": "Settings",
        "tip_theme": "Toggle theme", "tip_lang": "Switch language",
        "raw": "Raw Transcript", "summary": "Summary",
        "col_id": "ID", "col_file": "File", "col_status": "Status",
        "col_progress": "Progress", "col_details": "Details",
        "workers": "Parallel: {n}{mode}",
        "workers_auto_gpu": " (auto, 1 GPU)",
        "export": "Export:", "exp_raw": "Transcript", "exp_summary": "Summary",
        "exp_analysis": "Analysis", "do_export": "Export As…",
        "exporting": "Exporting…", "exported": "Saved: {name}",
        "export_err": "Export failed: {err}", "no_result": "No data to export",
        "obsidian": "→ Obsidian", "obs_no_vault": "Obsidian vault path not set",
        "obs_done": "To Obsidian: {name}", "obs_err": "Obsidian: {err}",
        "analysis": "Meeting Analysis",
        "speakers": "👥 Speakers",
        "export_speakers": "📤 By speaker",
        "exp_spk_done": "Exported speaker files: {n}",
        "transcript_save_err": "Could not save transcript: {err}",
        "regenerate": "🔄 Regenerate",
        "regen_tip": "Edit the transcript on the left (it is editable) and click — the app "
                     "saves your edits and creates a NEW summary and analysis version from "
                     "the corrected text.",
        "regen_confirm_title": "Regenerate",
        "regen_confirm": "Create a new summary and analysis version from the current transcript?",
        "regen_running": "Regenerating…",
        "regen_done": "Regeneration complete",
        "regen_failed": "Regeneration completed with an error",
        "regen_no_transcript": "No transcript to regenerate from",
        "ver_summary": "Summary version",
        "ver_analysis": "Analysis version",
        "ver_of": "of",
        "ver_from_summary": "← from summary v{n}",
        "ver_prov": "{prov}",
        "project": "Project:",
        "project_ph": "Project id for grouping and RAG",
        "add_to_rag": "➕ Add to KB",
        "rag": "🧠 Knowledge base",
        "search_btn": "🔎 Search",
        "rag_adding": "Adding to knowledge base…",
        "rag_added": "Added to knowledge base: {n} chunks",
        "rag_add_err": "RAG error: {err}",
        "rag_need_summary": "No summary to add to the knowledge base",
        "model_missing_title": "Transcription model not found",
        "model_missing_msg": "The selected model \"{model}\" for engine \"{engine}\" is not downloaded.\n\nOpen settings to download it?",
    },
}


# Backend transcription emits English progress details; translate the common
# fragments so the RU UI stays fully Russian.
_TX_TRANSLATE_RU = [
    # Multi-word phrases first (single-pass, first-occurrence replace per entry).
    ("no local model found, will download from huggingface",
     "локальная модель не найдена, скачаю с HuggingFace"),
    ("found local model at", "найдена локальная модель:"),
    ("extracting audio from video", "Извлечение аудио из видео"),
    ("splitting audio into chunks", "Разделение аудио на фрагменты"),
    ("split into", "Разделено на"),
    ("starting transcription", "начинаю транскрибацию"),
    ("transcribing chunk", "Транскрибация фрагмента"),
    ("processing chunk", "Обработка фрагмента"),
    ("extracting audio", "Извлечение аудио"),
    ("loading model", "Загрузка модели"),
    ("detecting language", "Определение языка"),
    ("converting audio", "Конвертация аудио"),
    ("diarizing", "Диаризация (спикеры)"),
    ("aligning", "Выравнивание"),
    ("transcribing", "Транскрибация"),
    ("extracting", "Извлечение"),
    ("downloading", "Загрузка"),
    ("preparing", "Подготовка"),
    ("finalizing", "Завершение"),
    ("merging", "Объединение"),
    ("complete", "Завершено"),
    ("done in", "готово за"),
    ("chunks", "фрагментов"),
    ("chunk", "фрагмент"),
    ("segment", "сегмент"),
]


# Span names as the backend tracer writes them -> what the user should read.
_TRACE_STAGE_NAMES = {
    "ru": {
        "video_processing": "Обработка",
        "extract_audio": "Извлечение аудио",
        "transcribe": "Транскрибация",
        "transcribe_whisper": "Транскрибация (Whisper)",
        "transcribe_faster_whisper": "Транскрибация (Faster-Whisper)",
        "transcribe_whisperx": "Транскрибация (WhisperX)",
        "transcribe_vosk": "Транскрибация (Vosk)",
        "transcribe_sherpa": "Транскрибация (sherpa-onnx)",
        "transcribe_whisper_cpp": "Транскрибация (whisper.cpp)",
        "transcribe_funasr": "Транскрибация (FunASR)",
        "diarization": "Разделение по спикерам",
        "summarize": "Саммари",
        "analysis": "Анализ",
    },
    "en": {
        "video_processing": "Processing",
        "extract_audio": "Audio extraction",
        "transcribe": "Transcription",
        "transcribe_whisper": "Transcription (Whisper)",
        "transcribe_faster_whisper": "Transcription (Faster-Whisper)",
        "transcribe_whisperx": "Transcription (WhisperX)",
        "transcribe_vosk": "Transcription (Vosk)",
        "transcribe_sherpa": "Transcription (sherpa-onnx)",
        "transcribe_whisper_cpp": "Transcription (whisper.cpp)",
        "transcribe_funasr": "Transcription (FunASR)",
        "diarization": "Speaker diarisation",
        "summarize": "Summary",
        "analysis": "Analysis",
    },
}


def _tr_detail(detail: str, lang: str) -> str:
    """Backend progress details arrive in English; map the known fragments to
    Russian (case-insensitively) so the RU UI never shows a mixed string."""
    if lang != "ru" or not detail:
        return detail
    low = detail.lower()
    for en, ru in _TX_TRANSLATE_RU:
        idx = low.find(en)
        if idx >= 0:
            detail = detail[:idx] + ru + detail[idx + len(en):]
            low = detail.lower()
    return detail


# One implementation, shared with the exporters (see media.duration_from_transcript):
# a private copy here is how the analysis panel ended up showing a real length
# while the Google Sheets row for the same meeting said "N/A".
from ..backend.media import duration_from_transcript as _duration_from_transcript


def _analysis_meta_from(transcript, analysis, entry_duration: str = "") -> dict:
    """Characteristics shown in the analysis panel — mirrors the export: meeting
    length, participants and word count (derived when not stored)."""
    meta: dict = {}
    tx = transcript or ""
    if tx:
        meta["wordCount"] = len(tx.split())
        meta["duration"] = entry_duration or _duration_from_transcript(tx)
    elif entry_duration:
        meta["duration"] = entry_duration
    parts = _participants_from_analysis(analysis)
    if parts:
        meta["participants"] = parts
    return meta


def _participants_from_analysis(analysis) -> str:
    """Derive a participant list from analysis data — the formal protocol's
    participants, else the speakers in the dominance distribution."""
    if not isinstance(analysis, dict):
        return ""
    fp = analysis.get("formalProtocol") or {}
    if isinstance(fp, dict) and fp.get("participants"):
        return ", ".join(str(x) for x in fp["participants"])
    dom = (analysis.get("sentiment") or {}).get("dominanceDistribution") or {}
    if isinstance(dom, dict) and dom:
        return ", ".join(str(k) for k in dom.keys())
    return ""


def _icon_button(glyph: str, tooltip: str) -> QPushButton:
    btn = QPushButton(glyph)
    btn.setProperty("variant", "icon")
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.PointingHandCursor)
    return btn


class MainWindow(QMainWindow):
    COL_ID, COL_FILE, COL_STATUS, COL_PROGRESS, COL_DETAILS = range(5)

    def __init__(self, settings: dict, store: HistoryStore, queue=None,
                 language: str = "ru", theme: str = "dark",
                 persist_ui_preferences: bool = False):
        super().__init__()
        self.settings = settings
        self.store = store
        self.queue = queue
        self.language = language if language in LABELS else "ru"
        self.theme = theme
        self._persist_ui_preferences = persist_ui_preferences
        self._rows: dict[int, int] = {}        # job_id -> table row index
        self._bars: dict[int, QProgressBar] = {}
        # Rows restored from persistent history are browseable, but must never
        # be reprocessed in bulk merely because the user presses "Process".
        # A restored row becomes startable only when explicitly selected.
        self._restored_ids: set[int] = set()
        self._pending: list[tuple[int, str]] = []  # (id, path) not yet enqueued
        self._current_result_id: Optional[int] = None
        self._current_analysis: Optional[dict] = None
        self._current_transcript: str = ""
        # Selected version indices (0-based) for the loaded result; -1 = none.
        self._sel_summary_idx: int = -1
        self._sel_analysis_idx: int = -1
        self._ew: Optional[ExportWorker] = None
        self._ow: Optional[ObsidianWorker] = None
        self._dw: Optional[DeviceWorker] = None
        self._device_cuda: Optional[bool] = None   # None => still probing
        self._device_name: str = ""

        self.setWindowTitle(self._t("title"))
        self.resize(1200, 860)
        self.setAcceptDrops(True)
        _icon = paths.ROOT / "resources" / "icon.png"
        if _icon.exists():
            _ic = QIcon(str(_icon))
            self.setWindowIcon(_ic)
            _app = QApplication.instance()
            if _app is not None:
                _app.setWindowIcon(_ic)
        # Live elapsed clock for the active job (shown on the status line) and the
        # stage timeline (completed stages + the one currently running).
        self._run_t0: Optional[float] = None
        # Per-file stage timeline: each job keeps its own list of finished stages
        # (with the time each took); the status panel shows the active job's list.
        self._stages_by_job: dict = {}
        # Live status belongs to a job, never to the window as a whole.  Keeping
        # it keyed by id is what lets the user inspect job B while job A emits
        # progress without A stealing the status panel back.
        self._live_by_job: dict[int, dict] = {}
        self._active_job = None
        self._cur_stage_label: str = ""
        self._cur_stage_t0: Optional[float] = None
        self._cur_detail: str = ""            # latest fine step (feeds the live timeline)
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._build_header()
        self._build_body()
        self._connect_queue()
        self._restore_history_rows()
        self.apply_theme(theme)
        self.retranslate()
        # Once the window is up, warn (non-blocking) if the configured model
        # isn't on disk, offering to open Settings to download it.
        QTimer.singleShot(0, self._check_configured_model)
        # Detect CUDA off the UI thread (torch import is slow) and update the
        # header indicator + pipeline concurrency when it returns.
        QTimer.singleShot(0, self._start_device_probe)

    # -- i18n ----------------------------------------------------------
    def _t(self, key: str) -> str:
        return LABELS[self.language].get(key, key)

    # -- header --------------------------------------------------------
    def _build_header(self) -> None:
        bar = QToolBar()
        bar.setMovable(False)
        self.addToolBar(bar)
        title = QLabel(self._t("title"))
        title.setObjectName("appTitle")
        bar.addWidget(title)
        spacer = QWidget()
        spacer.setObjectName("toolbarSpacer")
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(spacer)
        self.lbl_device = QLabel(self._t("device_probing"))
        self.lbl_device.setObjectName("deviceIndicator")
        bar.addWidget(self.lbl_device)
        self.btn_record = _icon_button("🎙", self._t("tip_record"))
        self.btn_record.clicked.connect(self._open_recorder)
        bar.addWidget(self.btn_record)
        self.btn_diag = _icon_button("📊", self._t("tip_diag"))
        self.btn_diag.clicked.connect(self._open_diagnostics)
        bar.addWidget(self.btn_diag)
        self.btn_stats = _icon_button("📈", self._t("tip_stats"))
        self.btn_stats.clicked.connect(self._open_stats)
        bar.addWidget(self.btn_stats)
        self.btn_settings = _icon_button("⚙", self._t("tip_settings"))
        self.btn_theme = _icon_button("🌓", self._t("tip_theme"))
        self.btn_lang = _icon_button("🌐", self._t("tip_lang"))
        self.btn_settings.clicked.connect(self._open_settings)
        self.btn_theme.clicked.connect(self.toggle_theme)
        self.btn_lang.clicked.connect(self.toggle_language)
        bar.addWidget(self.btn_settings)
        bar.addWidget(self.btn_theme)
        bar.addWidget(self.btn_lang)

    # -- device (CUDA) indicator --------------------------------------
    def _start_device_probe(self) -> None:
        self._dw = DeviceWorker(paths.python_executable(), parent=self)
        self._dw.detected.connect(self._on_device_detected)
        self._dw.start()

    def _on_device_detected(self, cuda: bool, name: str) -> None:
        self._device_cuda = bool(cuda)
        self._device_name = name or ""
        self._render_device_label()
        # A single GPU is VRAM-bound: recompute the "auto" worker count now that
        # CUDA is known and apply it to the running queue.
        if self.queue is not None:
            workers = resolve_workers(self.settings.get("parallelWorkers", "auto"), cuda=self._device_cuda)
            self.queue.set_max_concurrency(workers)
            self._refresh_workers_label()

    def _render_device_label(self) -> None:
        if not hasattr(self, "lbl_device"):
            return
        if self._device_cuda is None:
            self.lbl_device.setText(self._t("device_probing"))
            self.lbl_device.setToolTip("")
            return
        if self._device_cuda:
            self.lbl_device.setText("🟢 " + self._t("device_gpu"))
            self.lbl_device.setToolTip(self._device_name or "CUDA")
        else:
            self.lbl_device.setText("⚪ " + self._t("device_cpu"))
            self.lbl_device.setToolTip(self._t("device_cpu_tip"))

    # -- body ----------------------------------------------------------
    def _section(self, title_key: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("section")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)
        heading = QLabel(self._t(title_key))
        heading.setObjectName("sectionTitle")
        heading.setProperty("titleKey", title_key)
        layout.addWidget(heading)
        return frame, layout

    def _build_body(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        container.setObjectName("bodyContainer")
        root = QVBoxLayout(container)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(20)

        # Upload
        up_frame = QFrame()
        up_frame.setObjectName("dropZone")
        up = QVBoxLayout(up_frame)
        up.setContentsMargins(24, 28, 24, 28)
        up.setAlignment(Qt.AlignCenter)
        self.lbl_drop = QLabel("📁  " + self._t("drop"))
        self.lbl_drop.setAlignment(Qt.AlignCenter)
        self.btn_select = QPushButton(self._t("select"))
        self.btn_select.setProperty("variant", "primary")
        self.btn_select.clicked.connect(self._select_files)
        self.lbl_hint = QLabel(self._t("hint"))
        self.lbl_hint.setObjectName("hint")
        self.lbl_hint.setAlignment(Qt.AlignCenter)
        up.addWidget(self.lbl_drop)
        up.addWidget(self.btn_select, alignment=Qt.AlignCenter)
        up.addWidget(self.lbl_hint)
        # Or add a video by URL (YouTube / file server) — downloaded, then processed
        # exactly like a selected file.
        url_row = QHBoxLayout()
        self.ed_url = QLineEdit()
        self.ed_url.setPlaceholderText(self._t("url_ph"))
        self.btn_url = QPushButton(self._t("add_url"))
        self.btn_url.clicked.connect(self._add_url)
        url_row.addWidget(self.ed_url, 1)
        url_row.addWidget(self.btn_url)
        up.addLayout(url_row)
        root.addWidget(up_frame)

        # Queue
        q_frame, q_layout = self._section("queue")
        self.table = QTableWidget(0, 5)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COL_FILE, QHeaderView.Stretch)
        header.setSectionResizeMode(self.COL_DETAILS, QHeaderView.Stretch)
        # Delete key removes the selected queue rows; selection changes re-evaluate
        # the button states.
        QShortcut(QKeySequence.StandardKey.Delete, self.table,
                  activated=self._remove_selected)
        self.table.itemSelectionChanged.connect(self._on_queue_selection)
        # The body is one scrollable column, so the table only ever gets its size
        # hint - which is barely one row. Give it a floor of several rows and let
        # it grow with the queue up to a cap, otherwise a user with five files
        # queued can see only the first one.
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.MinimumExpanding)
        q_layout.addWidget(self.table)
        self._fit_queue_height()
        controls = QHBoxLayout()
        self.btn_process = QPushButton(self._t("process"))
        self.btn_process.setProperty("variant", "primary")
        self.btn_process.clicked.connect(self._process_pending)
        self.btn_cancel = QPushButton(self._t("cancel"))
        self.btn_cancel.clicked.connect(self._cancel_selected)
        self.btn_cancel.setEnabled(False)
        self.btn_remove = QPushButton(self._t("remove"))
        self.btn_remove.setToolTip(self._t("remove_tip"))
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_remove.setEnabled(False)
        # Emptying the queue one row at a time is not a workflow: after a batch
        # the user wants it gone in one press.
        self.btn_clear = QPushButton(self._t("clear_queue"))
        self.btn_clear.setToolTip(self._t("clear_tip"))
        self.btn_clear.clicked.connect(self._clear_queue)
        self.btn_clear.setEnabled(False)
        self.lbl_workers = QLabel("")
        self.lbl_workers.setObjectName("hint")
        controls.addWidget(self.btn_process)
        controls.addWidget(self.btn_cancel)
        controls.addWidget(self.btn_remove)
        controls.addWidget(self.btn_clear)
        controls.addStretch(1)
        controls.addWidget(self.lbl_workers)
        q_layout.addLayout(controls)
        root.addWidget(q_frame)

        # Status
        s_frame, s_layout = self._section("status")
        self.lbl_status = QLabel(self._t("idle"))
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        # Per-file stage timeline: completed stages stay visible with the time each
        # took, the current one ticks live (with its fine step). This is the single
        # place the panel shows "what's happening" — no separate detail line that
        # just repeats the status word. The fine step still lives in the queue
        # table's Details column.
        self.lbl_stages = QLabel("")
        self.lbl_stages.setObjectName("hint")
        self.lbl_stages.setWordWrap(True)
        self.lbl_stages.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_stages.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        stage_scroll = QScrollArea()
        stage_scroll.setObjectName("statusTimeline")
        stage_scroll.setWidgetResizable(True)
        stage_scroll.setFrameShape(QFrame.Shape.NoFrame)
        stage_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        stage_scroll.setFixedHeight(112)
        stage_scroll.setWidget(self.lbl_stages)
        self.status_timeline = stage_scroll
        s_layout.addWidget(self.lbl_status)
        s_layout.addWidget(self.progress)
        s_layout.addWidget(stage_scroll)
        # The timeline remains complete and scrollable, but the whole section no
        # longer grows by one row for every completed stage.
        s_frame.setFixedHeight(220)
        root.addWidget(s_frame)

        # Results
        r_frame, r_layout = self._section_results()
        root.addWidget(r_frame)

        root.addStretch(1)
        scroll.setWidget(container)
        self.setCentralWidget(scroll)

    def _section_results(self) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("section")
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(16, 14, 16, 16)
        # Project field (scopes RAG add/search; persisted on the history entry)
        proj_row = QHBoxLayout()
        self.lbl_project = QLabel(self._t("project"))
        self.lbl_project.setObjectName("sectionTitle")
        self.lbl_project.setProperty("titleKey", "project")
        self.edit_project = QLineEdit()
        self.edit_project.setObjectName("projectEdit")
        self.edit_project.setPlaceholderText(self._t("project_ph"))
        self.edit_project.setMaximumWidth(360)
        theme.fit_placeholder(self.edit_project)
        self.edit_project.editingFinished.connect(self._on_project_edited)
        proj_row.addWidget(self.lbl_project)
        proj_row.addWidget(self.edit_project)
        proj_row.addStretch(1)
        outer.addLayout(proj_row)
        row = QHBoxLayout()
        raw_box = QVBoxLayout()
        self.lbl_raw = QLabel(self._t("raw"))
        self.lbl_raw.setObjectName("sectionTitle")
        self.txt_raw = QPlainTextEdit()
        self.txt_raw.setMinimumHeight(220)
        raw_box.addWidget(self.lbl_raw)
        raw_box.addWidget(self.txt_raw)
        sum_box = QVBoxLayout()
        sum_header = QHBoxLayout()
        self.lbl_summary = QLabel(self._t("summary"))
        self.lbl_summary.setObjectName("sectionTitle")
        sum_header.addWidget(self.lbl_summary)
        sum_header.addStretch(1)
        # Summary version switcher: ◀  [dropdown]  ▶
        self.btn_sum_prev = QPushButton("◀")
        self.btn_sum_prev.setObjectName("verNav")
        self.btn_sum_prev.setFixedWidth(28)
        self.btn_sum_prev.clicked.connect(lambda: self._step_summary_version(-1))
        self.cb_sum_version = QComboBox()
        self.cb_sum_version.setObjectName("verCombo")
        self.cb_sum_version.currentIndexChanged.connect(self._on_summary_version_picked)
        self.btn_sum_next = QPushButton("▶")
        self.btn_sum_next.setObjectName("verNav")
        self.btn_sum_next.setFixedWidth(28)
        self.btn_sum_next.clicked.connect(lambda: self._step_summary_version(1))
        for w in (self.btn_sum_prev, self.cb_sum_version, self.btn_sum_next):
            sum_header.addWidget(w)
        self.txt_summary = QPlainTextEdit()
        self.txt_summary.setMinimumHeight(220)
        sum_box.addLayout(sum_header)
        sum_box.addWidget(self.txt_summary)
        row.addLayout(raw_box)
        row.addLayout(sum_box)
        outer.addLayout(row)
        # export bar (unified: raw / summary / analysis -> any format)
        exp = QHBoxLayout()
        self.lbl_export = QLabel(self._t("export"))
        self.cb_export_kind = QComboBox()
        for value, key in (("raw", "exp_raw"), ("summary", "exp_summary"),
                           ("analysis", "exp_analysis")):
            self.cb_export_kind.addItem(self._t(key), value)
        self.cb_export_fmt = QComboBox()
        for fmt in exporter.FORMATS:
            self.cb_export_fmt.addItem(fmt.upper(), fmt)
        # Default to the styled HTML export (TXT/MD are intentionally plain), so a
        # one-click export already looks like a formatted report.
        if "html" in exporter.FORMATS:
            self.cb_export_fmt.setCurrentIndex(exporter.FORMATS.index("html"))
        theme.fit_combo(self.cb_export_kind)
        theme.fit_combo(self.cb_export_fmt)
        self.btn_export = QPushButton(self._t("do_export"))
        self.btn_export.setProperty("variant", "primary")
        self.btn_export.clicked.connect(self._do_export)
        self.btn_obsidian = QPushButton(self._t("obsidian"))
        self.btn_obsidian.clicked.connect(self._do_obsidian)
        self.btn_speakers = QPushButton(self._t("speakers"))
        self.btn_speakers.setEnabled(False)
        self.btn_speakers.setToolTip("")
        self.btn_speakers.clicked.connect(self._do_speakers)
        self.btn_export_speakers = QPushButton(self._t("export_speakers"))
        self.btn_export_speakers.setEnabled(False)
        self.btn_export_speakers.clicked.connect(self._do_export_by_speaker)
        self.btn_regenerate = QPushButton(self._t("regenerate"))
        self.btn_regenerate.setEnabled(False)
        self.btn_regenerate.setToolTip(self._t("regen_tip"))
        self.btn_regenerate.clicked.connect(self._do_regenerate)
        self.txt_raw.setToolTip(self._t("regen_tip"))
        self.btn_add_rag = QPushButton(self._t("add_to_rag"))
        self.btn_add_rag.setEnabled(False)
        self.btn_add_rag.clicked.connect(self._do_add_to_rag)
        self.btn_rag = QPushButton(self._t("rag"))
        self.btn_rag.clicked.connect(self._open_rag)
        self.btn_search = QPushButton(self._t("search_btn"))
        self.btn_search.clicked.connect(self._open_search)
        self.lbl_export_status = QLabel("")
        self.lbl_export_status.setObjectName("hint")
        # Row 1: export destination controls. Row 2: meeting actions. Splitting the
        # old single 11-button row keeps the window from forcing a horizontal
        # scrollbar on normal widths (the layout wraps to two rows instead).
        for w in (self.lbl_export, self.cb_export_kind, self.cb_export_fmt,
                  self.btn_export, self.btn_obsidian):
            exp.addWidget(w)
        exp.addStretch(1)
        outer.addLayout(exp)
        actions_row = QHBoxLayout()
        for w in (self.btn_speakers, self.btn_export_speakers, self.btn_regenerate,
                  self.btn_add_rag, self.btn_rag, self.btn_search):
            actions_row.addWidget(w)
        actions_row.addWidget(self.lbl_export_status, 1)
        actions_row.addStretch(1)
        outer.addLayout(actions_row)

        # Analysis panels — header with version switcher
        an_header = QHBoxLayout()
        lbl_analysis = QLabel(self._t("analysis"))
        lbl_analysis.setObjectName("sectionTitle")
        lbl_analysis.setProperty("titleKey", "analysis")
        an_header.addWidget(lbl_analysis)
        an_header.addStretch(1)
        self.btn_an_prev = QPushButton("◀")
        self.btn_an_prev.setObjectName("verNav")
        self.btn_an_prev.setFixedWidth(28)
        self.btn_an_prev.clicked.connect(lambda: self._step_analysis_version(-1))
        self.cb_an_version = QComboBox()
        self.cb_an_version.setObjectName("verCombo")
        self.cb_an_version.currentIndexChanged.connect(self._on_analysis_version_picked)
        self.btn_an_next = QPushButton("▶")
        self.btn_an_next.setObjectName("verNav")
        self.btn_an_next.setFixedWidth(28)
        self.btn_an_next.clicked.connect(lambda: self._step_analysis_version(1))
        for w in (self.btn_an_prev, self.cb_an_version, self.btn_an_next):
            an_header.addWidget(w)
        outer.addLayout(an_header)
        self.analysis_widget = AnalysisWidget(language=self.language, parent=frame)
        self.analysis_widget.setMinimumHeight(300)
        self.analysis_widget.setVisible(True)
        outer.addWidget(self.analysis_widget, 1)
        return frame, outer

    # -- queue binding -------------------------------------------------
    def _connect_queue(self) -> None:
        self._refresh_workers_label()
        if not self.queue:
            return
        self.queue.status_changed.connect(self.on_status_changed)
        self.queue.progress.connect(self.on_progress)
        self.queue.job_finished.connect(self.on_finished)
        self.queue.speakers_needed.connect(self.on_speakers_needed)
        # Optional enrichment signals (button state, stage timeline). A queue that
        # doesn't emit them must still yield a working window — never let optional
        # UI wiring break construction.
        for name, slot in (("active_changed", self._on_active_changed),
                           ("stage_done", self.on_stage_done)):
            signal = getattr(self.queue, name, None)
            if signal is not None and hasattr(signal, "connect"):
                signal.connect(slot)
        self._update_run_buttons(0)

    # -- stage timeline (completed stages + how long each took) --------
    def on_stage_done(self, job_id, label: str, seconds: float) -> None:
        from ..core.pipeline import fmt_duration
        # A label that already carries its own mark (e.g. "✖ … — не удалось" for a
        # failed feature) is used verbatim; otherwise it's a normal ✔ success line.
        line = label if label[:1] in ("✔", "✖", "⏳") \
            else f"✔ {label} — {fmt_duration(seconds)}"
        self._stages_by_job.setdefault(job_id, []).append(line)
        # Fine-grained stages (analysis features, transcript chunks) complete
        # without changing the coarse JobStatus.  Restart the live timer here so
        # the next feature does not inherit the elapsed time of all predecessors.
        state = self._live_by_job.get(job_id)
        if state is not None and state.get("stage_t0") is not None:
            state["stage_t0"] = time.monotonic()
        if job_id == self._active_job:
            self._render_live()

    def _render_live(self) -> None:
        """Re-render the active job's timeline with the in-flight stage ticking
        live, labelled by its fine step (e.g. 'Транскрибация фрагмента 1/5')."""
        state = self._live_by_job.get(self._active_job, {})
        stage_t0 = state.get("stage_t0")
        if stage_t0 is None:
            self._render_stages()
            return
        from ..core.pipeline import fmt_duration
        live = state.get("detail") or state.get("stage_label") or ""
        self._render_stages(
            f"⏳ {live} — {fmt_duration(time.monotonic() - stage_t0)}")

    def _render_stages(self, current: str = "") -> None:
        """Render the active job's timeline as a vertical list — one stage per
        line — with the in-flight stage (if any) last."""
        parts = list(self._stages_by_job.get(self._active_job, []))
        if current:
            parts.append(current)
        self.lbl_stages.setText("<br>".join(parts))
        if hasattr(self, "status_timeline"):
            QTimer.singleShot(
                0, lambda: self.status_timeline.verticalScrollBar().setValue(
                    self.status_timeline.verticalScrollBar().maximum()))

    def _refresh_workers_label(self) -> None:
        n = getattr(self.queue, "max_concurrency", self.settings.get("parallelWorkers", "auto"))
        auto = str(self.settings.get("parallelWorkers", "auto")).strip().lower() == "auto"
        mode = self._t("workers_auto_gpu") if auto and self._device_cuda else ""
        self.lbl_workers.setText(self._t("workers").format(n=n, mode=mode))

    # -- queue model ---------------------------------------------------
    def _q_pending_count(self) -> int:
        """Queue introspection is optional: a minimal queue implementation (or a
        test double) must not break the window."""
        fn = getattr(self.queue, "pending_count", None)
        try:
            return int(fn()) if callable(fn) else 0
        except Exception:      # noqa: BLE001
            return 0

    def _q_pending_ids(self) -> set:
        fn = getattr(self.queue, "pending_ids", None)
        try:
            return set(fn()) if callable(fn) else set()
        except Exception:      # noqa: BLE001
            return set()

    def _q_runner(self, job_id):
        fn = getattr(self.queue, "runner", None)
        try:
            return fn(job_id) if callable(fn) else None
        except Exception:      # noqa: BLE001
            return None

    def _q_active_count(self) -> int:
        fn = getattr(self.queue, "active_count", None)
        try:
            return int(fn()) if callable(fn) else 0
        except Exception:      # noqa: BLE001
            return 0

    def _startable_rows(self) -> list:
        """(id, path) for every queue row that 'Process' should start: not
        finished, not already running, not already waiting in the queue.

        Derived from the TABLE + store rather than a side list, so cancelling or
        restarting can never leave orphan rows that no button can act on. Rows
        left mid-flight by a crash (status 'transcribing' with no runner) are
        startable again too."""
        if not self.queue:
            return []
        waiting = self._q_pending_ids()
        # A finished file is startable only when the user has SELECTED it — so
        # 'Process' can re-run a specific done meeting (new summary/analysis versions)
        # without silently re-processing every completed row.
        selected = {jid for jid, row in self._rows.items()
                    if row in {i.row() for i in self.table.selectedIndexes()}}
        out = []
        for job_id in self._rows:
            if job_id in waiting or self._q_runner(job_id) is not None:
                continue
            entry = self.store.get(job_id)
            if not entry:
                continue
            if job_id in self._restored_ids and job_id not in selected:
                continue
            if entry.status == JobStatus.DONE.value and job_id not in selected:
                continue
            out.append((job_id, entry.video_path))
        return out

    # -- run/cancel button state + elapsed clock ----------------------
    def _update_run_buttons(self, active: int) -> None:
        """Enable actions for startable work and the selected cancellable job."""
        # A running job must not block enqueueing more work: PipelineQueue owns
        # the concurrency cap and starts another job immediately when a slot is
        # free, otherwise it truthfully leaves it waiting.
        can_start = bool(self._startable_rows())
        self.btn_process.setEnabled(can_start)
        self.btn_process.setProperty("variant", "primary" if can_start else "")
        selected_id = self._selected_job_id()
        selected_entry = self.store.get(selected_id) if selected_id is not None else None
        can_cancel = bool(
            selected_entry
            and selected_entry.status not in {
                JobStatus.DONE.value, JobStatus.ERROR.value, JobStatus.CANCELLED.value
            }
            and (
                self._q_runner(selected_id) is not None
                or selected_id in self._q_pending_ids()
            )
        )
        self.btn_cancel.setEnabled(can_cancel)
        self.btn_cancel.setProperty("variant", "primary" if can_cancel else "")
        self.btn_remove.setEnabled(self.table.rowCount() > 0)
        self.btn_clear.setEnabled(self.table.rowCount() > 0)
        for b in (self.btn_process, self.btn_cancel):
            b.style().unpolish(b)
            b.style().polish(b)

    def _on_active_changed(self, active: int) -> None:
        self._update_run_buttons(active)
        if active > 0 and self._run_t0 is None:
            self._run_t0 = time.monotonic()
            self._elapsed_timer.start()
        elif active == 0:
            self._elapsed_timer.stop()
            self._run_t0 = None

    def _tick_elapsed(self) -> None:
        state = self._live_by_job.get(self._active_job, {})
        run_t0 = state.get("run_t0")
        if run_t0 is None:
            return
        secs = int(time.monotonic() - run_t0)
        m, s = divmod(secs, 60)
        t = f"{m}м {s}с" if (self.language == "ru") else f"{m}m {s}s"
        if m == 0:
            t = f"{s}с" if self.language == "ru" else f"{s}s"
        base = self.lbl_status.text().split("   ")[0]
        self.lbl_status.setText(f"{base}   {self._t('elapsed').format(t=t)}")
        # Live line for the stage in flight, after the finished ones.
        self._render_live()

    def _cancel_selected(self) -> None:
        """Cancel only the selected row; never stop unrelated active jobs."""
        job_id = self._selected_job_id()
        if job_id is None:
            return
        if self.queue and not self.queue.cancel(job_id):
            return
        self._pending = [item for item in self._pending if item[0] != job_id]
        self._stages_by_job.pop(job_id, None)
        state = self._live_by_job.setdefault(job_id, {})
        state.update({
            "status": JobStatus.CANCELLED.value,
            "progress": 0,
            "detail": "",
            "stage_label": "",
            "stage_t0": None,
        })
        try:
            self.store.set_status(job_id, JobStatus.CANCELLED)
        except KeyError:
            return
        row = self._rows.get(job_id)
        if row is not None:
            self.table.item(row, self.COL_STATUS).setText(
                main_label(JobStatus.CANCELLED, self.language))
            self.table.item(row, self.COL_DETAILS).setText("")
        bar = self._bars.get(job_id)
        if bar is not None:
            bar.setValue(0)
        self._show_status_job(job_id)
        self._update_run_buttons(self._q_active_count() if self.queue else 0)

    def _reindex_rows(self) -> None:
        """Rebuild id->row from the ID column (row indices shift after removal)."""
        rows = {}
        for r in range(self.table.rowCount()):
            item = self.table.item(r, self.COL_ID)
            if item is None:
                continue
            try:
                rows[int(item.text())] = r
            except ValueError:
                pass
        self._rows = rows

    def _clear_queue(self) -> None:
        """Empty the queue in one press, keeping only what is actually running.

        A row being processed cannot just be dropped - its subprocess would keep
        writing into an entry the table no longer knows about - so those stay and
        the status line says how many were kept.
        """
        kept = 0
        dropped_shown = False
        for row in range(self.table.rowCount() - 1, -1, -1):
            job_id = next((jid for jid, r in self._rows.items() if r == row), None)
            if job_id is None:
                continue
            if self.queue and self._q_runner(job_id) is not None:
                kept += 1
                continue
            if job_id in (self._current_result_id, self._active_job):
                dropped_shown = True
            self.table.removeRow(row)
            self._rows.pop(job_id, None)
            self._bars.pop(job_id, None)
            self._restored_ids.discard(job_id)
            self._stages_by_job.pop(job_id, None)
            self._live_by_job.pop(job_id, None)
            self._pending = [p for p in self._pending if p[0] != job_id]
            # The queue is rebuilt from history on every start: without this the
            # cleared meetings simply came back.
            self.store.remove(job_id)
        self._reindex_rows()
        self._fit_queue_height()
        if dropped_shown or self.table.rowCount() == 0:
            self._clear_results()
        if kept:
            self.lbl_status.setText(self._t("clear_kept").format(n=kept))
        self._update_run_buttons(self._q_active_count() if self.queue else 0)

    def _remove_selected(self) -> None:
        """Remove the selected rows from the queue. A row that is actively being
        processed is skipped — cancel it first."""
        selected = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not selected:
            return
        row_to_id = {row: jid for jid, row in self._rows.items()}
        skipped = 0
        dropped_shown = False
        for r in selected:
            job_id = row_to_id.get(r)
            if job_id is None:
                continue
            if self.queue and self._q_runner(job_id) is not None:
                skipped += 1
                continue
            if job_id in (self._current_result_id, self._active_job):
                dropped_shown = True
            self.table.removeRow(r)
            self._rows.pop(job_id, None)
            self._bars.pop(job_id, None)
            self._restored_ids.discard(job_id)
            # Everything keyed by this id goes with it, the stage timeline included.
            self._stages_by_job.pop(job_id, None)
            self._live_by_job.pop(job_id, None)
            self._pending = [p for p in self._pending if p[0] != job_id]
            self.store.remove(job_id)
        self._reindex_rows()
        self._fit_queue_height()
        # Reset the panels when the meeting they show is gone - not only when the
        # whole queue emptied, or deleting the selected row out of several left
        # another meeting's stages and artifacts on screen.
        if dropped_shown or self.table.rowCount() == 0:
            self._clear_results()
        if skipped:
            self.lbl_status.setText(self._t("remove_running"))
        self._update_run_buttons(self._q_active_count() if self.queue else 0)

    def _restore_history_rows(self) -> None:
        """Restore persistent meetings as browseable rows after app restart.

        History is newest-first so the latest meeting and its artifacts are
        immediately available for export/regeneration. Restoring rows never
        enqueues processing.
        """
        entries = list(reversed(self.store.load()))
        if not entries:
            return
        self.table.blockSignals(True)
        try:
            for entry in entries:
                self.add_job_row(entry, restored=True)
            self.table.selectRow(0)
        finally:
            self.table.blockSignals(False)
        self._on_queue_selection()

    QUEUE_MIN_ROWS = 5
    QUEUE_MAX_ROWS = 12

    def _fit_queue_height(self) -> None:
        """Size the queue table to its contents, between MIN and MAX rows."""
        header = self.table.horizontalHeader().height() or 28
        row_h = self.table.verticalHeader().defaultSectionSize() or 30
        rows = max(self.QUEUE_MIN_ROWS,
                   min(self.table.rowCount(), self.QUEUE_MAX_ROWS))
        self.table.setMinimumHeight(header + rows * row_h + 4)

    def add_job_row(self, entry, *, restored: bool = False) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, self.COL_ID, QTableWidgetItem(str(entry.id)))
        # Show the full path (with the file name); the basename alone is ambiguous
        # when several files share a name from different folders.
        file_item = QTableWidgetItem(entry.video_path or entry.video_name)
        file_item.setToolTip(entry.video_path or entry.video_name)
        self.table.setItem(row, self.COL_FILE, file_item)
        status = JobStatus(entry.status) if entry.status in JobStatus._value2member_map_ \
            else JobStatus.QUEUED
        self.table.setItem(row, self.COL_STATUS,
                           QTableWidgetItem(main_label(status, self.language)))
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(100 if status == JobStatus.DONE else 0)
        self.table.setCellWidget(row, self.COL_PROGRESS, bar)
        # A restored row used to have an empty Details cell, so a failed meeting
        # showed "Error / 0%" with no way to learn why.
        detail = QTableWidgetItem(getattr(entry, "error", "") or "")
        if getattr(entry, "error", ""):
            detail.setToolTip(entry.error)
        self.table.setItem(row, self.COL_DETAILS, detail)
        self._rows[entry.id] = row
        self._bars[entry.id] = bar
        if restored:
            self._restored_ids.add(entry.id)
        else:
            self._restored_ids.discard(entry.id)
        if self.table.currentRow() < 0:
            self.table.selectRow(row)
        self._fit_queue_height()

    def _selected_job_id(self) -> Optional[int]:
        rows = {item.row() for item in self.table.selectedIndexes()}
        if not rows:
            return None
        row = min(rows)
        item = self.table.item(row, self.COL_ID)
        try:
            return int(item.text()) if item is not None else None
        except (TypeError, ValueError):
            return None

    def _on_queue_selection(self) -> None:
        """Bind both result and live-status panels to the selected queue row."""
        job_id = self._selected_job_id()
        if job_id is not None:
            self._active_job = job_id
            self._show_status_job(job_id)
            self._load_results(job_id)
        elif self.table.rowCount() == 0:
            # Nothing left to show: the panels used to keep the transcript,
            # summary and analysis of a meeting that is no longer in the queue.
            self._clear_results()
        self._update_run_buttons(self._q_active_count() if self.queue else 0)

    def _clear_results(self) -> None:
        """Empty every result surface — the queue no longer holds that meeting."""
        self._current_result_id = None
        self._active_job = None
        self._current_transcript = ""
        self._current_analysis = None
        self.txt_raw.setPlainText("")
        self.txt_summary.setPlainText("")
        self.analysis_widget.clear()
        self.edit_project.blockSignals(True)
        self.edit_project.setText("")
        self.edit_project.blockSignals(False)
        for combo in (self.cb_sum_version, self.cb_an_version):
            combo.blockSignals(True)
            combo.clear()
            combo.blockSignals(False)
        self._sel_summary_idx = -1
        self._sel_analysis_idx = -1
        for button in (self.btn_regenerate, self.btn_add_rag, self.btn_speakers,
                       self.btn_export_speakers):
            button.setEnabled(False)
        self.lbl_status.setText("")
        self.progress.setValue(0)
        # The stage timeline is rendered HTML, not derived state: clearing
        # _active_job alone left "✔ Обработка — 10м 18с" of a deleted meeting on
        # screen, next to an empty queue and a 0% bar.
        self._render_stages()

    def _show_status_job(self, job_id: int) -> None:
        entry = self.store.get(job_id)
        if entry is None:
            return
        state = self._live_by_job.get(job_id, {})
        try:
            status = JobStatus(state.get("status", entry.status))
            status_text = main_label(status, self.language)
        except ValueError:
            status_text = str(state.get("status", entry.status) or "")
        line = f"{entry.video_name} · {status_text}"
        # Show the stored reason for a run that ended badly - the status alone is
        # not actionable, and after a restart it was all the user had.
        reason = (getattr(entry, "error", "") or "").strip()
        if reason and not state:
            line += f" — {reason.splitlines()[0][:160]}"
        self.lbl_status.setText(line)
        self.lbl_status.setToolTip(reason)
        bar = self._bars.get(job_id)
        self.progress.setValue(
            int(state.get("progress", bar.value() if bar is not None else 0)))
        if not state and job_id not in self._stages_by_job:
            # Nothing ran in THIS session: rebuild the timeline from what was
            # persisted. Clicking a meeting used to load its transcript, summary
            # and analysis while the status panel stayed empty.
            self._stages_by_job[job_id] = self._stages_from_history(entry)
        self._render_live()

    def _stages_from_history(self, entry) -> list:
        """Timeline for a meeting processed earlier: header + recorded stages.

        Per-stage timings live in the meeting's ``*_trace.json`` (the same file
        the diagnostics flame graph reads), everything else on the entry itself.
        """
        from ..core import trace as trace_mod
        from ..core.pipeline import fmt_duration

        lines = []
        when = (entry.processed_at or "").replace("T", " ")[:16]
        head = [p for p in (when, entry.duration, entry.size) if p]
        if head:
            lines.append(self._t("hist_processed").format(v=" · ".join(head)))
        try:
            path = trace_mod.find_trace(self.store.job_dir(entry.id))
            data = trace_mod.load_trace(path) if path else None
        except Exception:      # noqa: BLE001 - a missing trace is not an error
            data = None
        for span in (data or {}).get("spans", []):
            name = str(span.get("name", "")).strip()
            ms = span.get("duration")
            if not name:
                continue
            label = _TRACE_STAGE_NAMES[self.language].get(name, name)
            # A stage that finished instantly still has a duration: 0.0 is a
            # measurement, not a missing value, and rendering it as a bare label
            # made "Загрузка локальной LLM" look like a stage with no time at all
            # (it takes no time when the model is already up).
            lines.append(f"✔ {label} — {fmt_duration((ms or 0) / 1000.0)}"
                         if ms is not None else f"✔ {label}")
        for version in (entry.summary_versions or []):
            lines.append(self._t("hist_summary").format(
                n=version.version, p=version.provider or "—"))
        for version in (entry.analysis_versions or []):
            lines.append(self._t("hist_analysis").format(
                n=version.version, p=version.provider or "—"))
        reason = (getattr(entry, "error", "") or "").strip()
        if reason:
            lines.append("✖ " + reason.splitlines()[0][:200])
        if not lines:
            lines.append(self._t("hist_empty"))
        return lines

    def on_status_changed(self, job_id, status) -> None:
        row = self._rows.get(job_id)
        if row is None:
            return
        label = main_label(status, self.language)
        self.table.item(row, self.COL_STATUS).setText(label)
        state = self._live_by_job.setdefault(job_id, {})
        state["status"] = status.value if isinstance(status, JobStatus) else str(status)
        state["detail"] = ""
        state.setdefault("run_t0", time.monotonic())
        # Terminal states have no in-flight stage — no live "⏳ … — 0с" line.
        if status in (JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED):
            state["stage_label"] = ""
            state["stage_t0"] = None
        else:
            # Start timing the stage the job just entered (for the live line).
            state["stage_label"] = label.rstrip("… .")
            state["stage_t0"] = time.monotonic()
        if self._active_job is None:
            self._active_job = job_id
        if job_id == self._active_job:
            self._show_status_job(job_id)

    def on_progress(self, job_id, percent, detail) -> None:
        detail = _tr_detail(detail, self.language)
        bar = self._bars.get(job_id)
        if bar is not None:
            bar.setValue(int(percent))
        row = self._rows.get(job_id)
        if row is not None:
            self.table.item(row, self.COL_DETAILS).setText(detail)
        state = self._live_by_job.setdefault(job_id, {})
        state["progress"] = int(percent)
        state["detail"] = detail
        if job_id == self._active_job:
            self.progress.setValue(int(percent))
            self._render_live()

    def on_finished(self, job_id, ok, error) -> None:
        row = self._rows.get(job_id)
        was_regenerating = (
            job_id == self._active_job
            and self.lbl_export_status.text() == self._t("regen_running"))
        # An incomplete analysis can still persist a valid new summary plus all
        # successful sections.  Reload those artifacts for the selected meeting
        # even while keeping the run visibly red.
        if job_id == self._active_job:
            self._load_results(job_id)
            if was_regenerating:
                self.lbl_export_status.setText(
                    self._t("regen_done" if ok else "regen_failed"))
        if ok:
            return
        if error == "__cancelled__":
            if row is not None:
                self.table.item(row, self.COL_STATUS).setText(
                    main_label(JobStatus.CANCELLED, self.language))
        elif row is not None:
            item = self.table.item(row, self.COL_STATUS)
            item.setText(main_label(JobStatus.ERROR, self.language))
            item.setToolTip(error or "")
            # Surface WHY it failed — not buried in a tooltip. Show the message in
            # the Details cell, the status line, and as a ✖ line in the timeline.
            msg = error or main_label(JobStatus.ERROR, self.language)
            self.table.item(row, self.COL_DETAILS).setText(msg)
            self._stages_by_job.setdefault(job_id, []).append(f"✖ {msg}")
            state = self._live_by_job.setdefault(job_id, {})
            state.update({
                "status": JobStatus.ERROR.value,
                "detail": msg,
                "stage_label": "",
                "stage_t0": None,
            })
            if job_id == self._active_job:
                name = Path(self.table.item(row, self.COL_FILE).text()).name
                self.lbl_status.setText(
                    f"{name}: {main_label(JobStatus.ERROR, self.language)} — {msg}")
                self._render_stages()

    def _analysis_meta(self) -> dict:
        entry = self.store.get(self._current_result_id) \
            if self._current_result_id is not None else None
        return _analysis_meta_from(
            self._current_transcript, self._current_analysis,
            (getattr(entry, "duration", "") or "") if entry else "")

    def _load_results(self, job_id) -> None:
        entry = self.store.get(job_id)
        if not entry:
            return
        self._current_result_id = job_id

        # Project field (persisted on the entry)
        self.edit_project.blockSignals(True)
        self.edit_project.setText(entry.project or "")
        self.edit_project.blockSignals(False)
        self._current_transcript = ""
        if entry.transcript_path and Path(entry.transcript_path).exists():
            try:
                self._current_transcript = Path(entry.transcript_path).read_text(
                    encoding="utf-8", errors="replace")
            except OSError:
                self._current_transcript = ""
        self.txt_raw.setPlainText(self._current_transcript)
        self._update_speakers_button()

        # Default selection = latest version of each
        self._sel_summary_idx = (len(entry.summary_versions) - 1
                                 if entry.summary_versions else -1)
        self._sel_analysis_idx = (len(entry.analysis_versions) - 1
                                  if entry.analysis_versions else -1)

        self._populate_summary_versions(entry)
        self._populate_analysis_versions(entry)
        self._show_summary_version(entry)
        self._show_analysis_version(entry)

        # Regenerate is available whenever we have a transcript to work from
        self.btn_regenerate.setEnabled(bool(self._current_transcript))
        # Add-to-RAG needs at least a summary version
        self.btn_add_rag.setEnabled(bool(entry.summary_versions))

    # -- version switching --------------------------------------------
    def _version_label(self, ver, kind: str) -> str:
        """Human label for a version dropdown item."""
        prov = (ver.provider or "").strip()
        base = f"v{ver.version}"
        if prov:
            base += f" · {prov}"
        if kind == "analysis":
            src = getattr(ver, "source_summary_version", 0)
            if src:
                base += "  " + self._t("ver_from_summary").format(n=src)
        return base

    def _populate_summary_versions(self, entry) -> None:
        cb = self.cb_sum_version
        cb.blockSignals(True)
        cb.clear()
        for v in entry.summary_versions:
            cb.addItem(self._version_label(v, "summary"))
        has = bool(entry.summary_versions)
        if has and 0 <= self._sel_summary_idx < cb.count():
            cb.setCurrentIndex(self._sel_summary_idx)
        cb.blockSignals(False)
        multi = len(entry.summary_versions) > 1
        for w in (self.cb_sum_version, self.btn_sum_prev, self.btn_sum_next):
            w.setVisible(has)
        self.btn_sum_prev.setEnabled(multi)
        self.btn_sum_next.setEnabled(multi)
        self._sync_summary_nav()

    def _populate_analysis_versions(self, entry) -> None:
        cb = self.cb_an_version
        cb.blockSignals(True)
        cb.clear()
        for v in entry.analysis_versions:
            cb.addItem(self._version_label(v, "analysis"))
        has = bool(entry.analysis_versions)
        if has and 0 <= self._sel_analysis_idx < cb.count():
            cb.setCurrentIndex(self._sel_analysis_idx)
        cb.blockSignals(False)
        multi = len(entry.analysis_versions) > 1
        for w in (self.cb_an_version, self.btn_an_prev, self.btn_an_next):
            w.setVisible(has)
        self.btn_an_prev.setEnabled(multi)
        self.btn_an_next.setEnabled(multi)
        self._sync_analysis_nav()

    def _sync_summary_nav(self) -> None:
        n = self.cb_sum_version.count()
        i = self._sel_summary_idx
        self.btn_sum_prev.setEnabled(n > 1 and i > 0)
        self.btn_sum_next.setEnabled(n > 1 and i < n - 1)

    def _sync_analysis_nav(self) -> None:
        n = self.cb_an_version.count()
        i = self._sel_analysis_idx
        self.btn_an_prev.setEnabled(n > 1 and i > 0)
        self.btn_an_next.setEnabled(n > 1 and i < n - 1)

    def _show_summary_version(self, entry=None) -> None:
        entry = entry or self.store.get(self._current_result_id)
        if not entry or not entry.summary_versions:
            self.txt_summary.setPlainText("")
            return
        idx = max(0, min(self._sel_summary_idx, len(entry.summary_versions) - 1))
        path = Path(entry.summary_versions[idx].path)
        text = (path.read_text(encoding="utf-8", errors="replace")
                if path.exists() else "")
        self.txt_summary.setPlainText(text)

    def _show_analysis_version(self, entry=None) -> None:
        entry = entry or self.store.get(self._current_result_id)
        self._current_analysis = None
        if entry and entry.analysis_versions:
            idx = max(0, min(self._sel_analysis_idx, len(entry.analysis_versions) - 1))
            try:
                raw = Path(entry.analysis_versions[idx].path).read_text(
                    encoding="utf-8", errors="replace")
                self._current_analysis = json.loads(raw)
            except (OSError, ValueError):
                self._current_analysis = None
        if self._current_analysis:
            self.analysis_widget.set_language(self.language)
            self.analysis_widget.load(self._current_analysis, self._analysis_meta())
            self.analysis_widget.setVisible(True)
        else:
            self.analysis_widget.clear()
            self.analysis_widget.setVisible(True)

    def _on_summary_version_picked(self, idx: int) -> None:
        if idx < 0:
            return
        self._sel_summary_idx = idx
        self._sync_summary_nav()
        self._show_summary_version()

    def _on_analysis_version_picked(self, idx: int) -> None:
        if idx < 0:
            return
        self._sel_analysis_idx = idx
        self._sync_analysis_nav()
        self._show_analysis_version()

    def _step_summary_version(self, delta: int) -> None:
        n = self.cb_sum_version.count()
        if n <= 1:
            return
        new_idx = max(0, min(self._sel_summary_idx + delta, n - 1))
        if new_idx != self._sel_summary_idx:
            self.cb_sum_version.setCurrentIndex(new_idx)  # triggers picked slot

    def _step_analysis_version(self, delta: int) -> None:
        n = self.cb_an_version.count()
        if n <= 1:
            return
        new_idx = max(0, min(self._sel_analysis_idx + delta, n - 1))
        if new_idx != self._sel_analysis_idx:
            self.cb_an_version.setCurrentIndex(new_idx)

    # -- regenerate ---------------------------------------------------
    def _do_regenerate(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        if self._current_result_id is None:
            return
        transcript = self.txt_raw.toPlainText()
        if not transcript.strip():
            self.lbl_export_status.setText(self._t("regen_no_transcript"))
            return
        confirm = QMessageBox.question(
            self, self._t("regen_confirm_title"), self._t("regen_confirm"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if confirm != QMessageBox.Yes:
            return
        entry = self.store.get(self._current_result_id)
        if not entry:
            return
        # Persist the (possibly edited) transcript back to the raw file so the
        # regenerated summary/analysis derive from exactly what the user sees.
        transcript_path = entry.transcript_path
        if not transcript_path:
            transcript_path = str(
                self.store.job_dir(entry.id) / f"{Path(entry.video_name).stem}_raw.txt")
        try:
            Path(transcript_path).write_text(transcript, encoding="utf-8")
        except OSError as exc:
            self.lbl_export_status.setText(str(exc))
            return
        self._current_transcript = transcript
        if self.queue:
            self.btn_regenerate.setEnabled(False)
            self.lbl_export_status.setText(self._t("regen_running"))
            self.queue.enqueue_regenerate(
                entry.id, entry.video_path, transcript_path)

    # -- RAG / project ------------------------------------------------
    def _on_project_edited(self) -> None:
        """Persist the project id onto the current history entry."""
        if self._current_result_id is None:
            return
        try:
            self.store.set_project(self._current_result_id,
                                   self.edit_project.text())
        except KeyError:
            pass

    def _do_add_to_rag(self) -> None:
        """Add the current meeting (latest summary + transcript) to the KB."""
        jid = self._current_result_id
        entry = self.store.get(jid) if jid is not None else None
        if not entry:
            self.lbl_export_status.setText(self._t("no_result"))
            return
        if not entry.summary_versions:
            self.lbl_export_status.setText(self._t("rag_need_summary"))
            return
        # Persist project first (in case the user just typed it).
        self._on_project_edited()
        # The version the picker shows, not blindly the newest: the user chose it.
        summary_path = self._version_at(entry.summary_versions,
                                        self._sel_summary_idx).path
        transcript_path = entry.transcript_path or ""
        paths.ensure_runtime_dirs()
        cmd = [
            str(paths.python_executable()), str(paths.RAG_SCRIPT), "add",
            "--rag-dir", str(paths.rag_dir(self.settings)),
            "--doc-id", str(entry.id),
            "--project", (entry.project or ""),
            "--title", entry.video_name,
            "--date", (entry.processed_at or ""),
            "--summary-file", str(summary_path),
            "--settings", json.dumps(self.settings or {}),
        ]
        if transcript_path:
            cmd += ["--transcript-file", str(transcript_path)]
        self.btn_add_rag.setEnabled(False)
        self.lbl_export_status.setText(self._t("rag_adding"))
        self._rag_worker = RagWorker("add", cmd, parent=self)
        self._rag_worker.done.connect(self._on_rag_add_done)
        self._rag_worker.start()

    def _on_rag_add_done(self, op, ok, data, error) -> None:
        self.btn_add_rag.setEnabled(True)
        if ok:
            chunks = (data or {}).get("chunks", 0)
            self.lbl_export_status.setText(self._t("rag_added").format(n=chunks))
        else:
            self.lbl_export_status.setText(self._t("rag_add_err").format(err=error))

    def _open_recorder(self) -> None:
        """Record a meeting live; the WAV then enters the normal add-file flow
        (trim into per-meeting segments -> queue)."""
        from .recorder_dialog import RecorderDialog
        dlg = RecorderDialog(paths.ROOT, language=self.language, parent=self)
        if dlg.exec() and dlg.result_path:
            self._add_files([dlg.result_path])

    def _open_diagnostics(self) -> None:
        from .diagnostics_dialog import DiagnosticsDialog
        DiagnosticsDialog(self.store, language=self.language, parent=self).exec()

    def _open_stats(self) -> None:
        from .stats_dialog import StatsDialog
        StatsDialog(self.store, language=self.language, parent=self).exec()

    def _open_rag(self) -> None:
        paths.ensure_runtime_dirs()
        dlg = RagDialog(
            rag_dir=str(paths.rag_dir(self.settings)),
            python_exe=str(paths.python_executable()),
            rag_script=str(paths.RAG_SCRIPT),
            settings=self.settings, language=self.language, parent=self,
            history_file=str(getattr(self.store, "path", "") or paths.HISTORY_FILE))
        dlg.exec()

    def _open_search(self) -> None:
        entries = []
        for entry in self.store.load():
            entries.append({
                "video_name": entry.video_name,
                "video_path": entry.video_path,
                "processed_at": entry.processed_at or "",
                "transcript_path": entry.transcript_path or "",
            })
        dlg = SearchDialog(entries, language=self.language, parent=self)
        dlg.exec()

    # -- speakers -----------------------------------------------------
    def on_speakers_needed(self, job_id, transcript_text: str) -> None:
        """Auto-triggered after WhisperX transcription with diarisation."""
        self._current_transcript = transcript_text
        dlg = SpeakersDialog(transcript_text, language=self.language, parent=self)
        dlg.accepted_data.connect(
            lambda txt, parts: self._on_speakers_saved(job_id, txt, parts))
        dlg.cancelled.connect(lambda: self._on_speakers_cancelled(job_id))
        dlg.exec()

    def _update_speakers_button(self) -> None:
        """Enable speakers button only when transcript has diarisation markers."""
        has_speakers = bool(
            self._current_transcript and extract_speakers(self._current_transcript))
        self.btn_speakers.setEnabled(has_speakers)
        self.btn_export_speakers.setEnabled(has_speakers)
        self.btn_speakers.setToolTip(
            "" if has_speakers else
            "Доступно только для транскриптов с разделением по спикерам (WhisperX)"
            if self.language == "ru" else
            "Only available for diarised transcripts (WhisperX)"
        )

    def _do_speakers(self) -> None:
        """Manual open of speakers dialog from Results bar."""
        if not self._current_transcript:
            return
        dlg = SpeakersDialog(self._current_transcript, language=self.language, parent=self)
        dlg.accepted_data.connect(self._on_speakers_manual_save)
        dlg.cancelled.connect(lambda: None)  # no-op
        dlg.exec()

    def _do_export_by_speaker(self) -> None:
        """Write one .txt per speaker (the old app's 'Export by speaker')."""
        if not self._current_transcript:
            return
        entry = self.store.get(self._current_result_id) if self._current_result_id else None
        stem = Path(entry.video_name).stem if entry and getattr(entry, "video_name", "") else "transcript"
        folder = QFileDialog.getExistingDirectory(self, self._t("export_speakers"))
        if not folder:
            return
        try:
            written = export_by_speaker(self._current_transcript, folder, stem)
            self.lbl_export_status.setText(self._t("exp_spk_done").format(n=len(written)))
        except Exception as exc:  # noqa: BLE001
            self.lbl_export_status.setText(str(exc))

    def _on_speakers_saved(self, job_id, transcript_text: str, participants: list) -> None:
        """After automatic dialog: apply and resume pipeline."""
        self._current_transcript = transcript_text
        self.txt_raw.setPlainText(transcript_text)
        if self.queue:
            runner = self.queue.runner(job_id)
            if runner:
                runner.resume_summary(transcript_text, participants)

    def _on_speakers_cancelled(self, job_id) -> None:
        """After automatic dialog cancel: resume pipeline with original transcript."""
        if self.queue:
            runner = self.queue.runner(job_id)
            if runner:
                runner.skip_speakers()

    def _on_speakers_manual_save(self, transcript_text: str, participants: list) -> None:
        """After manual dialog: update the transcript file and UI only."""
        jid = self._current_result_id
        entry = self.store.get(jid) if jid is not None else None
        if entry and entry.transcript_path:
            try:
                Path(entry.transcript_path).write_text(
                    transcript_text, encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(
                    self, self._t("speakers"),
                    self._t("transcript_save_err").format(err=exc))
                return
        self._current_transcript = transcript_text
        self.txt_raw.setPlainText(transcript_text)
        self._update_speakers_button()

    # -- actions -------------------------------------------------------
    def _select_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, self._t("select"), "", VIDEO_FILTER)
        if paths:
            self._add_files(paths)

    def _add_url(self) -> None:
        """Download a video by URL (YouTube / file server), then hand the local file
        to the normal pipeline. Progress shows in a dialog; the app auto-reuses the
        user's browser cookies for auth-gated sites (settings: youtubeCookiesBrowser)."""
        url = self.ed_url.text().strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            QMessageBox.warning(self, self._t("add_url"), self._t("url_bad"))
            return
        uploads = paths.ROOT / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        cookies = str(self.settings.get("youtubeCookiesBrowser", "auto") or "auto")
        cmd = [str(paths.python_executable()), str(paths.URL_DOWNLOAD_SCRIPT), url,
               "--out-dir", str(uploads), "--cookies-from-browser", cookies]

        dlg = QProgressDialog(self._t("downloading"), self._t("cancel"), 0, 100, self)
        dlg.setWindowTitle(self._t("add_url"))
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(True)
        dlg.setValue(0)
        worker = ModelsWorker(cmd, parent=self)
        self._url_worker = worker   # keep a reference so it isn't GC'd

        def on_progress(pct: int, detail: str) -> None:
            dlg.setValue(pct)
            dlg.setLabelText(f"{self._t('downloading')} {detail} ({pct}%)".strip())

        def on_done(ok: bool, result, error: str) -> None:
            dlg.close()
            if ok and result and result.get("path"):
                self.ed_url.clear()
                self._add_files([result["path"]])
            else:
                QMessageBox.warning(self, self._t("add_url"), error or self._t("dl_failed"))

        worker.progress.connect(on_progress)
        worker.done.connect(on_done)
        dlg.canceled.connect(worker.stop)
        self.btn_url.setEnabled(False)
        worker.done.connect(lambda *_: self.btn_url.setEnabled(True))
        worker.start()

    def _add_files(self, paths) -> None:
        """Add files to the queue, offering to split each into per-meeting segments.

        One recording often holds several back-to-back meetings; processing it
        whole blends them into one summary. The trim dialog lets the user mark
        each meeting, and every segment is cut to its own file and queued as an
        independent job (own transcript / summary / analysis)."""
        new_jobs = []
        for path in paths:
            for queued_path in self._resolve_segments(path):
                # The details header renders these two and drops empty values, so
                # calling add() without them meant the length and size of every
                # recording were simply never shown. HistoryStore.add() has always
                # accepted them and media.probe_duration() works on audio-only
                # files too - nothing was producing them.
                entry_id = self.store.add(queued_path,
                                          duration=self._probe_duration_label(queued_path),
                                          size=self._file_size_label(queued_path))
                entry = self.store.get(entry_id)
                if entry:
                    self.add_job_row(entry)
                    self._pending.append((entry_id, queued_path))
                    new_jobs.append((entry_id, queued_path))
        # Adding a file is the user's instruction to process it.  The trim dialog
        # may change one file into several jobs, but it must not turn that action
        # into a second, hidden requirement to press "Process".
        if self.queue:
            for entry_id, queued_path in new_jobs:
                self.queue.enqueue(entry_id, queued_path)
            started = {entry_id for entry_id, _ in new_jobs}
            self._pending = [item for item in self._pending if item[0] not in started]
        self._update_run_buttons(self._q_active_count() if self.queue else 0)

    @staticmethod
    def _probe_duration_label(path: str) -> str:
        """Compact length for the history header, or "" if the probe cannot read it.

        Best-effort on purpose: a file we cannot probe must still be queueable.
        """
        try:
            from ..backend import media
            from ..core.pipeline import fmt_duration
            seconds = media.probe_duration(path)
            return fmt_duration(seconds) if seconds and seconds > 0 else ""
        except Exception:          # noqa: BLE001 - never block adding a file
            return ""

    @staticmethod
    def _file_size_label(path: str) -> str:
        """Human file size for the history header, or "" if it cannot be read."""
        try:
            n = float(Path(path).stat().st_size)
        except Exception:          # noqa: BLE001
            return ""
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{n:.1f} {unit}"
            n /= 1024
        return ""

    def _resolve_segments(self, path: str) -> list:
        """Ask the user for segments and cut them. Returns the file paths to queue,
        or an EMPTY list when the user cancelled — nothing may be queued then.

        The dialog offers three separate answers, and each must lead somewhere
        different: 'whole file' processes the original, 'process segments' queues
        the cuts, and Cancel means the file is not added at all. Folding Cancel in
        with 'whole file' started a full transcription of a recording the user had
        just declined to process."""
        from .trim_dialog import TrimDialog
        from ..backend import media
        try:
            dlg = TrimDialog(path, language=self.language, parent=self)
        except Exception:          # noqa: BLE001 — no media backend → queue as-is
            return [path]
        if not dlg.exec():
            return []
        if dlg.whole_file or not dlg.segments:
            return [path]

        out_dir = paths.ROOT / "segments"
        cut_paths, failures = [], []
        stopped = False
        progress = QProgressDialog(self._t("cutting"), self._t("cancel"),
                                   0, len(dlg.segments), self)
        progress.setWindowTitle(self._t("cutting_title"))
        progress.setMinimumDuration(0)
        for i, (start, end) in enumerate(dlg.segments):
            if progress.wasCanceled():
                stopped = True
                break
            progress.setValue(i)
            progress.setLabelText(
                f"{self._t('cutting')} {i + 1}/{len(dlg.segments)}  "
                f"{media.format_timecode(start)} — {media.format_timecode(end)}")
            QApplication.processEvents()
            dst = out_dir / media.segment_filename(path, start, end)
            try:
                cut_paths.append(media.cut_segment(path, dst, start, end))
            except Exception as exc:   # noqa: BLE001
                failures.append(str(exc))
        progress.setValue(len(dlg.segments))
        if failures:
            QMessageBox.warning(self, self._t("cutting_title"),
                                self._t("cut_failed").format(err=failures[0]))
        if stopped:
            return []
        # The user asked for segments, so a total failure must NOT quietly start a
        # full transcription of the whole recording instead - the warning above
        # says the cutting failed, and nothing is queued.
        return cut_paths

    def _process_pending(self) -> None:
        """Start every startable row in the queue (see ``_startable_rows``)."""
        if not self.queue:
            return
        for entry_id, path in self._startable_rows():
            self._stages_by_job.pop(entry_id, None)
            self._live_by_job.pop(entry_id, None)
            self.queue.enqueue(entry_id, path)
        self._pending.clear()
        self._update_run_buttons(self._q_active_count())

    @staticmethod
    def _version_at(versions: list, index: int):
        """The version the picker points at, clamped to what actually exists."""
        if not versions:
            return None
        return versions[max(0, min(index, len(versions) - 1))]

    def _do_export(self) -> None:
        jid = self._current_result_id
        entry = self.store.get(jid) if jid is not None else None
        if not entry:
            self.lbl_export_status.setText(self._t("no_result"))
            return
        kind = self.cb_export_kind.currentData()
        fmt = self.cb_export_fmt.currentData()
        stem = Path(entry.video_name).stem
        meta = {"video_name": entry.video_name,
                "duration": getattr(entry, "duration", "") or "",
                "language": self.language}
        if self._current_transcript:
            meta["wordCount"] = len(self._current_transcript.split())
            if not meta["duration"]:
                meta["duration"] = _duration_from_transcript(self._current_transcript)
        participants = _participants_from_analysis(self._current_analysis)
        if participants:
            meta["participants"] = participants
        try:
            if kind == "raw":
                src = entry.transcript_path
                if not src or not Path(src).exists():
                    self.lbl_export_status.setText(self._t("no_result"))
                    return
                data = Path(src).read_text(encoding="utf-8", errors="replace")
                version = 1
            elif kind == "summary":
                if not entry.summary_versions:
                    self.lbl_export_status.setText(self._t("no_result"))
                    return
                # Export what the user is LOOKING at. This used to take
                # ``versions[-1]`` regardless of the picker, so choosing v2 and
                # exporting silently wrote v4 - and, because the file name
                # carries that number, every selection collided on one file.
                chosen = self._version_at(entry.summary_versions,
                                          self._sel_summary_idx)
                version = int(getattr(chosen, "version", 1) or 1)
                data = Path(chosen.path).read_text(encoding="utf-8", errors="replace")
            else:  # analysis — the version selected in the picker, same rule
                if not entry.analysis_versions:
                    self.lbl_export_status.setText(self._t("no_result"))
                    return
                chosen = self._version_at(entry.analysis_versions,
                                          self._sel_analysis_idx)
                version = int(getattr(chosen, "version", 1) or 1)
                data = json.loads(Path(chosen.path).read_text(
                    encoding="utf-8", errors="replace"))
        except (OSError, ValueError) as exc:
            self.lbl_export_status.setText(self._t("export_err").format(err=exc))
            return
        meta["version"] = version
        out_path = exporter.default_export_path(
            self.store.job_dir(jid), stem, kind, version, fmt)
        self.lbl_export_status.setText(self._t("exporting"))
        self.btn_export.setEnabled(False)
        self._ew = ExportWorker(kind, data, fmt, str(out_path), meta, parent=self)
        self._ew.done.connect(self._on_export_done)
        self._ew.start()

    def _on_export_done(self, ok: bool, path: str, error: str) -> None:
        self.btn_export.setEnabled(True)
        if ok:
            self.lbl_export_status.setText(self._t("exported").format(name=Path(path).name))
        else:
            self.lbl_export_status.setText(self._t("export_err").format(err=error))

    def _do_obsidian(self) -> None:
        jid = self._current_result_id
        entry = self.store.get(jid) if jid is not None else None
        if not entry:
            self.lbl_export_status.setText(self._t("no_result"))
            return
        vault = self.settings.get("obsidianVaultPath", "")
        if not self.settings.get("obsidianIntegration") or not vault:
            self.lbl_export_status.setText(self._t("obs_no_vault"))
            return
        # Obsidian gets the SELECTED versions too - exporting a different
        # version than the one on screen is the same defect wherever it happens.
        summary_text = ""
        if entry.summary_versions:
            try:
                summary_text = Path(self._version_at(
                    entry.summary_versions, self._sel_summary_idx).path).read_text(
                    encoding="utf-8", errors="replace")
            except OSError:
                summary_text = ""
        analysis = {}
        if entry.analysis_versions:
            try:
                analysis = json.loads(Path(self._version_at(
                    entry.analysis_versions, self._sel_analysis_idx).path).read_text(
                    encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                analysis = {}
        transcript_text = ""
        if entry.transcript_path and Path(entry.transcript_path).exists():
            try:
                transcript_text = Path(entry.transcript_path).read_text(
                    encoding="utf-8", errors="replace")
            except OSError:
                transcript_text = ""
        # The Obsidian button must export what the two selectors say: the KIND
        # chosen next to it and the VERSION shown in the picker. It used to write
        # a summary note whatever the kind was, numbered by len(versions) - so
        # picking "Transcript" produced "…_summary_v4.md".
        kind = self.cb_export_kind.currentData() or "summary"
        chosen_summary = self._version_at(entry.summary_versions, self._sel_summary_idx)
        chosen_analysis = self._version_at(entry.analysis_versions, self._sel_analysis_idx)
        if kind == "summary" and chosen_summary is None:
            self.lbl_export_status.setText(self._t("no_result"))
            return
        if kind == "analysis" and chosen_analysis is None:
            self.lbl_export_status.setText(self._t("no_result"))
            return
        if kind == "raw" and not transcript_text:
            self.lbl_export_status.setText(self._t("no_result"))
            return
        kwargs = dict(
            stem=Path(entry.video_name).stem, video_name=entry.video_name,
            summary_text=summary_text, analysis=analysis, settings=self.settings,
            duration=(getattr(entry, "duration", "") or ""
                      or _duration_from_transcript(transcript_text)),
            summary_version=int(getattr(chosen_summary, "version", 1) or 1),
            analysis_version=int(getattr(chosen_analysis, "version", 1) or 1),
            transcript_text=transcript_text, language=self.language,
            kinds=(kind,))
        self.lbl_export_status.setText(self._t("exporting"))
        self.btn_obsidian.setEnabled(False)
        self._ow = ObsidianWorker(vault, kwargs, parent=self)
        self._ow.done.connect(self._on_obsidian_done)
        self._ow.start()

    def _on_obsidian_done(self, ok: bool, path: str, error: str) -> None:
        self.btn_obsidian.setEnabled(True)
        if ok:
            self.lbl_export_status.setText(self._t("obs_done").format(name=Path(path).name))
        else:
            self.lbl_export_status.setText(self._t("obs_err").format(err=error))

    # -- theme / language ---------------------------------------------
    def apply_theme(self, theme: str) -> None:
        self.theme = theme
        app = QApplication.instance()
        if app:
            app.setStyleSheet(build_stylesheet(theme))

    def toggle_theme(self) -> None:
        self.apply_theme("light" if self.theme == "dark" else "dark")
        self.settings["theme"] = self.theme
        if self._persist_ui_preferences:
            config.save_settings(self.settings)

    def toggle_language(self) -> None:
        self.language = "en" if self.language == "ru" else "ru"
        # Keep settings in sync so newly-queued jobs emit progress details in the
        # current UI language (the pipeline reads settings["language"]).
        self.settings["language"] = self.language
        if self._persist_ui_preferences:
            config.save_settings(self.settings)
        self.retranslate()
        # The panel must follow the language even with nothing loaded: gating this
        # on _current_analysis left its "analysis will appear here" placeholder in
        # the previous language on every meeting that has no analysis yet.
        self.analysis_widget.set_language(self.language)
        if self._current_analysis:
            self.analysis_widget.load(self._current_analysis)
        # retranslate already calls _update_speakers_button via btn_speakers.setText path

    def _check_configured_model(self) -> None:
        """On startup, warn (non-blocking) if the configured engine/model is not
        on disk, and offer to open Settings to download it. Read-only; any
        failure of the check itself is silent (never nag on a backend hiccup)."""
        engine = str(self.settings.get("transcriptionEngine", ""))
        model = str(self.settings.get("whisperModel", ""))
        if not engine or not model:
            return
        try:
            import subprocess
            proc = subprocess.run(
                [str(paths.python_executable()), str(paths.MODELS_CLI_SCRIPT),
                 "available", "--engine", engine, "--model", model],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30)
            data = json.loads((proc.stdout or "").strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            return        # never nag if the check itself fails
        if data.get("available"):
            return
        ans = QMessageBox.question(
            self, self._t("model_missing_title"),
            self._t("model_missing_msg").format(engine=engine, model=model))
        if ans == QMessageBox.StandardButton.Yes:
            self._open_settings()

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, language=self.language, parent=self)
        if dlg.exec() and dlg.result_settings is not None:
            # the dialog updated self.settings in place; reflect any side effects
            self.settings = dlg.result_settings
            self._refresh_workers_label()

    def retranslate(self) -> None:
        # Timelines rebuilt from history are cached per job; they were built in
        # the previous language, so drop the cached ones (jobs that ran in THIS
        # session keep their live lines, which the signals already translate).
        for job_id in [j for j in self._stages_by_job
                       if j not in self._live_by_job]:
            self._stages_by_job.pop(job_id, None)
        self.setWindowTitle(self._t("title"))
        self.btn_select.setText(self._t("select"))
        self.lbl_drop.setText("📁  " + self._t("drop"))
        self.lbl_hint.setText(self._t("hint"))
        self.ed_url.setPlaceholderText(self._t("url_ph"))
        self.btn_url.setText(self._t("add_url"))
        self.btn_process.setText(self._t("process"))
        self.btn_cancel.setText(self._t("cancel"))
        self.btn_remove.setText(self._t("remove"))
        self.btn_remove.setToolTip(self._t("remove_tip"))
        self.btn_clear.setText(self._t("clear_queue"))
        self.btn_clear.setToolTip(self._t("clear_tip"))
        self.btn_remove.setToolTip(self._t("remove_tip"))
        self.btn_record.setToolTip(self._t("tip_record"))
        self.btn_diag.setToolTip(self._t("tip_diag"))
        self.btn_stats.setToolTip(self._t("tip_stats"))
        self.btn_settings.setToolTip(self._t("tip_settings"))
        self.btn_theme.setToolTip(self._t("tip_theme"))
        self.btn_lang.setToolTip(self._t("tip_lang"))
        self.lbl_raw.setText(self._t("raw"))
        self.lbl_summary.setText(self._t("summary"))
        self.lbl_export.setText(self._t("export"))
        self.btn_export.setText(self._t("do_export"))
        self.btn_obsidian.setText(self._t("obsidian"))
        self.btn_speakers.setText(self._t("speakers"))
        self.btn_export_speakers.setText(self._t("export_speakers"))
        self.btn_regenerate.setText(self._t("regenerate"))
        self.btn_add_rag.setText(self._t("add_to_rag"))
        self.btn_rag.setText(self._t("rag"))
        self.btn_search.setText(self._t("search_btn"))
        self.edit_project.setPlaceholderText(self._t("project_ph"))
        self._update_speakers_button()
        self._render_device_label()
        for i, key in enumerate(("exp_raw", "exp_summary", "exp_analysis")):
            self.cb_export_kind.setItemText(i, self._t(key))
        # The other language's words are longer ("Транскрипция" vs "Transcript"),
        # so the width has to be re-measured or the new text is cut off instead.
        theme.fit_combo(self.cb_export_kind)
        self.table.setHorizontalHeaderLabels([
            self._t("col_id"), self._t("col_file"), self._t("col_status"),
            self._t("col_progress"), self._t("col_details")])
        # Re-translate each queue row's status cell from its stored JobStatus, and
        # the idle status line — otherwise they keep the old language after a toggle.
        for job_id, row in self._rows.items():
            entry = self.store.get(job_id)
            if not entry:
                continue
            status = (JobStatus(entry.status) if entry.status in JobStatus._value2member_map_
                      else JobStatus.QUEUED)
            item = self.table.item(row, self.COL_STATUS)
            if item is not None:
                item.setText(main_label(status, self.language))
        if self._active_job is not None:
            self._show_status_job(self._active_job)
        elif not (self.queue and self._q_active_count() > 0):
            self.lbl_status.setText(self._t("idle"))
        for label in self.findChildren(QLabel):
            key = label.property("titleKey")
            if key:
                label.setText(self._t(key))
        self._refresh_workers_label()

    # -- drag & drop ---------------------------------------------------
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self._add_files(paths)
