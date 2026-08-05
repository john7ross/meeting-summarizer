#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI client for meeting summary + analysis passes.

Reworked (was a carried-over Electron-era file with hardcoded per-provider models,
a dead ``gemini-pro`` default, an ignored ``advancedSettings`` block and a local
retry path that overwrote the user's prompt with hardcoded Russian text). Now:
  * ``--model`` selects the model per provider (sane current defaults);
  * OpenAI-compatible providers (openai/xai/mistral/deepseek) share one path and
    honour ``--endpoint`` as a base-URL override (compatible gateways/proxies);
  * ``--advanced`` (JSON: endpoint/model/headers/body with {{apiKey}}/{{model}}/
    {{prompt}}/{{text}} placeholders) gives a fully custom request — the Advanced
    API modal is now real;
  * the local 400 fallback keeps the user's prompt (no invented text).
Providers: local, openai, anthropic, google, xai, gemma, qwen, mistral, deepseek.
"""
import argparse
import json
import sys
import os
import subprocess
import time

# Windows console encoding fix
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

try:
    import requests
except ImportError:
    print("Error: requests library not installed", file=sys.stderr)
    sys.exit(1)


class _ModelLoading(Exception):
    """Local server is up but the model is still loading (HTTP 503). Distinct from
    a down server (ConnectionError): this resolves on its own in seconds-to-minutes,
    so it's polled on a short interval rather than the long connection-retry one."""


# Current sane defaults (used only when the caller passes no --model). Kept
# minimal on purpose — the UI offers an editable model list, so these are just
# fallbacks and are trivially overridden.
DEFAULT_MODELS = {
    "openai": "gpt-4o",
    "anthropic": "claude-3-5-sonnet-20241022",
    "google": "gemini-2.5-flash",       # gemini-pro / 1.5 are retired
    "xai": "grok-2-latest",             # grok-beta is retired
    "qwen": "qwen-max",
    "mistral": "mistral-large-latest",
    "deepseek": "deepseek-chat",
    "local": "local-model",
    "gemma": "local-model",
}

# Per-provider default max output tokens (used when --max-tokens is 0 = "auto").
# Local models have huge context (131k/262k) and reasoning models spend tokens on a
# <think> block, so they need a high ceiling or the visible answer gets truncated.
# Cloud APIs cap output (~8k) and may reject an oversized max_tokens — keep them modest.
PROVIDER_MAX_TOKENS = {"local": 32000, "gemma": 32000}
DEFAULT_MAX_TOKENS = 8000

# Map-reduce chunking is a FALLBACK for a transcript that won't fit the model's
# context — it costs some cross-part context (each part is processed without seeing
# the others), so it must trigger RARELY. This default (~48000 chars ≈ 20k tokens of
# Russian) keeps meetings up to ~1h whole on a 32k-context model. Models with a big
# context (e.g. Qwen 262k) should raise it via --chunk-chars / the chunkChars setting
# so even a 4h meeting goes in ONE pass (full context, best quality); chunking then
# only kicks in for extreme lengths.
CHUNK_CHARS = 48000
CHUNK_OVERLAP = 400   # chars carried between parts so nothing is lost at a split


def _split_text_by_lines(text, size, overlap):
    """Split *text* into <= ~*size*-char parts on line boundaries, carrying up to
    *overlap* trailing chars into the next part. Never splits mid-line (transcripts
    are ``[HH:MM:SS] …`` lines)."""
    lines = text.splitlines(keepends=True)
    chunks, cur, cur_len = [], [], 0
    for ln in lines:
        if cur_len + len(ln) > size and cur:
            chunks.append("".join(cur))
            carry, clen = [], 0
            for pln in reversed(cur):           # keep a little tail for continuity
                if clen + len(pln) > overlap:
                    break
                carry.insert(0, pln)
                clen += len(pln)
            cur, cur_len = list(carry), clen
        cur.append(ln)
        cur_len += len(ln)
    if cur:
        chunks.append("".join(cur))
    return chunks or [text]

# Providers speaking the OpenAI /chat/completions dialect (base URL + Bearer key).
OPENAI_COMPAT_BASE = {
    "openai": "https://api.openai.com/v1",
    "xai": "https://api.x.ai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "deepseek": "https://api.deepseek.com/v1",
}


def _substitute(obj, mapping):
    """Recursively replace {{placeholder}} tokens inside a JSON-like structure."""
    if isinstance(obj, str):
        for token, value in mapping.items():
            if token in obj:
                obj = obj.replace(token, value if value is not None else "")
        return obj
    if isinstance(obj, dict):
        return {k: _substitute(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute(v, mapping) for v in obj]
    return obj


def _extract_text(data):
    """Best-effort pull of the generated text from common provider shapes."""
    try:
        return data["choices"][0]["message"]["content"]          # OpenAI-compatible
    except (KeyError, IndexError, TypeError):
        pass
    try:
        return data["content"][0]["text"]                        # Anthropic
    except (KeyError, IndexError, TypeError):
        pass
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]   # Google
    except (KeyError, IndexError, TypeError):
        pass
    try:
        return data["output"]["text"]                            # Qwen/DashScope
    except (KeyError, TypeError):
        pass
    raise Exception(f"could not find generated text in response: {str(data)[:300]}")


def _release_temp_dir(tmp_ctx, attempts=6, delay=0.25):
    """Drop an agent's scratch directory without ever failing the request.

    An agent CLI spawns children of its own; on Windows their handles on the
    working directory can outlive ``subprocess.run``, so ``rmtree`` raises
    ``WinError 32``. Raised from a ``finally`` it REPLACES the already-computed
    answer, which showed up as analysis features failing at random ("ошибок 2
    из 11") on a run whose text was in fact produced successfully. Retry briefly,
    then leave the directory to the OS - a stale temp dir is harmless, a lost
    answer is not.
    """
    import time as _time
    for attempt in range(attempts):
        try:
            tmp_ctx.cleanup()
            return
        except Exception:      # noqa: BLE001 - cleanup must never propagate
            if attempt == attempts - 1:
                return
            _time.sleep(delay)


class AIClient:
    """Universal client for the supported AI providers."""

    def __init__(self, provider, api_key, endpoint, prompt, model="",
                 advanced=None, temperature=0.7, max_tokens=0,
                 timeout=600, no_think=False, chunk_chars=0,
                 chunk_mode="summary", retries=0, retry_delay=60, chunk_enabled=True,
                 agent_command="", agent_cwd=""):
        self.provider = provider
        # Local agent CLI (provider='agent'): command template + working directory.
        self.agent_command = (agent_command or "").strip()
        self.agent_cwd = (agent_cwd or "").strip()
        self.api_key = api_key
        self.endpoint = (endpoint or "").strip()
        self.prompt = prompt
        self.model = model or ""                       # user's explicit choice ("" if none)
        self.default_model = DEFAULT_MODELS.get(provider, "")
        self.advanced = advanced or None
        self.temperature = temperature
        # 0 => auto: a per-provider default (local/gemma get a high ceiling for
        # reasoning models; cloud stays modest). A positive value is an explicit override.
        self.max_tokens = int(max_tokens) if max_tokens and int(max_tokens) > 0 else \
            PROVIDER_MAX_TOKENS.get(provider, DEFAULT_MAX_TOKENS)
        # Per-request read timeout (seconds); long/local meetings may need > 600.
        self.timeout = int(timeout) if timeout and int(timeout) > 0 else 600
        # When True, ask reasoning-capable local models to skip the <think> phase
        # (much faster, some quality trade-off — the user decides via settings).
        self.no_think = bool(no_think)
        # Map-reduce chunking threshold (chars). 0 => the CHUNK_CHARS default. A very
        # large transcript is summarised in parts, then the parts are combined, so a
        # 3-4h meeting fits the context window and each call stays fast.
        self.chunk_chars = int(chunk_chars) if chunk_chars and int(chunk_chars) > 0 else CHUNK_CHARS
        # "summary" => parts are summarised then combined (default). "uniform" => the
        # SAME prompt runs on every part and on the combine step — for analysis, so a
        # feature (e.g. action items) is extracted per part and then merged.
        self.chunk_mode = chunk_mode if chunk_mode in ("summary", "uniform") else "summary"
        # Resilience: the local model may crash and be auto-restarted by a watchdog
        # (~2-3 min). Retry connection failures instead of failing the whole job.
        self.retries = max(0, int(retries or 0))
        self.retry_delay = max(1, int(retry_delay or 60))
        # When False the transcript is ALWAYS sent whole (no map-reduce) — the user's
        # choice: chunking breaks cross-part context (lower quality), so if their model
        # has the context to hold the whole transcript they turn it off. A too-long
        # transcript then simply errors (their decision, not ours).
        self.chunk_enabled = bool(chunk_enabled)

    def generate_summary(self, text):
        """One AI pass. When chunking is enabled (default), a transcript longer than
        ``chunk_chars`` is processed map-reduce (parts → combine) so it fits the context.
        When disabled, the transcript is ALWAYS sent whole (best quality; may exceed the
        model's context and error — the user's choice)."""
        if self.chunk_enabled and len(text) > self.chunk_chars:
            return self._summarize_chunked(text)
        return self._dispatch(text)

    def _dispatch(self, text):
        # A local agent CLI (Claude Code / Codex / Hermes / …) — checked before the
        # Advanced-API branch, since an agent is driven by a command, not a URL.
        if self.provider == "agent":
            return self._call_agent(text)
        # A custom Advanced-API config wins for any provider.
        if self.advanced and self.advanced.get("endpoint"):
            return self._call_advanced(text)
        if self.provider in ("local", "gemma"):
            return self._call_local(text)
        if self.provider in OPENAI_COMPAT_BASE:
            base = self.endpoint or OPENAI_COMPAT_BASE[self.provider]
            return self._call_openai_compatible(base, text)
        if self.provider == "anthropic":
            return self._call_anthropic(text)
        if self.provider == "google":
            return self._call_google(text)
        if self.provider == "qwen":
            return self._call_qwen(text)
        raise ValueError(f"Unknown provider: {self.provider}")

    _PARTIAL_PROMPT = (
        "Это ЧАСТЬ {i} из {n} транскрипции одной встречи. Кратко и по существу изложи "
        "ключевые факты именно этой части: обсуждаемые темы, принятые решения, задачи и "
        "важные детали. Не придумывай и не делай выводов о встрече целиком — только "
        "содержание этой части.")

    def _summarize_chunked(self, text):
        """Map: summarise each part; Reduce: combine the partials with the user's
        real prompt. The transcript is never fed whole, so context never overflows."""
        parts = _split_text_by_lines(text, self.chunk_chars, CHUNK_OVERLAP)
        if len(parts) == 1:
            return self._dispatch(text)
        print(f"Chunked ({self.chunk_mode}): {len(text)} chars -> {len(parts)} parts",
              file=sys.stderr)
        user_prompt = self.prompt
        partials = []
        for i, part in enumerate(parts, 1):
            # summary mode: parts get a "summarise this part" prompt; uniform mode
            # (analysis): every part gets the user's own feature prompt.
            if self.chunk_mode == "summary":
                self.prompt = self._PARTIAL_PROMPT.format(i=i, n=len(parts))
            partials.append(self._dispatch(part))
        self.prompt = user_prompt          # combine with the user's actual instructions
        combined = "\n\n".join(f"=== Часть {i} ===\n{p}" for i, p in enumerate(partials, 1))
        # If the joined partials are still huge, reduce recursively.
        if len(combined) > self.chunk_chars:
            return self._summarize_chunked(combined)
        return self._dispatch(combined)

    def _nothink_body(self):
        """Extra request fields to disable a reasoning model's <think> phase.
        llama.cpp / vLLM honour ``chat_template_kwargs.enable_thinking``; harmless
        to models without a reasoning toggle. Empty unless ``no_think`` is set."""
        return {"chat_template_kwargs": {"enable_thinking": False}} if self.no_think else {}

    def _post_with_retries(self, label, url, **kwargs):
        """POST a generation request with bounded cloud rate-limit recovery.

        Generation calls are safe to repeat. Retry only transient transport
        failures, HTTP 429, and provider/server 5xx responses. Honour a numeric
        Retry-After header or Google's ``retryDelay`` hint when present.
        """
        retryable_statuses = {429, 500, 502, 503, 504}
        for attempt in range(self.retries + 1):
            response = None
            try:
                response = requests.post(url, **kwargs)
                if (response.status_code not in retryable_statuses
                        or attempt >= self.retries):
                    return response
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout):
                if attempt >= self.retries:
                    raise

            wait = self.retry_delay
            if response is not None:
                retry_after = getattr(response, "headers", {}).get("Retry-After", "")
                if str(retry_after).strip().isdigit():
                    wait = max(1, int(str(retry_after).strip()))
                else:
                    import re
                    match = re.search(
                        r'"retryDelay"\s*:\s*"(\d+)s"',
                        getattr(response, "text", "") or "")
                    if match:
                        wait = max(1, int(match.group(1)))
            print(
                f"{label} transient failure"
                f"{f' (HTTP {response.status_code})' if response is not None else ''}"
                f" — retry {attempt + 1}/{self.retries} in {wait}s",
                file=sys.stderr)
            time.sleep(wait)
        raise AssertionError("unreachable")

    # -- custom (Advanced API modal) ----------------------------------
    # -- local agent CLI (Claude Code / Codex / Hermes / any command) ---
    def _call_agent(self, text):
        """Hand the work to a locally installed agent CLI.

        The complete system-prompt/transcript envelope goes in on **stdin** —
        neither an 80k-char meeting nor a long editable prompt is safe in argv
        (Windows caps a command line at ~32k). Legacy/custom commands can still
        substitute ``{prompt}``; agents that prefer files can use
        ``{prompt_file}`` / ``{text_file}`` instead.

        Whatever the agent prints on stdout is the answer. A non-zero exit is
        reported with the agent's own stderr, so a broken agent config (e.g. an
        invalid codex config.toml) is visible instead of looking like our bug.
        """
        import shlex
        import shutil
        import tempfile
        import re

        template = (self.agent_command or "").strip()
        if not template:
            raise Exception("Agent command is not configured "
                            "(Settings -> AI provider -> Agent).")

        tmp_ctx = tempfile.TemporaryDirectory(prefix="agent_")
        tmpdir = tmp_ctx.name
        prompt_file = os.path.join(tmpdir, "prompt.txt")
        text_file = os.path.join(tmpdir, "transcript.txt")
        with open(prompt_file, "w", encoding="utf-8") as fh:
            fh.write(self.prompt)
        with open(text_file, "w", encoding="utf-8") as fh:
            fh.write(text)

        def resolve_written_response(message):
            """Read a result file when a tool-capable agent prints only its path."""
            candidates = re.findall(r"`([^`\r\n]+)`", message or "")
            candidates += re.findall(r'"([A-Za-z]:\\[^"\r\n]+)"', message or "")
            temp_root = os.path.realpath(tmpdir)
            for raw_path in candidates:
                candidate = os.path.realpath(raw_path.strip())
                try:
                    inside_temp = os.path.commonpath(
                        [temp_root, candidate]) == temp_root
                except ValueError:
                    inside_temp = False
                if not inside_temp or not os.path.isfile(candidate):
                    continue
                if os.path.splitext(candidate)[1].lower() not in (
                        ".json", ".txt", ".md"):
                    continue
                try:
                    with open(candidate, "r", encoding="utf-8-sig") as fh:
                        written = fh.read().strip()
                except (OSError, UnicodeError):
                    continue
                if written:
                    return written
            return message

        def powershell_script_argv(script_path, script_args):
            """Run a PowerShell shim without losing Unicode or parsing args.

            Windows PowerShell 5 re-encodes a native child's UTF-8 stdout using
            its legacy console code page. Cyrillic then becomes literal ``?``
            before Python can decode it. A fixed wrapper selects UTF-8 while
            loading editable arguments from a UTF-8 JSON file. They therefore
            never pass through either ``-Command`` or PowerShell's ``-File``
            command-line parser.
            """
            wrapper = os.path.join(tmpdir, "invoke-agent.ps1")
            args_file = os.path.join(tmpdir, "agent-args.json")
            escaped = str(script_path).replace("'", "''")
            escaped_args = str(args_file).replace("'", "''")
            with open(args_file, "w", encoding="utf-8-sig") as fh:
                json.dump(list(script_args), fh, ensure_ascii=False)
            with open(wrapper, "w", encoding="utf-8-sig") as fh:
                fh.write(
                    "$utf8 = New-Object System.Text.UTF8Encoding($false)\n"
                    "[Console]::InputEncoding = $utf8\n"
                    "[Console]::OutputEncoding = $utf8\n"
                    "$OutputEncoding = $utf8\n"
                    f"$decodedArgs = Get-Content -LiteralPath '{escaped_args}' "
                    "-Raw -Encoding UTF8 | ConvertFrom-Json\n"
                    "$scriptArgs = @()\n"
                    "foreach ($item in $decodedArgs) { "
                    "$scriptArgs += [string]$item }\n"
                    f"& '{escaped}' @scriptArgs\n"
                    "exit $LASTEXITCODE\n"
                )
            return [
                "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", wrapper,
            ]

        uses_file_inputs = (
            "{prompt_file}" in template or "{text_file}" in template
        )
        if uses_file_inputs:
            stdin_payload = text
        else:
            stdin_payload = (
                "=== SYSTEM INSTRUCTIONS ===\n"
                f"{self.prompt}\n\n"
                "=== MEETING TRANSCRIPT ===\n"
                f"{text}"
            )

        argv = []
        for token in shlex.split(template, posix=False):
            token = token.strip('"')
            token = (token.replace("{prompt_file}", prompt_file)
                          .replace("{text_file}", text_file)
                          .replace("{prompt}", self.prompt))
            argv.append(token)

        # CreateProcess cannot execute the .cmd shims generated by npm.  Using
        # cmd.exe (or shell=True) would make the editable prompt part of shell
        # syntax, which is both lossy (%VAR% expansion) and an injection risk.
        # npm also installs an equivalent .ps1 shim, so execute that script as a
        # file and keep every prompt argument outside PowerShell's parser.
        resolved = shutil.which(argv[0])
        if resolved:
            suffix = os.path.splitext(resolved)[1].lower()
            if os.name == "nt" and suffix in (".cmd", ".bat"):
                ps1_shim = os.path.splitext(resolved)[0] + ".ps1"
                if not os.path.isfile(ps1_shim):
                    raise Exception(
                        f"Agent command resolves to {suffix}, which cannot be "
                        "executed safely with an editable prompt. Configure an "
                        ".exe or .ps1 command instead.")
                argv = powershell_script_argv(ps1_shim, argv[1:])
            elif os.name == "nt" and suffix == ".ps1":
                argv = powershell_script_argv(resolved, argv[1:])
            else:
                argv[0] = resolved

        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            try:
                agent_workdir = self.agent_cwd or os.path.join(tmpdir, "workspace")
                os.makedirs(agent_workdir, exist_ok=True)
                proc = subprocess.run(
                    argv, input=stdin_payload, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=self.timeout,
                    cwd=agent_workdir, env=env)
            except FileNotFoundError:
                raise Exception(f"Agent not found: {argv[0]!r}. Check the command "
                                f"and that the agent is on PATH.")
            except subprocess.TimeoutExpired:
                raise Exception(f"Agent timed out after {self.timeout}s")

            out = (proc.stdout or "").strip()
            if proc.returncode != 0 and not out:
                err = (proc.stderr or "").strip()[-400:] or f"exit code {proc.returncode}"
                raise Exception(f"Agent failed: {err}")
            if not out:
                err = (proc.stderr or "").strip()[-400:]
                suffix = f": {err}" if err else ""
                raise Exception(f"Agent returned an empty response{suffix}")
            out = resolve_written_response(out)
            # Some agent CLIs report quota/rate-limit failures on stdout and still
            # exit with code 0.  Without this guard the message is persisted as a
            # perfectly valid summary/analysis artifact.
            normalized = " ".join(out.lower().split())
            quota_markers = (
                "you've hit your weekly limit",
                "you have hit your weekly limit",
                "you've hit your usage limit",
                "you have hit your usage limit",
                "usage limit reached",
                "rate limit exceeded",
                "quota exceeded",
            )
            if len(normalized) < 1200 and any(marker in normalized for marker in quota_markers):
                raise Exception(f"Agent quota/rate limit: {out[:400]}")
            # Same shape, different cause: a misconfigured agent CLI prints its own
            # config error on stdout and exits 0. Left unrecognised it reaches the
            # JSON parser and the user is told "AI returned invalid JSON/schema",
            # which points at the model instead of at the one setting to change.
            config_markers = (
                "exceeds the provider's output cap",
                "exceeds the provider output cap",
                "max_tokens exceeds",
                "context length exceeded",
                "model not found",
                "unknown model",
            )
            if len(normalized) < 1200 and any(m in normalized for m in config_markers):
                raise Exception(f"Agent configuration problem: {out[:400]}")
            # A Windows CLI shim can silently replace every non-ASCII character
            # with '?'. Never persist that as a successful summary/analysis.
            if "\ufffd" in out or "????????" in out:
                raise Exception(
                    "Agent returned text with corrupted character encoding. "
                    "Use a UTF-8 capable command or the built-in agent preset.")
            return out
        finally:
            _release_temp_dir(tmp_ctx)

    def _call_advanced(self, text):
        cfg = self.advanced
        url = (cfg.get("endpoint") or "").strip()
        mapping = {
            "{{apiKey}}": self.api_key or "",
            "{{model}}": self.model or cfg.get("model", "") or self.default_model,
            "{{prompt}}": self.prompt or "",
            "{{text}}": text or "",
        }
        headers = _substitute(cfg.get("headers") or {"Content-Type": "application/json"}, mapping)
        body = _substitute(cfg.get("body") or {}, mapping)
        try:
            response = self._post_with_retries(
                "Advanced API", url, headers=headers, json=body, timeout=self.timeout)
            response.raise_for_status()
            return _extract_text(response.json())
        except requests.exceptions.HTTPError as e:
            raise Exception(f"Advanced API HTTP {e.response.status_code}: {e.response.text[:200]}")
        except Exception as e:
            raise Exception(f"Advanced API error: {str(e)}")

    # -- OpenAI-compatible (openai / xai / mistral / deepseek) ---------
    def _call_openai_compatible(self, base_url, text):
        url = base_url.rstrip("/") + "/chat/completions"
        try:
            response = self._post_with_retries(
                self.provider, url,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                json={"model": self.model or self.default_model,
                      "messages": [{"role": "system", "content": self.prompt},
                                   {"role": "user", "content": text}],
                      "temperature": self.temperature,
                      "max_tokens": self.max_tokens, **self._nothink_body()},
                timeout=self.timeout)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except requests.exceptions.HTTPError as e:
            raise Exception(f"{self.provider} API HTTP {e.response.status_code}: "
                            f"{e.response.text[:200]}")
        except Exception as e:
            raise Exception(f"{self.provider} API error: {str(e)}")

    # -- local (LM Studio / Ollama / llama.cpp, OpenAI-compatible, no auth) --
    def _call_local(self, text):
        base = self.endpoint or "http://localhost:1234/v1"
        # Two distinct failures during/after a GPU hand-off, handled differently:
        #  * HTTP 503 "loading model" — server is UP, model still warming up. It
        #    becomes ready on its own in seconds-to-minutes, so POLL it on a short
        #    fixed interval; it must NOT consume the long connection-retry budget.
        #  * ConnectionError — server is fully DOWN. Use the user's retry policy
        #    (sized for an external watchdog / healthcheck to bring it back).
        attempt = 0
        load_deadline = time.monotonic() + max(self.timeout, 300)
        while True:
            try:
                return self._call_local_once(base, text)
            except _ModelLoading:
                if time.monotonic() >= load_deadline:
                    raise Exception("Local model is still loading (HTTP 503) after "
                                    f"{int(max(self.timeout, 300))}s — is it stuck?")
                print("Local model loading (503) — waiting 5s for it to be ready",
                      file=sys.stderr)
                time.sleep(5)
            except requests.exceptions.ConnectionError:
                if attempt >= self.retries:
                    raise Exception(
                        f"Cannot connect to local API at {base} — is the server "
                        f"running? (after {attempt + 1} attempt(s)). The address "
                        f"comes from the 'Local endpoint' setting, or from "
                        f"'Local model port' when that field is empty; check that "
                        f"the port matches the model you actually started.")
                attempt += 1
                wait = self.retry_delay                 # fixed interval between retries
                print(f"Local API unreachable — retry {attempt}/{self.retries} in {wait}s "
                      f"(model may be restarting)", file=sys.stderr)
                time.sleep(wait)

    def _call_local_once(self, base, text):
        url = base.rstrip("/") + "/chat/completions"

        def _payload(messages):
            return {"model": self.model or "local-model", "messages": messages,
                    "temperature": self.temperature, "max_tokens": self.max_tokens,
                    "stream": False, **self._nothink_body()}
        try:
            print(f"Request size: text={len(text)} chars, prompt={len(self.prompt)} chars",
                  file=sys.stderr)
            response = requests.post(url, json=_payload(
                [{"role": "system", "content": self.prompt},
                 {"role": "user", "content": text}]), timeout=self.timeout)
            # Some local servers reject a system role -> fold the prompt into the
            # user turn (keeping the USER's prompt; no invented text).
            if response.status_code == 400:
                print("Local API 400 — retrying with prompt folded into the user message",
                      file=sys.stderr)
                combined = f"{self.prompt}\n\n---\n\n{text}"
                response = requests.post(url, json=_payload(
                    [{"role": "user", "content": combined}]), timeout=self.timeout)
            if response.status_code != 200:
                detail = ""
                try:
                    detail = " - " + str(response.json().get("error", response.text[:200]))
                except Exception:
                    detail = " - " + response.text[:200]
                print(f"Local API error: status={response.status_code}{detail}", file=sys.stderr)
            # 503 = server up but the model is still loading; signal the caller to
            # poll rather than fail (its retry loop waits it out).
            if response.status_code == 503:
                raise _ModelLoading()
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]
            content = (message.get("content") or "").strip()
            if not content:
                if message.get("reasoning_content"):
                    raise Exception("Model returned only reasoning without a final answer — "
                                    "disable reasoning mode or use a non-reasoning model.")
                raise Exception("Model returned empty response")
            return content
        except _ModelLoading:
            raise                                       # handled by the retry loop above
        except requests.exceptions.ConnectionError:
            raise                                       # handled by the retry loop above
        except requests.exceptions.Timeout:
            raise Exception("Local API timeout — model took too long")
        except requests.exceptions.HTTPError as e:
            raise Exception(f"Local API HTTP {e.response.status_code}")
        except Exception as e:
            if "Local API" in str(e) or "Model returned" in str(e):
                raise
            raise Exception(f"Local API error: {str(e)}")

    # -- anthropic ----------------------------------------------------
    def _call_anthropic(self, text):
        try:
            response = self._post_with_retries(
                "Anthropic", "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": self.api_key,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": self.model or self.default_model,
                      "max_tokens": self.max_tokens, "system": self.prompt,
                      "messages": [{"role": "user", "content": text}]},
                timeout=self.timeout)
            response.raise_for_status()
            return response.json()["content"][0]["text"]
        except requests.exceptions.HTTPError as e:
            raise Exception(f"Anthropic API HTTP {e.response.status_code}: {e.response.text[:200]}")
        except Exception as e:
            raise Exception(f"Anthropic API error: {str(e)}")

    # -- google gemini ------------------------------------------------
    def _call_google(self, text):
        model = self.model or self.default_model
        generation_config = {"temperature": self.temperature,
                             "maxOutputTokens": self.max_tokens}
        if self.no_think:
            # Gemini 2.5 uses a numeric thinking budget; Gemini 3.x uses
            # thinking levels.  Both controls are part of generateContent's
            # generationConfig and were live-verified against the public API.
            if model.startswith("gemini-2.5"):
                generation_config["thinkingConfig"] = {"thinkingBudget": 0}
            elif model.startswith("gemini-3"):
                generation_config["thinkingConfig"] = {"thinkingLevel": "minimal"}
        try:
            response = self._post_with_retries(
                "Google API",
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={self.api_key}",
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": f"{self.prompt}\n\n{text}"}]}],
                      "generationConfig": generation_config},
                timeout=self.timeout)
            response.raise_for_status()
            return response.json()["candidates"][0]["content"]["parts"][0]["text"]
        except requests.exceptions.HTTPError as e:
            raise Exception(f"Google API HTTP {e.response.status_code}: {e.response.text[:200]}")
        except Exception as e:
            raise Exception(f"Google API error: {str(e)}")

    # -- qwen (DashScope) ---------------------------------------------
    def _call_qwen(self, text):
        try:
            response = self._post_with_retries(
                "Qwen API",
                "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                json={"model": self.model or self.default_model,
                      "input": {"messages": [
                          {"role": "system", "content": self.prompt},
                          {"role": "user", "content": text}]},
                      "parameters": {"temperature": self.temperature,
                                     "max_tokens": self.max_tokens}},
                timeout=self.timeout)
            response.raise_for_status()
            return response.json()["output"]["text"]
        except requests.exceptions.HTTPError as e:
            raise Exception(f"Qwen API HTTP {e.response.status_code}: {e.response.text[:200]}")
        except Exception as e:
            raise Exception(f"Qwen API error: {str(e)}")


