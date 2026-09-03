"""Fake agent CLI for the live-summary self-test: real argv/stdin shape.

``ai_client.py`` treats provider ``agent`` as "run this command, whatever it
prints on stdout is the answer". That makes it the cheapest way to drive
``backend/live_summary.py`` end to end — through the real ``AIClient``, the real
prompt assembly and the real state file — without a model server.

``FAKE_LIVE_MODE`` picks what the "model" answers:

* ``ok``       — a well-formed ``{"updated_state": ..., "live_delta": ...}``
* ``fenced``   — the same, wrapped in a ```json fence (models do this constantly)
* ``bare``     — the state object WITHOUT the ``updated_state`` wrapper
* ``garbage``  — prose, no JSON at all
* ``empty``    — a valid but blank state (the regression a live summary must reject)
"""
import json
import os
import sys

payload_in = sys.stdin.read()          # the envelope AIClient pipes in
mode = os.environ.get("FAKE_LIVE_MODE", "ok")

state = {
    "short_summary": "## Запуск продукта\n- Релиз перенесли на пятницу",
    "timeline": [{"time": "00:01", "event": "Начали обсуждение релиза"}],
    "decisions": ["Перенести релиз на пятницу"],
    "action_items": [{"owner": "Иван", "task": "Собрать релиз-ноуты",
                      "deadline": "пятница", "status": "open"}],
    "open_questions": ["Кто готовит релиз-ноуты"],
    "entities": {"people": ["Иван"], "projects": ["Релиз"], "terms": []},
}
empty = {"short_summary": "", "timeline": [], "decisions": [], "action_items": [],
         "open_questions": [], "entities": {"people": [], "projects": [], "terms": []}}

if mode == "garbage":
    print("Конечно! Вот сводка встречи: обсудили релиз. JSON я, впрочем, не верну.")
elif mode == "bare":
    print(json.dumps(state, ensure_ascii=False))
elif mode == "empty":
    print(json.dumps({"updated_state": empty, "live_delta": ""}, ensure_ascii=False))
else:
    body = json.dumps(
        {"updated_state": state,
         "live_delta": f"Запуск продукта: релиз перенесли (вход {len(payload_in)} симв.)"},
        ensure_ascii=False)
    print(f"```json\n{body}\n```" if mode == "fenced" else body)
