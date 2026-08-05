/**
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
}
