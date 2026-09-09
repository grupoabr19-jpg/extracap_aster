/**
 * Upload direto do CSV "Resumo Comercial" e ingestão do Aster.
 *
 * REGRA DEFINITIVA DE DADOS:
 * - toda carga representa o SALDO DE UMA ÚNICA DATA;
 * - não existe acumulado mensal na entrada;
 * - não existe cálculo de delta entre dias;
 * - Data + Vendedor é chave única;
 * - linhas repetidas do mesmo vendedor na mesma carga sao somadas;
 * - reenviar uma data substitui integralmente os registros daquela data;
 * - demais datas permanecem intactas;
 * - a coluna E de 1_Lançamentos Diários permanece preservada.
 *
 * A aba antiga _Snapshots Acumulados passa a ser apenas LEGADO.
 * Este arquivo NÃO lê e NÃO escreve nela.
 */

const RESUMO_UPLOAD_SHEET = '7_Upload Resumo Comercial';
const RESUMO_METAS_SHEET = '4_Metas';
const RESUMO_HISTORY_SHEET = '_Histórico de Cargas';
const RESUMO_BUTTON_TITLE = 'Upload de resumo comercial';
const LOAD_MODE_DATE_BALANCE = 'date_balance';
const RESUMO_OUTPUT_HEADERS = [
  'Data', 'Vendedor', 'Peso do dia (kg)', 'Observação'
];
const RESUMO_REVENUE_HEADER = 'Faturamento (R$)';

/**
 * Abre a janela com o seletor de arquivos.
 */
function abrirUploadResumoComercial() {
  const spreadsheet =
    SpreadsheetApp.getActiveSpreadsheet() ||
    SpreadsheetApp.openById(SPREADSHEET_ID);

  const today = formatDateKey_(getSpreadsheetToday_(spreadsheet));

  const html = HtmlService
    .createHtmlOutput(getUploadDialogHtml_(today))
    .setWidth(560)
    .setHeight(500);

  SpreadsheetApp.getUi().showModalDialog(
    html,
    'Upload de resumo comercial'
  );
}

/**
 * Mantém compatibilidade com o item antigo do menu.
 */
function abrirAbaResumoComercial() {
  abrirUploadResumoComercial();
}

/**
 * Recebe o CSV manual e grava o saldo da data informada.
 */
function processarArquivoResumoComercial(
  fileName,
  csvText,
  referenceDateText
) {
  const lock = LockService.getScriptLock();
  let spreadsheet = null;
  let referenceDate = null;
  const safeName = String(fileName || '').trim();

  try {
    lock.waitLock(30000);

    const content = String(csvText || '');

    if (!safeName || !/\.csv$/i.test(safeName)) {
      throw new Error('Selecione um arquivo CSV.');
    }

    if (!content.trim()) {
      throw new Error('O arquivo selecionado está vazio.');
    }

    spreadsheet = SpreadsheetApp.openById(SPREADSHEET_ID);

    const uploadSheet = spreadsheet.getSheetByName(RESUMO_UPLOAD_SHEET);
    const outputSheet = spreadsheet.getSheetByName(OUTPUT_SHEET);

    if (!uploadSheet || !outputSheet) {
      throw new Error('Aba de upload ou de lançamentos não encontrada.');
    }

    referenceDate = parseReferenceDateInput_(
      referenceDateText,
      spreadsheet,
      true
    );

    const csvValues = parseResumoCsv_(content);
    const directory = buildSellerDirectory_(spreadsheet, referenceDate);
    const summary = readResumoValues_(csvValues, directory);

    if (summary.validVarejoRows === 0) {
      throw new Error('Nenhuma linha válida de VAREJO foi encontrada.');
    }

    if (summary.unmatchedSellers.length > 0) {
      throw new Error(
        'Upload cancelado. Vendedores não reconhecidos em 4_Metas: ' +
        summary.unmatchedSellers.join(', ')
      );
    }

    if (summary.invalidWeightRows.length > 0) {
      throw new Error(
        'Upload cancelado. Peso inválido nas linhas: ' +
        summary.invalidWeightRows.join(', ')
      );
    }

    if (summary.invalidRevenueRows.length > 0) {
      throw new Error(
        'Upload cancelado. Faturamento inválido nas linhas: ' +
        summary.invalidRevenueRows.join(', ')
      );
    }

    writeRawUploadSheet_(uploadSheet, csvValues);

    const loadResult = replaceRowsForDate_(
      outputSheet,
      referenceDate,
      summary.totalsBySeller,
      'MANUAL'
    );

    const result = {
      fileName: safeName,
      processedAt: new Date(),
      referenceDate: referenceDate,
      validVarejoRows: summary.validVarejoRows,
      ignoredAtacadoRows: summary.ignoredAtacadoRows,
      source: 'MANUAL',
      mode: LOAD_MODE_DATE_BALANCE,
      sellersWritten: Object.keys(summary.totalsBySeller).length,
      rowsWritten: loadResult.rowsWritten,
      totalKg: loadResult.totalKg,
      totalRevenue: loadResult.totalRevenue
    };

    try {
      appendLoadHistory_(spreadsheet, {
        executedAt: result.processedAt,
        referenceDate: referenceDate,
        source: result.source,
        mode: result.mode,
        status: 'SUCESSO',
        rows: result.rowsWritten,
        totalKg: result.totalKg,
        fileName: safeName,
        message:
          'Saldo da data substituído integralmente; nenhuma diferença entre dias foi calculada.'
      });

      writeResumoStatus_(uploadSheet, result);
    } catch (historyError) {
      console.warn('Falha ao registrar auditoria: ' + historyError);
    }

    SpreadsheetApp.flush();

    return {
      success: true,
      message:
        'Upload concluído para ' + formatDateBr_(referenceDate) + '. ' +
        result.sellersWritten + ' vendedores, saldo do dia de ' +
        formatKg_(result.totalKg) + ' e faturamento de ' +
        formatCurrency_(result.totalRevenue) + '. Os registros anteriores dessa data foram substituídos.',
      sellersWritten: result.sellersWritten,
      totalKg: result.totalKg,
      totalRevenue: result.totalRevenue,
      ignoredAtacadoRows: result.ignoredAtacadoRows,
      referenceDate: formatDateKey_(referenceDate)
    };

  } catch (error) {
    if (spreadsheet) {
      try {
        appendLoadHistory_(spreadsheet, {
          executedAt: new Date(),
          referenceDate: referenceDate,
          source: 'MANUAL',
          mode: LOAD_MODE_DATE_BALANCE,
          status: 'ERRO',
          rows: 0,
          totalKg: 0,
          fileName: safeName,
          message: String(error.message || error)
        });
      } catch (ignoredHistoryError) {
        // O erro original é mais importante.
      }
    }

    throw error;

  } finally {
    try {
      lock.releaseLock();
    } catch (ignoredError) {
      // O bloqueio pode não ter sido adquirido em caso de timeout.
    }
  }
}

