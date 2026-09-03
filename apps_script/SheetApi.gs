/**
 * Endpoint de leitura e escrita usado pelo Python.
 *
 * Propriedades obrigatorias do projeto:
 * - SPREADSHEET_ID
 * - ASTER_SHEETS_TOKEN
 */

function doGet(e) {
  try {
    validarToken_(e && e.parameter && e.parameter.token);
    const spreadsheet = abrirPlanilha_();
    const sheets = spreadsheet.getSheets().map(function(sheet) {
      const range = sheet.getDataRange();
      return {
        name: sheet.getName(),
        rowCount: range.getNumRows(),
        columnCount: range.getNumColumns(),
        rows: range.getDisplayValues()
      };
    });
    return json_({
      spreadsheetId: spreadsheet.getId(),
      fetchedAt: new Date().toISOString(),
      sheets: sheets
    });
  } catch (error) {
    return json_({ error: String(error.message || error) });
  }
}

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const payload = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    validarToken_(payload.token);
    if (payload.sheetName !== '1_Lançamentos Diários') {
      throw new Error('A escrita automatica e permitida somente na aba 1_Lançamentos Diários.');
    }
    if (!Array.isArray(payload.headers) || !Array.isArray(payload.rows)) {
      throw new Error('headers e rows sao obrigatorios.');
    }

    const spreadsheet = abrirPlanilha_();
    const sheet = spreadsheet.getSheetByName(payload.sheetName);
    if (!sheet) {
      throw new Error('Aba nao encontrada: ' + payload.sheetName);
    }
    const values = [payload.headers].concat(payload.rows);
    const columns = payload.headers.length;
    if (!columns || values.some(function(row) { return row.length !== columns; })) {
      throw new Error('Todas as linhas devem possuir a mesma quantidade de colunas.');
    }

    if (sheet.getMaxRows() < values.length) {
      sheet.insertRowsAfter(sheet.getMaxRows(), values.length - sheet.getMaxRows());
    }
    if (sheet.getMaxColumns() < columns) {
      sheet.insertColumnsAfter(sheet.getMaxColumns(), columns - sheet.getMaxColumns());
    }

    sheet.getDataRange().clearContent();
    sheet.getRange(1, 1, values.length, columns).setValues(values);
    if (values.length > 1) {
      sheet.getRange(2, 1, values.length - 1, 1).setNumberFormat('dd/mm/yyyy');
      sheet.getRange(2, 3, values.length - 1, 1).setNumberFormat('#,##0.00');
    }
    sheet.setFrozenRows(1);
    SpreadsheetApp.flush();
    return json_({ status: 'ok', sheetName: payload.sheetName, rows: payload.rows.length });
  } catch (error) {
    return json_({ error: String(error.message || error) });
  } finally {
    lock.releaseLock();
  }
}

function abrirPlanilha_() {
  const spreadsheetId = PropertiesService.getScriptProperties().getProperty('SPREADSHEET_ID');
  if (!spreadsheetId) {
    throw new Error('Propriedade SPREADSHEET_ID nao configurada.');
  }
  return SpreadsheetApp.openById(spreadsheetId);
}

function validarToken_(receivedToken) {
  const expectedToken = PropertiesService.getScriptProperties().getProperty('ASTER_SHEETS_TOKEN');
  if (!expectedToken || receivedToken !== expectedToken) {
    throw new Error('Token invalido.');
  }
}

function json_(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}
