"""Summary & analysis via the existing backend ``ai_client.py`` subprocess.

CLI contract::

    python ai_client.py --provider <p> --endpoint <url> --text-file <path>

The model's text is written to stdout; errors go to stderr with a non-zero
exit code. The same primitive serves both passes: the summary pass (input is
the raw transcript) and the analysis pass (input is the generated summary),
differing only by prompt and input text.

API keys and the editable prompt are supplied in the child-process environment,
not argv, so they are not exposed by Task Manager/WMI process inspection.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Sequence, Union

from .. import paths
from .command import SecureCommand

PROVIDERS = ("local", "agent", "openai", "anthropic", "google", "xai",
             "gemma", "qwen", "mistral", "deepseek")


class AIError(RuntimeError):
    """Raised when the AI subprocess fails or returns nothing usable."""


# Output-language control: summary/analysis may be produced in a language other
# than the transcript's (e.g. an English recording → a Russian summary). When the
# requested OUTPUT language differs from the transcript language, a translate
# directive is appended to the prompt.
OUTPUT_LANG_DIRECTIVE = {
    "ru": "\n\nВАЖНО: подготовь итоговый результат на РУССКОМ языке, даже если "
          "транскрипция на другом языке — переведи содержание на русский.",
    "en": "\n\nIMPORTANT: produce the final result in ENGLISH, even if the "
          "transcript is in another language — translate the content into English.",
}


def resolve_output_language(settings: dict) -> str:
    """The language for summary/analysis OUTPUT. ``outputLanguage`` = 'auto' (or
    unset) → follow the transcription language; else the explicit ru/en override."""
    out = (settings.get("outputLanguage") or "auto").strip().lower()
    if out in ("ru", "en"):
        return out
    tl = (settings.get("transcriptionLanguage") or "ru").strip().lower()
    return tl if tl in ("ru", "en") else "ru"


def apply_output_language(prompt: str, output_language: str,
                          transcription_language: str) -> str:
    """Append the translate directive only when OUTPUT language differs from the
    transcript language; a no-op when they match or nothing is specified."""
    out = (output_language or "").strip().lower()
    src = (transcription_language or "").strip().lower()
    if out in ("ru", "en") and out != src:
        return prompt + OUTPUT_LANG_DIRECTIVE[out]
    return prompt


def build_command(prompt: str, text_file, *, provider="local", api_key="",
                  endpoint="", model="", advanced=None, participants=None,
                  timeout=0, no_think=False, chunk_chars=0, chunk_mode="",
                  no_chunk=False, retries=0, retry_delay=0,
                  output_language="", transcription_language="",
                  agent_command="", agent_cwd="",
                  python_exe=None, ai_client_script=None) -> list[str]:
    """Build the argv for one AI pass. Raises on an unknown provider.

    ``model`` selects the model (empty → the backend's per-provider default);
    ``advanced`` is the per-provider Advanced-API config (dict) for a fully custom
    request — serialised to ``--advanced`` JSON when present. ``timeout`` (seconds,
    0=default) raises the per-request limit for long meetings; ``no_think`` asks a
    reasoning model to skip its <think> phase; ``chunk_chars`` (0=default) tunes the
    map-reduce summary threshold.
    """
    if provider not in PROVIDERS:
        raise ValueError(
            f"Unknown provider {provider!r}; expected one of {PROVIDERS}.")
    python_exe = Path(python_exe) if python_exe else paths.python_executable()
    ai_client_script = (Path(ai_client_script) if ai_client_script
                        else paths.AI_CLIENT_SCRIPT)
    prompt = apply_output_language(prompt, output_language, transcription_language)
    environment = {
        "MEETING_SUMMARIZER_API_KEY": api_key or "",
        "MEETING_SUMMARIZER_PROMPT": prompt,
    }
    command = SecureCommand([
        str(python_exe), str(ai_client_script),
        "--provider", provider,
        "--endpoint", endpoint or "",
        "--text-file", str(text_file),
    ], environment=environment)
    if timeout and int(timeout) > 0:
        command += ["--timeout", str(int(timeout))]
    if no_think:
        command += ["--no-think"]
    if chunk_chars and int(chunk_chars) > 0:
        command += ["--chunk-chars", str(int(chunk_chars))]
    if chunk_mode:
        command += ["--chunk-mode", str(chunk_mode)]
    if no_chunk:
        command += ["--no-chunk"]
    if retries and int(retries) > 0:
        command += ["--retries", str(int(retries))]
        if retry_delay and int(retry_delay) > 0:
            command += ["--retry-delay", str(int(retry_delay))]
    if model:
        command += ["--model", str(model)]
    if advanced:
        import json as _json
        command.environment["MEETING_SUMMARIZER_ADVANCED"] = _json.dumps(
            advanced, ensure_ascii=False)
    if agent_command:
        command += ["--agent-command", str(agent_command)]
    if agent_cwd:
        command += ["--agent-cwd", str(agent_cwd)]
    if participants:
        if isinstance(participants, (list, tuple)):
            participants = ",".join(str(p) for p in participants)
        command += ["--participants", str(participants)]
    return command


def run(prompt: str, text: str, *, provider="local", api_key="", endpoint="",
        model="", advanced=None,
        participants: Optional[Union[str, Sequence[str]]] = None,
        timeout: int = 900, python_exe=None, ai_client_script=None) -> str:
    """Run one AI pass over ``text`` and return the generated text.

    ``text`` is written to a temporary UTF-8 file and passed via ``--text-file``
    (the backend prefers this over inline args to avoid command-length limits).
    """
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="utf-8")
    tmp_path = handle.name
    try:
        handle.write(text)
        handle.close()
        command = build_command(
            prompt, tmp_path, provider=provider, api_key=api_key,
            endpoint=endpoint, model=model, advanced=advanced,
            participants=participants,
            python_exe=python_exe, ai_client_script=ai_client_script)
        proc = subprocess.run(
            command, capture_output=True, encoding="utf-8",
            errors="replace", timeout=timeout,
            env=command.process_environment())
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass
    if proc.returncode != 0:
        message = (proc.stderr or "").strip() or "AI subprocess failed"
        raise AIError(message)
    output = (proc.stdout or "").strip()
    if not output:
        raise AIError("AI returned an empty response")
    return output