/**
 * Camada única de ingestão do Aster.
 *
 * Compatibilidade com o Python atual:
 * o Aster pode enviar o histórico inteiro + a fotografia da data mais recente.
 * Este método IGNORA as datas históricas do payload e utiliza SOMENTE
 * as linhas da data de referência (payload.referenceDate ou maior data recebida).
 *
 * Assim, a escrita continua obedecendo à regra:
 * uma execução altera somente uma data.
 */
function processAutomaticPayload_(spreadsheet, outputSheet, payload) {
  let referenceDate = null;

  try {
    const normalizedRows = normalizeAutomaticRows_(payload.rows, payload.headers);
    const latestDate = deriveMaxRowDate_(normalizedRows);

    referenceDate = payload.referenceDate
      ? parseReferenceDateInput_(
          payload.referenceDate,
          spreadsheet,
          true
        )
      : latestDate;

    if (!referenceDate) {
      throw new Error(
        'Não foi possível determinar a data de referência da carga do Aster.'
      );
    }

    const referenceKey = formatDateKey_(referenceDate);

    const dateRows = normalizedRows.filter(function (row) {
      return row[0] && formatDateKey_(row[0]) === referenceKey;
    });

    if (dateRows.length === 0) {
      throw new Error(
        'Nenhuma linha do Aster corresponde à data de referência ' +
        formatDateBr_(referenceDate) + '.'
      );
    }

    const directory = buildSellerDirectory_(spreadsheet, referenceDate);
    const totalsBySeller = {};
    const unmatched = {};

    dateRows.forEach(function (row) {
      const seller = matchSeller_(row[1], directory);

      if (!seller) {
        unmatched[String(row[1] || '').trim()] = true;
        return;
      }

      addSellerTotals_(
        totalsBySeller,
        seller,
        row[2],
        row.length >= 5 ? row[4] : 0
      );
    });

    const unmatchedNames = Object.keys(unmatched)
      .filter(Boolean)
      .sort();

    if (unmatchedNames.length > 0) {
      throw new Error(
        'Vendedores não reconhecidos em 4_Metas: ' +
        unmatchedNames.join(', ')
      );
    }

    if (Object.keys(totalsBySeller).length === 0) {
      throw new Error('Nenhum vendedor válido foi recebido do Aster.');
    }

    const loadResult = replaceRowsForDate_(
      outputSheet,
      referenceDate,
      totalsBySeller,
      'ASTER'
    );

    const result = {
      referenceDate: referenceDate,
      source: 'ASTER',
      mode: LOAD_MODE_DATE_BALANCE,
      rowsWritten: loadResult.rowsWritten,
      totalKg: loadResult.totalKg,
      totalRevenue: loadResult.totalRevenue
    };

    try {
      appendLoadHistory_(spreadsheet, {
        executedAt: new Date(),
        referenceDate: referenceDate,
        source: result.source,
        mode: result.mode,
        status: 'SUCESSO',
        rows: result.rowsWritten,
        totalKg: result.totalKg,
        fileName: '',
        message:
          'Saldo da data substituído pelo Aster. Linhas históricas recebidas no payload foram ignoradas para escrita.'
      });

      writeLastLoadStatus_(spreadsheet, result);
    } catch (historyError) {
      console.warn('Falha ao registrar auditoria: ' + historyError);
    }

    return result;

  } catch (error) {
    try {
      appendLoadHistory_(spreadsheet, {
        executedAt: new Date(),
        referenceDate: referenceDate,
        source: 'ASTER',
        mode: LOAD_MODE_DATE_BALANCE,
        status: 'ERRO',
        rows: 0,
        totalKg: 0,
        fileName: '',
        message: String(error.message || error)
      });
    } catch (ignoredHistoryError) {
      // O endpoint devolverá o erro original.
    }

    throw error;
  }
}

/**
 * Normaliza as linhas recebidas do Aster.
 *
 * Suporta as duas estruturas já existentes no projeto Python:
 *
 * 1) Log diário:
 *    Data | Vendedor | Peso do dia (kg) | Observação | Região
 *
 * 2) Comparativo de 12 colunas:
 *    Data | Região | Líder | Segmento | Vendedor | Meta (t) |
 *    Vendido no dia (t) | Vendido acumulado (t) | ...
 *
 * No segundo formato SOMENTE "Vendido no dia (t)" é usado.
 * "Vendido acumulado (t)" é deliberadamente ignorado.
 */
