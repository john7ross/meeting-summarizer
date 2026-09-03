#!/usr/bin/env python3
"""Live meeting summary — one AI pass over the running transcript.

    <python> backend/live_summary.py --mode update --provider local \
        --state-file <live_summary.json> --chunk-file <chunk.txt> \
        --recent-file <recent.txt>

Called once per update by the front end, exactly like the post-meeting summary
pass calls ``ai_client.py``: same providers, same keys, same settings. Nothing
about "which AI" is re-implemented here — this module owns only the *state* of a
live summary and the prompts that maintain it, and hands the actual call to
``AIClient``. That is why local endpoints, cloud keys, agent CLIs and the
built-in model all work live on day one.

Three modes, and the difference between them is the whole design:

``update``
    Cheap and incremental: current state + the last ~2 minutes of raw transcript
    + the new chunk. This is what runs every ~30 seconds.

``regen``
    Anti-drift: rebuild the state from the TRANSCRIPT alone, ignoring the
    previous summary. An ``update`` builds on top of its own last answer, so a
    mistake made at minute 3 is quoted back to the model at minutes 4, 5, 6 and
    becomes permanent. Rebuilding from the source is the only thing that
    actually removes it. Mic Recorder's own design notes reached this conclusion
    and then shipped the incremental path anyway, with a consolidation pass as a
    patch — consolidation tidies the state, it cannot know the state is wrong.

``consolidate``
    Housekeeping on the state itself: merge duplicate topics, drop ASR debris.
    Useful, but never a substitute for ``regen``.

The caller decides the mix. For a local model, where tokens cost nothing but
time, every update can be a ``regen``; for a metered cloud, ``update`` between
periodic ``regen`` passes keeps the bill sane without letting errors set.

Output: a single JSON object on stdout. On success the state file is rewritten
atomically; on any failure it is left exactly as it was, because a live summary
that vanishes mid-meeting is worse than one that is 30 seconds stale.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_client import AIClient

STATE_FIELDS = ("short_summary", "timeline", "decisions", "action_items",
                "open_questions", "entities")
ENTITY_FIELDS = ("people", "projects", "terms")

# A live update is deliberately small. Anything past this is the tail of the
# meeting, which is what a rolling summary is supposed to be looking at anyway.
DEFAULT_MAX_TRANSCRIPT_CHARS = 24000


def empty_state() -> dict:
    return {
        "short_summary": "",
        "timeline": [],
        "decisions": [],
        "action_items": [],
        "open_questions": [],
        "entities": {"people": [], "projects": [], "terms": []},
    }


def normalize_state(raw) -> dict:
    """Coerce whatever the model returned into the schema, field by field.

    A model that answers with ``"decisions": "нет"`` instead of ``[]`` must not
    take the whole update down — the rest of its answer is usually fine.
    """
    if not isinstance(raw, dict):
        return empty_state()
    state = empty_state()
    summary = raw.get("short_summary")
    state["short_summary"] = summary.strip() if isinstance(summary, str) else ""
    for field in ("timeline", "decisions", "action_items", "open_questions"):
        value = raw.get(field)
        state[field] = value if isinstance(value, list) else []
    entities = raw.get("entities")
    if isinstance(entities, dict):
        for field in ENTITY_FIELDS:
            value = entities.get(field)
            state["entities"][field] = value if isinstance(value, list) else []
    return state


def state_is_empty(state: dict) -> bool:
    if (state.get("short_summary") or "").strip():
        return False
    return not any(state.get(field) for field in
                   ("timeline", "decisions", "action_items", "open_questions"))


# -- prompts ---------------------------------------------------------------
# Kept in the backend rather than the UI: a live summary has a JSON contract,
# and a prompt the user can freely rewrite would break parsing, not tuning. The
# *post-meeting* summary is the opposite — that prompt is fully editable and
# ships as 13 templates. Two different jobs, two different policies.

_SHORT_SUMMARY_RULES_RU = """Формат поля short_summary (тематические блоки):
- Разбивай сводку по темам обсуждения, а не одним сплошным абзацем.
- Каждая тема — заголовок строкой «## Название темы» (3–6 слов по сути).
- Под заголовком — пункты списка «- ...» по этой теме.
- Пока идёт та же тема — дополняй её пункты, не создавай дубликат заголовка.
- При явной смене темы — новый блок «## ...».
- Не создавай новую тему на каждую реплику; объединяй близкие под одним заголовком."""

_SHORT_SUMMARY_RULES_EN = """Format of the short_summary field (topic blocks):
- Break the summary down by topic, not one solid paragraph.
- Each topic is a heading line "## Topic name" (3-6 meaningful words).
- Under the heading, bullet points "- ..." for that topic.
- While the same topic continues, extend its bullets; do not repeat the heading.
- On a clear topic change, start a new "## ..." block.
- Do not create a topic per remark; merge related points under one heading."""

_SCHEMA = """{
  "updated_state": {
    "short_summary": "## Topic\\n- point\\n\\n## Another topic\\n- point",
    "timeline": [{ "time": "...", "event": "..." }],
    "decisions": [],
    "action_items": [{ "owner": "...", "task": "...", "deadline": "...", "status": "open" }],
    "open_questions": [],
    "entities": { "people": [], "projects": [], "terms": [] }
  },
  "live_delta": "what is new in this update: name the topic and the point"
}"""

UPDATE_PROMPT = {
    "ru": f"""Ты — движок live-summary для встречи, которая идёт прямо сейчас.

