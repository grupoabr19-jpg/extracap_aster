/**
 * Acionador da automação hospedada no Render.
 *
 * A função configurarAutomacao solicita o TRIGGER_TOKEN
 * e o salva nas propriedades privadas do Apps Script.
 */

const ASTER_RENDER_URL_PADRAO =
  'https://grupoabr-aster-varejo.onrender.com';

const RENDER_URL_PROPERTY = 'ASTER_RENDER_URL';
const RENDER_TOKEN_PROPERTY = 'ASTER_RENDER_TOKEN';
const SCHEDULED_FUNCTION = 'executarAtualizacaoAgendada';

/**
 * Cria o menu sempre que a planilha é aberta.
 */
function onOpen() {
  try {
    instalarBotaoUploadResumoComercial();
  } catch (error) {
    console.error(
      'Não foi possível instalar o botão de upload: ' +
      String(error.message || error)
    );
  }

  SpreadsheetApp
    .getUi()
    .createMenu('Automação de vendas')
    .addItem(
      'Configurar integração',
      'configurarAutomacao'
    )
    .addSeparator()
    .addItem(
      'Atualizar agora',
      'atualizarVendasAgora'
    )
    .addItem(
      'Consultar status',
      'consultarStatusAutomacao'
    )
    .addSeparator()
    .addItem(
      'Ativar atualização diária (05h)',
      'ativarAtualizacaoDiaria'
    )
    .addItem(
      'Desativar atualização diária',
      'desativarAtualizacaoDiaria'
    )
    .addItem(
      'Consultar agendamento',
      'consultarAgendamento'
    )
    .addSeparator()
    .addItem(
      'Upload de resumo comercial',
      'abrirUploadResumoComercial'
    )
    .addItem(
      'Reinstalar botão de upload',
      'instalarBotaoUploadResumoComercial'
    )
    .addToUi();
}

/**
 * Solicita e armazena o TRIGGER_TOKEN do Render.
 */
function configurarAutomacao() {
  const ui = SpreadsheetApp.getUi();

  const response = ui.prompt(
    'Configurar integração',
    'Cole o valor da variável TRIGGER_TOKEN configurada no Render:',
    ui.ButtonSet.OK_CANCEL
  );

  if (response.getSelectedButton() !== ui.Button.OK) {
    return;
  }

  const token = response.getResponseText().trim();

  if (!token) {
    ui.alert('O token não pode ficar vazio.');
    return;
  }

  PropertiesService
    .getScriptProperties()
    .setProperties({
      ASTER_RENDER_URL: ASTER_RENDER_URL_PADRAO,
      ASTER_RENDER_TOKEN: token
    });

  ui.alert(
    'Integração configurada',
    'A integração com o Render foi configurada. Agora utilize Atualizar agora para fazer o primeiro teste.',
    ui.ButtonSet.OK
  );
}

/**
 * Aciona a atualização manualmente.
 */
function atualizarVendasAgora() {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();

  spreadsheet.toast(
    'Enviando solicitação ao Render...',
    'Automação de vendas',
    5
  );

  const result = triggerRenderUpdate_();

  spreadsheet.toast(
    result.message,
    'Automação de vendas',
    10
  );

  if (!result.success) {
    SpreadsheetApp.getUi().alert(
      'Falha no acionamento',
      result.message,
      SpreadsheetApp.getUi().ButtonSet.OK
    );
  }
}

/**
 * Função usada pelo gatilho diário.
 *
 * Não utiliza alertas, menus ou toast, porque é executada
 * em segundo plano pelo Google.
 */
function executarAtualizacaoAgendada() {
  const result = triggerRenderUpdate_();

  console.log(JSON.stringify({
    success: result.success,
    statusCode: result.statusCode,
    message: result.message,
    executedAt: new Date().toISOString()
  }));

  if (!result.success) {
    throw new Error(result.message);
  }
}

/**
 * Envia a chamada POST para o Render.
 */
function triggerRenderUpdate_() {
  const configuration = getRenderConfiguration_();

  if (!configuration.success) {
    return configuration;
  }

  try {
    const response = UrlFetchApp.fetch(
      configuration.baseUrl + '/run',
      {
        method: 'post',
        contentType: 'application/json',
        payload: JSON.stringify({}),
        headers: {
          Authorization:
            'Bearer ' + configuration.token
        },
        muteHttpExceptions: true,
        followRedirects: true
      }
    );

    const statusCode = response.getResponseCode();
    const responseText = response.getContentText();

    if (statusCode === 202) {
      return {
        success: true,
        statusCode: statusCode,
        message:
          'Atualização iniciada. Aguarde alguns minutos e consulte o status.'
      };
    }

    if (statusCode === 409) {
      return {
        success: true,
        statusCode: statusCode,
        message:
          'Já existe uma atualização em andamento.'
      };
    }

    if (statusCode === 401) {
      return {
        success: false,
        statusCode: statusCode,
        message:
          'Token recusado pelo Render. Confira o valor de TRIGGER_TOKEN.'
      };
    }

    return {
      success: false,
      statusCode: statusCode,
      message:
        'O Render respondeu com HTTP ' +
        statusCode +
        '. Resposta: ' +
        responseText
    };

  } catch (error) {
    return {
      success: false,
      statusCode: null,
      message:
        'Não foi possível acessar o Render: ' +
        String(error.message || error)
    };
  }
}