function normalizeAutomaticRows_(rows, headers) {
  if (!Array.isArray(rows)) {
    throw new Error('O campo rows é obrigatório.');
  }

  if (!Array.isArray(headers) || headers.length === 0) {
    throw new Error('O campo headers é obrigatório para identificar o payload do Aster.');
  }

  const normalizedHeaders = headers.map(normalizeText_);

  const dateIndex = findHeaderIndex_(normalizedHeaders, [
    'DATA'
  ]);

  const sellerIndex = findHeaderIndex_(normalizedHeaders, [
    'VENDEDOR',
    'VENDEDOR A',
    'CONSULTOR'
  ]);

  const typeIndex = findHeaderIndex_(normalizedHeaders, [
    'TIPO',
    'CANAL'
  ]);

  const dailyKgIndex = findHeaderIndex_(normalizedHeaders, [
    'PESO DO DIA KG',
    'PESO DIA KG',
    'VENDIDO NO DIA KG'
  ]);

  const dailyTonsIndex = findHeaderIndex_(normalizedHeaders, [
    'VENDIDO NO DIA T',
    'VENDIDO DIA T',
    'VENDA DO DIA T'
  ]);

  const observationIndex = findHeaderIndex_(normalizedHeaders, [
    'OBSERVACAO'
  ]);

  const revenueIndex = findHeaderIndex_(normalizedHeaders, [
    'VALOR TOTAL',
    'VALOR',
    'FATURAMENTO'
  ]);

  if (dateIndex < 0 || sellerIndex < 0) {
    throw new Error(
      'Payload do Aster sem as colunas Data e Vendedor.'
    );
  }

  if (dailyKgIndex < 0 && dailyTonsIndex < 0) {
    throw new Error(
      'Payload do Aster sem Peso do dia (kg) ou Vendido no dia (t). ' +
      'O campo acumulado não é aceito como saldo diário.'
    );
  }

  const usesTons = dailyKgIndex < 0 && dailyTonsIndex >= 0;
  const weightIndex = usesTons ? dailyTonsIndex : dailyKgIndex;

  return rows.map(function (row, index) {
    if (!Array.isArray(row) || row.length <= Math.max(dateIndex, sellerIndex, weightIndex)) {
      throw new Error('Linha inválida na posição ' + (index + 2) + '.');
    }

    if (
      typeIndex >= 0 &&
      row.length > typeIndex &&
      normalizeText_(row[typeIndex]) !== 'VAREJO'
    ) {
      return null;
    }

    const date = parseSheetDate_(row[dateIndex]);
    const seller = String(row[sellerIndex] || '').trim();
    const parsedWeight = parseFlexibleNumber_(row[weightIndex]);
    const parsedRevenue = revenueIndex >= 0 && row.length > revenueIndex
      ? parseCurrency_(row[revenueIndex])
      : 0;

    if (!date) {
      throw new Error('Data inválida na linha ' + (index + 2) + '.');
    }

    if (!seller) {
      throw new Error('Vendedor vazio na linha ' + (index + 2) + '.');
    }

    if (parsedWeight === null) {
      throw new Error('Peso inválido na linha ' + (index + 2) + '.');
    }

    if (parsedRevenue === null) {
      throw new Error('Faturamento inválido na linha ' + (index + 2) + '.');
    }

    const weightKg = usesTons
      ? round2_(Number(parsedWeight) * 1000)
      : round2_(Number(parsedWeight));

    return [
      date,
      seller,
      weightKg,
      observationIndex >= 0 && row.length > observationIndex
        ? String(row[observationIndex] || '').trim()
        : 'Automação Aster - saldo diário',
      round2_(parsedRevenue)
    ];
  }).filter(function (row) {
    return row !== null;
  });
}

/**
 * Localiza a primeira opção de cabeçalho disponível.
 */
function findHeaderIndex_(normalizedHeaders, candidates) {
  for (let i = 0; i < candidates.length; i++) {
    const index = normalizedHeaders.indexOf(candidates[i]);
    if (index >= 0) {
      return index;
    }
  }

  return -1;
}

/**
 * Retorna a maior data presente no payload do Aster.
 */
function deriveMaxRowDate_(rows) {
  return rows.reduce(function (latest, row) {
    const date = row[0];
    return date && (!latest || date > latest) ? date : latest;
  }, null);
}

/**
 * SUBSTITUI SOMENTE UMA DATA em 1_Lançamentos Diários.
 *
 * Não usa snapshot.
 * Não calcula delta.
 * Não reconstrói o mês.
 */
