"""Fake ai_client.py: real argv shape. Reads --text-file, prints a deterministic
response whose marker (SUMMARY/ANALYSIS) is derived from the prompt, so the
pipeline self-test can verify the right pass produced the right artifact.
"""
import argparse
import os

p = argparse.ArgumentParser()
p.add_argument("--provider", required=True)
p.add_argument("--api-key", dest="api_key", default="")
p.add_argument("--endpoint", default="")
# The real ai_client takes the prompt from the environment (it must not appear in
# argv); --prompt stays only as a fallback, so mirror that contract here.
p.add_argument("--prompt", default=os.environ.get("MEETING_SUMMARIZER_PROMPT", ""))
p.add_argument("--text-file", dest="text_file", default="")
p.add_argument("--participants", default="")
# Tolerate the real ai_client's operational flags (--timeout/--no-think/--chunk-mode/
# --chunk-chars/--no-chunk/--retries/--retry-delay); they don't affect the canned response.
a, _ = p.parse_known_args()

text = ""
if a.text_file:
    try:
        with open(a.text_file, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = ""

prompt_lower = a.prompt.lower()
marker = "ANALYSIS" if ("json" in prompt_lower or "анализ" in prompt_lower) else "SUMMARY"

def flaky_answer_is_due() -> bool:
    """Opt-in flakiness for the retry self-test; a no-op unless asked for.

    ``FAKE_AI_FLAKY_FILE`` names a counter file and ``FAKE_AI_FLAKY_CALLS`` how
    many of the first ANALYSIS calls answer with text that will not parse — the
    way a real sampled model occasionally does. Neither set (the default) leaves
    every other self-test seeing exactly the canned response it always saw.
    """
    counter = os.environ.get("FAKE_AI_FLAKY_FILE", "")
    wanted = int(os.environ.get("FAKE_AI_FLAKY_CALLS", "0") or 0)
    if not counter or wanted <= 0:
        return False
    try:
        with open(counter, "r", encoding="utf-8") as fh:
            seen = int((fh.read() or "0").strip() or 0)
    except (OSError, ValueError):
        seen = 0
    with open(counter, "w", encoding="utf-8") as fh:
        fh.write(str(seen + 1))
    return seen < wanted


if marker == "ANALYSIS":
    # Analysis passes are parsed as JSON and merged, so emit valid JSON.
    import json
    if flaky_answer_is_due():
        print("Конечно! Вот результат анализа: тут нет никакого JSON.")
        raise SystemExit(0)
    object_prompt = prompt_lower.startswith((
        "analyze the sentiment",
        "проанализируй тональность",
        "categorize the following meeting",
        "категоризируй следующую встречу",
        "generate a formal meeting protocol",
        "сгенерируй формальный протокол",
    ))
    payload = (
        {"provider": a.provider, "input_chars": len(text)}
        if object_prompt
        else [{"task": "demo", "provider": a.provider,
               "input_chars": len(text)}]
    )
    print(json.dumps(payload))
else:
    print(f"[SUMMARY] provider={a.provider} input_chars={len(text)} "
          f"participants={a.participants}")
