# Correção do login ASTER no Render

## Causa observada

O bot usava `locator("input")` em modo estrito, mas a tela possui pelo menos dois campos visíveis: e-mail e senha. Isso gera `strict mode violation`. Nas tentativas seguintes, o código aguardava apenas `input#email` e a SPA permanecia no estado de carregamento, causando timeout de 90 segundos.

Também havia uma validação frágil de autenticação: sair de `/Login` era tratado como prova suficiente de login, mesmo quando a sessão retornava para `/Login` ao abrir o relatório.

## O que foi alterado

A rotina de login agora espera a montagem da SPA, tenta os seletores configurados e fallbacks sem usar seletores genéricos de forma estrita, preenche e-mail e senha separadamente, submete o formulário e exige um estado autenticado antes de continuar. Quando uma etapa falha, salva URL implícita nos logs, HTML e screenshot em `output/` sem registrar credenciais.

A abertura do relatório também valida a sessão, aguarda a interface e reporta separadamente falhas de readiness, perda de sessão e ausência da tabela. O Chromium recebe argumentos adequados para o container Linux do Render, incluindo `--disable-dev-shm-usage` e `--no-sandbox` quando o processo roda como root.

O endpoint `/run` passou a reservar a execução no mesmo lock usado para a verificação de estado, evitando duas execuções simultâneas. Um `.dockerignore` também foi incluído para impedir que `.env`, logs, saídas e ambientes virtuais sejam copiados para a imagem.

## Render

Mantenha os valores sensíveis somente nas variáveis de ambiente do Render. O comando do container continua sendo:

```text
gunicorn --bind 0.0.0.0:8080 --workers 1 --timeout 1800 web_app:app
```

O `Dockerfile` usa a imagem oficial do Playwright Python, portanto não é necessário executar `playwright install` novamente dentro do container.

## Integração com a planilha

O objetivo operacional é `ASTER → Playwright → relatório → Apps Script → Google Sheets`. O ID da planilha usado pelo Apps Script corresponde ao documento informado e a aba de destino é `1_Lançamentos Diários`. O payload automático usa quatro colunas: `Data`, `Vendedor`, `Peso do dia (kg)` e `Observação`. A coluna `Região (automática)` observada na planilha permanece fora do payload para que a própria planilha continue calculando esse campo.

O Apps Script recebido tinha `doPost()` chamando `processAutomaticPayload_()` sem que essa função existisse. Isso fazia qualquer publicação falhar mesmo quando o login e a extração fossem concluídos. O pacote agora inclui as funções de carga diária e acumulada, substituição idempotente por data, snapshots ocultos para o modo acumulado, preservação das colunas posteriores e exposição controlada das funções para os testes de contrato.

A publicação passou a ser o comportamento padrão de `main.py`; ela ainda pode ser desativada explicitamente com `DAILY_COMPARISON_ENABLED=false`. O Render precisa possuir `SHEETS_API_URL`, `SHEETS_API_TOKEN` e, opcionalmente, `SHEETS_OUTPUT_TAB=1_Lançamentos Diários` configurados como variáveis de ambiente. O token não deve ser enviado no chat nem gravado no repositório.

## Validação realizada

A compilação dos módulos Python, os sete testes unitários existentes e os testes de contrato JavaScript do Apps Script passaram. Os testes confirmam o upsert diário por data, a preservação das fórmulas fora das quatro colunas automáticas e o cálculo de deltas para snapshots acumulados.

O novo log confirmou que o login agora funciona: o bot encontrou e preencheu e-mail e senha, enviou o formulário e chegou a `https://aster.gruposps.com.br/Companies`. A falha restante estava no cartão: `text=Resumo Comercial` resolvia um `span` oculto, então o Playwright esperava 90 segundos por um elemento que nunca seria clicável. O código agora percorre as ocorrências, procura o ancestral interativo visível (`button`, `a`, `role=button` ou `tabindex=0`) e só depois usa o container visível como fallback. Também salva `aster_report_card_timeout.*` caso nenhum alvo fique clicável.

O login e a escrita real na planilha ainda não foram executados nesta sessão porque não foram fornecidos valores de credenciais e token de produção. A validação final no Render deve acionar `/run`, consultar `/status` e verificar a nova linha na aba `1_Lançamentos Diários`.