function replaceRowsForDate_(
  outputSheet,
  referenceDate,
  totalsBySeller,
  source
) {
  const rowCount = Math.max(0, outputSheet.getMaxRows() - 1);

  const existingRows = rowCount > 0
    ? outputSheet.getRange(2, 1, rowCount, 6).getValues().map(function (row) {
        return [row[0], row[1], row[2], row[3], row[5]];
      })
    : [];

  const referenceKey = formatDateKey_(referenceDate);

  const retainedRows = existingRows.filter(function (row) {
    const hasContent = row.some(function (cell) {
      return cell !== '' && cell !== null;
    });

    if (!hasContent) {
      return false;
    }

    const rowDate = parseSheetDate_(row[0]);

    if (!rowDate) {
      return true;
    }

    return formatDateKey_(rowDate) !== referenceKey;
  });

  const newRows = Object.keys(totalsBySeller)
    .sort()
    .map(function (seller) {
      return [
        referenceDate,
        seller,
        round2_(sellerTotalKg_(totalsBySeller[seller])),
        source + ' - saldo de ' + formatDateBr_(referenceDate),
        round2_(sellerTotalRevenue_(totalsBySeller[seller]))
      ];
    });

  if (newRows.length === 0) {
    throw new Error('A carga da data não possui vendedores válidos.');
  }

  const allRows = retainedRows
    .concat(newRows)
    .sort(compareOutputRows_);

  validateUniqueDateSellerRows_(allRows);

  overwriteOutputData_(
    outputSheet,
    RESUMO_OUTPUT_HEADERS,
    allRows,
    source
  );

  const totalKg = round2_(newRows.reduce(function (sum, row) {
    return sum + Number(row[2] || 0);
  }, 0));

  const totalRevenue = round2_(newRows.reduce(function (sum, row) {
    return sum + Number(row[4] || 0);
  }, 0));

  return {
    rowsWritten: newRows.length,
    totalKg: totalKg,
    totalRevenue: totalRevenue
  };
}

function addSellerTotals_(totalsBySeller, seller, kg, revenue) {
  if (!isFinite(Number(kg)) || Number(kg) < 0) {
    throw new Error('Peso inválido para o vendedor ' + seller + '.');
  }
  if (!totalsBySeller[seller] || typeof totalsBySeller[seller] !== 'object') {
    totalsBySeller[seller] = {
      kg: sellerTotalKg_(totalsBySeller[seller]),
      revenue: sellerTotalRevenue_(totalsBySeller[seller])
    };
  }

  totalsBySeller[seller].kg = round2_(
    Number(totalsBySeller[seller].kg || 0) + Number(kg || 0)
  );

  totalsBySeller[seller].revenue = round2_(
    Number(totalsBySeller[seller].revenue || 0) + Number(revenue || 0)
  );
}

function sellerTotalKg_(value) {
  return value && typeof value === 'object'
    ? Number(value.kg || 0)
    : Number(value || 0);
}

function sellerTotalRevenue_(value) {
  return value && typeof value === 'object'
    ? Number(value.revenue || 0)
    : 0;
}

/**
 * Ordena a saída por data e depois vendedor.
 */
function compareOutputRows_(a, b) {
  const dateA = parseSheetDate_(a[0]);
  const dateB = parseSheetDate_(b[0]);

  if (dateA && dateB && dateA - dateB !== 0) {
    return dateA - dateB;
  }

  if (dateA && !dateB) return -1;
  if (!dateA && dateB) return 1;

  return String(a[1] || '').localeCompare(String(b[1] || ''));
}

/**
 * Garante unicidade Data + Vendedor em toda a base final.
 */
function validateUniqueDateSellerRows_(rows) {
  const seen = {};
  const duplicates = [];

  rows.forEach(function (row) {
    const date = parseSheetDate_(row[0]);
    const seller = String(row[1] || '').trim();

    if (!date || !seller) {
      return;
    }

    const key = formatDateKey_(date) + '|' + normalizeText_(seller);

    if (seen[key]) {
      duplicates.push(formatDateBr_(date) + ' / ' + seller);
      return;
    }

    seen[key] = true;
  });

  if (duplicates.length > 0) {
    throw new Error(
      'A base final contém sobreposição de Data + Vendedor: ' +
      duplicates.join(', ') +
      '. A carga foi cancelada.'
    );
  }
}

/**
 * Registra auditoria das cargas.
 */
function appendLoadHistory_(spreadsheet, entry) {
  const sheet = getOrCreateTechnicalSheet_(
    spreadsheet,
    RESUMO_HISTORY_SHEET
  );

  const headers = [
    'ID', 'Executado em', 'Data de referência', 'Fonte', 'Modo',
    'Situação', 'Linhas', 'Peso total (kg)', 'Arquivo', 'Mensagem'
  ];

  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  }

  sheet.appendRow([
    Utilities.getUuid(),
    entry.executedAt || new Date(),
    entry.referenceDate || '',
    entry.source || '',
    entry.mode || LOAD_MODE_DATE_BALANCE,
    entry.status || '',
    Number(entry.rows || 0),
    Number(entry.totalKg || 0),
    entry.fileName || '',
    entry.message || ''
  ]);

  const lastRow = sheet.getLastRow();

  sheet.getRange(lastRow, 2)
    .setNumberFormat('dd/mm/yyyy hh:mm:ss');

  if (entry.referenceDate) {
    sheet.getRange(lastRow, 3)
      .setNumberFormat('dd/mm/yyyy');
  }

  sheet.getRange(lastRow, 8)
    .setNumberFormat('#,##0.00');

  sheet.setFrozenRows(1);

  if (!sheet.isSheetHidden()) {
    sheet.hideSheet();
  }
}

function getOrCreateTechnicalSheet_(spreadsheet, name) {
  return spreadsheet.getSheetByName(name) || spreadsheet.insertSheet(name);
}

function ensureSheetSize_(sheet, rowCount, columnCount) {
  if (sheet.getMaxRows() < rowCount) {
    sheet.insertRowsAfter(sheet.getMaxRows(), rowCount - sheet.getMaxRows());
  }

  if (sheet.getMaxColumns() < columnCount) {
    sheet.insertColumnsAfter(sheet.getMaxColumns(), columnCount - sheet.getMaxColumns());
  }
}

