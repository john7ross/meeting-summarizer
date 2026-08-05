"""Settings dialog — full port of the Electron settings modal (and the Advanced
API sub-modal), wired to ``config.load_settings`` / ``config.save_settings``.

Every persisted field maps 1:1 to a real ``settings.json`` key. The dialog edits
a copy of the live settings dict and, on Save, merges the changed fields back so
unknown keys (Electron round-trip data) are preserved, then persists atomically.

Scope: all persisted data fields + the Advanced API modal, with conditional
visibility. Model download/update actions and prompt-template selection,
creation, editing, import/export and deletion are wired to their live backends.
``theme`` / ``language`` are owned by the main window toolbar, not this dialog.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
from typing import Optional


def _utf8_env() -> dict:
    """Child env that forces UTF-8 stdout — otherwise a Windows subprocess emits
    cp1251 and Cyrillic labels come back as mojibake when we decode as UTF-8."""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from .. import config, paths
from ..backend import templates as prompt_templates
from ..core.worker import ModelsWorker

# -- option lists (value must match settings.json / Electron) --------------
ENGINES = [("whisper", "OpenAI Whisper"),
           ("faster-whisper", "Faster-Whisper (2-4x faster)"),
           ("whisperx", "WhisperX (fastest, speakers)"),
           ("vosk", "Vosk (lightweight, offline)"),
           ("sherpa-onnx", "sherpa-onnx (offline, ONNX)"),
           ("whisper-cpp", "whisper.cpp (offline, ggml)"),
           ("funasr", "FunASR (SenseVoice/Paraformer, EN)"),
           ("sherpa-extra", "Extra models (download-only, not bundled)")]
MODELS = [("tiny", "Tiny (75 MB)"), ("base", "Base (142 MB)"),
          ("small", "Small (466 MB)"), ("medium", "Medium (1.5 GB)"),
          ("large", "Large (2.9 GB)")]
LANGS = [("ru", "Русский"), ("en", "English")]
# Language-keyed option lists (values are language-independent; only labels differ).
DIARIZATION = {
    "ru": [("sherpa", "Офлайн (sherpa-onnx, без токена)"),
           ("pyannote", "pyannote (нужен HF-токен)"), ("off", "Выключена")],
    "en": [("sherpa", "Offline (sherpa-onnx, no token)"),
           ("pyannote", "pyannote (needs HF token)"), ("off", "Off")],
}
DEVICES = {
    "ru": [("auto", "Авто (GPU если доступен)"), ("cuda", "GPU (CUDA)"), ("cpu", "CPU")],
    "en": [("auto", "Auto (GPU if available)"), ("cuda", "GPU (CUDA)"), ("cpu", "CPU")],
}
WORKERS = {
    "ru": [("auto", "Авто"), ("1", "1"), ("2", "2"), ("3", "3"), ("4", "4")],
    "en": [("auto", "Auto"), ("1", "1"), ("2", "2"), ("3", "3"), ("4", "4")],
}
_PROV_LOCAL = {
    "ru": "Локальная модель (llama.cpp / LM Studio / Ollama / любой OpenAI-совместимый)",
    "en": "Local model (llama.cpp / LM Studio / Ollama / any OpenAI-compatible)",
}
_PROV_AGENT = {
    "ru": "Локальный агент (Claude Code / Codex / Hermes / свой)",
    "en": "Local agent CLI (Claude Code / Codex / Hermes / custom)",
}
# Ready-made command templates.  The complete system-prompt/transcript envelope
# is piped on stdin, keeping long meetings and editable prompts out of argv.
# Editable — file placeholders remain available for CLIs such as Hermes.
AGENT_PRESETS = [
    (config.CLAUDE_AGENT_COMMAND, "Claude Code"),
    (config.CODEX_AGENT_COMMAND, "Codex"),
    (config.GEMINI_AGENT_COMMAND, "Gemini CLI"),
    (config.HERMES_AGENT_COMMAND, "Hermes agent"),
]


def _providers(lang: str) -> list:
    return [("local", _PROV_LOCAL.get(lang, _PROV_LOCAL["en"])),
            ("agent", _PROV_AGENT.get(lang, _PROV_AGENT["en"])),
            ("openai", "OpenAI (ChatGPT)"),
            ("anthropic", "Anthropic (Claude)"), ("google", "Google (Gemini)"),
            ("xai", "xAI (Grok)"), ("qwen", "Qwen (Alibaba Cloud)"),
            ("mistral", "Mistral AI"), ("deepseek", "DeepSeek"), ("gemma", "Gemma")]
# Suggested models per provider (editable — the user can type any model id). These
# are only convenience presets; the empty first entry means "provider default".
# Preset model ids per provider (verified current mid-2026; the field is editable
# so any id can be typed, and "" means the provider's own default). "-latest"
# aliases are preferred where a provider offers them, so they don't go stale.
MODELS_BY_PROVIDER = {
    "openai": ["", "gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano"],
    "anthropic": ["", "claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
    "google": ["", "gemini-flash-latest", "gemini-3.5-flash", "gemini-2.5-pro", "gemini-2.5-flash"],
    "xai": ["", "grok-4.3", "grok-4.20", "grok-4"],
    "qwen": ["", "qwen-max-latest", "qwen-plus-latest", "qwen-turbo-latest"],
    "mistral": ["", "mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"],
    "deepseek": ["", "deepseek-chat", "deepseek-reasoner"],
    "local": ["", "local-model", "gpt-4o"],
    "gemma": ["", "local-model", "gemma-2-9b", "gemma-2-27b"],
}
# Only Whisper-family engines expose a meaningful "check for model update"; for
# the others the button is hidden (it would only report "not supported").
WHISPER_UPDATE_ENGINES = {"whisper", "faster-whisper", "whisperx"}
TEMPLATES = [("custom", "Custom"), ("general", "General Meeting"),
             ("standup", "Daily Standup"), ("retrospective", "Retrospective"),
             ("planning", "Planning Session"), ("brainstorming", "Brainstorming"),
             ("client", "Client Meeting"), ("interview", "Interview")]

LABELS = {
    "ru": {
        "title": "Настройки", "save": "Сохранить настройки", "cancel": "Отмена",
        "g_whisper": "Настройки Whisper", "engine": "Движок транскрибации:",
        "hint_field": "Словарь/термины:", "hint_ph": "напр.: API, Kubernetes, названия проектов (по умолчанию пусто)",
        "diar": "Диаризация (спикеры, WhisperX):", "hf_token": "HuggingFace-токен:",
        "diar_hint": "Офлайн-диаризация (sherpa-onnx) работает без токена и подходит для большинства. "
                     "pyannote даёт качество выше, но требует бесплатный HuggingFace-токен и принятия условий "
                     "моделей pyannote/segmentation-3.0 и speaker-diarization-3.1.",
        "model": "Модель:", "download": "Скачать модель",
        "lang": "Язык транскрибации:", "device": "Устройство:",
        "output_lang": "Язык саммари/анализа:", "output_auto": "Авто (как транскрипция)",
        "workers": "Параллельных потоков:", "checkUpdate": "Проверить обновление Whisper",
        "g_ai": "AI-провайдер", "provider": "Провайдер:", "apiKey": "API-ключ:",
        "ai_model": "Модель:", "ai_model_ph": "по умолчанию для провайдера",
        "endpoint": "Локальный эндпоинт:", "advanced": "Расширенные настройки API",
        "agent_cmd": "Команда агента:", "agent_cwd": "Рабочая папка (необязательно):",
        "agent_cwd_ph": "например C:\Projects\my-repo — если агенту нужен контекст проекта",
        "agent_hint": "Транскрипт передаётся агенту через stdin, промпт подставляется вместо {prompt}. Подойдёт любой CLI, который читает stdin и печатает ответ. Плейсхолдеры: {prompt}, {prompt_file}, {text_file}. Ключи и модель берутся из настроек самого агента.",
        "localAi": "🤖 Встроенный локальный ИИ (скачать и запустить)",
        "localAi_tip": "Нет своей нейросети? Приложение скачает и запустит её само — саммари и анализ будут работать офлайн, без ключей и настройки.",
        "g_proc": "Обработка AI (саммари и анализ)",
        "analysisSource": "Анализ строить по:",
        "as_summary": "Саммари (быстро)",
        "as_transcript": "Транскрипту (полнее, но медленнее)",
        "analysisSource_tip": "По саммари — быстро (короткий текст), достаточно для задач, "
                              "рисков, категории, протокола. По транскрипту — полнее для цитат "
                              "и технологий, но медленно (весь текст встречи × каждая фича).",
        "chunking": "Включить чанкинг (map-reduce)",
        "chunkWarn": "⚠ Чанкинг режет длинный транскрипт на части перед саммаризацией. "
                     "Настройка нужна для локальных моделей с маленьким контекстным окном "
                     "или для бесплатных тарифов облачных моделей, где слишком длинный запрос "
                     "может отклоняться (к транскрипту добавляется ещё и промпт на саммари/анализ). "
                     "Чанкинг мягко обходит такие ограничения, НО снижает качество: при разбиении "
                     "у модели в момент саммари/анализа нет контекста всей встречи целиком. "
                     "Держите ВЫКЛ, если модель вмещает весь транскрипт (напр. Qwen с окном 262k).\n\n"
                     "Ориентир по объёму (приблизительно): транскрипт часовой встречи ≈ 12–18 тыс. "
                     "токенов на русском и ≈ 7–10 тыс. на английском; сам запрос = транскрипт + "
                     "промпт (ещё ~0.5–1 тыс. токенов). Встреча на 4 часа — соответственно ×4 "
                     "(≈ 50–70 тыс. токенов на русском).",
        "chunkChars": "Порог чанкинга (символы, 0 = по умолчанию):",
        "chunkChars_tip": "Порог в СИМВОЛАХ текста транскрипта. Если транскрипт длиннее порога — "
                          "он режется на части (при включённом чанкинге). 0 = значение по умолчанию.",
        "g_rag_storage": "Хранилище RAG",
        "ragCatalogMode": "Режим каталога:",
        "ragIsolated": "Изолированный (только desktop)",
        "ragShared": "Общий по секретному коду",
        "ragSharedKey": "Секретный код каталога:",
        "ragGenerate": "Создать новый код", "ragCopy": "Копировать",
        "ragSharedHint": "Один и тот же код подключает desktop и выбранный server-аккаунт "
                         "в этой установке к общей базе. Это не синхронизация между "
                         "разными компьютерами. Любой, кто знает код, получает доступ.",
        "ragBadKey": "Секретный код общего RAG-каталога некорректен.",
        "reasoning": "Отключить reasoning (быстрее)",
        "reasoning_tip": "Reasoning — режим, когда модель сначала «думает вслух» (цепочка "
                         "рассуждений) перед ответом. Точнее, но заметно медленнее и дороже по "
                         "токенам. Отключите для скорости на простых саммари; для сложного "
                         "анализа лучше оставить включённым. Работает не у всех моделей.",
        "aiTimeout": "Таймаут запроса (с, 0 = по умолчанию):",
        "aiRetries": "Повторы при обрыве локальной модели:",
        "aiRetryDelay": "Пауза между повторами (с):",
        "gpuHandoff": "Освобождать VRAM под транскрибацию (останавливать локальную LLM)",
        "llamaPort": "Порт локальной LLM (для остановки):",
        "yt_cookies": "Cookies YouTube (браузер, для входа):",
        "g_templates": "Шаблоны промптов", "template": "Выбрать шаблон:",
        "saveTpl": "Сохранить как шаблон", "manageTpl": "Управление шаблонами",
        "tpl_name": "Название шаблона:",
        "g_prompt": "Промпт AI", "useSpeaker": "Промпт с указанием спикеров (для WhisperX)",
        "g_md": "Экспорт Markdown", "enableMd": "Включить экспорт в Markdown",
        "obsidian": "Интеграция с Obsidian", "vault": "Путь к хранилищу Obsidian:",
        "idx": "Обновлять индекс встреч (Meetings/_index/By Date.md)",
        "people": "Создавать заметки People (участники)",
        "topics": "Создавать заметки Topics (проекты/системы)",
        "dataview": "Создавать Dataview-запросы",
        "g_sheets": "Интеграция с Google Sheets", "enableSheets": "Включить экспорт в Google Sheets",
        "sheetsUrl": "URL веб-приложения (/exec):", "copyScript": "Скопировать Apps Script",
        "sheetsToken": "Токен (необязательно):",
        "sheetsToken_ph": "если в скрипте задано свойство SHARED_TOKEN",
        "scriptCopied": "Apps Script скопирован в буфер обмена.",
        "sheetsHelp": "1) В таблице: Расширения → Apps Script, вставьте скрипт (кнопка ниже). "
                      "2) Развернуть → Новое развёртывание → Веб-приложение "
                      "(«От имени: я», «Доступ: все»). 3) Скопируйте URL /exec сюда. "
                      "Строка добавляется автоматически после каждой обработки.",
        "g_adv": "Расширенные AI-функции", "toggle_all": "Включить/выключить все",
        "actionItems": "Извлекать задачи и действия",
        "sentiment": "Анализ тональности и настроения", "categorize": "Автокатегоризация встречи",
        "followup": "Генерировать вопросы к следующей встрече",
        "protocol": "Формальный протокол (ГОСТ/ISO)",
        "memory": "Контекстная память (предыдущие встречи по проекту)",
        "memory_hint": "Подмешивает саммари прошлых встреч ТОЛЬКО того же проекта. Для встреч на другую тему используйте другой проект или отключите эту опцию.",
        "projectId": "Project ID (группировка связанных встреч):",
        "advTitle": "Расширенные настройки API", "advEndpoint": "API endpoint:",
        "advModel": "Имя модели:", "advHeaders": "Заголовки (JSON):",
        "advBody": "Шаблон тела запроса (JSON):",
        "advHint": "Плейсхолдеры: {{model}}, {{prompt}}, {{text}}",
        "badJson": "Некорректный JSON в поле «{field}»:\n{err}",
        "notImpl": " — нет адаптера",
        "st_installed": "✓ Модель установлена",
        "st_missing": "⬇ Модель не скачана",
        "st_noadapter": "Движок пока без адаптера — недоступен",
        "st_nolang": "У этого движка нет модели для выбранного языка — выберите другой движок или язык",
        "st_downloading": "Загрузка модели…",
        "st_checking": "Проверка обновления…",
        "upd_available": "Доступно обновление модели.",
        "upd_uptodate": "Установлена актуальная версия.",
    },
    "en": {
        "title": "Settings", "save": "Save Settings", "cancel": "Cancel",
        "g_whisper": "Whisper Settings", "engine": "Transcription Engine:",
        "hint_field": "Vocabulary/terms:", "hint_ph": "e.g. API, Kubernetes, project names (empty by default)",
        "diar": "Diarization (speakers, WhisperX):", "hf_token": "HuggingFace token:",
        "diar_hint": "Offline diarization (sherpa-onnx) needs no token and suits most users. "
                     "pyannote gives higher quality but requires a free HuggingFace token and accepting the "
                     "terms for pyannote/segmentation-3.0 and speaker-diarization-3.1.",
        "model": "Model:", "download": "Download Model",
        "lang": "Transcription Language:", "device": "Device:",
        "output_lang": "Summary/analysis language:", "output_auto": "Auto (same as transcription)",
        "workers": "Parallel Workers:", "checkUpdate": "Check for Whisper Update",
        "g_ai": "AI Provider", "provider": "Provider:", "apiKey": "API Key:",
        "ai_model": "Model:", "ai_model_ph": "provider default",
        "endpoint": "Local Endpoint:", "advanced": "Advanced API Settings",
        "agent_cmd": "Agent command:", "agent_cwd": "Working directory (optional):",
        "agent_cwd_ph": "e.g. C:\Projects\my-repo — if the agent needs project context",
        "agent_hint": "The transcript is piped to the agent on stdin; the prompt replaces {prompt}. Any CLI that reads stdin and prints an answer works. Placeholders: {prompt}, {prompt_file}, {text_file}. Keys and model come from the agent's own config.",
        "localAi": "🤖 Built-in local AI (download & run)",
        "localAi_tip": "No local model of your own? The app downloads and runs one for you — summary and analysis work offline, no keys, no setup.",
        "g_proc": "AI processing (summary & analysis)",
        "analysisSource": "Build analysis from:",
        "as_summary": "Summary (fast)",
        "as_transcript": "Transcript (fuller, but slower)",
        "analysisSource_tip": "From the summary — fast (short text), enough for action items, "
                              "risks, category, protocol. From the transcript — fuller for quotes "
                              "and technologies, but slow (the whole meeting text × each feature).",
        "chunking": "Enable chunking (map-reduce)",
        "chunkWarn": "⚠ Chunking splits a long transcript into parts before summarizing. "
                     "It's for local models with a small context window, or free cloud tiers "
                     "where an over-long request may be rejected (the summary/analysis prompt is "
                     "sent on top of the transcript). Chunking works around those limits, BUT "
                     "lowers quality: when split, the model has no whole-meeting context at the "
                     "moment it writes the summary/analysis. Keep it OFF if the model fits the "
                     "whole transcript (e.g. Qwen with a 262k window).\n\n"
                     "Rough sizing: a one-hour meeting transcript is ≈ 12–18k tokens in Russian "
                     "and ≈ 7–10k in English; the request = transcript + prompt (another "
                     "~0.5–1k tokens). A 4-hour meeting scales ×4 accordingly.",
        "chunkChars": "Chunk threshold (chars, 0 = default):",
        "chunkChars_tip": "Threshold in CHARACTERS of the transcript text. If the transcript is "
                          "longer than this, it is split into parts (when chunking is on). "
                          "0 = use the default.",
        "g_rag_storage": "RAG storage",
        "ragCatalogMode": "Catalog mode:",
        "ragIsolated": "Isolated (desktop only)",
        "ragShared": "Shared by secret code",
        "ragSharedKey": "Shared catalog secret:",
        "ragGenerate": "Generate new code", "ragCopy": "Copy",
        "ragSharedHint": "The same code connects desktop and the selected server account "
                         "in this installation. It does not sync different computers. "
                         "Anyone who knows the code can access the catalog.",
        "ragBadKey": "The shared RAG catalog secret is invalid.",
        "reasoning": "Disable reasoning (faster)",
        "reasoning_tip": "Reasoning is when the model 'thinks out loud' (a chain of thought) "
                         "before answering. More accurate, but noticeably slower and more "
                         "token-expensive. Turn it off for speed on simple summaries; keep it on "
                         "for complex analysis. Not all models support it.",
        "aiTimeout": "Request timeout (s, 0 = default):",
        "aiRetries": "Retries on local-model failure:",
        "aiRetryDelay": "Delay between retries (s):",
        "gpuHandoff": "Free VRAM for transcription (stop local LLM)",
        "llamaPort": "Local LLM port (to stop):",
        "yt_cookies": "YouTube cookies (browser, for sign-in):",
        "g_templates": "Prompt Templates", "template": "Select Template:",
        "saveTpl": "Save as Template", "manageTpl": "Manage Templates",
        "tpl_name": "Template name:",
        "g_prompt": "AI Prompt", "useSpeaker": "Use speaker-aware prompt (for WhisperX)",
        "g_md": "Markdown Export", "enableMd": "Enable Markdown export",
        "obsidian": "Obsidian integration", "vault": "Obsidian Vault Path:",
        "idx": "Update meeting index (Meetings/_index/By Date.md)",
        "people": "Create People notes (participants)",
        "topics": "Create Topics notes (projects/systems)",
        "dataview": "Create Dataview queries",
        "g_sheets": "Google Sheets Integration", "enableSheets": "Enable Google Sheets export",
        "sheetsUrl": "Web App URL (/exec):", "copyScript": "Copy Apps Script",
        "sheetsToken": "Token (optional):",
        "sheetsToken_ph": "only if the script defines a SHARED_TOKEN property",
        "scriptCopied": "Apps Script copied to clipboard.",
        "sheetsHelp": "1) In the sheet: Extensions → Apps Script, paste the script (button below). "
                      "2) Deploy → New deployment → Web app (Execute as: Me, Access: Anyone). "
                      "3) Copy the /exec URL here. A row is appended automatically after each run.",
        "g_adv": "Advanced AI Features", "toggle_all": "Enable/disable all",
        "actionItems": "Extract action items and tasks",
        "sentiment": "Analyze meeting sentiment and tone", "categorize": "Automatically categorize meeting type",
        "followup": "Generate follow-up questions for next meeting",
        "protocol": "Generate formal meeting protocol (GOST/ISO)",
        "memory": "Contextual memory (remember meetings by project)",
        "memory_hint": "Injects prior summaries from the SAME project only. For meetings on a different topic, use a different project or turn this off.",
        "projectId": "Project ID (group related meetings):",
        "advTitle": "Advanced API Settings", "advEndpoint": "API Endpoint:",
        "advModel": "Model Name:", "advHeaders": "Custom Headers (JSON):",
        "advBody": "Request Body Template (JSON):",
        "advHint": "Placeholders: {{model}}, {{prompt}}, {{text}}",
        "badJson": "Invalid JSON in \"{field}\":\n{err}",
        "notImpl": " — no adapter",
        "st_installed": "✓ Model installed",
        "st_missing": "⬇ Model not downloaded",
        "st_noadapter": "Engine has no adapter yet — unavailable",
        "st_nolang": "This engine has no model for the selected language — pick another engine or language",
        "st_downloading": "Downloading model…",
        "st_checking": "Checking for update…",
        "upd_available": "A model update is available.",
        "upd_uptodate": "You have the latest version.",
    },
}


# -- combobox helpers ------------------------------------------------------
def _shrink_combo(cb: QComboBox) -> QComboBox:
    """Stop a combo from sizing itself to its longest item (a long label like the
    local-provider one otherwise forces a horizontal scrollbar on the dialog). The
    popup still shows full text."""
    cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    cb.setMinimumContentsLength(6)
    from PySide6.QtWidgets import QSizePolicy
    cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return cb


def _combo(options) -> QComboBox:
    cb = QComboBox()
    for value, label in options:
        cb.addItem(label, value)
    return _shrink_combo(cb)


def _combo_set(cb: QComboBox, value) -> None:
    idx = cb.findData(value)
    cb.setCurrentIndex(idx if idx >= 0 else 0)


def _combo_val(cb: QComboBox):
    return cb.currentData()


def _as_int(value, default: int = 0) -> int:
    """Coerce a settings value (possibly str/float/None) to int, else *default*."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _fetch_catalog(python_exe, script) -> Optional[dict]:
    """Ask ``models_cli.py catalog`` for the engine/model catalog. Returns the
    parsed dict, or None on any failure (caller falls back)."""
    try:
        proc = subprocess.run(
            [str(python_exe), str(script), "catalog"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, env=_utf8_env())
    except Exception:                                          # noqa: BLE001
        return None
    out = (proc.stdout or "").strip()
    if proc.returncode == 0 and out:
        try:
            return json.loads(out.splitlines()[-1])
        except ValueError:
            return None
    return None


def _fallback_catalog() -> dict:
    """A registry-free catalog built from the hardcoded option lists, so the
    dialog still works if the backend can't be reached. Availability unknown
    (False); engines all treated as implemented."""
    engines = []
    for eid, lbl in ENGINES:
        if eid == "vosk":
            names = ("vosk-model-small-ru-0.22", "vosk-model-ru-0.22",
                     "vosk-model-small-en-us-0.15", "vosk-model-en-us-0.22")
            models = [{"id": n, "label": {"ru": n, "en": n}, "available": False}
                      for n in names]
            default = "vosk-model-small-ru-0.22"
        else:
            models = [{"id": v, "label": {"ru": l, "en": l}, "available": False}
                      for v, l in MODELS]
            default = "medium"
        engines.append({"id": eid, "label": {"ru": lbl, "en": lbl},
                        "implemented": True, "default_model": default,
                        "models": models})
    return {"engines": engines}


class AdvancedApiDialog(QDialog):
    """Per-provider advanced request settings: endpoint, model, headers (JSON),
    request-body template (JSON with {{model}}/{{prompt}}/{{text}})."""

    def __init__(self, current: dict, language: str = "ru", parent=None):
        super().__init__(parent)
        self._lang = language if language in LABELS else "ru"
        self.result: Optional[dict] = None
        self.setWindowTitle(self._t("advTitle"))
        self.setMinimumWidth(560)

        form = QFormLayout()
        self.ed_endpoint = QLineEdit(str(current.get("endpoint", "")))
        self.ed_endpoint.setPlaceholderText("https://api.example.com/v1/chat/completions")
        self.ed_model = QLineEdit(str(current.get("model", "")))
        self.ed_model.setPlaceholderText("gpt-4o")
        self.ed_headers = QPlainTextEdit(self._dumps(current.get("headers")))
        self.ed_headers.setPlaceholderText(
            '{"Authorization": "Bearer YOUR_KEY", "Content-Type": "application/json"}')
        self.ed_headers.setFixedHeight(96)
        self.ed_body = QPlainTextEdit(self._dumps(current.get("body")))
        self.ed_body.setPlaceholderText(
            '{"model": "{{model}}", "messages": [{"role": "user", "content": "{{text}}"}]}')
        self.ed_body.setFixedHeight(150)

        form.addRow(self._t("advEndpoint"), self.ed_endpoint)
        form.addRow(self._t("advModel"), self.ed_model)
        form.addRow(self._t("advHeaders"), self.ed_headers)
        form.addRow(self._t("advBody"), self.ed_body)

        hint = QLabel(self._t("advHint"))
        hint.setObjectName("hint")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(self._t("save"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self._t("cancel"))
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    def _t(self, key: str) -> str:
        return LABELS[self._lang].get(key, key)

    @staticmethod
    def _dumps(obj) -> str:
        if not obj:
            return ""
        if isinstance(obj, str):
            return obj
        return json.dumps(obj, ensure_ascii=False, indent=2)

    def _parse_json_field(self, text: str, field: str):
        """Return parsed JSON (or None if blank). Raises ValueError with a
        user-facing message on invalid JSON."""
        text = text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError as exc:
            raise ValueError(self._t("badJson").format(field=field, err=exc))

    def _on_save(self) -> None:
        try:
            headers = self._parse_json_field(
                self.ed_headers.toPlainText(), self._t("advHeaders"))
            body = self._parse_json_field(
                self.ed_body.toPlainText(), self._t("advBody"))
        except ValueError as exc:
            QMessageBox.warning(self, self._t("advTitle"), str(exc))
            return
        result = {
            "endpoint": self.ed_endpoint.text().strip(),
            "model": self.ed_model.text().strip(),
        }
        if headers is not None:
            result["headers"] = headers
        if body is not None:
            result["body"] = body
        self.result = result
        self.accept()


def _labeled_row(label_text: str, field: QWidget) -> QWidget:
    """A single horizontal 'label: field' row as one widget (so it can be
    shown/hidden as a unit for conditional visibility)."""
    row = QWidget()
    box = QHBoxLayout(row)
    box.setContentsMargins(0, 0, 0, 0)
    lab = QLabel(label_text)
    lab.setMinimumWidth(190)
    box.addWidget(lab)
    box.addWidget(field, 1)
    return row


class ManageTemplatesDialog(QDialog):
    """Manage prompt templates. Built-ins are listed too and CAN be edited —
    editing a built-in saves your version as a new user template (the original
    stays as reference); user templates are edited/renamed in place."""

    _L = {
        "ru": {"title": "Управление шаблонами", "edit": "Редактировать", "delete": "Удалить",
               "export": "Экспорт…", "import": "Импорт…", "close": "Закрыть",
               "hint": "Встроенные шаблоны можно редактировать — сохранится ваша копия, "
                       "оригинал останется. Свои шаблоны правятся на месте.",
               "builtin_tag": "(встроенный)", "copy_suffix": "(моя копия)",
               "cant_delete": "Встроенный шаблон удалить нельзя — можно отредактировать "
                              "(создастся ваша копия) или удалить свою копию.",
               "editTitle": "Редактировать шаблон", "editTitleCopy": "Копия встроенного шаблона",
               "tpl_name": "Название:", "tpl_prompt": "Промпт:",
               "ok": "Сохранить", "cancel": "Отмена"},
        "en": {"title": "Manage templates", "edit": "Edit", "delete": "Delete",
               "export": "Export…", "import": "Import…", "close": "Close",
               "hint": "Built-in templates can be edited — your version is saved as a copy "
                       "and the original stays. Your own templates are edited in place.",
               "builtin_tag": "(built-in)", "copy_suffix": "(my copy)",
               "cant_delete": "A built-in template can't be deleted — edit it (a copy is "
                              "created) or delete your own copy.",
               "editTitle": "Edit template", "editTitleCopy": "Copy of a built-in template",
               "tpl_name": "Name:", "tpl_prompt": "Prompt:",
               "ok": "Save", "cancel": "Cancel"},
    }

    def __init__(self, language: str = "ru", use_speaker: bool = False, parent=None):
        super().__init__(parent)
        self._lang = language
        self._use_speaker = use_speaker
        self.setWindowTitle(self._t("title"))
        self.resize(480, 400)
        v = QVBoxLayout(self)
        hint = QLabel(self._t("hint"))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _: self._edit())
        v.addWidget(self.list, 1)
        self._reload()
        row = QHBoxLayout()
        b_edit = QPushButton(self._t("edit")); b_edit.clicked.connect(self._edit)
        b_del = QPushButton(self._t("delete")); b_del.clicked.connect(self._delete)
        b_exp = QPushButton(self._t("export")); b_exp.clicked.connect(self._export)
        b_imp = QPushButton(self._t("import")); b_imp.clicked.connect(self._import)
        row.addWidget(b_edit); row.addWidget(b_del); row.addWidget(b_exp); row.addWidget(b_imp)
        row.addStretch(1)
        b_close = QPushButton(self._t("close")); b_close.clicked.connect(self.accept)
        row.addWidget(b_close)
        v.addLayout(row)

    def _t(self, key):
        return self._L.get(self._lang, self._L["ru"]).get(key, key)

    def _reload(self):
        self.list.clear()
        self.list.setEnabled(True)
        # Built-ins first (marked), then user templates (marked with a bullet).
        for t in prompt_templates.all_templates(self._lang, self._use_speaker):
            suffix = ("   " + self._t("builtin_tag")) if t["builtin"] else "  •"
            item = QListWidgetItem(t["name"] + suffix)
            item.setData(Qt.UserRole, t)      # the whole template dict
            self.list.addItem(item)

    def _edit(self):
        it = self.list.currentItem()
        if not it:
            return
        t = it.data(Qt.UserRole) or {}
        is_builtin = bool(t.get("builtin"))
        old_name = "" if is_builtin else t.get("name", "")   # "" => save as NEW
        start_name = t.get("name", "")
        if is_builtin:
            start_name = f"{start_name} {self._t('copy_suffix')}"

        dlg = QDialog(self)
        dlg.setWindowTitle(self._t("editTitleCopy") if is_builtin else self._t("editTitle"))
        dlg.resize(560, 420)
        v = QVBoxLayout(dlg)
        ed_name = QLineEdit(start_name)
        ed_prompt = QPlainTextEdit(t.get("prompt", ""))
        v.addWidget(QLabel(self._t("tpl_name"))); v.addWidget(ed_name)
        v.addWidget(QLabel(self._t("tpl_prompt"))); v.addWidget(ed_prompt, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText(self._t("ok"))
        bb.button(QDialogButtonBox.Cancel).setText(self._t("cancel"))
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        new_name = ed_name.text().strip()
        if not new_name:
            return
        try:
            prompt_templates.save_user(new_name, ed_prompt.toPlainText(), old_name=old_name)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, self._t("title"), str(exc))
            return
        self._reload()

    def _delete(self):
        it = self.list.currentItem()
        if not it:
            return
        t = it.data(Qt.UserRole) or {}
        if t.get("builtin"):
            QMessageBox.information(self, self._t("title"), self._t("cant_delete"))
            return
        prompt_templates.delete_user(t.get("name", ""))
        self._reload()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, self._t("export"), "prompt_templates.json", "JSON (*.json)")
        if path:
            prompt_templates.export_user(path)

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, self._t("import"), "", "JSON (*.json)")
        if path:
            try:
                prompt_templates.import_user(path)
                self._reload()
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, self._t("title"), str(exc))


