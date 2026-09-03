"""Google Sheets export via a user-deployed Apps Script webhook.

The old Electron app called the Sheets REST API v4 with only an API key — which
cannot write (append needs OAuth), so its export never actually worked. Here the
user deploys a tiny Apps Script bound to their sheet ("Deploy → Web app", access
"Anyone"), pastes the resulting ``/exec`` URL into Settings, and we POST the row
to it. No OAuth, no extra dependency (stdlib ``urllib``), free.

The one-time script the user pastes into their sheet is ``APPS_SCRIPT`` below;
the Settings dialog exposes it via a "Copy Apps Script" button. Qt-free.
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime

from .media import duration_from_transcript

# Column order. One column per section the app actually produces, carrying the
# section's CONTENT. The previous layout counted things ("Action Items: 20") and
# truncated the summary to its first line, so the sheet did not correspond to the
# summary/analysis the user sees in the app.
HEADERS = ["Date", "Meeting Name", "Duration", "Participants", "Word Count",
           "Summary", "Key Topics", "Category", "Tags", "Sentiment",
           "Action Items", "Risks", "Agenda", "Quotes", "Technologies",
           "Open Questions", "Recommendations", "Follow-up Questions"]

# The one-time script the user pastes into their sheet. Kept here as the SINGLE
# SOURCE OF TRUTH; docs/google-sheets/code.gs is a copy for hand-off and
# _selftest_gsheets asserts the two never drift apart.
#
# Deploy: Extensions → Apps Script, paste, then Deploy → New deployment →
# Web app (Execute as: Me, Who has access: Anyone).
APPS_SCRIPT = """/**
 * Meeting Summarizer -> Google Sheets bridge.
 *
 * Setup: Extensions > Apps Script, paste this, then
 *        Deploy > New deployment > Web app
 *        (Execute as: Me, Who has access: Anyone) and copy the /exec URL
 *        into the app's settings.
 *
 * Check it works: open the /exec URL in a browser - it must answer
 *        {"ok":true,"service":"meeting-summarizer"}.
 *
 * Optional security: Project Settings > Script Properties > add a property
 *        SHARED_TOKEN. When present, only requests carrying the same token are
 *        accepted (put the same value in the app's settings). Without it the
 *        endpoint accepts any request that knows the URL.
 */

function doGet() {
  // Health check so the deployment can be verified before the first export.
  return _reply({ok: true, service: 'meeting-summarizer', ready: true});
}

function doPost(e) {
  // Two meetings can finish at once; without a lock both could see an empty
  // sheet and write the header row twice.
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(30000);
  } catch (err) {
    return _reply({ok: false, error: 'busy, try again'});
  }
  try {
    if (!e || !e.postData || !e.postData.contents) {
      return _reply({ok: false, error: 'empty request'});
    }
    var data = JSON.parse(e.postData.contents);

    var expected = PropertiesService.getScriptProperties().getProperty('SHARED_TOKEN');
    if (expected && data.token !== expected) {
      return _reply({ok: false, error: 'unauthorized'});
    }
    if (!data.values || !data.values.length) {
      return _reply({ok: false, error: 'no values'});
    }

    var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
    if (data.headers && data.headers.length) {
      if (sheet.getLastRow() === 0) {
        sheet.appendRow(data.headers);
      } else {
        // A sheet written by an older version has a shorter/different header
        // row; appending wider rows under it would put content in unlabelled
        // columns. Rewrite the header row so the sheet self-heals.
        var width = Math.max(sheet.getLastColumn(), data.headers.length);
        var current = sheet.getRange(1, 1, 1, width).getValues()[0];
        var same = current.length >= data.headers.length;
        for (var i = 0; same && i < data.headers.length; i++) {
          if (String(current[i]).trim() !== String(data.headers[i]).trim()) same = false;
        }
        if (!same) {
          sheet.getRange(1, 1, 1, width).clearContent();
          sheet.getRange(1, 1, 1, data.headers.length).setValues([data.headers]);
        }
      }
      sheet.getRange(1, 1, 1, data.headers.length).setFontWeight('bold');
      sheet.setFrozenRows(1);
    }
    sheet.appendRow(data.values);
    // Section columns hold multi-line text; without wrapping the sheet shows
    // one clipped line per cell.
    sheet.getRange(sheet.getLastRow(), 1, 1, data.values.length)
         .setVerticalAlignment('top').setWrap(true);
    return _reply({ok: true, row: sheet.getLastRow()});
  } catch (err) {
    // Always answer JSON: an uncaught throw returns Google's HTML error page,
    // which the app cannot explain to the user.
    return _reply({ok: false, error: String(err)});
  } finally {
    lock.releaseLock();
  }
}