function formatOutputSheet_(sheet, rowCount, columnCount) {
  sheet.setFrozenRows(1);
  sheet.getRange(1, 1, 1, columnCount)
    .setBackground('#253575')
    .setFontColor('#FFFFFF')
    .setFontWeight('bold')
    .setFontFamily('Montserrat');

  if (rowCount > 1) {
    sheet.getRange(2, 1, rowCount - 1, 1).setNumberFormat('dd/mm/yyyy');
    sheet.getRange(2, 3, rowCount - 1, 1).setNumberFormat('#,##0.00');
    if (columnCount >= 6) {
      sheet.getRange(2, 6, rowCount - 1, 1).setNumberFormat('R$ #,##0.00');
    }
  }
}

/**
 * Atualiza o painel de status quando a fonte é o Aster.
 */
function writeLastLoadStatus_(spreadsheet, result) {
  const sheet = spreadsheet.getSheetByName(RESUMO_UPLOAD_SHEET);

  if (!sheet) {
    return;
  }

  writeResumoStatus_(sheet, {
    fileName: 'Automação Aster',
    processedAt: new Date(),
    referenceDate: result.referenceDate,
    source: result.source,
    mode: result.mode,
    sellersWritten: '',
    rowsWritten: result.rowsWritten,
    totalKg: result.totalKg,
    totalRevenue: result.totalRevenue,
    ignoredAtacadoRows: ''
  });
}

/**
 * Ponto único de sobrescrita da base.
 * Somente A:D e F são apagadas.
 * A coluna E é preservada e reparada.
 */
function overwriteOutputData_(sheet, headers, rows, sourceLabel) {
  if (!sheet) {
    throw new Error('Aba de saída não informada.');
  }

  if (!Array.isArray(headers) || headers.length < 3) {
    throw new Error('O cabeçalho precisa conter Data, Vendedor e Peso.');
  }

  if (!Array.isArray(rows)) {
    throw new Error('As linhas de saída são obrigatórias.');
  }

  const normalizedRows = rows.map(function (row, index) {
    if (!Array.isArray(row) || row.length < 3) {
      throw new Error('Linha inválida na posição ' + (index + 2) + '.');
    }

    return [
      row[0],
      row[1],
      row[2],
      row.length >= 4 ? row[3] : (sourceLabel || ''),
      row.length >= 5 ? row[4] : 0
    ];
  });

  const requiredRows = Math.max(2, normalizedRows.length + 1);

  ensureSheetSize_(sheet, requiredRows, 6);

  sheet
    .getRange(1, 1, sheet.getMaxRows(), 4)
    .clearContent();

  sheet
    .getRange(1, 6, sheet.getMaxRows(), 1)
    .clearContent();

  sheet
    .getRange(1, 1, 1, 4)
    .setValues([RESUMO_OUTPUT_HEADERS]);

  if (normalizedRows.length > 0) {
    sheet
      .getRange(2, 1, normalizedRows.length, 4)
      .setValues(normalizedRows.map(function (row) {
        return row.slice(0, 4);
      }));

    sheet
      .getRange(2, 6, normalizedRows.length, 1)
      .setValues(normalizedRows.map(function (row) {
        return [row[4]];
      }));
  }

  sheet.getRange('E1').setValue('Região (automática)');
  sheet.getRange('F1').setValue(RESUMO_REVENUE_HEADER);

  ensureOutputRegionFormulas_(sheet);
  formatOutputSheet_(sheet, requiredRows, 6);
}

function outputRegionFormula_(rowNumber) {
  return '=IF(B' + rowNumber + '="";"";IFERROR(INDEX(FILTER(' +
    "'4_Metas'!$B$2:$B$1000;" +
    "'4_Metas'!$E$2:$E$1000=B" + rowNumber + ";" +
    "'4_Metas'!$A$2:$A$1000=" +
    'DATE(YEAR(A' + rowNumber + ');MONTH(A' + rowNumber + ');1));1);""))';
}

function ensureOutputRegionFormulas_(sheet) {
  const rowCount = sheet.getMaxRows();

  if (rowCount <= 1) {
    return;
  }

  const formulas = [];

  for (let rowNumber = 2; rowNumber <= rowCount; rowNumber++) {
    formulas.push([outputRegionFormula_(rowNumber)]);
  }

  sheet
    .getRange(2, 5, rowCount - 1, 1)
    .setFormulas(formulas);
}

/**
 * Reinstala a ligação do botão existente, quando houver,
 * e atualiza as instruções da aba de upload.
 *
 * Não cria uma nova imagem em base64. Caso o botão antigo não exista,
 * o upload continua acessível pelo menu Automação de vendas.
 */
