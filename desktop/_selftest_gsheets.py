"""GAP — Google Sheets webhook export: row assembly + POST payload.

No network: the HTTP call is monkeypatched to capture the request. Verifies the
row carries one column per summary/analysis section and that export() posts
headers+values
to the configured /exec URL.

Run: backend\\python\\python.exe desktop\\_selftest_gsheets.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desktop.app.backend import gsheets as G

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if (detail and not cond) else ""))

# ── row assembly ──────────────────────────────────────────────────────────────
# Every column carries its section's CONTENT: the sheet is meant to mirror the
# summary/analysis the user sees in the app, not to count its items.
analysis = {
    "characteristics": {"keyTopics": ["Сроки", "Бюджет"]},
    "actionItems": [{"task": "Собрать смету", "assignee": "Ann",
                     "priority": "high", "deadline": "2026-06-01"},
                    {"task": "Согласовать", "assignee": "Не назначен",
                     "priority": "low", "deadline": "Не указан"}],
    "risks": [{"description": "Сроки поджимают", "severity": "high",
               "impact": "срыв релиза"}],
    "quotes": [{"text": "Успеем", "speaker": "Bob"}],
    "technologies": [{"name": "PostgreSQL", "category": "database"}],
    "questions": [{"question": "Кто владелец?", "owner": "Ann"}],
    "recommendations": [{"recommendation": "Заложить буфер"}],
    "followupQuestions": [{"question": "Нужен ли аудит?"}],
    "sentiment": {"overall": "positive", "engagement": "high"},
    "category": {"category": "Планирование/Стратегия", "tags": ["план", "бюджет"]},
    "formalProtocol": {"participants": ["Ann", "Bob"],
                       "agenda": ["Смета", "Сроки"]},
}
SUMMARY_TEXT = "Первая строка саммари.\nВторая строка."
row = G.build_values("2026-05-19 13-04-45 standup", SUMMARY_TEXT,
                     analysis, duration="00:42:10",
                     transcript_text="one two three four five", participants=None)
col = {name: row[i] for i, name in enumerate(G.HEADERS)}

check("row_len_matches_headers", len(row) == len(G.HEADERS), f"{len(row)} vs {len(G.HEADERS)}")
check("date_from_name", col["Date"] == "2026-05-19", col["Date"])
check("meeting_name", col["Meeting Name"] == "2026-05-19 13-04-45 standup")
check("duration", col["Duration"] == "00:42:10", col["Duration"])
check("word_count", col["Word Count"] == 5, str(col["Word Count"]))

# The summary column used to hold only the first line, which made it useless.
check("summary_is_whole_text", col["Summary"] == SUMMARY_TEXT.strip(), col["Summary"])
check("participants_are_names", col["Participants"] == "Ann, Bob", col["Participants"])
check("key_topics_joined", col["Key Topics"] == "Сроки, Бюджет", col["Key Topics"])
check("category", col["Category"] == "Планирование/Стратегия", col["Category"])
check("tags_joined", col["Tags"] == "план, бюджет", col["Tags"])
check("sentiment_with_engagement", col["Sentiment"] == "positive (high)", col["Sentiment"])

check("action_items_are_text", "Собрать смету" in col["Action Items"]
      and "Ann" in col["Action Items"] and "2026-06-01" in col["Action Items"],
      col["Action Items"])
check("action_items_one_per_line", col["Action Items"].count("\n") == 1,
      repr(col["Action Items"]))
check("action_items_hide_placeholders",
      "Не назначен" not in col["Action Items"] and "Не указан" not in col["Action Items"],
      col["Action Items"])
check("risks_carry_severity_and_impact",
      "[high]" in col["Risks"] and "срыв релиза" in col["Risks"], col["Risks"])
check("agenda_from_protocol", col["Agenda"] == "Смета\nСроки", repr(col["Agenda"]))
check("quotes_attributed", col["Quotes"] == "«Успеем» — Bob", col["Quotes"])
check("technologies_categorised", col["Technologies"] == "PostgreSQL (database)",
      col["Technologies"])
check("open_questions_with_owner", col["Open Questions"] == "Кто владелец? — Ann",
      col["Open Questions"])
check("recommendations_text", col["Recommendations"] == "Заложить буфер",
      col["Recommendations"])
check("followups_text", col["Follow-up Questions"] == "Нужен ли аудит?",
      col["Follow-up Questions"])

# Duration is derived from the transcript when the entry has none stored - the
# analysis panel already did this, so the sheet must not write an empty cell for
# a meeting whose length the app is showing.
derived = G.build_values("m", "", {}, duration="",
                         transcript_text="[00:00:01] a\n[00:30:49] b")
check("duration_derived_from_transcript",
      derived[G.HEADERS.index("Duration")] == "30m 49s",
      derived[G.HEADERS.index("Duration")])
check("duration_stored_wins_over_derived",
      G.build_values("m", "", {}, duration="1h 2m 3s",
                     transcript_text="[00:30:49] b")[G.HEADERS.index("Duration")]
      == "1h 2m 3s")

# participants arg wins over protocol; empty analysis is safe
row2 = G.build_values("no-date-here", "", {}, participants=["x", "y", "z"])
col2 = {name: row2[i] for i, name in enumerate(G.HEADERS)}
check("participants_arg_wins", col2["Participants"] == "x, y, z", col2["Participants"])
check("empty_analysis_safe",
      col2["Action Items"] == "" and col2["Sentiment"] == ""
      and col2["Key Topics"] == "" and len(row2) == len(G.HEADERS), str(row2))
check("malformed_entries_do_not_crash",
      isinstance(G.build_values("m", "", {"actionItems": ["plain string", None],
                                          "risks": "not-a-list"}), list))
check("date_fallback_today", len(col2["Date"]) == 10 and col2["Date"][4] == "-",
      col2["Date"])

# ── export() POST payload (monkeypatched, no network) ────────────────────────
captured = {}
class _Resp:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return b'{"ok":true}'

def _fake_urlopen(req, timeout=30):
    captured["url"] = req.full_url
    captured["method"] = req.get_method()
    captured["body"] = json.loads(req.data.decode("utf-8"))
    captured["ctype"] = req.headers.get("Content-type")
    return _Resp()

G.urllib.request.urlopen = _fake_urlopen
out = G.export("https://script.google.com/macros/s/AK/exec", row)
check("post_url", captured.get("url") == "https://script.google.com/macros/s/AK/exec", captured.get("url"))
check("post_method", captured.get("method") == "POST", captured.get("method"))
check("post_json_ctype", captured.get("ctype") == "application/json", captured.get("ctype"))
check("post_sends_headers", captured["body"]["headers"] == G.HEADERS)
check("post_sends_values", captured["body"]["values"] == row)
check("returns_response_text", "ok" in out, out)

# ── Apps Script snippet sanity ───────────────────────────────────────────────
check("apps_script_has_doPost", "function doPost" in G.APPS_SCRIPT)
check("apps_script_appends", "appendRow(data.values)" in G.APPS_SCRIPT)

# --- the shipped code.gs must never drift from the in-app constant ----------
import pathlib  # noqa: E402
_gs = pathlib.Path(__file__).resolve().parents[1] / "docs" / "google-sheets" / "code.gs"
check("code_gs_file_exists", _gs.is_file(), str(_gs))
if _gs.is_file():
    check("code_gs_matches_constant",
          _gs.read_text(encoding="utf-8").strip() == G.APPS_SCRIPT.strip(),
          "docs/google-sheets/code.gs is out of sync with gsheets.APPS_SCRIPT")

# --- hardening the user-facing script --------------------------------------
check("apps_script_locks", "LockService" in G.APPS_SCRIPT,
      "concurrent exports could otherwise write the header row twice")
check("apps_script_catches", "catch (err)" in G.APPS_SCRIPT,
      "an uncaught throw returns Google's HTML page instead of JSON")
check("apps_script_health_get", "function doGet" in G.APPS_SCRIPT,
      "the deployment must be verifiable before the first export")
check("apps_script_optional_token", "SHARED_TOKEN" in G.APPS_SCRIPT)
check("apps_script_always_json", G.APPS_SCRIPT.count("_reply(") >= 6)

# --- export(): a refusal must NOT look like success -------------------------
import io  # noqa: E402
import json as _json  # noqa: E402
from unittest import mock  # noqa: E402


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(body):
    return lambda req, timeout=0: _Resp(body.encode("utf-8"))


captured = {}


def _capture(req, timeout=0):
    captured["body"] = _json.loads(req.data.decode("utf-8"))
    return _Resp(b'{"ok":true,"row":2}')


with mock.patch("urllib.request.urlopen", _capture):
    G.export("https://example/exec", ["a", "b"])
check("export_sends_headers_and_values",
      captured["body"].get("headers") == G.HEADERS and captured["body"].get("values") == ["a", "b"])
check("export_omits_token_when_unset", "token" not in captured["body"])

with mock.patch("urllib.request.urlopen", _capture):
    G.export("https://example/exec", ["a"], token="s3cret")
check("export_includes_token_when_set", captured["body"].get("token") == "s3cret")

for body, label in ((_json.dumps({"ok": False, "error": "unauthorized"}), "refusal"),
                    ("<html>Google login</html>", "html_page")):
    try:
        with mock.patch("urllib.request.urlopen", _fake_urlopen(body)):
            G.export("https://example/exec", ["a"])
        check(f"export_raises_on_{label}", False, "returned success instead of raising")
    except RuntimeError:
        check(f"export_raises_on_{label}", True)

with mock.patch("urllib.request.urlopen", _fake_urlopen('{"ok":true,"row":5}')):
    check("export_returns_body_on_success", '"row":5' in G.export("https://example/exec", ["a"]))

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)}): {', '.join(FAIL)}"); sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)"); sys.exit(0)
