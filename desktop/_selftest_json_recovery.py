"""Recovery of a model's JSON answer (analysis.parse_json_response).

Two failures this pins down, both seen on real meetings:

* a VALID response was rejected because the delimiters were counted with
  ``str.count`` — one unmatched ``[`` or ``{`` inside a question's text made
  it look unbalanced, so ``json.loads`` was never tried and the user was told
  "AI returned invalid JSON/schema";
* a TRUNCATED response lost every question it had already produced, because
  the repair pass appended all ``]`` before all ``}`` (which cannot close an
  array of objects) and cut the tail at the last comma — a comma that is
  usually inside a Russian sentence.

Run:
    backend\\python\\python.exe desktop\\_selftest_json_recovery.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desktop.app.backend import analysis as A

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")


def question(i: int) -> str:
    """One question whose text carries the things that used to break parsing:
    a comma, an unmatched bracket and an unmatched brace."""
    return ('{"question":"Вопрос %d, требует уточнения по п. [%d и формату { отчёта",'
            '"category":"technical","priority":"high","owner":"Команда %d"}' % (i, i, i))


FULL = "[\n" + ",\n".join(question(i) for i in range(1, 6)) + "\n]"
assert len(json.loads(FULL)) == 5, "fixture itself must be valid JSON"

# ── 1. valid JSON is never touched by the repair pass ────────────────────────
check("intact_array_survives", A.parse_json_response(FULL) == json.loads(FULL))
check("unmatched_bracket_in_a_string_is_not_a_syntax_error",
      len(A.parse_json_response(
          '[{"question":"Проверить п. [3","category":"technical",'
          '"priority":"high","owner":"Команда"}]') or []) == 1)
check("unmatched_brace_in_a_string_is_not_a_syntax_error",
      len(A.parse_json_response(
          '[{"question":"Формат { для отчёта","category":"technical",'
          '"priority":"high","owner":"Команда"}]') or []) == 1)
check("prose_and_a_fence_around_the_json_are_stripped",
      A.parse_json_response(
          "Вот JSON массив с вопросами:\n```json\n" + FULL + "\n```\nГотово.")
      == json.loads(FULL))
check("an_object_feature_still_parses_as_an_object",
      (A.parse_json_response('{"overall":"positive","score":7}') or {}).get("score") == 7)
check("a_trailing_comma_is_tolerated",
      len(A.parse_json_response('[{"a":1},{"b":2},]') or []) == 2)
check("an_empty_array_stays_a_valid_nothing_found_answer",
      A.parse_json_response("[]") == [])
check("garbage_is_None_not_an_empty_result",
      A.parse_json_response("not json at all") is None)

# ── 2. a truncated answer keeps every element that DID complete ──────────────
worst = None
for cut in range(len(FULL) // 6, len(FULL)):
    truncated = FULL[:cut]
    complete = truncated.count('"owner":"Команда')   # objects that reached their last value
    recovered = A.parse_json_response(truncated)
    kept = len(recovered) if isinstance(recovered, list) else 0
    # the object being written when the text stopped may be dropped; no other may be
    if kept < complete - 1:
        worst = (cut, complete, kept)
        break
check("truncation_never_discards_a_completed_question", worst is None, str(worst or ""))
check("truncation_mid_sentence_with_a_comma_still_recovers",
      len(A.parse_json_response(
          '[\n{"question":"Первый вопрос","category":"technical","priority":"high",'
          '"owner":"A"},\n{"question":"Второй вопрос, который обрывается прямо'
      ) or []) >= 1)
check("a_dangling_key_at_the_cut_is_dropped_not_guessed",
      (A.parse_json_response(
          '[{"question":"Первый","category":"technical","priority":"high","owner"'
      ) or [{}])[0].get("owner") is None)

print("\nSUMMARY " + (f"ALL_PASS ({len(PASS)} checks)" if not FAIL
                      else f"FAIL ({len(FAIL)} failed): " + ", ".join(FAIL)))
sys.exit(1 if FAIL else 0)