def main():
    parser = argparse.ArgumentParser(description='Generate meeting summary using AI')
    parser.add_argument('--provider', required=True, help='AI provider')
    parser.add_argument('--api-key',
                        default=os.environ.get('MEETING_SUMMARIZER_API_KEY', ''),
                        help='API key (prefer MEETING_SUMMARIZER_API_KEY)')
    parser.add_argument('--endpoint', default='', help='Base URL override (local or compatible gateway)')
    parser.add_argument('--model', default='', help='Model id (defaults per provider)')
    parser.add_argument('--advanced',
                        default=os.environ.get('MEETING_SUMMARIZER_ADVANCED', ''),
                        help='Advanced API config JSON (endpoint/model/headers/body)')
    parser.add_argument('--agent-command', dest='agent_command', default='',
                        help="Command template for provider 'agent' "
                             "(placeholders: {prompt}, {prompt_file}, {text_file}); "
                             "the transcript is piped on stdin")
    parser.add_argument('--agent-cwd', dest='agent_cwd', default='',
                        help="Working directory for the agent command")
    parser.add_argument('--temperature', type=float, default=0.7)
    parser.add_argument('--max-tokens', dest='max_tokens', type=int, default=0,
                        help='Max output tokens; 0 = auto per-provider (local 32000, cloud 8000)')
    parser.add_argument('--timeout', type=int, default=600,
                        help='Per-request read timeout in seconds (default 600; raise for long meetings)')
    parser.add_argument('--no-think', dest='no_think', action='store_true',
                        help='Ask reasoning models to skip the <think> phase (faster; quality trade-off)')
    parser.add_argument('--chunk-chars', dest='chunk_chars', type=int, default=0,
                        help='Map-reduce chunk threshold in chars; 0 = default (24000)')
    parser.add_argument('--chunk-mode', dest='chunk_mode', default='summary',
                        choices=['summary', 'uniform'],
                        help="'summary' = summarise then combine; 'uniform' = same prompt per "
                             "part + combine (for analysis feature extraction)")
    parser.add_argument('--no-chunk', dest='no_chunk', action='store_true',
                        help='Disable chunking — always send the transcript WHOLE '
                             '(best quality; may exceed the model context and error)')
    parser.add_argument('--retries', type=int, default=0,
                        help='Retry local-model connection failures N times (watchdog restart)')
    parser.add_argument('--retry-delay', dest='retry_delay', type=int, default=60,
                        help='Seconds between retries (fixed interval)')
    parser.add_argument('--prompt',
                        default=os.environ.get('MEETING_SUMMARIZER_PROMPT', ''),
                        help='System prompt (prefer MEETING_SUMMARIZER_PROMPT)')
    parser.add_argument('--text', default='', help='Text to summarize (deprecated)')
    parser.add_argument('--text-file', default='', help='File containing text to summarize')
    parser.add_argument('--participants', default='', help='Comma-separated list of participants')

    args = parser.parse_args()

    try:
        import time
        start_time = time.time()

        if args.text_file:
            if not os.path.exists(args.text_file):
                raise ValueError(f"Text file not found: {args.text_file}")
            with open(args.text_file, 'r', encoding='utf-8') as f:
                text = f.read()
        else:
            text = args.text

        if not text or not text.strip():
            raise ValueError("No text provided or text is empty")

        prompt = args.prompt
        if not prompt:
            raise ValueError("No prompt provided")
        if args.participants:
            participants_text = ', '.join(args.participants.split(','))
            prompt = f"{args.prompt}\n\nУчастники встречи: {participants_text}"

        advanced = None
        if args.advanced:
            try:
                advanced = json.loads(args.advanced)
            except ValueError:
                advanced = None

        client = AIClient(
            provider=args.provider, api_key=args.api_key, endpoint=args.endpoint,
            prompt=prompt, model=args.model, advanced=advanced,
            temperature=args.temperature, max_tokens=args.max_tokens,
            timeout=args.timeout, no_think=args.no_think, chunk_chars=args.chunk_chars,
            chunk_mode=args.chunk_mode, retries=args.retries, retry_delay=args.retry_delay,
            chunk_enabled=not args.no_chunk,
            agent_command=args.agent_command, agent_cwd=args.agent_cwd)

        summary = client.generate_summary(text)

        if not summary or not summary.strip():
            raise ValueError("AI returned empty summary")

        elapsed = time.time() - start_time
        print(f"<!-- Summary generated in {elapsed:.1f}s -->", file=sys.stderr)
        print(summary)

    except KeyboardInterrupt:
        print("Error: Process interrupted by user", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        import traceback
        print(f"Error: {str(e)}", file=sys.stderr)
        print(f"Traceback: {traceback.format_exc()}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
