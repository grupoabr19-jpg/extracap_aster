const SPREADSHEET_ID = '10XOSE1z8HucS0_5K_8iX8VFg6s0ASZJwDAMfo0uacIA';
const TOKEN_PROPERTY = 'SHEETS_API_TOKEN';
const OUTPUT_SHEET = '1_Lançamentos Diários';
const SNAPSHOT_SHEET = '__AsterSnapshots';
const OUTPUT_HEADERS = ['Data', 'Vendedor', 'Peso do dia (kg)', 'Observação'];

function doGet(event) {
  try {
    validateSheetsToken_(event && event.parameter ? String(event.parameter.token || '') : '');
    const spreadsheet = SpreadsheetApp.openById(SPREADSHEET_ID);
    const sheets = spreadsheet.getSheets().map(function(sheet) {
      const range = sheet.getDataRange();
      return {
        name: sheet.getName(),
        rows: range.getDisplayValues(),
        rowCount: range.getNumRows(),
        columnCount: range.getNumColumns()
      };
    });
    return jsonResponse_({ status: 'ok', spreadsheetId: SPREADSHEET_ID, sheets: sheets });
  } catch (error) {
    return jsonResponse_({ status: 'error', error: String(error.message || error) });
  }
}

function doPost(event) {
  const lock = LockService.getScriptLock();
  try {
    lock.waitLock(30000);
    const rawContent = event && event.postData && event.postData.contents
      ? event.postData.contents
      : '{}';
    const payload = JSON.parse(rawContent);
    validateSheetsToken_(String(payload.token || ''));
    if (payload.sheetName !== OUTPUT_SHEET) {
      throw new Error('A escrita automática é permitida somente na aba ' + OUTPUT_SHEET + '.');
    }
    validatePayload_(payload);
    const spreadsheet = SpreadsheetApp.openById(SPREADSHEET_ID);
    const sheet = spreadsheet.getSheetByName(OUTPUT_SHEET);
    if (!sheet) throw new Error('Aba não encontrada: ' + OUTPUT_SHEET);
    const result = processAutomaticPayload_(spreadsheet, sheet, payload);
    SpreadsheetApp.flush();
    return jsonResponse_({
      status: 'ok',
      spreadsheetId: SPREADSHEET_ID,
      sheetName: OUTPUT_SHEET,
      rowsWritten: result.rowsWritten,
      referenceDate: result.referenceDate,
      dataMode: result.mode,
      totalKg: result.totalKg
    });
  } catch (error) {
    return jsonResponse_({ status: 'error', error: String(error.message || error) });
  } finally {
    try { lock.releaseLock(); } catch (ignoredError) {}
  }
}

function validateSheetsToken_(receivedToken) {
  const expectedToken = PropertiesService.getScriptProperties().getProperty(TOKEN_PROPERTY);
  if (!expectedToken || !receivedToken || receivedToken !== expectedToken) {
    throw new Error('Token inválido.');
  }
}

function validatePayload_(payload) {
  if (!Array.isArray(payload.headers) || payload.headers.length !== 4) {
    throw new Error('O payload deve possuir exatamente quatro colunas.');
  }
  if (!Array.isArray(payload.rows)) throw new Error('O campo rows é obrigatório.');
  if (!payload.referenceDate || !/^\d{4}-\d{2}-\d{2}$/.test(String(payload.referenceDate))) {
    throw new Error('referenceDate deve estar no formato ISO YYYY-MM-DD.');
  }
  if (['daily_rows', 'cumulative_by_seller'].indexOf(payload.dataMode) === -1) {
    throw new Error('dataMode inválido.');
  }
  payload.rows.forEach(function(row) {
    if (!Array.isArray(row) || row.length !== 4) throw new Error('Todas as linhas devem possuir quatro colunas.');
    if (!String(row[1] || '').trim()) throw new Error('Vendedor vazio.');
    if (parseNumber_(row[2]) === null) throw new Error('Peso inválido.');
  });
}

function processAutomaticPayload_(spreadsheet, sheet, payload) {
  if (payload.dataMode === 'cumulative_by_seller') {
    return applyCumulative(spreadsheet, sheet, payload);
  }
  return applyDaily(sheet, payload);
}

function applyDaily(sheet, payload) {
  const incoming = payload.rows.map(function(row) { return row.slice(0, 4); });
  const current = sheet.getDataRange().getValues();
  const header = payload.headers.slice(0, 4);
  const dates = {};
  incoming.forEach(function(row) { dates[dateKey_(row[0])] = true; });
  if (!Object.keys(dates).length) dates[String(payload.referenceDate)] = true;

  const preserved = current.slice(1).filter(function(row) {
    return !dates[dateKey_(row[0])];
  }).map(function(row) { return row.slice(0, 4); });
  const outputRows = preserved.concat(incoming);
  writeOutput_(sheet, [header].concat(outputRows));
  return {
    rowsWritten: incoming.length,
    referenceDate: String(payload.referenceDate),
    mode: 'daily_rows',
    totalKg: incoming.reduce(function(total, row) { return total + (parseNumber_(row[2]) || 0); }, 0)
  };
}

