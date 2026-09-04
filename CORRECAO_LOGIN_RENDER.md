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

## Validação realizada

A compilação dos módulos Python e os sete testes unitários existentes passaram. O teste de contrato JavaScript do Apps Script ainda falha por uma incompatibilidade pré-existente entre o arquivo `apps_script/Code.gs` e o harness de teste: o harness espera `globalThis.__ASTER_TEST__.applyDaily`, mas essa função de teste não está exposta pelo script atual. A correção do login não altera esse contrato.

O login real não foi executado nesta sessão porque não foram fornecidos valores de credenciais, e eles não devem ser enviados no chat nem incluídos no arquivo corrigido.
