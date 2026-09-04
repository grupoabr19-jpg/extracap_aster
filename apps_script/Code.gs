/**
 * API da planilha utilizada pela automação do Aster.
 *
 * Propriedade obrigatória:
 * SHEETS_API_TOKEN
 *
 * A implantação deve ser feita como Aplicativo da Web:
 * - Executar como: você
 * - Quem pode acessar: qualquer pessoa
 */

const SPREADSHEET_ID = '10XOSE1z8HucS0_5K_8iX8VFg6s0ASZJwDAMfo0uacIA';
const TOKEN_PROPERTY = 'SHEETS_API_TOKEN';
const OUTPUT_SHEET = '1_Lançamentos Diários';

/**
 * Permite que o Python consulte os dados da planilha.
 */
function doGet(event) {
  try {
    const receivedToken =
      event && event.parameter
        ? String(event.parameter.token || '')
        : '';

    validateSheetsToken_(receivedToken);

    const spreadsheet = SpreadsheetApp.openById(SPREADSHEET_ID);

    const sheets = spreadsheet.getSheets().map(function (sheet) {
      const range = sheet.getDataRange();

      return {
        name: sheet.getName(),
        rows: range.getDisplayValues(),
        rowCount: range.getNumRows(),
        columnCount: range.getNumColumns()
      };
    });

    return jsonResponse_({
      status: 'ok',
      spreadsheetId: SPREADSHEET_ID,
      fetchedAt: new Date().toISOString(),
      sheets: sheets
    });

  } catch (error) {
    return jsonResponse_({
      status: 'error',
      error: String(error.message || error)
    });
  }
}

/**
 * Recebe do Python o histórico atualizado das vendas.
 *
 * Por segurança, somente a aba 1_Lançamentos Diários
 * pode ser alterada por este endpoint.
 */
function doPost(event) {
  const lock = LockService.getScriptLock();

  try {
    lock.waitLock(30000);

    const rawContent =
      event &&
      event.postData &&
      event.postData.contents
        ? event.postData.contents
        : '{}';

    const payload = JSON.parse(rawContent);

    validateSheetsToken_(String(payload.token || ''));

    if (payload.sheetName !== OUTPUT_SHEET) {
      throw new Error(
        'A escrita automática é permitida somente na aba ' +
        OUTPUT_SHEET +
        '.'
      );
    }

    if (!Array.isArray(payload.headers)) {
      throw new Error('O campo headers é obrigatório.');
    }

    if (!Array.isArray(payload.rows)) {
      throw new Error('O campo rows é obrigatório.');
    }

    if (payload.headers.length === 0) {
      throw new Error('O cabeçalho não pode ficar vazio.');
    }

    const totalColumns = payload.headers.length;
    const values = [payload.headers].concat(payload.rows);

    const invalidRow = values.some(function (row) {
      return !Array.isArray(row) || row.length !== totalColumns;
    });

    if (invalidRow) {
      throw new Error(
        'Todas as linhas devem possuir a mesma quantidade de colunas.'
      );
    }

    const spreadsheet = SpreadsheetApp.openById(SPREADSHEET_ID);
    const sheet = spreadsheet.getSheetByName(OUTPUT_SHEET);

    if (!sheet) {
      throw new Error('Aba não encontrada: ' + OUTPUT_SHEET);
    }

    /*
     * Manual e Aster usam a mesma validação, auditoria, bloqueio e
     * preservação das fórmulas da coluna E.
     */
    const loadResult = processAutomaticPayload_(
      spreadsheet,
      sheet,
      payload
    );

    SpreadsheetApp.flush();

    return jsonResponse_({
      status: 'ok',
      spreadsheetId: SPREADSHEET_ID,
      sheetName: OUTPUT_SHEET,
      rowsWritten: loadResult.rowsWritten,
      referenceDate: formatDateKey_(loadResult.referenceDate),
      dataMode: loadResult.mode,
      totalKg: loadResult.totalKg,
      updatedAt: new Date().toISOString()
    });

  } catch (error) {
    return jsonResponse_({
      status: 'error',
      error: String(error.message || error)
    });

  } finally {
    try {
      lock.releaseLock();
    } catch (ignoredError) {
      // O bloqueio pode não ter sido adquirido em caso de timeout.
    }
  }
}

/**
 * Valida o token utilizado pelo Python.
 */
function validateSheetsToken_(receivedToken) {
  const expectedToken = PropertiesService
    .getScriptProperties()
    .getProperty(TOKEN_PROPERTY);

  if (!expectedToken) {
    throw new Error(
      'A propriedade ASTER_SHEETS_TOKEN não está configurada.'
    );
  }

  if (!receivedToken || receivedToken !== expectedToken) {
    throw new Error('Token inválido.');
  }
}

/**
 * Garante que a aba tenha linhas e colunas suficientes.
 */
function ensureSheetSize_(sheet, requiredRows, requiredColumns) {
  const currentRows = sheet.getMaxRows();
  const currentColumns = sheet.getMaxColumns();

  if (currentRows < requiredRows) {
    sheet.insertRowsAfter(
      currentRows,
      requiredRows - currentRows
    );
  }

  if (currentColumns < requiredColumns) {
    sheet.insertColumnsAfter(
      currentColumns,
      requiredColumns - currentColumns
    );
  }
}

/**
 * Reaplica os formatos essenciais da aba de lançamentos.
 */
function formatOutputSheet_(sheet, totalRows, totalColumns) {
  sheet.setFrozenRows(1);

  if (totalRows <= 1) {
    return;
  }

  // Coluna A: data.
  if (totalColumns >= 1) {
    sheet
      .getRange(2, 1, totalRows - 1, 1)
      .setNumberFormat('dd/mm/yyyy');
  }

  // Coluna C: vendas em quilogramas.
  if (totalColumns >= 3) {
    sheet
      .getRange(2, 3, totalRows - 1, 1)
      .setNumberFormat('#,##0.00');
  }
}

/**
 * Retorna uma resposta em JSON.
 */
function jsonResponse_(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}