Тебе дано:
1. current_summary_state — текущее сжатое состояние встречи
2. recent_transcript_buffer — последние фрагменты сырой транскрипции
3. new_transcript_chunk — новый фрагмент транскрипции

Задача:
- Обнови summary_state по новому фрагменту, дополняя и уточняя, а не пересказывая заново.
- Не теряй решения, задачи, дедлайны и открытые вопросы, которые уже есть в состоянии.
- Убирай повторы и мусор автоматического распознавания речи.
- НЕ ПРИДУМЫВАЙ факты. Транскрипция — единственный источник.
- Если фраза неясная или оборванная — не делай из неё решение.
- Верни ТОЛЬКО валидный JSON, без пояснений и без markdown-обёртки.

{_SHORT_SUMMARY_RULES_RU}

Формат ответа:
{_SCHEMA}""",
    "en": f"""You are the live-summary engine for a meeting happening right now.

You are given:
1. current_summary_state - the current compressed state of the meeting
2. recent_transcript_buffer - the latest raw transcript fragments
3. new_transcript_chunk - the newest transcript fragment

Your task:
- Update summary_state from the new fragment: extend and refine, do not retell.
- Never lose decisions, tasks, deadlines or open questions already in the state.
- Remove repetitions and speech-recognition debris.
- DO NOT INVENT facts. The transcript is the only source.
- If a phrase is unclear or cut off, do not turn it into a decision.
- Return ONLY valid JSON, with no explanation and no markdown fence.

{_SHORT_SUMMARY_RULES_EN}

Response format:
{_SCHEMA}""",
}

REGEN_PROMPT = {
    "ru": f"""Ты — движок live-summary для встречи, которая идёт прямо сейчас.

Тебе дана транскрипция встречи с самого начала (возможно, обрезанная сверху).

Задача:
- Собери summary_state ЗАНОВО по транскрипции. Прошлой сводки у тебя нет и не должно быть.
- Транскрипция — единственный источник правды. НЕ ПРИДУМЫВАЙ факты.
- Убирай повторы и мусор автоматического распознавания речи.
- Если фраза неясная или оборванная — не делай из неё решение.
- Верни ТОЛЬКО валидный JSON, без пояснений и без markdown-обёртки.

{_SHORT_SUMMARY_RULES_RU}

Формат ответа:
{_SCHEMA}""",
    "en": f"""You are the live-summary engine for a meeting happening right now.

You are given the meeting transcript from the beginning (possibly truncated at
the top).

Your task:
- Build summary_state FROM SCRATCH out of the transcript. There is no previous
  summary and there must not be one.
- The transcript is the only source of truth. DO NOT INVENT facts.
- Remove repetitions and speech-recognition debris.
- If a phrase is unclear or cut off, do not turn it into a decision.
- Return ONLY valid JSON, with no explanation and no markdown fence.

{_SHORT_SUMMARY_RULES_EN}

Response format:
{_SCHEMA}""",
}

CONSOLIDATE_PROMPT = {
    "ru": f"""Ты — движок консолидации live-summary.

Тебе дан current_summary_state — накопленное состояние встречи.

