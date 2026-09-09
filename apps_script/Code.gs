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
    if (!payload || payload.sheetName !== OUTPUT_SHEET) {
      throw new Error('A escrita automatica permite somente ' + OUTPUT_SHEET);
    }
    const dataMode = String(payload.dataMode || 'date_balance');
    if (dataMode !== 'date_balance') {
      throw new Error('A escrita automatica aceita somente date_balance');
    }
    if (typeof processAutomaticPayload_ !== 'function') {
      throw new Error('processAutomaticPayload_ ausente; publique tambem ResumoComercial.gs');
    }
    const spreadsheet = SpreadsheetApp.openById(SPREADSHEET_ID);
    const sheet = spreadsheet.getSheetByName(OUTPUT_SHEET);
    if (!sheet) throw new Error('Aba nao encontrada: ' + OUTPUT_SHEET);
    const result = processAutomaticPayload_(spreadsheet, sheet, payload);
    return json_({
      status: 'ok',
      sheetName: OUTPUT_SHEET,
      dataMode: dataMode,
      rowsWritten: result.rowsWritten,
      totalKg: result.totalKg,
      totalRevenue: result.totalRevenue || 0
    });
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

if (typeof globalThis !== 'undefined' && globalThis.__ASTER_TEST__) {
  globalThis.__ASTER_TEST__.outputSheet = OUTPUT_SHEET;
}