function instalarBotaoUploadResumoComercial() {
  const spreadsheet =
    SpreadsheetApp.getActiveSpreadsheet() ||
    SpreadsheetApp.openById(SPREADSHEET_ID);

  const sheet = spreadsheet.getSheetByName(RESUMO_UPLOAD_SHEET);

  if (!sheet) {
    throw new Error('Aba não encontrada: ' + RESUMO_UPLOAD_SHEET);
  }

  const existingButton = sheet.getImages().filter(function (image) {
    return image.getAltTextTitle() === RESUMO_BUTTON_TITLE;
  })[0];

  if (existingButton) {
    existingButton
      .setAltTextDescription(
        'Clique para selecionar o CSV com o saldo de uma única data'
      )
      .assignScript('abrirUploadResumoComercial');
  } else {
    sheet.getRange('H10:I12').clearContent();
    sheet.getRange('H10:I12').setValues([
      ['UPLOAD DE RESUMO COMERCIAL', 'Use também o menu Automação de vendas.'],
      ['Regra', 'Cada arquivo representa somente o saldo da data informada.'],
      ['Atenção', 'Se o mesmo vendedor aparecer mais de uma vez, os pesos serão somados.']
    ]);

    sheet.getRange('H10:I10')
      .setBackground('#253575')
      .setFontColor('#FFFFFF')
      .setFontWeight('bold');
  }

  sheet.getRange('H15:I20').setValues([
    ['COMO USAR', 'O botão/menu abre o explorador de arquivos.'],
    ['1', 'Clique em UPLOAD DE RESUMO COMERCIAL.'],
    ['2', 'Informe a data a que o saldo do arquivo pertence.'],
    ['3', 'Selecione o CSV Resumo Comercial referente àquela data.'],
    ['4', 'Clique em Enviar e substituir o saldo da data.'],
    ['5', 'Se a data já existir, somente essa data será substituída.']
  ]);

  sheet.getRange('H15:I15')
    .setBackground('#253575')
    .setFontColor('#FFFFFF')
    .setFontWeight('bold');

  sheet.getRange('H16:H20')
    .setBackground('#F18800')
    .setFontColor('#FFFFFF')
    .setFontWeight('bold')
    .setHorizontalAlignment('center');

  sheet.getRange('H10:I20').setFontFamily('Montserrat');

  spreadsheet.setActiveSheet(sheet);
  SpreadsheetApp.flush();
}

/**
 * Detecta o delimitador e lê o CSV.
 */
function parseResumoCsv_(csvText) {
  const clean = String(csvText || '').replace(/^\uFEFF/, '');

  const firstLine = clean.split(/\r?\n/).filter(function (line) {
    return line.trim() !== '';
  })[0] || '';

  const semicolons = (firstLine.match(/;/g) || []).length;
  const commas = (firstLine.match(/,/g) || []).length;

  const delimiter = semicolons > 0 && semicolons >= commas
    ? ';'
    : ',';

  return Utilities.parseCsv(clean, delimiter);
}

/**
 * Lê somente as linhas VAREJO e soma linhas repetidas do mesmo vendedor.
 */
function readResumoValues_(values, directory) {
  const header = findResumoHeader_(values);

  if (!header) {
    throw new Error(
      'Cabeçalho inválido. Use Vendedor, Tipo, Região, Segmento e Peso total.'
    );
  }

  const totalsBySeller = {};
  const unmatched = {};
  const invalidWeightRows = [];
  const invalidRevenueRows = [];

  let validVarejoRows = 0;
  let ignoredAtacadoRows = 0;

  for (let i = header.rowIndex + 1; i < values.length; i++) {
    const row = values[i];
    const rawSeller = String(row[header.columns.vendedor] || '').trim();
    const type = normalizeText_(row[header.columns.tipo]);

    if (!rawSeller && !type) {
      continue;
    }

    if (type !== 'VAREJO') {
      if (type === 'ATACADO') {
        ignoredAtacadoRows++;
      }
      continue;
    }

    const weight = parsePtBrNumber_(row[header.columns.pesoTotal]);
    const revenue = header.columns.valorTotal >= 0
      ? parseCurrency_(row[header.columns.valorTotal])
      : 0;
    const seller = matchSeller_(rawSeller, directory);

    if (weight === null) {
      invalidWeightRows.push(String(i + 1));
      continue;
    }

    if (revenue === null) {
      invalidRevenueRows.push(String(i + 1));
      continue;
    }

    if (!seller) {
      unmatched[rawSeller] = true;
      continue;
    }

    addSellerTotals_(totalsBySeller, seller, weight, revenue);
    validVarejoRows++;
  }

  return {
    totalsBySeller: totalsBySeller,
    validVarejoRows: validVarejoRows,
    ignoredAtacadoRows: ignoredAtacadoRows,
    unmatchedSellers: Object.keys(unmatched).sort(),
    duplicateSellers: [],
    invalidWeightRows: invalidWeightRows,
    invalidRevenueRows: invalidRevenueRows
  };
}

function findResumoHeader_(values) {
  const maximumRows = Math.min(values.length, 20);

  for (let rowIndex = 0; rowIndex < maximumRows; rowIndex++) {
    const normalized = values[rowIndex].map(normalizeText_);

    const columns = {
      vendedor: normalized.indexOf('VENDEDOR'),
      tipo: normalized.indexOf('TIPO'),
      regiao: normalized.indexOf('REGIAO'),
      segmento: normalized.indexOf('SEGMENTO'),
      pesoTotal: normalized.indexOf('PESO TOTAL'),
      valorTotal: normalized.indexOf('VALOR TOTAL')
    };

    if (
      columns.vendedor >= 0 &&
      columns.tipo >= 0 &&
      columns.regiao >= 0 &&
      columns.segmento >= 0 &&
      columns.pesoTotal >= 0
    ) {
      return {
        rowIndex: rowIndex,
        columns: columns
      };
    }
  }

  return null;
}

/**
 * Monta o diretório de vendedores válidos para o mês da data recebida.
 */
function buildSellerDirectory_(spreadsheet, referenceDate) {
  const sheet = spreadsheet.getSheetByName(RESUMO_METAS_SHEET);

  if (!sheet) {
    throw new Error('Aba não encontrada: ' + RESUMO_METAS_SHEET);
  }

  const values = sheet.getDataRange().getValues();
  const byNormalizedName = {};
  const bySeller = {};

  for (let i = 1; i < values.length; i++) {
    const month = parseSheetDate_(values[i][0]);
    const region = String(values[i][1] || '').trim();
    const seller = String(values[i][4] || '').trim();

    if (!seller || !month || !sameMonth_(month, referenceDate)) {
      continue;
    }

    const normalized = normalizeText_(seller);

    byNormalizedName[normalized] = seller;
    bySeller[seller] = {
      region: region
    };
  }

  if (Object.keys(bySeller).length === 0) {
    throw new Error('Não há vendedores em 4_Metas para o mês da carga.');
  }

  return {
    byNormalizedName: byNormalizedName,
    bySeller: bySeller
  };
}

