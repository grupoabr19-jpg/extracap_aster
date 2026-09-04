/*
 * Execute no Console do navegador depois de abrir a tela desejada.
 * Copia o HTML completo da pagina para a area de transferencia.
 */
(async function copyPageHtml() {
  const html = '<!DOCTYPE html>\n' + document.documentElement.outerHTML;

  try {
    await navigator.clipboard.writeText(html);
    console.log('HTML copiado para a area de transferencia.', html.length, 'caracteres');
  } catch (error) {
    const area = document.createElement('textarea');
    area.value = html;
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.focus();
    area.select();
    document.execCommand('copy');
    area.remove();
    console.log('HTML copiado usando o metodo alternativo.', html.length, 'caracteres');
  }
})();
