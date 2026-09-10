/** Aster V2: publique este projeto como Web App separado do endpoint legado. */
function doGet(e) {
  try {
    validate_(e && e.parameter && e.parameter.token);
    const ss = workbook_();
    const names = ['1_Lançamentos Diários', '4_Metas', '5_Configurações'];
    return json_({spreadsheetId: ss.getId(), sheets: names.map(function(name) {
      const sheet = ss.getSheetByName(name);
      if (!sheet) throw new Error('Aba não encontrada: ' + name);
      return {name: name, rows: sheet.getDataRange().getDisplayValues()};
    })});
  } catch (error) { return json_({error: String(error.message || error)}); }
}

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const payload = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    validate_(payload.token);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(payload.referenceDate || ''))) throw new Error('referenceDate deve estar no formato YYYY-MM-DD.');
    if (payload.action !== 'upsert_daily' || payload.sheetName !== '1_Lançamentos Diários') throw new Error('Ação não permitida.');
    if (!Array.isArray(payload.rows)) throw new Error('rows é obrigatório.');
    const sheet = workbook_().getSheetByName(payload.sheetName);
    if (!sheet) throw new Error('Aba não encontrada.');
    const values = sheet.getDataRange().getValues();
    if (!values.length) throw new Error('Aba sem cabeçalho.');
    const headers = values[0], dateColumn = headers.indexOf('Data'), noteColumn = headers.indexOf('Observação');
    if (dateColumn < 0 || noteColumn < 0) throw new Error('Colunas Data e Observação são obrigatórias.');
    const marker = 'ASTER - saldo de ' + formatDate_(payload.referenceDate);
    for (let row = values.length - 1; row >= 1; row--) if (String(values[row][noteColumn] || '') === marker) sheet.deleteRow(row + 1);
    payload.rows.forEach(function(row) { if (!Array.isArray(row) || row.length !== headers.length) throw new Error('Linha com quantidade de colunas inválida.'); });
    if (payload.rows.length) {
      const start = sheet.getLastRow() + 1;
      if (sheet.getMaxRows() < start + payload.rows.length - 1) sheet.insertRowsAfter(sheet.getMaxRows(), start + payload.rows.length - 1 - sheet.getMaxRows());
      sheet.getRange(start, 1, payload.rows.length, headers.length).setValues(payload.rows);
      sheet.getRange(start, 1, payload.rows.length, 1).setNumberFormat('dd/mm/yyyy');
      sheet.getRange(start, 3, payload.rows.length, 1).setNumberFormat('#,##0.00');
      sheet.getRange(start, 6, payload.rows.length, 1).setNumberFormat('R$ #,##0.00');
    }
    SpreadsheetApp.flush();
    return json_({status: 'ok', rowsAdded: payload.rows.length, rowsTotal: sheet.getLastRow() - 1});
  } catch (error) { return json_({error: String(error.message || error)}); }
  finally { lock.releaseLock(); }
}

function workbook_() {
  const id = PropertiesService.getScriptProperties().getProperty('SPREADSHEET_ID');
  if (!id) throw new Error('SPREADSHEET_ID não configurado.');
  return SpreadsheetApp.openById(id);
}
function formatDate_(iso) { const value = String(iso).split('-'); return value[2] + '/' + value[1] + '/' + value[0]; }
function validate_(token) { const expected = PropertiesService.getScriptProperties().getProperty('ASTER_V2_TOKEN'); if (!expected || token !== expected) throw new Error('Token inválido.'); }
function json_(value) { return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON); }
