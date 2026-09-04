/**
 * Acionamento manual da automacao hospedada no Render.
 *
 * A funcao configurarAutomacao solicita o token uma unica vez e o armazena
 * nas propriedades privadas do projeto.
 */
const ASTER_RENDER_URL_PADRAO = 'https://grupoabr-aster-varejo.onrender.com';

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Automação de vendas')
    .addItem('Atualizar agora', 'atualizarVendasAgora')
    .addItem('Consultar status', 'consultarStatusAutomacao')
    .addSeparator()
    .addItem('Ativar atualização diária (05h)', 'ativarAtualizacaoDiaria')
    .addItem('Desativar atualização diária', 'desativarAtualizacaoDiaria')
    .addSeparator()
    .addItem('Configurar integração', 'configurarAutomacao')
    .addToUi();
}

function configurarAutomacao() {
  const ui = SpreadsheetApp.getUi();
  const resposta = ui.prompt(
    'Configurar automação',
    'Cole o valor de TRIGGER_TOKEN configurado no Render:',
    ui.ButtonSet.OK_CANCEL
  );

  if (resposta.getSelectedButton() !== ui.Button.OK) return;

  const token = resposta.getResponseText().trim();
  if (!token) {
    ui.alert('O token não pode ficar vazio.');
    return;
  }

  PropertiesService.getScriptProperties().setProperties({
    ASTER_RENDER_URL: ASTER_RENDER_URL_PADRAO,
    ASTER_RENDER_TOKEN: token
  });
  ui.alert('Integração configurada. Use o menu Automação de vendas > Atualizar agora.');
}

function atualizarVendasAgora() {
  const resultado = acionarAtualizacao_();
  SpreadsheetApp.getActive().toast(
    resultado.mensagem,
    'Automação de vendas',
    8
  );
}

function executarAtualizacaoAgendada() {
  acionarAtualizacao_();
}

function acionarAtualizacao_() {
  const properties = PropertiesService.getScriptProperties();
  const baseUrl = properties.getProperty('ASTER_RENDER_URL');
  const token = properties.getProperty('ASTER_RENDER_TOKEN');

  if (!baseUrl || !token) {
    return {
      sucesso: false,
      mensagem: 'Configure a integração antes de executar.'
    };
  }

  const response = UrlFetchApp.fetch(baseUrl.replace(/\/$/, '') + '/run', {
    method: 'post',
    contentType: 'application/json',
    payload: '{}',
    headers: { Authorization: 'Bearer ' + token },
    muteHttpExceptions: true
  });

  const status = response.getResponseCode();
  const message = status === 202
    ? 'Atualização iniciada. Os rankings serão recalculados ao término.'
    : 'Não foi possível iniciar. Código HTTP: ' + status;

  return { sucesso: status === 202, mensagem: message };
}

function ativarAtualizacaoDiaria() {
  const properties = PropertiesService.getScriptProperties();
  if (!properties.getProperty('ASTER_RENDER_TOKEN')) {
    SpreadsheetApp.getUi().alert('Configure a integração antes de ativar o agendamento.');
    return;
  }

  removerGatilhosDiarios_();
  ScriptApp.newTrigger('executarAtualizacaoAgendada')
    .timeBased()
    .atHour(5)
    .nearMinute(0)
    .everyDays(1)
    .inTimezone('America/Sao_Paulo')
    .create();

  SpreadsheetApp.getUi().alert(
    'Atualização diária ativada para aproximadamente 05:00, no horário de São Paulo.'
  );
}

function desativarAtualizacaoDiaria() {
  const removidos = removerGatilhosDiarios_();
  SpreadsheetApp.getUi().alert(
    removidos
      ? 'Atualização diária desativada.'
      : 'Nenhum agendamento diário estava ativo.'
  );
}

function removerGatilhosDiarios_() {
  let removidos = 0;
  ScriptApp.getProjectTriggers().forEach(function(gatilho) {
    if (gatilho.getHandlerFunction() === 'executarAtualizacaoAgendada') {
      ScriptApp.deleteTrigger(gatilho);
      removidos++;
    }
  });
  return removidos;
}

function consultarStatusAutomacao() {
  const properties = PropertiesService.getScriptProperties();
  const baseUrl = properties.getProperty('ASTER_RENDER_URL');
  const token = properties.getProperty('ASTER_RENDER_TOKEN');
  if (!baseUrl || !token) {
    SpreadsheetApp.getUi().alert(
      'Use Automação de vendas > Configurar integração antes de consultar.'
    );
    return;
  }
  const response = UrlFetchApp.fetch(baseUrl.replace(/\/$/, '') + '/status', {
    method: 'get',
    headers: { Authorization: 'Bearer ' + token },
    muteHttpExceptions: true
  });
  SpreadsheetApp.getUi().alert(
    'Status da automação',
    response.getContentText(),
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}
