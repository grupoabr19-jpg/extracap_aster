const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

function serialDate(year, month, day) {
  const epoch = Date.UTC(1899, 11, 30);
  return (Date.UTC(year, month - 1, day) - epoch) / 86400000;
}

function a1ToIndexes(a1) {
  const match = /^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$/.exec(a1);
  if (!match) throw new Error(`unsupported A1 range: ${a1}`);
  const columnNumber = (letters) => {
    let column = 0;
    for (const char of letters) {
      column = column * 26 + char.charCodeAt(0) - 64;
    }
    return column;
  };
  const row = Number(match[2]);
  const column = columnNumber(match[1]);
  if (!match[3]) return { row, column, rowCount: 1, columnCount: 1 };
  const endColumn = columnNumber(match[3]);
  const endRow = Number(match[4]);
  return {
    row,
    column,
    rowCount: endRow - row + 1,
    columnCount: endColumn - column + 1,
  };
}

class FakeRange {
  constructor(sheet, row, column, rowCount = 1, columnCount = 1) {
    this.sheet = sheet;
    this.row = row;
    this.column = column;
    this.rowCount = rowCount;
    this.columnCount = columnCount;
  }

  getValues() {
    const result = [];
    for (let rowIndex = 0; rowIndex < this.rowCount; rowIndex++) {
      const source = this.sheet.rows[this.row - 1 + rowIndex] || [];
      const valuesRow = [];
      for (let columnIndex = 0; columnIndex < this.columnCount; columnIndex++) {
        valuesRow.push(source[this.column - 1 + columnIndex] ?? '');
      }
      result.push(valuesRow);
    }
    return result;
  }

  setValues(values) {
    while (this.sheet.rows.length < this.row - 1 + values.length) {
      this.sheet.rows.push([]);
    }
    values.forEach((valuesRow, rowIndex) => {
      const target = this.sheet.rows[this.row - 1 + rowIndex];
      while (target.length < this.column - 1 + valuesRow.length) target.push('');
      valuesRow.forEach((value, columnIndex) => {
        target[this.column - 1 + columnIndex] = value;
      });
    });
    return this;
  }

  clearContent() {
    for (let rowIndex = 0; rowIndex < this.rowCount; rowIndex++) {
      const target = this.sheet.rows[this.row - 1 + rowIndex];
      if (!target) continue;
      for (let columnIndex = 0; columnIndex < this.columnCount; columnIndex++) {
        target[this.column - 1 + columnIndex] = '';
      }
    }
    return this;
  }

  getFormulaR1C1() {
    return this.sheet.formulas[`${this.row}:${this.column}`] || '';
  }

  setFormulaR1C1(formula) {
    for (let rowIndex = 0; rowIndex < this.rowCount; rowIndex++) {
      this.sheet.formulas[`${this.row + rowIndex}:${this.column}`] = formula;
    }
    return this;
  }

  setFormulas(values) {
    values.forEach((valuesRow, rowIndex) => {
      valuesRow.forEach((formula, columnIndex) => {
        this.sheet.formulas[`${this.row + rowIndex}:${this.column + columnIndex}`] = formula;
      });
    });
    return this;
  }

  setValue(value) {
    this.setValues([[value]]);
    return this;
  }

  setNumberFormat() { return this; }
  setBackground() { return this; }
  setFontColor() { return this; }
  setFontWeight() { return this; }
  setFontFamily() { return this; }
  setHorizontalAlignment() { return this; }
}

class FakeSheet {
  constructor(name, rows, maxRows = 20, maxColumns = 10) {
    this.name = name;
    this.rows = rows.map((row) => row.slice());
    this.maxRows = maxRows;
    this.maxColumns = maxColumns;
    this.formulas = {};
    this.hidden = false;
  }

  getName() { return this.name; }
  getLastRow() { return this.rows.length; }
  getMaxRows() { return this.maxRows; }
  getMaxColumns() { return this.maxColumns; }
  setFrozenRows() {}
  isSheetHidden() { return this.hidden; }
  hideSheet() { this.hidden = true; }
  getImages() { return []; }

  getDataRange() {
    const width = Math.max(1, ...this.rows.map((row) => row.length));
    return {
      getValues: () => this.rows.map((row) => row.slice()),
      getDisplayValues: () => this.rows.map((row) => row.map(String)),
      getNumRows: () => this.rows.length,
      getNumColumns: () => width,
    };
  }

  getRange(row, column, rowCount, columnCount) {
    if (typeof row === 'string') {
      const indexes = a1ToIndexes(row);
      return new FakeRange(this, indexes.row, indexes.column, indexes.rowCount, indexes.columnCount);
    }
    return new FakeRange(this, row, column, rowCount, columnCount);
  }

  insertRowsAfter(_, count) { this.maxRows += count; }
  insertColumnsAfter(_, count) { this.maxColumns += count; }

  appendRow(row) {
    this.rows.push(row.slice());
  }
}

class FakeSpreadsheet {
  constructor(sheets) {
    this.sheets = sheets;
  }

  getSheetByName(name) {
    return this.sheets[name] || null;
  }

  getSheets() {
    return Object.values(this.sheets);
  }

  getSpreadsheetTimeZone() {
    return 'America/Sao_Paulo';
  }
}

