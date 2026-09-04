const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

class FakeSheet {
  constructor(rows) {
    this.rows = rows.map((row) => row.slice());
    this.maxRows = 20;
    this.maxColumns = 5;
  }
  getDataRange() { return { getValues: () => this.rows.map((row) => row.slice()) }; }
  getMaxRows() { return this.maxRows; }
  getMaxColumns() { return this.maxColumns; }
  insertRowsAfter(_, count) { this.maxRows += count; }
  insertColumnsAfter(_, count) { this.maxColumns += count; }
  setFrozenRows() {}
  hideSheet() {}
  getRange(row, column, rowCount, columnCount) {
    return {
      setValues: (values) => {
        while (this.rows.length < row - 1 + rowCount) this.rows.push(['', '', '', '', '=FORMULA']);
        values.forEach((valuesRow, rowIndex) => {
          for (let columnIndex = 0; columnIndex < columnCount; columnIndex++) {
            this.rows[row - 1 + rowIndex][column - 1 + columnIndex] = valuesRow[columnIndex];
          }
        });
      },
      clearContent: () => {
        for (let rowIndex = row - 1; rowIndex < row - 1 + rowCount; rowIndex++) {
          if (!this.rows[rowIndex]) continue;
          for (let columnIndex = column - 1; columnIndex < column - 1 + columnCount; columnIndex++) this.rows[rowIndex][columnIndex] = '';
        }
      }
    };
  }
}

class FakeBook {
  constructor() { this.sheets = {}; }
  getSheetByName(name) { return this.sheets[name] || null; }
  insertSheet(name) { this.sheets[name] = new FakeSheet([['referenceDate', 'Vendedor', 'Peso acumulado (kg)', 'Observação']]); return this.sheets[name]; }
}

const exported = {};
const context = { console, globalThis: { __ASTER_TEST__: exported } };
vm.runInNewContext(fs.readFileSync('apps_script/Code.gs', 'utf8'), context);

const headers = ['Data', 'Vendedor', 'Peso do dia (kg)', 'Observação'];
const sheet = new FakeSheet([
  headers.concat(['=FORMULA']),
  ['2026-09-01', 'VENDEDOR A', 10000, 'manual'].concat(['=FORMULA']),
  ['2026-09-02', 'VENDEDOR A', 15000, 'manual'].concat(['=FORMULA'])
]);

function payload(referenceDate, rows) {
  return { referenceDate, dataMode: 'daily_rows', headers, rows };
}

exported.applyDaily(sheet, payload('2026-09-03', [['2026-09-03', 'VENDEDOR A', 8000, 'Automação Aster']]));
assert.deepStrictEqual(sheet.rows.slice(0, 4).map((row) => row.slice(0, 4)), [
  headers,
  ['2026-09-01', 'VENDEDOR A', 10000, 'manual'],
  ['2026-09-02', 'VENDEDOR A', 15000, 'manual'],
  ['2026-09-03', 'VENDEDOR A', 8000, 'Automação Aster']
]);
assert.strictEqual(sheet.rows[1][4], '=FORMULA');
assert.strictEqual(sheet.rows[2][4], '=FORMULA');

exported.applyDaily(sheet, payload('2026-09-02', [['2026-09-02', 'VENDEDOR A', 14000, 'Automação Aster']]));
const result = sheet.rows.slice(1, 4).map((row) => row.slice(0, 3));
assert.deepStrictEqual(result, [
  ['2026-09-01', 'VENDEDOR A', 10000],
  ['2026-09-03', 'VENDEDOR A', 8000],
  ['2026-09-02', 'VENDEDOR A', 14000]
]);
assert.strictEqual(result.reduce((total, row) => total + row[2], 0), 32000);
console.log('daily_rows upsert test: ok');
console.log(JSON.stringify({ first_total: 33000, second_total: 32000, formulas_preserved: true }));

const book = new FakeBook();
const cumulativeSheet = new FakeSheet([headers.concat(['=FORMULA'])]);
const cumulativePayload = (referenceDate, rows) => ({
  referenceDate,
  dataMode: 'cumulative_by_seller',
  headers: ['Data', 'Vendedor', 'Peso acumulado (kg)', 'Observação'],
  rows
});
exported.applyCumulative(book, cumulativeSheet, cumulativePayload('2026-09-01', [['2026-09-01', 'VENDEDOR A', 100, 'snapshot']]));
exported.applyCumulative(book, cumulativeSheet, cumulativePayload('2026-09-02', [['2026-09-02', 'VENDEDOR A', 250, 'snapshot']]));
assert.deepStrictEqual(cumulativeSheet.rows.slice(1, 3).map((row) => row.slice(0, 3)), [
  ['2026-09-01', 'VENDEDOR A', 100],
  ['2026-09-02', 'VENDEDOR A', 150]
]);
exported.applyCumulative(book, cumulativeSheet, cumulativePayload('2026-09-02', [['2026-09-02', 'VENDEDOR A', 220, 'snapshot corrigido']]));
assert.deepStrictEqual(cumulativeSheet.rows.slice(1, 3).map((row) => row.slice(0, 3)), [
  ['2026-09-01', 'VENDEDOR A', 100],
  ['2026-09-02', 'VENDEDOR A', 120]
]);
console.log('cumulative_by_seller snapshot test: ok');