/**
 * Consulta o status da execução.
 */
function consultarStatusAutomacao() {
  const configuration = getRenderConfiguration_();

  if (!configuration.success) {
    SpreadsheetApp.getUi().alert(
      'Configuração pendente',
      configuration.message,
      SpreadsheetApp.getUi().ButtonSet.OK
    );
    return;
  }

  try {
    const response = UrlFetchApp.fetch(
      configuration.baseUrl + '/status',
      {
        method: 'get',
        headers: {
          Authorization:
            'Bearer ' + configuration.token
        },
        muteHttpExceptions: true,
        followRedirects: true
      }
    );

    const statusCode = response.getResponseCode();
    const responseText = response.getContentText();

    if (statusCode !== 200) {
      SpreadsheetApp.getUi().alert(
        'Falha na consulta',
        'HTTP ' + statusCode + ': ' + responseText,
        SpreadsheetApp.getUi().ButtonSet.OK
      );
      return;
    }

    const status = JSON.parse(responseText);

    const stateLabels = {
      idle: 'Nenhuma execução iniciada',
      running: 'Atualização em andamento',
      success: 'Atualização concluída',
      error: 'Atualização com erro'
    };

    const message = [
      'Situação: ' +
        (stateLabels[status.state] || status.state || 'desconhecida'),
      '',
      'Mensagem: ' +
        (status.message || 'Sem mensagem'),
      'Data processada: ' +
        (status.reference_date || 'Não informada'),
      'Início: ' +
        (status.started_at || 'Não informado'),
      'Término: ' +
        (status.finished_at || 'Não informado')
    ].join('\n');

    SpreadsheetApp.getUi().alert(
      'Status da automação',
      message,
      SpreadsheetApp.getUi().ButtonSet.OK
    );

  } catch (error) {
    SpreadsheetApp.getUi().alert(
      'Erro ao consultar',
      String(error.message || error),
      SpreadsheetApp.getUi().ButtonSet.OK
    );
  }
}

/**
 * Cria o gatilho diário próximo das 05:00.
 */
function ativarAtualizacaoDiaria() {
  const configuration = getRenderConfiguration_();

  if (!configuration.success) {
    SpreadsheetApp.getUi().alert(
      'Configuração pendente',
      configuration.message,
      SpreadsheetApp.getUi().ButtonSet.OK
    );
    return;
  }

  removeScheduledTriggers_();

  ScriptApp
    .newTrigger(SCHEDULED_FUNCTION)
    .timeBased()
    .atHour(5)
    .nearMinute(0)
    .everyDays(1)
    .inTimezone('America/Sao_Paulo')
    .create();

  SpreadsheetApp.getUi().alert(
    'Agendamento ativado',
    'A atualização será executada diariamente, próximo das 05:00, no horário de São Paulo.',
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}

/**
 * Remove o gatilho diário.
 */
function desativarAtualizacaoDiaria() {
  const removed = removeScheduledTriggers_();

  SpreadsheetApp.getUi().alert(
    'Agendamento',
    removed > 0
      ? 'Atualização diária desativada.'
      : 'Nenhum agendamento diário estava ativo.',
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}

/**
 * Mostra se o gatilho diário está instalado.
 */
function consultarAgendamento() {
  const triggers = ScriptApp
    .getProjectTriggers()
    .filter(function (trigger) {
      return (
        trigger.getHandlerFunction() ===
        SCHEDULED_FUNCTION
      );
    });

  SpreadsheetApp.getUi().alert(
    'Agendamento automático',
    triggers.length > 0
      ? 'Ativo: execução diária próxima das 05:00.'
      : 'Inativo: nenhum gatilho diário foi encontrado.',
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}

/**
 * Remove gatilhos anteriores da mesma função.
 */
function removeScheduledTriggers_() {
  let removed = 0;

  ScriptApp
    .getProjectTriggers()
    .forEach(function (trigger) {
      if (
        trigger.getHandlerFunction() ===
        SCHEDULED_FUNCTION
      ) {
        ScriptApp.deleteTrigger(trigger);
        removed++;
      }
    });

  return removed;
}

/**
 * Recupera e valida URL e token do Render.
 */
function getRenderConfiguration_() {
  const properties =
    PropertiesService.getScriptProperties();

  const baseUrl = String(
    properties.getProperty(RENDER_URL_PROPERTY) ||
    ASTER_RENDER_URL_PADRAO
  )
    .trim()
    .replace(/\/$/, '');

  const token = String(
    properties.getProperty(RENDER_TOKEN_PROPERTY) ||
    ''
  ).trim();

  if (!token) {
    return {
      success: false,
      statusCode: null,
      message:
        'Abra Automação de vendas → Configurar integração e informe o TRIGGER_TOKEN do Render.'
    };
  }

  return {
    success: true,
    baseUrl: baseUrl,
    token: token
  };
}