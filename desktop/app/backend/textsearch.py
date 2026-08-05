"""Plain-text transcript search (port of the Electron search.js logic).

This is NOT semantic search — it's a literal/regex grep across the raw
transcripts of past meetings, with optional date and speaker filters and a few
lines of surrounding context per match. (Semantic search lives in rag.py.)

Pure functions here so they can be unit-tested without Qt:

    matches = search_in_text(text, query, use_regex, case_sensitive, speaker_filter)
    -> list[{"line_number", "line", "context"}]

    keep = passes_date_filter(processed_at_iso, date_filter, now=None)
    -> bool
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional


def _compile(query: str, use_regex: bool, case_sensitive: bool):
    flags = 0 if case_sensitive else re.IGNORECASE
    if use_regex:
        return re.compile(query, flags)  # may raise re.error
    return re.compile(re.escape(query), flags)


# A leading [HH:MM:SS] (or [H:MM:SS.mmm]) timestamp, optionally present.
_TS_PREFIX = re.compile(r"^\s*\[\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\]\s*")
# Any [bracketed] token.
_BRACKET = re.compile(r"\[([^\]]+)\]")


def _line_speaker_matches(line: str, speaker_filter_lc: str) -> bool:
    """True if the line's speaker tag contains *speaker_filter_lc*.

    The real transcript format is ``[HH:MM:SS] [SPEAKER]: text`` (timestamp
    first), so the speaker tag is the FIRST bracket *after* an optional leading
    timestamp — not simply the first bracket on the line. Falls back to the
    first bracket when there is no leading timestamp.
    """
    rest = _TS_PREFIX.sub("", line, count=1)
    m = _BRACKET.search(rest)
    if not m:
        return False
    return speaker_filter_lc in m.group(1).lower()


def search_in_text(text: str, query: str, use_regex: bool = False,
                   case_sensitive: bool = False, speaker_filter: str = "",
                   context_lines: int = 3) -> list[dict]:
    """Return per-line matches with surrounding context.

    Mirrors search.js: a line matches if the pattern is found in it; when a
    speaker filter is given, only lines whose ``[speaker]`` tag contains the
    filter (case-insensitive) are considered. Context is ``context_lines`` lines
    before and after, joined with newlines.

    Raises ``re.error`` on an invalid regex so the caller can report it.
    """
    if not query:
        return []
    pattern = _compile(query, use_regex, case_sensitive)
    lines = text.split("\n")
    speaker_filter = (speaker_filter or "").strip().lower()
    out: list[dict] = []
    for i, line in enumerate(lines):
        if speaker_filter:
            if not _line_speaker_matches(line, speaker_filter):
                continue
        if pattern.search(line):
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            out.append({
                "line_number": i + 1,
                "line": line,
                "context": "\n".join(lines[start:end]),
            })
    return out


def passes_date_filter(processed_at: str, date_filter: str,
                       now: Optional[datetime] = None) -> bool:
    """Whether an ISO ``processed_at`` falls within the date filter window.

    date_filter is one of: 'all', 'today', 'week', 'month'. Unparseable dates
    pass only when the filter is 'all'.
    """
    date_filter = (date_filter or "all").lower()
    if date_filter == "all":
        return True
    now = now or datetime.now()
    try:
        # Tolerate trailing Z / fractional seconds / date-only.
        cleaned = processed_at.replace("Z", "").strip()
        dt = datetime.fromisoformat(cleaned)
    except (ValueError, AttributeError):
        return False
    if date_filter == "today":
        start = datetime(now.year, now.month, now.day)
        return dt >= start
    if date_filter == "week":
        return dt >= now - timedelta(days=7)
    if date_filter == "month":
        return dt >= now - timedelta(days=30)
    return True


def highlight_html(context: str, query: str, use_regex: bool,
                   case_sensitive: bool) -> str:
    """Wrap matches in <mark> for display. Escapes the rest as plain text.

    Falls back to the escaped original if the pattern can't compile.
    """
    import html
    escaped = html.escape(context)
    try:
        # Build the pattern against the ESCAPED text so offsets line up; we
        # escape the query the same way for literal mode.
        if use_regex:
            pat = re.compile(query, 0 if case_sensitive else re.IGNORECASE)
            # Apply to raw context, then escape piecewise.
        else:
            q = re.escape(query)
            pat = re.compile(q, 0 if case_sensitive else re.IGNORECASE)
    except re.error:
        return escaped

    # Highlight on the raw context, escaping segments around each match.
    result = []
    last = 0
    for m in pat.finditer(context):
        result.append(html.escape(context[last:m.start()]))
        result.append("<mark>" + html.escape(m.group(0)) + "</mark>")
        last = m.end()
    result.append(html.escape(context[last:]))
    return "".join(result)
