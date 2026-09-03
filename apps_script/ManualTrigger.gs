/**
 * Acionamento manual da automacao hospedada no Render.
 *
 * Propriedades obrigatorias do projeto:
 * - ASTER_RENDER_URL: https://grupoabr-aster-varejo.onrender.com
 * - ASTER_RENDER_TOKEN: mesmo valor de TRIGGER_TOKEN no Render
 */
function atualizarVendasAgora() {
  const properties = PropertiesService.getScriptProperties();
  const baseUrl = properties.getProperty('ASTER_RENDER_URL');
  const token = properties.getProperty('ASTER_RENDER_TOKEN');

  if (!baseUrl || !token) {
    SpreadsheetApp.getUi().alert(
      'Configure ASTER_RENDER_URL e ASTER_RENDER_TOKEN nas propriedades do Apps Script.'
    );
    return;
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

  SpreadsheetApp.getActive().toast(message, 'Automação de vendas', 8);
}

function consultarStatusAutomacao() {
  const properties = PropertiesService.getScriptProperties();
  const baseUrl = properties.getProperty('ASTER_RENDER_URL');
  const token = properties.getProperty('ASTER_RENDER_TOKEN');
  const response = UrlFetchApp.fetch(baseUrl.replace(/\/$/, '') + '/status', {
    method: 'get',
    headers: { Authorization: 'Bearer ' + token },
    muteHttpExceptions: true
  });
  SpreadsheetApp.getUi().alert(response.getContentText());
}