function applyCumulative(spreadsheet, sheet, payload) {
  const snapshotSheet = getSnapshotSheet_(spreadsheet);
  const current = snapshotSheet.getDataRange().getValues();
  const firstHeader = current.length && current[0].length >= 3
    ? String(current[0][0]).trim().toLowerCase()
    : '';
  const snapshots = current.length && (firstHeader === 'date' || firstHeader === 'data')
    ? current.slice(1).filter(function(row) { return String(row[0] || '').trim(); }).map(function(row) { return row.slice(0, 4); })
    : [];
  const incoming = payload.rows.map(function(row) { return row.slice(0, 4); });
  const byKey = {};
  snapshots.concat(incoming).forEach(function(row) {
    byKey[dateKey_(row[0]) + '|' + String(row[1]).trim()] = row;
  });
  const ordered = Object.keys(byKey).map(function(key) { return byKey[key]; }).sort(function(a, b) {
    return dateKey_(a[0]).localeCompare(dateKey_(b[0]));
  });
  writeSnapshot_(snapshotSheet, [['Data', 'Vendedor', 'Peso acumulado (kg)', 'Observação']].concat(ordered));

  const previous = {};
  const dailyRows = ordered.map(function(row) {
    const key = String(row[1]).trim();
    const cumulative = parseNumber_(row[2]) || 0;
    const delta = cumulative - (previous[key] || 0);
    previous[key] = cumulative;
    return [row[0], key, delta, row[3]];
  });
  const dailyPayload = {
    headers: OUTPUT_HEADERS,
    rows: dailyRows,
    referenceDate: payload.referenceDate,
    dataMode: 'daily_rows'
  };
  const result = applyDaily(sheet, dailyPayload);
  result.mode = 'cumulative_by_seller';
  return result;
}

function getSnapshotSheet_(spreadsheet) {
  let sheet = spreadsheet.getSheetByName(SNAPSHOT_SHEET);
  if (!sheet) {
    sheet = spreadsheet.insertSheet(SNAPSHOT_SHEET);
    try { sheet.hideSheet(); } catch (ignoredError) {}
  }
  return sheet;
}

function writeOutput_(sheet, values) {
  const width = 4;
  const existingRows = Math.max(sheet.getMaxRows ? sheet.getMaxRows() : values.length, values.length);
  ensureSheetSize_(sheet, existingRows, width);
  if (sheet.getRange) {
    sheet.getRange(1, 1, existingRows, width).clearContent();
    sheet.getRange(1, 1, values.length, width).setValues(values);
  }
  if (sheet.setFrozenRows) sheet.setFrozenRows(1);
}

function writeSnapshot_(sheet, values) {
  ensureSheetSize_(sheet, values.length, 4);
  sheet.getRange(1, 1, sheet.getMaxRows(), 4).clearContent();
  sheet.getRange(1, 1, values.length, 4).setValues(values);
}

function ensureSheetSize_(sheet, requiredRows, requiredColumns) {
  const currentRows = sheet.getMaxRows ? sheet.getMaxRows() : requiredRows;
  const currentColumns = sheet.getMaxColumns ? sheet.getMaxColumns() : requiredColumns;
  if (currentRows < requiredRows && sheet.insertRowsAfter) sheet.insertRowsAfter(currentRows, requiredRows - currentRows);
  if (currentColumns < requiredColumns && sheet.insertColumnsAfter) sheet.insertColumnsAfter(currentColumns, requiredColumns - currentColumns);
}

function dateKey_(value) {
  if (Object.prototype.toString.call(value) === '[object Date]' && !isNaN(value.getTime())) {
    return Utilities.formatDate(value, Session.getScriptTimeZone() || 'America/Sao_Paulo', 'yyyy-MM-dd');
  }
  const text = String(value || '').trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text;
  const match = text.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  return match ? match[3] + '-' + match[2] + '-' + match[1] : text;
}

function parseNumber_(value) {
  if (typeof value === 'number' && isFinite(value)) return value;
  const text = String(value == null ? '' : value).replace(/kg/gi, '').trim();
  if (!text) return null;
  const normalized = text.indexOf(',') >= 0
    ? text.replace(/\./g, '').replace(',', '.')
    : text.replace(/[^\d.-]/g, '');
  const number = Number(normalized);
  return isFinite(number) ? number : null;
}

function jsonResponse_(payload) {
  return ContentService.createTextOutput(JSON.stringify(payload)).setMimeType(ContentService.MimeType.JSON);
}

if (typeof globalThis !== 'undefined' && globalThis.__ASTER_TEST__) {
  globalThis.__ASTER_TEST__.applyDaily = applyDaily;
  globalThis.__ASTER_TEST__.applyCumulative = applyCumulative;
}