function _reply(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}"""


def _first_line(text: str, limit: int = 200) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:limit]
    return ""


def _extract_date(video_name: str) -> str:
    """The meeting's date from its file name, today's date when it has none.

    Same parser as everywhere else (see ``media.meeting_datetime_from_name``); the
    private copy that lived here recognised only ``YYYY-MM-DD``."""
    from .media import meeting_datetime_from_name
    date_iso, _ = meeting_datetime_from_name(video_name)
    return date_iso or datetime.now().strftime("%Y-%m-%d")


def _lines(items, render) -> str:
    """One rendered entry per line — a Sheets cell keeps the newlines."""
    out = []
    for item in items or []:
        try:
            text = render(item) if isinstance(item, dict) else str(item)
        except Exception:  # noqa: BLE001 - a malformed entry must not kill the row
            text = str(item)
        text = (text or "").strip()
        if text:
            out.append(text)
    return "\n".join(out)


def _joined(items) -> str:
    return ", ".join(str(i).strip() for i in (items or []) if str(i).strip())


def _action_item(a: dict) -> str:
    parts = [str(a.get("task") or "").strip()]
    who = str(a.get("assignee") or "").strip()
    if who and who not in ("Unassigned", "Не назначен"):
        parts.append(f"— {who}")
    meta = [str(a.get("priority") or "").strip()]
    when = str(a.get("deadline") or "").strip()
    if when and when not in ("Not specified", "Не указан"):
        meta.append(when)
    meta = [m for m in meta if m]
    if meta:
        parts.append(f"({'; '.join(meta)})")
    return " ".join(p for p in parts if p)


def _risk(r: dict) -> str:
    text = str(r.get("description") or "").strip()
    sev = str(r.get("severity") or "").strip()
    impact = str(r.get("impact") or "").strip()
    if sev:
        text = f"[{sev}] {text}"
    return f"{text} — {impact}" if impact else text


def _quote(q: dict) -> str:
    text = str(q.get("text") or "").strip()
    who = str(q.get("speaker") or "").strip()
    return f"«{text}» — {who}" if who else (f"«{text}»" if text else "")


def _tech(t: dict) -> str:
    name = str(t.get("name") or "").strip()
    cat = str(t.get("category") or "").strip()
    return f"{name} ({cat})" if name and cat else name


def _question(q: dict) -> str:
    text = str(q.get("question") or "").strip()
    owner = str(q.get("owner") or "").strip()
    if owner and owner not in ("Unassigned", "Не назначен"):
        return f"{text} — {owner}"
    return text


def _recommendation(r: dict) -> str:
    return str(r.get("recommendation") or r.get("description") or "").strip()


def _participants(participants, protocol: dict) -> str:
    """Participant NAMES. The sheet used to carry a bare count, which told the
    reader nothing the meeting itself did not already say."""
    if participants:
        if isinstance(participants, (list, tuple, set)):
            return _joined(participants)
        return str(participants).strip()
    people = protocol.get("participants")
    if isinstance(people, list):
        return _joined(people)
    return str(people or "").strip()


def build_values(video_name: str, summary_text: str, analysis: dict, *,
                 duration: str = "", transcript_text: str = "",
                 participants=None) -> list:
    """Assemble one row (in HEADERS order) from a finished meeting's artifacts."""
    analysis = analysis if isinstance(analysis, dict) else {}
    sentiment = analysis.get("sentiment") or {}
    category = analysis.get("category") or {}
    protocol = analysis.get("formalProtocol") or {}
    characteristics = analysis.get("characteristics") or {}

    # The stored duration is often empty (older entries, URL downloads, engines
    # that report none). Derive it from the transcript exactly like the analysis
    # panel does, so the sheet never says N/A for a meeting whose length the app
    # is happily displaying.
    if not duration:
        duration = duration_from_transcript(transcript_text)

    mood = str(sentiment.get("overall") or "").strip()
    engagement = str(sentiment.get("engagement") or "").strip()
    if mood and engagement:
        mood = f"{mood} ({engagement})"

    return [
        _extract_date(video_name),
        video_name or "",
        duration or "",
        _participants(participants, protocol),
        len((transcript_text or "").split()),
        (summary_text or "").strip(),
        _joined(characteristics.get("keyTopics")),
        str(category.get("category") or "").strip(),
        _joined(category.get("tags")),
        mood,
        _lines(analysis.get("actionItems"), _action_item),
        _lines(analysis.get("risks"), _risk),
        _lines(protocol.get("agenda"), lambda a: str(a)),
        _lines(analysis.get("quotes"), _quote),
        _lines(analysis.get("technologies"), _tech),
        _lines(analysis.get("questions"), _question),
        _lines(analysis.get("recommendations"), _recommendation),
        _lines(analysis.get("followupQuestions"), _question),
    ]


def export(webhook_url: str, values: list, timeout: int = 30, token: str = "") -> str:
    """POST one row to the Apps Script webhook. Raises on network/HTTP error, and
    ALSO when the script itself refuses the row.

    Sends ``{"headers": [...], "values": [...]}`` so the script can write the
    header row on first use; ``token`` is included only when configured (the
    script requires it if its SHARED_TOKEN property is set). urllib follows the
    /exec → googleusercontent 302.

    Apps Script answers HTTP 200 even when it rejects the request, so the body
    must be inspected — otherwise a refusal ('unauthorized', a script error)
    would be reported to the user as a successful export."""
    body = {"headers": HEADERS, "values": values}
    if token:
        body["token"] = token
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    try:
        result = json.loads(text)
    except ValueError:
        # Not JSON: almost always Google's HTML error/consent page — the usual
        # cause is a deployment whose access is not set to "Anyone".
        raise RuntimeError(
            "Google Sheets webhook did not return JSON. Check that the Apps "
            "Script deployment is a Web app with access 'Anyone'.")
    if isinstance(result, dict) and result.get("ok") is False:
        raise RuntimeError(f"Google Sheets webhook refused the row: "
                           f"{result.get('error', 'unknown error')}")
    return text