function matchSeller_(rawSeller, directory) {
  const normalizedRaw = normalizeText_(
    String(rawSeller || '').replace(/^[^-]+\s*-\s*/, '')
  );

  if (directory.byNormalizedName[normalizedRaw]) {
    return directory.byNormalizedName[normalizedRaw];
  }

  const candidates = Object.keys(directory.byNormalizedName)
    .filter(function (name) {
      return (
        normalizedRaw === name ||
        normalizedRaw.indexOf(name + ' ') === 0 ||
        normalizedRaw.endsWith(' ' + name)
      );
    })
    .sort(function (a, b) {
      return b.length - a.length;
    });

  return candidates.length
    ? directory.byNormalizedName[candidates[0]]
    : null;
}

/**
 * Mantém uma cópia visual do último CSV recebido na aba 7.
 */
function writeRawUploadSheet_(sheet, values) {
  const columns = Math.min(6, Math.max(1, values[0].length));

  const normalized = values.map(function (row) {
    const result = [];

    for (let i = 0; i < columns; i++) {
      result.push(row[i] === undefined ? '' : row[i]);
    }

    return result;
  });

  ensureSheetSize_(sheet, Math.max(2, normalized.length), 10);

  sheet
    .getRange(1, 1, sheet.getMaxRows(), 6)
    .clearContent();

  sheet
    .getRange(1, 1, normalized.length, columns)
    .setValues(normalized);

  sheet
    .getRange(1, 1, 1, columns)
    .setBackground('#253575')
    .setFontColor('#FFFFFF')
    .setFontWeight('bold')
    .setFontFamily('Montserrat');

  sheet.setFrozenRows(1);
}

/**
 * Atualiza o painel visual da última carga.
 */
function writeResumoStatus_(sheet, result) {
  sheet.getRange('H1:I9').setValues([
    ['CONTROLE DE CARGAS', 'RESUMO COMERCIAL / ASTER'],
    ['Última carga', result.processedAt],
    ['Fonte', result.source || 'MANUAL'],
    ['Data do saldo', result.referenceDate],
    ['Modo', result.mode || LOAD_MODE_DATE_BALANCE],
    ['Situação', 'CARGA CONCLUÍDA; SALDO DA DATA SUBSTITUÍDO'],
    ['Arquivo', result.fileName || ''],
    ['Peso do dia (kg)', result.totalKg],
    ['Faturamento (R$)', result.totalRevenue || 0]
  ]);

  sheet.getRange('H1:I1')
    .setBackground('#253575')
    .setFontColor('#FFFFFF')
    .setFontWeight('bold');

  sheet.getRange('H2:H9')
    .setBackground('#F18800')
    .setFontColor('#FFFFFF')
    .setFontWeight('bold');

  sheet.getRange('H1:I9').setFontFamily('Montserrat');
  sheet.getRange('I2').setNumberFormat('dd/mm/yyyy hh:mm:ss');
  sheet.getRange('I4').setNumberFormat('dd/mm/yyyy');
  sheet.getRange('I8').setNumberFormat('#,##0.00');
  sheet.getRange('I9').setNumberFormat('R$ #,##0.00');
}

function getSpreadsheetToday_(spreadsheet) {
  const parts = Utilities
    .formatDate(
      new Date(),
      spreadsheet.getSpreadsheetTimeZone(),
      'yyyy-MM-dd'
    )
    .split('-');

  return new Date(
    Number(parts[0]),
    Number(parts[1]) - 1,
    Number(parts[2])
  );
}

function parsePtBrNumber_(value) {
  if (typeof value === 'number' && isFinite(value)) {
    return value;
  }

  let text = String(value || '').trim();

  if (!text) {
    return null;
  }

  const negative =
    text.indexOf('-') >= 0 ||
    /^\(.*\)$/.test(text);

  text = text
    .replace(/R\$/gi, '')
    .replace(/\s/g, '')
    .replace(/\./g, '')
    .replace(',', '.')
    .replace(/[()\-]/g, '')
    .replace(/[^0-9.]/g, '');

  const number = Number(text);

  if (!text || !isFinite(number)) {
    return null;
  }

  return negative ? -number : number;
}

function parseFlexibleNumber_(value) {
  if (typeof value === 'number' && isFinite(value)) {
    return value;
  }

  const text = String(value || '').trim();

  if (!text) {
    return null;
  }

  if (text.indexOf(',') >= 0) {
    return parsePtBrNumber_(text);
  }

  const number = Number(text.replace(/\s/g, ''));

  return isFinite(number) ? number : null;
}

function parseCurrency_(value) {
  if (value === '' || value === null || value === undefined) {
    return 0;
  }

  return parsePtBrNumber_(value);
}

function parseSheetDate_(value) {
  if (
    Object.prototype.toString.call(value) === '[object Date]' &&
    !isNaN(value.getTime())
  ) {
    return startOfDay_(value);
  }

  if (typeof value === 'number' && isFinite(value)) {
    // Converte o serial do Google Sheets sem passar por UTC, evitando
    // deslocamento de um dia no fuso America/Sao_Paulo.
    const base = new Date(1899, 11, 30);
    base.setDate(base.getDate() + Math.floor(value));
    return startOfDay_(base);
  }

  const text = String(value || '').trim();

  const brMatch = text.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})/);

  if (brMatch) {
    return validatedDate_(brMatch[3], brMatch[2], brMatch[1]);
  }

  const isoMatch = text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/);

  return isoMatch
    ? validatedDate_(isoMatch[1], isoMatch[2], isoMatch[3])
    : null;
}

