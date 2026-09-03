"""TODO #17 — Prompt templates: built-in library + user CRUD.

Run: backend\\python\\python.exe desktop\\_selftest_templates.py
"""
import os, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desktop.app.backend import templates as T

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if (detail and not cond) else ""))

# ── built-in library ─────────────────────────────────────────────────────────
bru = T.builtin_templates("ru")
ben = T.builtin_templates("en")
ids = [t["id"] for t in bru]
check("builtin_has_custom_plus_13", ids == ["custom", "general", "standup", "retrospective",
      "planning", "brainstorming", "client", "interview", "one_on_one", "status",
      "kickoff", "demo", "all_hands", "web_video"], str(ids))
check("custom_has_default_prompt",
      bru[0]["id"] == "custom" and "КРИТИЧЕСКИ ВАЖНО" in bru[0]["prompt"] and len(bru[0]["prompt"]) > 500)
check("general_ru_nonempty", any(t["id"] == "general" and len(t["prompt"]) > 20 for t in bru))
check("general_en_nonempty", any(t["id"] == "general" and "summary" in t["prompt"].lower() for t in ben))
check("no_transcript_placeholder", all("{transcript}" not in t["prompt"] for t in bru + ben))
check("all_builtin_flag", all(t["builtin"] for t in bru))
# new meeting types present with non-empty prompts (RU + EN)
for nt in ("one_on_one", "status", "kickoff", "demo", "all_hands"):
    check(f"newtype_{nt}_ru", any(t["id"] == nt and len(t["prompt"]) > 20 for t in bru))
    check(f"newtype_{nt}_en", any(t["id"] == nt and len(t["prompt"]) > 20 for t in ben))

# ── speaker-aware variants ────────────────────────────────────────────────────
sru = T.builtin_templates("ru", use_speaker=True)
sen = T.builtin_templates("en", use_speaker=True)
gen_plain = next(t["prompt"] for t in bru if t["id"] == "general")
gen_spk = next(t["prompt"] for t in sru if t["id"] == "general")
check("speaker_variant_differs", gen_spk != gen_plain)
check("speaker_ru_marks_participants", "Имя участника" in gen_spk)
check("plain_ru_no_participant_marker", "Имя участника" not in gen_plain)
check("speaker_en_marks_participants",
      "Participant Name" in next(t["prompt"] for t in sen if t["id"] == "general"))
check("custom_speaker_variant",
      "СПИКЕРОВ" in next(t["prompt"] for t in sru if t["id"] == "custom"))

# ── user CRUD (isolated store) ──────────────────────────────────────────────
tmp = Path(tempfile.mkdtemp())
T._store_path = lambda: tmp / "prompt_templates.json"   # isolate from real config

check("user_empty_initially", T.load_user() == [])
T.save_user("Мой шаблон", "Промпт A")
check("user_saved", [t["name"] for t in T.load_user()] == ["Мой шаблон"])
check("all_includes_user", any(not t["builtin"] and t["name"] == "Мой шаблон"
                               for t in T.all_templates("ru")))
T.save_user("Мой шаблон", "Промпт B")   # replace by name
users = T.load_user()
check("user_replaced_not_dup", len(users) == 1 and users[0]["prompt"] == "Промпт B")
T.save_user("Второй", "П2")
check("user_two", len(T.load_user()) == 2)
# edit-in-place (re-save same name) then rename via old_name
T.save_user("Второй", "П2-ред")
check("user_edit_inplace", next(t["prompt"] for t in T.load_user() if t["name"] == "Второй") == "П2-ред")
T.save_user("Второй-переименован", "П2-ред", old_name="Второй")
names = {t["name"] for t in T.load_user()}
check("user_renamed", names == {"Мой шаблон", "Второй-переименован"}, str(names))
check("rename_no_dup", len(T.load_user()) == 2)
T.delete_user("Второй-переименован")
T.delete_user("Мой шаблон")
T.save_user("Второй", "П2")   # restore for the export/import section below
check("user_deleted", [t["name"] for t in T.load_user()] == ["Второй"])

# ── export / import ──────────────────────────────────────────────────────────
exp = tmp / "export.json"
T.export_user(exp)
T.delete_user("Второй")
check("empty_after_delete", T.load_user() == [])
_added = T.import_user(exp)
check("imported_back", [t["name"] for t in T.load_user()] == ["Второй"])
# The count is what a caller shows the user; it must be what was ADDED, not what
# the file happened to contain.
check("import_reports_what_it_added", _added == 1, f"reported {_added}")
_again = T.import_user(exp)
check("re_import_adds_nothing", _again == 0, f"reported {_again}")
check("re_import_does_not_duplicate",
      [t["name"] for t in T.load_user()] == ["Второй"],
      str([t["name"] for t in T.load_user()]))

# Concurrent UI/background saves must serialize as complete transactions.
with ThreadPoolExecutor(max_workers=8) as pool:
    list(pool.map(lambda i: T.save_user(f"Параллельный {i}", f"Текст {i}"),
                  range(20)))
parallel = {t["name"]: t["prompt"] for t in T.load_user()}
check("concurrent_saves_not_lost",
      all(parallel.get(f"Параллельный {i}") == f"Текст {i}" for i in range(20)),
      str(len(parallel)))
check("concurrent_no_temp_files", not list(tmp.glob("prompt_templates.json.*.tmp")))

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)}): {', '.join(FAIL)}"); sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)"); sys.exit(0)