class SettingsDialog(QDialog):
    """Full settings editor. Edits a copy of the live settings dict and, on
    Save, merges changes back (preserving unknown keys) and persists via
    ``config.save_settings``. The updated dict is exposed as ``result_settings``
    and the original live dict is updated in place so the app stays consistent.
    """

    def __init__(self, settings: dict, language: str = "ru", parent=None, *,
                 catalog=None, python_exe=None, models_cli_script=None):
        super().__init__(parent)
        self._orig = settings
        self._lang = language if language in LABELS else "ru"
        self.result_settings: Optional[dict] = None
        self._adv_pending: dict[str, dict] = {}

        # The engine/model catalog drives the engine + model selectors. It is
        # injected in tests; otherwise fetched from the backend (one shot), with
        # a static fallback so the dialog still works offline.
        self._python_exe = python_exe or paths.python_executable()
        self._models_cli = models_cli_script or paths.MODELS_CLI_SCRIPT
        if catalog is None:
            catalog = _fetch_catalog(self._python_exe, self._models_cli) or _fallback_catalog()
        self._catalog_engines = catalog.get("engines") or _fallback_catalog()["engines"]
        self._busy = False
        self._workers: list = []
        # Prompt state: which template is active, and the set of pristine built-in
        # texts (so language/speaker changes re-render an *unedited* built-in prompt
        # but never clobber a prompt the user has hand-customized).
        self._current_tpl_id = "custom"
        self._known_builtin_texts = prompt_templates.all_builtin_texts()

        self.setWindowTitle(self._t("title"))
        # Only the scroll viewport should shrink on a short display.  A 720 px
        # minimum pushed the fixed Save/Cancel row below a 768p work area.
        # Width follows the widest row the form actually needs (~850 px), so the
        # dialog never opens with a horizontal scrollbar; the height stays low so
        # the fixed Save/Cancel row keeps fitting a 768p work area.
        self.setMinimumSize(900, 480)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # The body is a vertical form and must follow the viewport width. The
        # scrollbar stays "as needed" rather than forced off so nothing can ever
        # be clipped out of reach - the minimum width above is what keeps it from
        # appearing at all.
        body = QWidget()
        self._v = QVBoxLayout(body)
        self._v.setContentsMargins(18, 18, 18, 18)
        self._v.setSpacing(14)

        self._build_whisper()
        self._build_ai()
        self._build_ai_processing()
        self._build_templates()
        self._build_prompt()
        self._build_markdown()
        self._build_sheets()
        self._build_rag_storage()
        self._build_advanced_ai()
        self._v.addStretch(1)
        scroll.setWidget(body)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(self._t("save"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self._t("cancel"))
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(scroll, 1)
        root.addWidget(buttons)

        self._load()
        self._wire()
        self._apply_visibility()
        self._fit_to_content(scroll)

    def _fit_to_content(self, scroll: QScrollArea, cap: int = 1000) -> None:
        """Widen the dialog to the width its own form needs.

        The 900 px above was measured once, by hand, against one font; the form has
        grown since and the body then needed 918 px inside an 866 px viewport - a
        horizontal scrollbar on every open. Measure instead, and re-measure after
        the stylesheet lands (it enlarges fonts, so anything measured during
        construction is measured against the wrong metrics). Capped so the dialog
        still fits a small screen; past the cap the scroll area does its job.
        """
        from PySide6.QtCore import QTimer

        def _apply() -> None:
            body = scroll.widget()
            if body is None:
                return
            extra = (scroll.verticalScrollBar().sizeHint().width()
                     + 2 * scroll.frameWidth()
                     + self.layout().contentsMargins().left()
                     + self.layout().contentsMargins().right())
            needed = min(cap, body.minimumSizeHint().width() + extra)
            if self.minimumWidth() < needed:
                self.setMinimumWidth(needed)
                if self.width() < needed:
                    self.resize(needed, self.height())

        def _later() -> None:
            try:
                _apply()
            except RuntimeError:      # the dialog was closed before this fired
                pass

        _apply()
        QTimer.singleShot(0, _later)

    def _t(self, key: str) -> str:
        return LABELS[self._lang].get(key, key)

    def _group(self, title_key: str) -> QVBoxLayout:
        box = QGroupBox(self._t(title_key))
        inner = QVBoxLayout(box)
        inner.setSpacing(8)
        self._v.addWidget(box)
        return inner

    # -- groups --------------------------------------------------------
    def _build_whisper(self) -> None:
        g = self._group("g_whisper")
        self.cb_engine = self._build_engine_combo()
        self.cb_model = _shrink_combo(QComboBox())   # populated from the catalog in _load
        self.cb_lang = _combo(LANGS)
        self.cb_device = _combo(DEVICES[self._lang])
        self.cb_workers = _combo(WORKERS[self._lang])
        self.lbl_model_status = QLabel("")
        self.lbl_model_status.setObjectName("hint")
        self.btn_download = QPushButton(self._t("download"))
        self.btn_check = QPushButton(self._t("checkUpdate"))
        actions = QHBoxLayout()
        actions.addWidget(self.btn_download)
        actions.addWidget(self.btn_check)
        actions.addStretch(1)
        g.addWidget(_labeled_row(self._t("engine"), self.cb_engine))
        g.addWidget(_labeled_row(self._t("model"), self.cb_model))
        g.addWidget(self.lbl_model_status)
        g.addLayout(actions)
        g.addWidget(_labeled_row(self._t("lang"), self.cb_lang))
        self.cb_output_lang = _combo([("auto", self._t("output_auto")),
                                      ("ru", "Русский"), ("en", "English")])
        g.addWidget(_labeled_row(self._t("output_lang"), self.cb_output_lang))
        g.addWidget(_labeled_row(self._t("device"), self.cb_device))
        g.addWidget(_labeled_row(self._t("workers"), self.cb_workers))
        self.ed_hint = QLineEdit()
        self.ed_hint.setPlaceholderText(self._t("hint_ph"))
        g.addWidget(_labeled_row(self._t("hint_field"), self.ed_hint))
        # Speaker diarization (WhisperX only): offline sherpa-onnx by default (no
        # token), or pyannote for users who provide their own gated HF token.
        self.cb_diar = _combo(DIARIZATION[self._lang])
        g.addWidget(_labeled_row(self._t("diar"), self.cb_diar))
        self.ed_hf_token = QLineEdit()
        self.ed_hf_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_hf_token.setPlaceholderText("hf_...")
        self.w_hf_token = _labeled_row(self._t("hf_token"), self.ed_hf_token)
        g.addWidget(self.w_hf_token)
        self.lbl_diar_hint = QLabel(self._t("diar_hint"))
        self.lbl_diar_hint.setObjectName("hint")
        self.lbl_diar_hint.setWordWrap(True)
        g.addWidget(self.lbl_diar_hint)

    def _build_engine_combo(self) -> QComboBox:
        cb = _shrink_combo(QComboBox())
        for e in self._catalog_engines:
            label = self._engine_label(e)
            if not e.get("implemented", True):
                label += self._t("notImpl")
            cb.addItem(label, e["id"])
            if not e.get("implemented", True):       # listed but not selectable
                item = cb.model().item(cb.count() - 1)
                if item is not None:
                    item.setEnabled(False)
        return cb

    def _build_ai(self) -> None:
        g = self._group("g_ai")
        self.cb_provider = _combo(_providers(self._lang))
        self.cb_ai_model = _shrink_combo(QComboBox())
        self.cb_ai_model.setEditable(True)
        self.cb_ai_model.setPlaceholderText(self._t("ai_model_ph"))
        self.ed_apikey = QLineEdit()
        self.ed_apikey.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_apikey.setPlaceholderText("sk-...")
        self.ed_endpoint = QLineEdit()
        self.ed_endpoint.setPlaceholderText("http://localhost:1234/v1")
        self.btn_advanced = QPushButton(self._t("advanced"))
        self.btn_local_ai = QPushButton(self._t("localAi"))
        self.btn_local_ai.setToolTip(self._t("localAi_tip"))
        self.btn_local_ai.clicked.connect(self._open_local_ai)
        # Local agent CLI: an editable command template + working directory.
        self.cb_agent_cmd = QComboBox()
        self.cb_agent_cmd.setEditable(True)
        # Presets are whole command lines; a combo sized to its longest item made
        # the WHOLE settings body 2600 px wide, so the dialog always had a
        # horizontal scrollbar no matter how large the window was.
        self.cb_agent_cmd.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cb_agent_cmd.setMinimumContentsLength(24)
        for cmd, label in AGENT_PRESETS:
            self.cb_agent_cmd.addItem(f"{label}   ({cmd})", cmd)
        # The dropdown itself may be wide - it is a popup, it does not stretch the
        # dialog - but elide so it cannot run off the screen either.
        self.cb_agent_cmd.view().setTextElideMode(Qt.TextElideMode.ElideRight)
        self.cb_agent_cmd.view().setMinimumWidth(520)
        # Picking a preset must put the COMMAND (itemData) in the edit line, not the
        # display label. Qt overwrites the line edit with the item's display text
        # *after* the signal, so defer via singleShot to win that race.
        self.cb_agent_cmd.activated.connect(
            lambda i: QTimer.singleShot(
                0, lambda: self.cb_agent_cmd.setEditText(
                    str(self.cb_agent_cmd.itemData(i) or self.cb_agent_cmd.currentText()))))
        self.ed_agent_cwd = QLineEdit()
        self.ed_agent_cwd.setPlaceholderText(self._t("agent_cwd_ph"))
        self.w_agent_cmd = _labeled_row(self._t("agent_cmd"), self.cb_agent_cmd)
        self.w_agent_cwd = _labeled_row(self._t("agent_cwd"), self.ed_agent_cwd)
        self.lbl_agent_hint = QLabel(self._t("agent_hint"))
        self.lbl_agent_hint.setObjectName("hint")
        self.lbl_agent_hint.setWordWrap(True)
        self.w_ai_model = _labeled_row(self._t("ai_model"), self.cb_ai_model)
        self.w_apikey = _labeled_row(self._t("apiKey"), self.ed_apikey)
        self.w_endpoint = _labeled_row(self._t("endpoint"), self.ed_endpoint)
        g.addWidget(_labeled_row(self._t("provider"), self.cb_provider))
        g.addWidget(self.w_ai_model)
        g.addWidget(self.w_apikey)
        g.addWidget(self.w_endpoint)
        g.addWidget(self.w_agent_cmd)
        g.addWidget(self.w_agent_cwd)
        g.addWidget(self.lbl_agent_hint)
        g.addWidget(self.btn_advanced)
        g.addWidget(self.btn_local_ai)

    def _open_local_ai(self) -> None:
        """Built-in local AI: download+run a model for users without their own."""
        from .local_ai_dialog import LocalAiDialog
        dlg = LocalAiDialog(self._orig, language=self._lang, parent=self)
        dlg.exec()
        # The dialog may have rewired provider/endpoint — reflect that in the form.
        _combo_set(self.cb_provider, self._orig.get("aiProvider", "local"))
        self.ed_endpoint.setText(str(self._orig.get("localEndpoint", "")))
        self._apply_visibility()

    @staticmethod
    def _spin(minimum: int, maximum: int) -> QSpinBox:
        sb = QSpinBox()
        sb.setRange(minimum, maximum)
        sb.setMaximumWidth(160)
        return sb

    def _build_ai_processing(self) -> None:
        """Runtime AI controls (parity with the web cabinet): chunking opt-out with
        a quality warning, reasoning toggle, request timeout, local-model retries,
        and the VRAM hand-off that stops the local LLM during GPU transcription."""
        g = self._group("g_proc")
        self.cb_analysis_source = _combo([("summary", self._t("as_summary")),
                                          ("transcript", self._t("as_transcript"))])
        _as_row = _labeled_row(self._t("analysisSource"), self.cb_analysis_source)
        _as_row.setToolTip(self._t("analysisSource_tip"))
        self.cb_analysis_source.setToolTip(self._t("analysisSource_tip"))
        g.addWidget(_as_row)
        self.chk_chunking = QCheckBox(self._t("chunking"))
        g.addWidget(self.chk_chunking)
        self.lbl_chunk_warn = QLabel(self._t("chunkWarn"))
        self.lbl_chunk_warn.setObjectName("warning")
        self.lbl_chunk_warn.setWordWrap(True)
        g.addWidget(self.lbl_chunk_warn)
        self.sp_chunk_chars = self._spin(0, 100_000_000)
        self.sp_chunk_chars.setToolTip(self._t("chunkChars_tip"))
        _chunk_row = _labeled_row(self._t("chunkChars"), self.sp_chunk_chars)
        _chunk_row.setToolTip(self._t("chunkChars_tip"))
        g.addWidget(_chunk_row)

        self.chk_reasoning = QCheckBox(self._t("reasoning"))
        self.chk_reasoning.setToolTip(self._t("reasoning_tip"))
        g.addWidget(self.chk_reasoning)
        self.sp_timeout = self._spin(0, 100_000)
        g.addWidget(_labeled_row(self._t("aiTimeout"), self.sp_timeout))
        self.sp_retries = self._spin(0, 100)
        g.addWidget(_labeled_row(self._t("aiRetries"), self.sp_retries))
        self.sp_retry_delay = self._spin(0, 3600)
        g.addWidget(_labeled_row(self._t("aiRetryDelay"), self.sp_retry_delay))

        self.chk_gpu = QCheckBox(self._t("gpuHandoff"))
        g.addWidget(self.chk_gpu)
        self.sp_llama_port = self._spin(1, 65535)
        self.w_llama_port = _labeled_row(self._t("llamaPort"), self.sp_llama_port)
        g.addWidget(self.w_llama_port)
        self.cb_yt_cookies = _combo([("auto", "auto"), ("off", "off"), ("chrome", "chrome"),
                                     ("firefox", "firefox"), ("edge", "edge"),
                                     ("brave", "brave"), ("opera", "opera")])
        g.addWidget(_labeled_row(self._t("yt_cookies"), self.cb_yt_cookies))

    def _build_templates(self) -> None:
        g = self._group("g_templates")
        self.cb_template = QComboBox()
        self._reload_templates()
        # `activated` fires only on a real user pick (not programmatic changes),
        # so populating/loading never clobbers the prompt field.
        self.cb_template.activated.connect(self._on_template_selected)
        g.addWidget(_labeled_row(self._t("template"), self.cb_template))
        row = QHBoxLayout()
        self.btn_save_tpl = QPushButton(self._t("saveTpl"))
        self.btn_save_tpl.clicked.connect(self._on_save_template)
        self.btn_manage_tpl = QPushButton(self._t("manageTpl"))
        self.btn_manage_tpl.clicked.connect(self._on_manage_templates)
        row.addWidget(self.btn_save_tpl)
        row.addWidget(self.btn_manage_tpl)
        row.addStretch(1)
        g.addLayout(row)

    def _reload_templates(self) -> None:
        """(Re)fill the template selector: built-in library + user templates.

        Built-ins expose the speaker-aware variant when the ``useSpeaker`` box is
        ticked. The currently active template stays selected across a reload (so
        toggling speaker mode never silently jumps back to the first item)."""
        use_speaker = getattr(self, "chk_speaker", None) is not None and self.chk_speaker.isChecked()
        self.cb_template.blockSignals(True)
        self.cb_template.clear()
        sel = 0
        for i, t in enumerate(prompt_templates.all_templates(self._lang, use_speaker)):
            label = t["name"] + ("" if t["builtin"] else "  •")
            self.cb_template.addItem(label, t)   # store the template dict as data
            if t.get("id") == self._current_tpl_id:
                sel = i
        self.cb_template.setCurrentIndex(sel)
        self.cb_template.blockSignals(False)

    def _on_template_selected(self, index: int) -> None:
        t = self.cb_template.itemData(index)
        if not t:
            return
        self._current_tpl_id = t.get("id", "custom")
        # Explicit pick: always load the template's text (in the current
        # language + speaker mode).
        self._render_prompt(force=True)

    def _prompt_lang(self) -> str:
        """Language the visible prompt should be written in: the chosen output
        language, or (when 'auto') the transcription language."""
        o = _combo_val(self.cb_output_lang)
        if o in ("ru", "en"):
            return o
        tl = _combo_val(self.cb_lang)
        return tl if tl in ("ru", "en") else "ru"

    def _render_prompt(self, force: bool = False) -> None:
        """Set the prompt field from the active template in the current language
        and speaker mode. When *force* is False, only a pristine built-in prompt is
        replaced — a prompt the user has hand-edited is left untouched."""
        speaker = self.chk_speaker.isChecked()
        new = prompt_templates.template_prompt(
            self._current_tpl_id, self._prompt_lang(), speaker,
            fallback=self.ed_prompt.toPlainText())
        cur = self.ed_prompt.toPlainText().strip()
        if force or not cur or cur in self._known_builtin_texts:
            self.ed_prompt.setPlainText(new)

    def _on_save_template(self) -> None:
        name, ok = QInputDialog.getText(self, self._t("saveTpl"), self._t("tpl_name"))
        if not ok or not name.strip():
            return
        prompt_templates.save_user(name.strip(), self.ed_prompt.toPlainText())
        self._reload_templates()
        idx = next((i for i in range(self.cb_template.count())
                    if (self.cb_template.itemData(i) or {}).get("name") == name.strip()
                    and not (self.cb_template.itemData(i) or {}).get("builtin")), -1)
        if idx >= 0:
            self.cb_template.setCurrentIndex(idx)

    def _on_manage_templates(self) -> None:
        use_speaker = self.chk_speaker.isChecked() if hasattr(self, "chk_speaker") else False
        ManageTemplatesDialog(self._lang, use_speaker=use_speaker, parent=self).exec()
        self._reload_templates()

    def _build_prompt(self) -> None:
        g = self._group("g_prompt")
        self.chk_speaker = QCheckBox(self._t("useSpeaker"))
        # Toggling speaker-mode swaps built-in templates to their speaker-aware
        # variant: re-fill the selector AND re-render the prompt so the visible
        # text actually gains/loses the speaker instructions.
        self.chk_speaker.toggled.connect(self._reload_templates)
        self.chk_speaker.toggled.connect(lambda *_: self._render_prompt(force=False))
        self.ed_prompt = QPlainTextEdit()
        self.ed_prompt.setMinimumHeight(180)
        g.addWidget(self.chk_speaker)
        g.addWidget(self.ed_prompt)

    def _build_markdown(self) -> None:
        g = self._group("g_md")
        self.chk_md = QCheckBox(self._t("enableMd"))
        self.chk_obsidian = QCheckBox(self._t("obsidian"))
        g.addWidget(self.chk_md)
        g.addWidget(self.chk_obsidian)
        self.ed_vault = QLineEdit()
        self.ed_vault.setPlaceholderText(r"C:\Users\You\Documents\Obsidian Vault")
        self.chk_idx = QCheckBox(self._t("idx"))
        self.chk_people = QCheckBox(self._t("people"))
        self.chk_topics = QCheckBox(self._t("topics"))
        self.chk_dataview = QCheckBox(self._t("dataview"))
        self.w_obsidian_extra = QWidget()
        ob = QVBoxLayout(self.w_obsidian_extra)
        ob.setContentsMargins(20, 0, 0, 0)
        ob.addWidget(_labeled_row(self._t("vault"), self.ed_vault))
        for chk in (self.chk_idx, self.chk_people, self.chk_topics, self.chk_dataview):
            ob.addWidget(chk)
        g.addWidget(self.w_obsidian_extra)

    def _build_sheets(self) -> None:
        g = self._group("g_sheets")
        self.chk_sheets = QCheckBox(self._t("enableSheets"))
        g.addWidget(self.chk_sheets)
        self.ed_sheets_url = QLineEdit()
        self.ed_sheets_url.setPlaceholderText(
            "https://script.google.com/macros/s/AKfyc…/exec")
        self.w_sheets_extra = QWidget()
        sh = QVBoxLayout(self.w_sheets_extra)
        sh.setContentsMargins(20, 0, 0, 0)
        sh.addWidget(_labeled_row(self._t("sheetsUrl"), self.ed_sheets_url))
        self.ed_sheets_token = QLineEdit()
        self.ed_sheets_token.setPlaceholderText(self._t("sheetsToken_ph"))
        sh.addWidget(_labeled_row(self._t("sheetsToken"), self.ed_sheets_token))
        help_lbl = QLabel(self._t("sheetsHelp"))
        help_lbl.setObjectName("hint")
        help_lbl.setWordWrap(True)
        sh.addWidget(help_lbl)
        btn_copy = QPushButton(self._t("copyScript"))
        btn_copy.clicked.connect(self._copy_apps_script)
        row = QHBoxLayout()
        row.addWidget(btn_copy)
        row.addStretch(1)
        sh.addLayout(row)
        g.addWidget(self.w_sheets_extra)

    def _copy_apps_script(self) -> None:
        from ..backend import gsheets
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(gsheets.APPS_SCRIPT)
        QMessageBox.information(self, self._t("g_sheets"), self._t("scriptCopied"))

    def _build_rag_storage(self) -> None:
        g = self._group("g_rag_storage")
        self.cb_rag_catalog_mode = _combo([
            ("isolated", self._t("ragIsolated")),
            ("shared", self._t("ragShared")),
        ])
        g.addWidget(_labeled_row(self._t("ragCatalogMode"), self.cb_rag_catalog_mode))
        self.ed_rag_shared_key = QLineEdit()
        self.ed_rag_shared_key.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self.w_rag_shared_key = _labeled_row(
            self._t("ragSharedKey"), self.ed_rag_shared_key)
        g.addWidget(self.w_rag_shared_key)
        actions = QHBoxLayout()
        self.btn_rag_generate = QPushButton(self._t("ragGenerate"))
        self.btn_rag_copy = QPushButton(self._t("ragCopy"))
        actions.addWidget(self.btn_rag_generate)
        actions.addWidget(self.btn_rag_copy)
        actions.addStretch(1)
        self.w_rag_shared_actions = QWidget()
        self.w_rag_shared_actions.setLayout(actions)
        g.addWidget(self.w_rag_shared_actions)
        self.lbl_rag_shared_hint = QLabel(self._t("ragSharedHint"))
        self.lbl_rag_shared_hint.setObjectName("hint")
        self.lbl_rag_shared_hint.setWordWrap(True)
        g.addWidget(self.lbl_rag_shared_hint)

    def _generate_rag_key(self) -> None:
        self.ed_rag_shared_key.setText(paths.generate_rag_shared_key())

    def _copy_rag_key(self) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.ed_rag_shared_key.text().strip())

    def _build_advanced_ai(self) -> None:
        g = self._group("g_adv")
        self.chk_action = QCheckBox(self._t("actionItems"))
        self.chk_sentiment = QCheckBox(self._t("sentiment"))
        self.chk_categorize = QCheckBox(self._t("categorize"))
        self.chk_followup = QCheckBox(self._t("followup"))
        self.chk_protocol = QCheckBox(self._t("protocol"))
        self.chk_memory = QCheckBox(self._t("memory"))
        # The analysis feature toggles (memory is a separate concern).
        self._analysis_checks = (self.chk_action, self.chk_sentiment,
                                 self.chk_categorize, self.chk_followup, self.chk_protocol)
        toggle_row = QHBoxLayout()
        self.btn_toggle_analysis = QPushButton(self._t("toggle_all"))
        self.btn_toggle_analysis.clicked.connect(self._toggle_all_analysis)
        toggle_row.addWidget(self.btn_toggle_analysis)
        toggle_row.addStretch(1)
        g.addLayout(toggle_row)
        for chk in (self.chk_action, self.chk_sentiment, self.chk_categorize,
                    self.chk_followup, self.chk_protocol, self.chk_memory):
            g.addWidget(chk)
        self.ed_project = QLineEdit()
        self.ed_project.setPlaceholderText("project-alpha, team-standup")
        self.w_project = _labeled_row(self._t("projectId"), self.ed_project)
        hint = QLabel(self._t("memory_hint"))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        wrap_w = QWidget()
        wl = QVBoxLayout(wrap_w)
        wl.setContentsMargins(20, 0, 0, 0)
        wl.addWidget(self.w_project)
        wl.addWidget(hint)
        self.w_project_wrap = wrap_w
        g.addWidget(wrap_w)

    def _toggle_all_analysis(self) -> None:
        """One click to enable/disable all analysis features. If any is on, turn
        all off; if all are off, turn all on."""
        target = not any(c.isChecked() for c in self._analysis_checks)
        for c in self._analysis_checks:
            c.setChecked(target)

    # -- load / wire / visibility -------------------------------------
    def _load(self) -> None:
        s = self._orig
        _combo_set(self.cb_engine, s.get("transcriptionEngine", "faster-whisper"))
        self._repopulate_models()
        _combo_set(self.cb_model, s.get("whisperModel", "medium"))
        self._refresh_model_status()
        _combo_set(self.cb_lang, s.get("transcriptionLanguage", "ru"))
        _combo_set(self.cb_output_lang, s.get("outputLanguage", "auto"))
        _combo_set(self.cb_device, s.get("whisperDevice", "auto"))
        _combo_set(self.cb_workers, str(s.get("parallelWorkers", "auto")))
        self.ed_hint.setText(str(s.get("transcriptionHint", "")))
        _combo_set(self.cb_diar, s.get("diarizationBackend", "sherpa"))
        self.ed_hf_token.setText(str(s.get("hfToken", "")))
        _combo_set(self.cb_provider, s.get("aiProvider", "local"))
        self._reload_ai_models()
        self.cb_ai_model.setCurrentText(str(s.get("aiModel", "")))
        self.ed_apikey.setText(str(s.get("apiKey", "")))
        self.ed_endpoint.setText(str(s.get("localEndpoint", "")))
        self.cb_agent_cmd.setEditText(str(s.get("agentCommand", "")))
        self.ed_agent_cwd.setText(str(s.get("agentCwd", "")))
        _combo_set(self.cb_analysis_source, s.get("analysisSource", "transcript"))
        self.chk_chunking.setChecked(bool(s.get("chunkingEnabled", False)))
        self.sp_chunk_chars.setValue(_as_int(s.get("chunkChars"), 0))
        self.chk_reasoning.setChecked(bool(s.get("disableReasoning", False)))
        self.sp_timeout.setValue(_as_int(s.get("aiTimeout"), 0))
        self.sp_retries.setValue(_as_int(s.get("aiRetries"), 0))
        self.sp_retry_delay.setValue(_as_int(s.get("aiRetryDelay"), 0))
        self.chk_gpu.setChecked(bool(s.get("gpuHandoff", False)))
        self.sp_llama_port.setValue(_as_int(s.get("llamaPort"), 8080))
        _combo_set(self.cb_yt_cookies, s.get("youtubeCookiesBrowser", "auto"))
        self.chk_speaker.setChecked(bool(s.get("useSpeakerPrompt", True)))
        self.ed_prompt.setPlainText(str(s.get("prompt", "")))
        self.chk_md.setChecked(bool(s.get("enableMarkdownExport", True)))
        self.chk_obsidian.setChecked(bool(s.get("obsidianIntegration", False)))
        self.ed_vault.setText(str(s.get("obsidianVaultPath", "")))
        self.chk_idx.setChecked(bool(s.get("updateMeetingIndex", True)))
        self.chk_people.setChecked(bool(s.get("createPeopleNotes", True)))
        self.chk_topics.setChecked(bool(s.get("createTopicNotes", True)))
        self.chk_dataview.setChecked(bool(s.get("createDataviewQueries", True)))
        self.chk_sheets.setChecked(bool(s.get("googleSheetsIntegration", False)))
        self.ed_sheets_url.setText(str(s.get("googleSheetsUrl", "")))
        self.ed_sheets_token.setText(str(s.get("googleSheetsToken", "")))
        _combo_set(self.cb_rag_catalog_mode, s.get("ragCatalogMode", "isolated"))
        self.ed_rag_shared_key.setText(str(s.get("ragSharedCatalogKey", "")))
        self.chk_action.setChecked(bool(s.get("extractActionItems", True)))
        self.chk_sentiment.setChecked(bool(s.get("analyzeSentiment", True)))
        self.chk_categorize.setChecked(bool(s.get("categorizeAutomatically", True)))
        self.chk_followup.setChecked(bool(s.get("generateFollowupQuestions", True)))
        self.chk_protocol.setChecked(bool(s.get("generateFormalProtocol", True)))
        self.chk_memory.setChecked(bool(s.get("useContextualMemory", False)))
        self.ed_project.setText(str(s.get("projectId", "")))

    def _wire(self) -> None:
        self.cb_engine.currentIndexChanged.connect(self._on_engine_changed)
        self.cb_model.currentIndexChanged.connect(self._refresh_model_status)
        # Output language (and 'auto' -> transcription language) drives the prompt
        # language, so re-render the prompt when either changes.
        self.cb_output_lang.currentIndexChanged.connect(lambda *_: self._render_prompt(force=False))
        self.cb_lang.currentIndexChanged.connect(lambda *_: self._render_prompt(force=False))
        # The model list depends on the language, so it must follow it.
        self.cb_lang.currentIndexChanged.connect(
            lambda *_: (self._repopulate_models(), self._refresh_model_status()))
        self.btn_download.clicked.connect(self._on_download)
        self.btn_check.clicked.connect(self._on_check_update)
        self.cb_provider.currentIndexChanged.connect(self._apply_visibility)
        self.cb_provider.currentIndexChanged.connect(self._reload_ai_models)
        self.cb_diar.currentIndexChanged.connect(self._apply_visibility)
        self.chk_obsidian.toggled.connect(self._apply_visibility)
        self.chk_sheets.toggled.connect(self._apply_visibility)
        self.chk_memory.toggled.connect(self._apply_visibility)
        self.cb_rag_catalog_mode.currentIndexChanged.connect(self._apply_visibility)
        self.btn_rag_generate.clicked.connect(self._generate_rag_key)
        self.btn_rag_copy.clicked.connect(self._copy_rag_key)
        self.btn_advanced.clicked.connect(self._open_advanced)

    def _apply_visibility(self, *_) -> None:
        provider = _combo_val(self.cb_provider)
        is_local, is_agent = provider == "local", provider == "agent"
        self.w_endpoint.setVisible(is_local)
        self.w_apikey.setVisible(not is_local and not is_agent)
        # An agent is driven by a command, not a URL/key/model.
        for w in (self.w_agent_cmd, self.w_agent_cwd, self.lbl_agent_hint):
            w.setVisible(is_agent)
        self.w_ai_model.setVisible(not is_agent)
        self.w_obsidian_extra.setVisible(self.chk_obsidian.isChecked())
        self.w_sheets_extra.setVisible(self.chk_sheets.isChecked())
        self.w_project_wrap.setVisible(self.chk_memory.isChecked())
        self.w_hf_token.setVisible(_combo_val(self.cb_diar) == "pyannote")
        shared_rag = _combo_val(self.cb_rag_catalog_mode) == "shared"
        for w in (self.w_rag_shared_key, self.w_rag_shared_actions,
                  self.lbl_rag_shared_hint):
            w.setVisible(shared_rag)

    def _reload_ai_models(self, *_) -> None:
        """Repopulate the editable AI-model presets for the selected provider. The
        field stays editable so any custom model id can be typed; empty = the
        backend's per-provider default."""
        provider = _combo_val(self.cb_provider)
        self.cb_ai_model.blockSignals(True)
        self.cb_ai_model.clear()
        self.cb_ai_model.addItems(MODELS_BY_PROVIDER.get(provider, [""]))
        self.cb_ai_model.setCurrentText("")
        self.cb_ai_model.blockSignals(False)

    def _open_advanced(self) -> None:
        provider = _combo_val(self.cb_provider)
        base = self._orig.get("advancedSettings", {}) or {}
        current = dict(base.get(provider, {})) if isinstance(base.get(provider), dict) else {}
        current.update(self._adv_pending.get(provider, {}))
        dlg = AdvancedApiDialog(current, language=self._lang, parent=self)
        if dlg.exec() and dlg.result is not None:
            self._adv_pending[provider] = dlg.result

    # -- engine / model catalog ---------------------------------------
    def _engine_label(self, e: dict) -> str:
        lbl = e.get("label") or {}
        return lbl.get(self._lang) or lbl.get("en") or e.get("id", "?")

    def _model_label(self, m: dict) -> str:
        lbl = m.get("label") or {}
        return lbl.get(self._lang) or lbl.get("en") or m.get("id", "?")

    def _engine_entry(self, eid):
        return next((e for e in self._catalog_engines if e.get("id") == eid), None)

    def _models_for_language(self, engine_entry: dict) -> list:
        """Models of this engine that can actually transcribe the chosen language.

        Language-specific engines (vosk, sherpa-onnx, FunASR) tag each model with
        its ``lang``; multilingual ones (the Whisper family) tag none and serve
        every language. Offering the whole list regardless of language is how a
        user picks FunASR - which has no Russian at all - for a Russian meeting
        and gets confident nonsense instead of an error.
        """
        wanted = _combo_val(self.cb_lang)
        models = engine_entry.get("models", [])
        if not wanted or wanted == "auto":
            return models
        return [m for m in models if not m.get("lang") or m.get("lang") == wanted]

    def _repopulate_models(self) -> None:
        e = self._engine_entry(_combo_val(self.cb_engine)) or {}
        previous = _combo_val(self.cb_model)
        self.cb_model.blockSignals(True)
        self.cb_model.clear()
        for m in self._models_for_language(e):
            mark = "  ✓" if m.get("available") else "  ⬇"
            self.cb_model.addItem(self._model_label(m) + mark, m.get("id"))
        if previous:
            _combo_set(self.cb_model, previous)
        self.cb_model.blockSignals(False)

    def _selected(self):
        e = self._engine_entry(_combo_val(self.cb_engine)) or {}
        mid = _combo_val(self.cb_model)
        m = next((x for x in e.get("models", []) if x.get("id") == mid), None)
        return e, m

    def _refresh_model_status(self, *_) -> None:
        e, m = self._selected()
        # "Check for Whisper Update" only applies to Whisper-family engines.
        self.btn_check.setVisible(_combo_val(self.cb_engine) in WHISPER_UPDATE_ENGINES)
        if not bool(e.get("implemented", True)):
            self.lbl_model_status.setText(self._t("st_noadapter"))
            self.cb_model.setEnabled(False)
            self.btn_download.setEnabled(False)
            self.btn_check.setEnabled(False)
            return
        # A language-specific engine can have nothing to offer for the chosen
        # language (FunASR has no Russian at all). Say so instead of leaving an
        # empty dropdown that looks like a glitch.
        if self.cb_model.count() == 0:
            self.lbl_model_status.setText(self._t("st_nolang"))
            self.cb_model.setEnabled(False)
            self.btn_download.setEnabled(False)
            self.btn_check.setEnabled(False)
            return
        self.cb_model.setEnabled(not self._busy)
        available = bool(m.get("available")) if m else False
        if not self._busy:
            self.lbl_model_status.setText(
                self._t("st_installed") if available else self._t("st_missing"))
        self.btn_download.setEnabled(bool(m) and not available and not self._busy)
        self.btn_check.setEnabled(bool(m) and available and not self._busy)

    def _on_engine_changed(self, *_) -> None:
        self._repopulate_models()
        self._refresh_model_status()

    def _set_busy(self, busy: bool, status_key: str = "") -> None:
        self._busy = busy
        if status_key:
            self.lbl_model_status.setText(self._t(status_key))
        if busy:
            self.btn_download.setEnabled(False)
            self.btn_check.setEnabled(False)
            self.cb_engine.setEnabled(False)
            self.cb_model.setEnabled(False)
        else:
            self.cb_engine.setEnabled(True)
            self._refresh_model_status()

    def _model_command(self, op: str) -> list:
        return [str(self._python_exe), str(self._models_cli), op,
                "--engine", _combo_val(self.cb_engine),
                "--model", _combo_val(self.cb_model)]

    def _on_download(self) -> None:
        if not _combo_val(self.cb_model):
            return
        self._set_busy(True, "st_downloading")
        w = ModelsWorker(self._model_command("download"))
        self._workers.append(w)
        w.progress.connect(self._on_dl_progress)
        w.done.connect(self._on_dl_done)
        w.start()

    def _on_dl_progress(self, percent: int, detail: str) -> None:
        self.lbl_model_status.setText(
            f"{detail} ({percent}%)" if detail else f"{percent}%")

    def _on_dl_done(self, ok: bool, result, error: str) -> None:
        if ok:
            self._refresh_catalog()
        else:
            QMessageBox.warning(self, self._t("g_whisper"), error or "download failed")
        self._set_busy(False)

    def _on_check_update(self) -> None:
        if not _combo_val(self.cb_model):
            return
        self._set_busy(True, "st_checking")
        w = ModelsWorker(self._model_command("check-update"))
        self._workers.append(w)
        w.done.connect(self._on_check_done)
        w.start()

    def _on_check_done(self, ok: bool, result, error: str) -> None:
        self._set_busy(False)
        if not ok:
            QMessageBox.warning(self, self._t("checkUpdate"), error or "failed")
            return
        r = result or {}
        if not r.get("supported", False):
            msg = r.get("detail", "")
        elif r.get("update_available"):
            msg = self._t("upd_available")
        else:
            msg = self._t("upd_uptodate")
        QMessageBox.information(self, self._t("checkUpdate"), msg)

    def _refresh_catalog(self) -> None:
        cat = _fetch_catalog(self._python_exe, self._models_cli)
        if cat and cat.get("engines"):
            self._catalog_engines = cat["engines"]
        mid = _combo_val(self.cb_model)
        self._repopulate_models()
        _combo_set(self.cb_model, mid)

    # -- collect / save -----------------------------------------------
    def _agent_command(self) -> str:
        """The agent command to save. The combo lists presets as
        ``"Claude Code   (claude -p {prompt})"`` (display) with the bare command in
        itemData. If the edit line still holds a preset's DISPLAY label (Qt can put
        it there), return the command; otherwise return whatever the user typed."""
        cb = self.cb_agent_cmd
        txt = cb.currentText().strip()
        for i in range(cb.count()):
            if cb.itemText(i) == txt:
                return str(cb.itemData(i) or txt).strip()
        return txt

    def _collect(self) -> dict:
        updated = copy.deepcopy(self._orig)
        updated.update({
            "transcriptionEngine": _combo_val(self.cb_engine),
            "whisperModel": _combo_val(self.cb_model),
            "transcriptionLanguage": _combo_val(self.cb_lang),
            "outputLanguage": _combo_val(self.cb_output_lang),
            "transcriptionHint": self.ed_hint.text().strip(),
            "diarizationBackend": _combo_val(self.cb_diar),
            "hfToken": self.ed_hf_token.text().strip(),
            "whisperDevice": _combo_val(self.cb_device),
            "parallelWorkers": _combo_val(self.cb_workers),
            "aiProvider": _combo_val(self.cb_provider),
            "aiModel": self.cb_ai_model.currentText().strip(),
            "apiKey": self.ed_apikey.text(),
            "localEndpoint": self.ed_endpoint.text().strip(),
            "agentCommand": self._agent_command(),
            "agentCwd": self.ed_agent_cwd.text().strip(),
            "analysisSource": _combo_val(self.cb_analysis_source),
            "chunkingEnabled": self.chk_chunking.isChecked(),
            "chunkChars": self.sp_chunk_chars.value(),
            "disableReasoning": self.chk_reasoning.isChecked(),
            "aiTimeout": self.sp_timeout.value(),
            "aiRetries": self.sp_retries.value(),
            "aiRetryDelay": self.sp_retry_delay.value(),
            "gpuHandoff": self.chk_gpu.isChecked(),
            "llamaPort": self.sp_llama_port.value(),
            "youtubeCookiesBrowser": _combo_val(self.cb_yt_cookies),
            "useSpeakerPrompt": self.chk_speaker.isChecked(),
            "prompt": self.ed_prompt.toPlainText(),
            "enableMarkdownExport": self.chk_md.isChecked(),
            "obsidianIntegration": self.chk_obsidian.isChecked(),
            "obsidianVaultPath": self.ed_vault.text().strip(),
            "updateMeetingIndex": self.chk_idx.isChecked(),
            "createPeopleNotes": self.chk_people.isChecked(),
            "createTopicNotes": self.chk_topics.isChecked(),
            "createDataviewQueries": self.chk_dataview.isChecked(),
            "googleSheetsIntegration": self.chk_sheets.isChecked(),
            "googleSheetsUrl": self.ed_sheets_url.text().strip(),
            "googleSheetsToken": self.ed_sheets_token.text().strip(),
            "ragCatalogMode": _combo_val(self.cb_rag_catalog_mode),
            "ragSharedCatalogKey": self.ed_rag_shared_key.text().strip(),
            "extractActionItems": self.chk_action.isChecked(),
            "analyzeSentiment": self.chk_sentiment.isChecked(),
            "categorizeAutomatically": self.chk_categorize.isChecked(),
            "generateFollowupQuestions": self.chk_followup.isChecked(),
            "generateFormalProtocol": self.chk_protocol.isChecked(),
            "useContextualMemory": self.chk_memory.isChecked(),
            "projectId": self.ed_project.text().strip(),
        })
        adv = updated.get("advancedSettings", {})
        adv = copy.deepcopy(adv) if isinstance(adv, dict) else {}
        for prov, val in self._adv_pending.items():
            base = adv.get(prov, {})
            base = dict(base) if isinstance(base, dict) else {}
            base.update(val)
            adv[prov] = base
        updated["advancedSettings"] = adv
        return updated

    def _on_save(self) -> None:
        updated = self._collect()
        if updated.get("ragCatalogMode") == "shared":
            try:
                paths.validate_rag_shared_key(updated.get("ragSharedCatalogKey", ""))
            except ValueError:
                QMessageBox.warning(self, self._t("g_rag_storage"),
                                    self._t("ragBadKey"))
                return
        config.save_settings(updated)
        # keep the shared live dict consistent (same object the app holds)
        self._orig.clear()
        self._orig.update(updated)
        self.result_settings = self._orig
        self.accept()
