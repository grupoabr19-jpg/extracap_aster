const SPREADSHEET_ID = '10XOSE1z8HucS0_5K_8iX8VFg6s0ASZJwDAMfo0uacIA';
const TOKEN_PROPERTY = 'SHEETS_API_TOKEN';
const OUTPUT_SHEET = '1_Lançamentos Diários';

function doGet(event) {
  try {
    validate_(event && event.parameter ? event.parameter.token : '');
    const book = SpreadsheetApp.openById(SPREADSHEET_ID);
    return json_({status: 'ok', sheets: book.getSheets().map(function(sheet) {
      const range = sheet.getDataRange();
      return {name: sheet.getName(), rows: range.getDisplayValues(), rowCount: range.getNumRows(), columnCount: range.getNumColumns()};
    })});
  } catch (error) { return json_({status: 'error', error: String(error.message || error)}); }
}

function doPost(event) {
  const lock = LockService.getScriptLock();
  try {
    lock.waitLock(30000);
    const payload = JSON.parse(event.postData.contents || '{}');
    validate_(payload.token);
    if (payload.sheetName !== OUTPUT_SHEET) throw new Error('A escrita automatica permite somente ' + OUTPUT_SHEET);
    if (!Array.isArray(payload.headers) || !Array.isArray(payload.rows)) throw new Error('headers e rows sao obrigatorios');
    const sheet = SpreadsheetApp.openById(SPREADSHEET_ID).getSheetByName(OUTPUT_SHEET);
    if (!sheet) throw new Error('Aba nao encontrada: ' + OUTPUT_SHEET);
    const values = [payload.headers].concat(payload.rows);
    const current = sheet.getDataRange().getValues();
    const dateColumn = 0;
    const processedDate = values.length > 1 ? String(values[1][dateColumn]) : '';
    const preserved = current.filter(function(row, index) { return index === 0 || String(row[dateColumn]) !== processedDate; });
    const headers = values[0];
    const output = preserved.slice(1).concat(values.slice(1));
    sheet.clearContents();
    sheet.getRange(1, 1, output.length + 1, headers.length).setValues([headers].concat(output));
    sheet.setFrozenRows(1);
    return json_({status: 'ok', sheetName: OUTPUT_SHEET, rowsWritten: values.length - 1});
  } catch (error) { return json_({status: 'error', error: String(error.message || error)}); }
  finally { try { lock.releaseLock(); } catch (ignored) {} }
}

function validate_(received) {
  const expected = PropertiesService.getScriptProperties().getProperty(TOKEN_PROPERTY);
  if (!expected || received !== expected) throw new Error('Token invalido');
}
function json_(payload) {
  return ContentService.createTextOutput(JSON.stringify(payload)).setMimeType(ContentService.MimeType.JSON);
}
