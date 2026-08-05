# Google Sheets integration — setup

**English** · [Русский](README.ru.md)

After every processed meeting the app can append one row to your spreadsheet:
date, meeting name, first line of the summary, duration, participants, word
count, action-item count, sentiment, category and key topics.

There is no OAuth and no API key. You deploy a small script **inside your own
spreadsheet**; the app just POSTs a row to the URL it gives you. The sheet stays
yours — the app never sees your Google account.

## Setup (about two minutes)

1. Open (or create) the spreadsheet that should collect the meetings.
2. **Extensions → Apps Script**. Delete whatever is in the editor.
3. Paste the contents of [`code.gs`](code.gs).
   *(The same script is available in the app: Settings → Google Sheets →
   "Copy Apps Script".)*
4. **Deploy → New deployment → Web app**:
   - *Execute as:* **Me**
   - *Who has access:* **Anyone**
   Google will ask for permission the first time — it is your own script writing
   to your own sheet.
5. Copy the **`/exec` URL** it shows you.
6. **Check it works:** open that URL in a browser. It must answer
   `{"ok":true,"service":"meeting-summarizer","ready":true}`. If you see an
   HTML page or a login screen instead, the deployment access is not "Anyone".
7. In the app: **Settings → Google Sheets**, tick the integration on and paste
   the `/exec` URL.

The header row is written automatically on the first export (bold, frozen).

## Optional: require a token

Deployed with access "Anyone", the URL is the only secret — whoever knows it can
append rows. To require a token as well:

1. In Apps Script: **Project Settings → Script Properties → Add script
   property**, name `SHARED_TOKEN`, value — any long random string.
2. Put the same value in the app's Google Sheets settings.

Requests without a matching token are refused. Leave the property unset to keep
the simple setup.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| The app reports "did not return JSON" | The deployment is not a Web app with access "Anyone", or the URL is not the `/exec` one. Re-deploy and copy the `/exec` URL again. |
| "unauthorized" | `SHARED_TOKEN` is set in the script but the app's token is missing or different. |
| "no values" | The app sent an empty row — the meeting produced no summary/analysis to report. |
| "busy, try again" | Two meetings finished at the same moment; the row is simply retried on the next export. |
| Duplicate header rows | Only possible with a pre-existing older copy of the script — replace it with the current `code.gs`, which takes a lock. |
| Nothing happens at all | The integration checkbox is off, or the URL field is empty in Settings. |

## Changing the columns

The column order lives in `HEADERS` in `desktop/app/backend/gsheets.py` and the
row is assembled by `build_values()` there. The script writes whatever it is
sent, so changing those two places is enough — no need to touch `code.gs`.
