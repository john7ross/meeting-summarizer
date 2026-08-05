"""Tests for plain-text transcript search (backend/textsearch.py).

Run:
    backend\\python\\python.exe desktop\\_selftest_textsearch.py
"""
import sys, re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desktop.app.backend.textsearch import (
    search_in_text, passes_date_filter, highlight_html,
)

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if (detail and not cond) else ""))

TEXT = """[00:00:01] [Иван]: Обсудим бюджет проекта.
[00:00:08] [Мария]: Бюджет ограничен, нужны сроки.
[00:00:15] [Иван]: Согласен, БЮДЖЕТ важен.
[00:00:20] [Алексей]: А что с архитектурой API?
[00:00:25] [Мария]: API спроектируем позже."""

# ── literal, case-insensitive ────────────────────────────────────────────────
r = search_in_text(TEXT, "бюджет")
check("literal_finds_3", len(r) == 3, str(len(r)))
check("line_numbers", [m["line_number"] for m in r] == [1, 2, 3], str([m["line_number"] for m in r]))
check("context_present", all("\n" in m["context"] or m["line"] in m["context"] for m in r))

# ── case-sensitive ───────────────────────────────────────────────────────────
r_cs = search_in_text(TEXT, "БЮДЖЕТ", case_sensitive=True)
check("case_sensitive_1", len(r_cs) == 1, str(len(r_cs)))
check("case_sensitive_line3", r_cs[0]["line_number"] == 3)

# ── speaker filter ───────────────────────────────────────────────────────────
r_sp = search_in_text(TEXT, "бюджет", speaker_filter="Иван")
check("speaker_filter_ivan_2", len(r_sp) == 2, str(len(r_sp)))
check("speaker_filter_lines", [m["line_number"] for m in r_sp] == [1, 3])

r_sp2 = search_in_text(TEXT, "API", speaker_filter="Алексей")
check("speaker_filter_alexey_1", len(r_sp2) == 1)

# speaker filter excludes non-matching speaker
r_sp3 = search_in_text(TEXT, "API", speaker_filter="Иван")
check("speaker_filter_excludes", len(r_sp3) == 0)

# ── regex ─────────────────────────────────────────────────────────────────────
r_re = search_in_text(TEXT, r"API|архитектур", use_regex=True)
check("regex_finds_2", len(r_re) == 2, str(len(r_re)))

# invalid regex raises
raised = False
try:
    search_in_text(TEXT, "[unterminated", use_regex=True)
except re.error:
    raised = True
check("invalid_regex_raises", raised)

# ── empty query ───────────────────────────────────────────────────────────────
check("empty_query_no_results", search_in_text(TEXT, "") == [])

# ── context window size ───────────────────────────────────────────────────────
r_ctx = search_in_text(TEXT, "архитектурой", context_lines=1)
# line 4 match, context = lines 3..5
ctx = r_ctx[0]["context"].split("\n")
check("context_1_gives_3_lines", len(ctx) == 3, str(len(ctx)))

# ── date filter ────────────────────────────────────────────────────────────────
now = datetime(2026, 3, 1, 12, 0, 0)
check("date_all_passes", passes_date_filter("2020-01-01T00:00:00", "all", now))
check("date_today_pass", passes_date_filter("2026-03-01T09:00:00", "today", now))
check("date_today_fail", not passes_date_filter("2026-02-28T09:00:00", "today", now))
check("date_week_pass", passes_date_filter("2026-02-25T00:00:00", "week", now))
check("date_week_fail", not passes_date_filter("2026-02-01T00:00:00", "week", now))
check("date_month_pass", passes_date_filter("2026-02-10T00:00:00", "month", now))
check("date_month_fail", not passes_date_filter("2025-12-01T00:00:00", "month", now))
check("date_bad_fails_nonall", not passes_date_filter("not-a-date", "week", now))
check("date_bad_passes_all", passes_date_filter("not-a-date", "all", now))

# ── highlight ──────────────────────────────────────────────────────────────────
h = highlight_html("Обсудим бюджет сейчас", "бюджет", False, False)
check("highlight_wraps", "<mark>бюджет</mark>" in h, h)
check("highlight_escapes", "&lt;" not in h or "<mark>" in h)  # no raw injection
h2 = highlight_html("a < b and бюджет", "бюджет", False, False)
check("highlight_escapes_lt", "&lt;" in h2, h2)

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)} failed): {', '.join(FAIL)}")
    sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
sys.exit(0)
