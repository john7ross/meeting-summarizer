"""Application settings: load/save the shared config/settings.json.

The native client reuses the exact same settings file as the Electron app, so
the two front-ends stay interchangeable. On load, missing keys are filled from
DEFAULTS; on save, all existing keys are preserved and writing is atomic.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Optional

from . import paths
from .backend import templates as _templates
from .core.atomic_io import atomic_write_json

HERMES_AGENT_COMMAND = (
    'hermes -z "Read the system instructions from {prompt_file} and the '
    'meeting transcript from {text_file}. Follow the system instructions '
    'exactly and return only the requested answer."'
)
CLAUDE_AGENT_COMMAND = "claude -p"
CODEX_AGENT_COMMAND = "codex exec --sandbox read-only --skip-git-repo-check -"
GEMINI_AGENT_COMMAND = (
    'gemini --skip-trust -p "Follow the system instructions and process the meeting '
    'transcript provided on stdin. Return only the requested answer."'
)

_GEMINI_FILE_AGENT_COMMAND = (
    'gemini --skip-trust -p "Read the system instructions from {prompt_file} and '
    'the meeting transcript from {text_file}. Follow the system instructions '
    'exactly and return only the requested answer."'
)

_LEGACY_AGENT_COMMANDS = {
    "hermes {prompt}": HERMES_AGENT_COMMAND,
    "claude -p {prompt}": CLAUDE_AGENT_COMMAND,
    "codex exec {prompt}": CODEX_AGENT_COMMAND,
    "codex exec --sandbox read-only --skip-git-repo-check {prompt}":
        CODEX_AGENT_COMMAND,
    "gemini -p {prompt}": GEMINI_AGENT_COMMAND,
    _GEMINI_FILE_AGENT_COMMAND: GEMINI_AGENT_COMMAND,
}

DEFAULTS: dict[str, Any] = {
    "whisperModel": "medium",
    "transcriptionEngine": "faster-whisper",
    "transcriptionLanguage": "ru",
    "outputLanguage": "auto",   # summary/analysis language: auto (=transcription) | ru | en
    "analysisSource": "transcript",  # quality-first; summary remains an explicit faster option
    "whisperDevice": "auto",
    "aiProvider": "local",
    "apiKey": "",
    "localEndpoint": "http://localhost:1234/v1",
    # AI processing controls (parity with the web cabinet).
    # Whole-transcript processing is the quality-first default.  Chunking is an
    # explicit opt-in for models whose context window cannot fit the meeting.
    "chunkingEnabled": False,
    "chunkChars": 0,          # map-reduce threshold in chars (0 = ai_client default)
    "disableReasoning": False,  # ask a reasoning model to skip its <think> phase
    "aiTimeout": 0,           # per-request seconds (0 = default 600)
    "aiRetries": 0,           # retry local-model connection failures (watchdog restart)
    "aiRetryDelay": 0,        # base seconds between retries (escalates)
    "gpuHandoff": False,      # stop the local LLM to free VRAM for GPU transcription
    "llamaPort": 8080,        # local LLM port to stop during hand-off
    # Built-in local AI (downloaded on demand; never shipped in the build).
    "localAiPort": 8081,      # separate port so a user's own LLM on 8080 is untouched
    "localAiModel": "",
    # Local agent CLI (provider="agent"): any tool that reads stdin and prints an answer.
    "agentCommand": "",       # e.g. claude -p {prompt} / codex exec {prompt}
    "agentCwd": "",
    "youtubeCookiesBrowser": "auto",  # cookie source for auth-gated URLs (YouTube): auto|off|chrome|firefox|edge|…
    # -- recording & live -------------------------------------------------
    # System audio (WASAPI loopback) is written as the second channel of the
    # recording, so an online meeting captures the other participants too. Off
    # by default: it depends on the machine's output device and on an optional
    # package, and a recorder that silently starts capturing what the speakers
    # play would be a surprise, not a feature.
    "recordSystemAudio": False,
    "liveTranscription": False,   # stream the recording through an engine live
    "liveSummary": False,         # rolling summary while recording (needs the above)
    # Empty = follow the batch transcription settings. A separate choice exists
    # because live and batch optimise for opposite things: a user may well want
    # medium faster-whisper for the archived transcript and a small CPU model
    # live, so the GPU stays free.
    "liveEngine": "",
    "liveModel": "",
    "liveSummaryInterval": 30,    # seconds between summary updates
    # auto   = rebuild from the transcript on a local model, hybrid on a cloud one
    # regen  = always rebuild from the transcript (most accurate, most tokens)
    # hybrid = incremental updates with periodic rebuilds (cheapest that is safe)
    "liveSummaryStrategy": "auto",
    "liveSummaryMaxUpdates": 0,   # 0 = unlimited; a spend cap for metered clouds
    "prompt": _templates.default_prompt("ru"),
    "useSpeakerPrompt": True,
    "theme": "dark",
    "language": "ru",
    "enableMarkdownExport": True,
    "obsidianIntegration": False,
    "obsidianVaultPath": "",
    "updateMeetingIndex": True,
    "createPeopleNotes": True,
    "createTopicNotes": True,
    "createDataviewQueries": True,
    "googleSheetsIntegration": False,
    "googleSheetsUrl": "",
    "googleSheetsToken": "",   # optional SHARED_TOKEN required by the Apps Script
    "googleApiKey": "",
    "extractActionItems": True,
    "analyzeSentiment": True,
    "categorizeAutomatically": True,
    "generateFollowupQuestions": True,
    "generateFormalProtocol": True,
    "useContextualMemory": False,   # opt-in: only mixes prior meetings of the SAME project
    "projectId": "meets",
    "parallelWorkers": "auto",
    # RAG embeddings: default to the self-contained offline model, since a local
    # chat server (llama.cpp/LM Studio) usually cannot serve /v1/embeddings.
    "ragEmbeddingBackend": "sentence-transformers",
    "ragEmbeddingModel": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "ragCatalogMode": "isolated",
    "ragSharedCatalogKey": "",
    "advancedSettings": {},
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_settings(path: Optional[Path] = None) -> dict:
    """Load settings, falling back to DEFAULTS for a missing/invalid file."""
    path = path or paths.SETTINGS_FILE
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return copy.deepcopy(DEFAULTS)
    if not isinstance(data, dict):
        return copy.deepcopy(DEFAULTS)
    merged = _deep_merge(DEFAULTS, data)
    # Historical presets expanded the full editable system prompt into argv.
    # That is lossy and can exceed Windows' command-line limit.  Migrate only
    # exact values shipped by the application; custom commands are untouched.
    legacy_command = str(merged.get("agentCommand", "")).strip()
    if legacy_command in _LEGACY_AGENT_COMMANDS:
        merged["agentCommand"] = _LEGACY_AGENT_COMMANDS[legacy_command]
    return merged


def save_settings(settings: dict, path: Optional[Path] = None) -> None:
    """Atomically persist settings to disk (write temp file, then replace)."""
    path = path or paths.SETTINGS_FILE
    atomic_write_json(path, settings)