Задача:
- Убери повторы и объедини похожие пункты.
- Объедини похожие темы под одним заголовком «## ...», если это одно обсуждение.
- Сохрани структуру short_summary: блоки «## Тема» и пункты «- ...» под каждой.
- Вычисти мусор распознавания, сохранив смысл.
- Сохрани решения, задачи, дедлайны, открытые вопросы и важный timeline.
- НЕ ПРИДУМЫВАЙ факты и ничего не добавляй от себя.
- Верни ТОЛЬКО валидный JSON, без пояснений и без markdown-обёртки.

Формат ответа:
{_SCHEMA}""",
    "en": f"""You are the live-summary consolidation engine.

You are given current_summary_state - the accumulated state of the meeting.

Your task:
- Remove repetitions and merge similar points.
- Merge similar topics under one "## ..." heading when they are one discussion.
- Keep the short_summary structure: "## Topic" blocks with "- ..." bullets.
- Clean up recognition debris while keeping the meaning.
- Keep decisions, tasks, deadlines, open questions and the meaningful timeline.
- DO NOT INVENT facts and add nothing of your own.
- Return ONLY valid JSON, with no explanation and no markdown fence.

Response format:
{_SCHEMA}""",
}


def build_prompt(mode: str, language: str) -> str:
    lang = language if language in ("ru", "en") else "ru"
    table = {"update": UPDATE_PROMPT, "regen": REGEN_PROMPT,
             "consolidate": CONSOLIDATE_PROMPT}[mode]
    return table[lang]


# -- response parsing ------------------------------------------------------
_FENCE_HEAD = re.compile(r"^```(?:json)?\s*", re.IGNORECASE)
_FENCE_TAIL = re.compile(r"\s*```\s*$")
_TRAILING_COMMA = re.compile(r",(\s*[\]}])")


def parse_response(raw: str):
    """Tolerant JSON extraction from the model's answer, or ``None``.

    Deliberately lighter than the analysis pass's recovery machinery. There, a
    truncated answer is worth salvaging element by element because losing it
    costs a whole run. Here a failed parse costs one 30-second update and the
    previous summary stays on screen — so "reject it and try again shortly" is
    both simpler and the correct behaviour.
    """
    if not raw:
        return None
    cleaned = _FENCE_TAIL.sub("", _FENCE_HEAD.sub("", raw.strip()))
    start = cleaned.find("{")
    if start > 0:
        cleaned = cleaned[start:]
    end = cleaned.rfind("}")
    if end != -1:
        cleaned = cleaned[:end + 1]
    for candidate in (cleaned, _TRAILING_COMMA.sub(r"\1", cleaned)):
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def extract_result(parsed, previous: dict):
    """``(state, delta)`` from a parsed answer, or ``None`` if unusable.

    A model that answers with the state object directly instead of wrapping it
    in ``updated_state`` is accepted — that is a formatting slip, not a wrong
    answer, and rejecting it would throw away a correct summary.
    """
    if not isinstance(parsed, dict):
        return None
    payload = parsed.get("updated_state")
    if not isinstance(payload, dict):
        payload = parsed if any(field in parsed for field in STATE_FIELDS) else None
    if payload is None:
        return None
    state = normalize_state(payload)
    # An answer that emptied a summary which HAD content is a regression, not an
    # update: keep the last good state instead of blanking the panel.
    if state_is_empty(state) and not state_is_empty(previous):
        return None
    delta = parsed.get("live_delta")
    return state, (delta.strip() if isinstance(delta, str) else "")


def read_text(path: str, limit: int = 0) -> str:
    if not path or not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if limit and len(text) > limit:
        # Keep the TAIL: the recent part of a meeting is what a rolling summary
        # is behind on. The state already carries what came before.
        marker = "[... начало встречи обрезано ...]\n"
        text = marker + text[-limit:]
    return text


def load_state(path: str) -> dict:
    if not path or not os.path.isfile(path):
        return empty_state()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return normalize_state(json.load(fh).get("state"))
    except (OSError, ValueError):
        return empty_state()


def save_state(path: str, state: dict, delta: str, updates: int) -> None:
    """Atomic write: a half-written state file read by the UI would render as an
    empty summary, which is the one thing this feature must never do."""
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "state": state,
        "live_delta": delta,
        "updates": updates,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, target)


def previous_updates(path: str) -> int:
    if not path or not os.path.isfile(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(json.load(fh).get("updates") or 0)
    except (OSError, ValueError, TypeError):
        return 0


def build_user_payload(mode: str, state: dict, chunk: str, recent: str,
                       transcript: str) -> str:
    if mode == "consolidate":
        payload = {"current_summary_state": state}
    elif mode == "regen":
        payload = {"transcript": transcript}
    else:
        payload = {
            "current_summary_state": state,
            "recent_transcript_buffer": recent,
            "new_transcript_chunk": chunk,
        }
    return json.dumps(payload, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Live meeting summary update")
    parser.add_argument("--mode", default="update",
                        choices=["update", "regen", "consolidate"])
    parser.add_argument("--state-file", dest="state_file", default="")
    parser.add_argument("--chunk-file", dest="chunk_file", default="")
    parser.add_argument("--recent-file", dest="recent_file", default="")
    parser.add_argument("--transcript-file", dest="transcript_file", default="")
    parser.add_argument("--language", default="ru", help="output language: ru | en")
    parser.add_argument("--max-transcript-chars", dest="max_transcript_chars",
                        type=int, default=DEFAULT_MAX_TRANSCRIPT_CHARS)
    # -- AI provider arguments: same names and same env-var convention as
    #    ai_client.py, so the front end reuses one command builder.
    parser.add_argument("--provider", default="local")
    parser.add_argument("--api-key",
                        default=os.environ.get("MEETING_SUMMARIZER_API_KEY", ""))
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--advanced",
                        default=os.environ.get("MEETING_SUMMARIZER_ADVANCED", ""))
    parser.add_argument("--agent-command", dest="agent_command", default="")
    parser.add_argument("--agent-cwd", dest="agent_cwd", default="")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", dest="max_tokens", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=120,
                        help="a live update that takes minutes is useless; fail fast")
    parser.add_argument("--no-think", dest="no_think", action="store_true")
    parser.add_argument("--retries", type=int, default=0)
    parser.add_argument("--retry-delay", dest="retry_delay", type=int, default=10)
    args = parser.parse_args()
    # The result JSON carries Russian text; the Windows console default would
    # mangle it before the caller reads the line.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):           # pragma: no cover
            pass

    started = time.time()
    state = load_state(args.state_file)
    chunk = read_text(args.chunk_file)
    recent = read_text(args.recent_file)
    transcript = read_text(args.transcript_file, args.max_transcript_chars)

    if args.mode == "update" and not chunk.strip():
        print(json.dumps({"success": False, "error": "empty chunk",
                          "skipped": True}, ensure_ascii=False))
        return 0
    if args.mode == "regen" and not transcript.strip():
        print(json.dumps({"success": False, "error": "empty transcript",
                          "skipped": True}, ensure_ascii=False))
        return 0
    if args.mode == "consolidate" and state_is_empty(state):
        print(json.dumps({"success": False, "error": "nothing to consolidate",
                          "skipped": True}, ensure_ascii=False))
        return 0

    advanced = None
    if args.advanced:
        try:
            advanced = json.loads(args.advanced)
        except ValueError:
            advanced = None

    client = AIClient(
        provider=args.provider, api_key=args.api_key, endpoint=args.endpoint,
        prompt=build_prompt(args.mode, args.language), model=args.model,
        advanced=advanced, temperature=args.temperature,
        max_tokens=args.max_tokens, timeout=args.timeout,
        no_think=args.no_think, retries=args.retries,
        retry_delay=args.retry_delay,
        # Never map-reduce a live update: the payload is already bounded, and
        # chunking would turn one 30-second update into several sequential calls.
        chunk_enabled=False,
        agent_command=args.agent_command, agent_cwd=args.agent_cwd)

    payload = build_user_payload(args.mode, state, chunk, recent, transcript)
    try:
        raw = client.generate_summary(payload)
    except Exception as exc:                           # noqa: BLE001
        print(json.dumps({"success": False, "error": str(exc)},
                         ensure_ascii=False))
        return 1

    result = extract_result(parse_response(raw), state)
    if result is None:
        # The state file is untouched: the UI keeps showing the last good summary.
        print(json.dumps({"success": False, "error": "AI returned unusable JSON",
                          "raw": (raw or "")[:500]}, ensure_ascii=False))
        return 1

    new_state, delta = result
    updates = previous_updates(args.state_file) + 1
    save_state(args.state_file, new_state, delta, updates)
    print(json.dumps({
        "success": True,
        "mode": args.mode,
        "updates": updates,
        "updated_state": new_state,
        "live_delta": delta,
        "elapsed": round(time.time() - started, 2),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