function validatedDate_(year, month, day) {
  const y = Number(year);
  const m = Number(month);
  const d = Number(day);

  const date = new Date(y, m - 1, d);

  return (
    date.getFullYear() === y &&
    date.getMonth() === m - 1 &&
    date.getDate() === d
  )
    ? date
    : null;
}

function parseReferenceDateInput_(value, spreadsheet, rejectFuture) {
  const date = parseSheetDate_(value);

  if (!date) {
    throw new Error('Informe uma data de referência válida.');
  }

  if (rejectFuture && date > getSpreadsheetToday_(spreadsheet)) {
    throw new Error('A data de referência não pode estar no futuro.');
  }

  return date;
}

function formatDateKey_(date) {
  return Utilities.formatDate(
    date,
    Session.getScriptTimeZone(),
    'yyyy-MM-dd'
  );
}

function formatDateBr_(date) {
  return Utilities.formatDate(
    date,
    Session.getScriptTimeZone(),
    'dd/MM/yyyy'
  );
}

function startOfDay_(date) {
  return new Date(
    date.getFullYear(),
    date.getMonth(),
    date.getDate()
  );
}

function sameMonth_(dateA, dateB) {
  return (
    dateA.getFullYear() === dateB.getFullYear() &&
    dateA.getMonth() === dateB.getMonth()
  );
}

function normalizeText_(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, ' ')
    .trim()
    .replace(/\s+/g, ' ');
}

function round2_(value) {
  return Math.round(
    (Number(value) + Number.EPSILON) * 100
  ) / 100;
}

function formatKg_(value) {
  const parts = Number(value || 0).toFixed(2).split('.');

  parts[0] = parts[0].replace(
    /\B(?=(\d{3})+(?!\d))/g,
    '.'
  );

  return parts[0] + ',' + parts[1] + ' kg';
}

function formatCurrency_(value) {
  const parts = Number(value || 0).toFixed(2).split('.');

  parts[0] = parts[0].replace(
    /\B(?=(\d{3})+(?!\d))/g,
    '.'
  );

  return 'R$ ' + parts[0] + ',' + parts[1];
}

/**
 * HTML do upload manual.
 */
function getUploadDialogHtml_(today) {
  return [
    '<!doctype html><html><head><base target="_top">',
    '<style>',
    'body{font-family:Arial,sans-serif;margin:0;padding:24px;color:#253575;background:#f7f8fc}',
    '.card{background:white;border:1px solid #d9deef;border-radius:14px;padding:22px}',
    'h2{margin:0 0 8px;font-size:21px}p{color:#586174;line-height:1.45}',
    'label{display:block;margin:14px 0 6px;font-size:13px;font-weight:700}',
    'input{width:100%;box-sizing:border-box;padding:14px;border:1px solid #bbc3d7;border-radius:8px}',
    'button{margin-top:18px;width:100%;padding:13px;border:0;border-radius:8px;background:#253575;color:white;font-weight:700;font-size:15px;cursor:pointer}',
    'button:disabled{opacity:.55;cursor:wait}.note{font-size:12px;color:#6d7484}',
    '#status{margin-top:15px;white-space:pre-line;font-size:13px;font-weight:700}',
    '.ok{color:#137333}.error{color:#b3261e}',
    '</style></head><body><div class="card">',
    '<h2>Upload de resumo comercial</h2>',
    '<p><b>Cada arquivo representa o saldo de uma única data.</b> Ao reenviar uma data, os registros anteriores somente daquela data são substituídos. Não há cálculo de acumulado nem diferença entre dias.</p>',
    '<label for="referenceDate">Data a que este saldo pertence</label>',
    '<input id="referenceDate" type="date" required max="' + today + '" value="' + today + '">',
    '<label for="file">Arquivo Resumo Comercial (.csv)</label>',
    '<input id="file" type="file" accept=".csv,text/csv">',
    '<button id="send" onclick="sendFile()">Enviar e substituir o saldo da data</button>',
    '<div id="status"></div>',
    '<p class="note">Somente VAREJO é gravado. ATACADO é ignorado. Linhas repetidas do mesmo vendedor são somadas antes da publicação.</p>',
    '</div><script>',
    'function setStatus(text,kind){var s=document.getElementById("status");s.textContent=text;s.className=kind||"";}',
    'function sendFile(){var input=document.getElementById("file"),date=document.getElementById("referenceDate").value,button=document.getElementById("send"),file=input.files[0];',
    'if(!date){setStatus("Informe a data a que este saldo pertence.","error");return;}',
    'if(!file){setStatus("Selecione um arquivo CSV.","error");return;}',
    'if(file.size>5*1024*1024){setStatus("O arquivo deve ter no máximo 5 MB.","error");return;}',
    'button.disabled=true;setStatus("Lendo e validando o saldo da data...","");',
    'var reader=new FileReader();',
    'reader.onload=function(e){google.script.run.withSuccessHandler(function(result){setStatus(result.message,"ok");button.disabled=false;}',
    ').withFailureHandler(function(error){setStatus(error.message||String(error),"error");button.disabled=false;}',
    ').processarArquivoResumoComercial(file.name,e.target.result,date);};',
    'reader.onerror=function(){setStatus("Não foi possível ler o arquivo.","error");button.disabled=false;};',
    'reader.readAsText(file,"UTF-8");}',
    '</script></body></html>'
  ].join('');
}