const output = new FakeSheet('1_Lançamentos Diários', [
  ['Data', 'Vendedor', 'Peso do dia (kg)', 'Observação', 'Região (automática)', 'Faturamento (R$)'],
  [new Date(2026, 8, 1), 'ANA', 100, 'MANUAL', 'BRAG. PTA.', 55],
  [new Date(2026, 8, 8), 'HELOÁ', 1, 'linha parcial', 'BRAG. PTA.', 9],
], 2000, 7);
output.formulas['2:5'] = '=IF(RC[-3]="","",RC[-3])';

const metas = new FakeSheet('4_Metas', [
  ['Mês', 'Região', 'Líder', 'Segmento', 'Vendedor'],
  [new Date(2026, 8, 1), 'BRAG. PTA.', '', 'VCORP', 'HELOÁ'],
  [new Date(2026, 8, 1), 'EXTREMA', '', 'VAREJO', 'ANA'],
], 1000, 9);

const upload = new FakeSheet('7_Upload Resumo Comercial', [], 1000, 10);
const history = new FakeSheet('_Historico de Cargas', [], 1000, 10);
const spreadsheet = new FakeSpreadsheet({
  '1_Lançamentos Diários': output,
  '4_Metas': metas,
  '7_Upload Resumo Comercial': upload,
  '_Historico de Cargas': history,
});

let responseBody = '';
const exported = {};
const context = {
  console,
  globalThis: { __ASTER_TEST__: exported },
  Date,
  JSON,
  Math,
  Number,
  Object,
  String,
  Array,
  RegExp,
  SpreadsheetApp: {
    openById: () => spreadsheet,
    flush: () => {},
  },
  LockService: {
    getScriptLock: () => ({ waitLock: () => {}, releaseLock: () => {} }),
  },
  PropertiesService: {
    getScriptProperties: () => ({ getProperty: () => 'secret' }),
  },
  ContentService: {
    MimeType: { JSON: 'application/json' },
    createTextOutput: (text) => ({
      setMimeType: () => {
        responseBody = text;
        return text;
      },
    }),
  },
  Utilities: {
    getUuid: () => 'uuid-1',
    formatDate: (value, _zone, pattern) => {
      const year = value.getFullYear();
      const month = String(value.getMonth() + 1).padStart(2, '0');
      const day = String(value.getDate()).padStart(2, '0');
      if (pattern === 'dd/MM/yyyy') return `${day}/${month}/${year}`;
      if (pattern === 'yyyy-MM-dd') return `${year}-${month}-${day}`;
      return `${day}/${month}/${year} 00:00:00`;
    },
  },
  Session: { getScriptTimeZone: () => 'America/Sao_Paulo' },
};

vm.runInNewContext(fs.readFileSync('apps_script/Code.gs', 'utf8'), context);
vm.runInNewContext(fs.readFileSync('apps_script/ResumoComercial.gs', 'utf8'), context);

const headers = ['Data', 'Vendedor', 'Tipo', 'Regiao', 'Segmento', 'Peso do dia (kg)', 'Valor total'];
context.doPost({
  postData: {
    contents: JSON.stringify({
      token: 'secret',
      sheetName: '1_Lançamentos Diários',
      referenceDate: '2026-09-08',
      dataMode: 'date_balance',
      headers,
      rows: [
        ['2026-09-08', 'VCORP - HELOA LEITE', 'VAREJO', 'BRAGANCA', 'VCORP', 3864.81, 'R$ 1'],
        ['2026-09-08', 'VARE - HELOA LEITE', 'VAREJO', 'EXTREMA', 'VARE', 215.64, 'R$ 2'],
        ['2026-09-08', 'ATF - HELOA LEITE', 'ATACADO', 'EXTREMA', 'ATF', 999, 'R$ 3'],
      ],
    }),
  },
});

const result = JSON.parse(responseBody);
assert.strictEqual(result.status, 'ok');
assert.strictEqual(result.rowsWritten, 1);
assert.strictEqual(result.totalKg, 4080.45);
assert.strictEqual(result.totalRevenue, 3);

assert.deepStrictEqual(output.rows[0].slice(0, 6), [
  'Data',
  'Vendedor',
  'Peso do dia (kg)',
  'Observação',
  'Região (automática)',
  'Faturamento (R$)',
]);
assert.strictEqual(output.rows[1][1], 'ANA');
assert.strictEqual(output.rows[1][5], 55);
assert.strictEqual(output.rows[2][1], 'HELOÁ');
assert.strictEqual(output.rows[2][2], 4080.45);
assert.strictEqual(output.rows[2][3], 'ASTER - saldo de 08/09/2026');
assert.strictEqual(output.rows[2][5], 3);
assert.strictEqual(
  output.formulas['2:5'],
  '=IF(B2="";"";IFERROR(INDEX(FILTER(\'4_Metas\'!$B$2:$B$1000;\'4_Metas\'!$E$2:$E$1000=B2;\'4_Metas\'!$A$2:$A$1000=DATE(YEAR(A2);MONTH(A2);1));1);""))',
);
assert.strictEqual(
  output.formulas['2000:5'],
  '=IF(B2000="";"";IFERROR(INDEX(FILTER(\'4_Metas\'!$B$2:$B$1000;\'4_Metas\'!$E$2:$E$1000=B2000;\'4_Metas\'!$A$2:$A$1000=DATE(YEAR(A2000);MONTH(A2000);1));1);""))',
);
assert.strictEqual(history.rows.length, 2);
assert.strictEqual(history.rows[1][5], 'SUCESSO');
assert.strictEqual(upload.rows[0][7], 'CONTROLE DE CARGAS');

console.log('apps_script publication contract: ok');
