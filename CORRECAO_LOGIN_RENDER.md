# Correção do login ASTER no Render

## Validação real de navegação e extração — 08/09/2026

Foi executado `login_and_extract()` com as credenciais locais, em Chromium headless, sem publicação ou e-mail. Resultado: login, abertura do relatório, filtros de 01/09/2026 a 07/09/2026, exportação e parsing concluídos. O arquivo possui 21 registros, sendo 17 de varejo e quatro de atacado. A suíte passou com 23 testes.

O problema de abertura foi confirmado na interface: a aba Relatórios não contém o cartão procurado. A aba Tudo exibe o cartão; a outra ocorrência de “Resumo Comercial” pertence a um menu oculto. O título/ícone do cartão não recebem eventos de ponteiro; o alvo é o contêiner com o botão de favoritos. O código agora seleciona esse contêiner por estrutura semântica, exige a rota `/ExecuteReport/` e aceita abertura em nova aba. O identificador completo da rota não foi fixado: ele mudou entre sessões observadas.

Os inputs reais de data são editáveis, com rótulos três níveis acima e sem associação `label/for`. O resolvedor agora procura o contexto de um único campo nesses ancestrais. A digitação seguida de Tab foi validada no ERP; não foi necessário manipular o calendário nem remover atributos do DOM.

O botão “Baixar XLSX” entregou um CSV UTF-8 com BOM. A exportação foi restabelecida e preserva a extensão informada pelo download; os nomes locais incluem microssegundos. O parser aceita zero numérico e rejeita números não finitos. A publicação diária exige datas por linha quando o período engloba vários dias, impedindo lançar um resumo mensal como venda de um dia.

A consulta somente de leitura ao endpoint em uso mostrou abas `_Snapshots Acumulados` e `_Historico de Cargas`, com cargas manuais inclusive no modo `date_balance`, ausente no contrato local. Os vendedores da planilha usam nomes curtos e os do ERP usam prefixos/nomes completos. A integração final depende do código Apps Script atualmente implantado para preservar filtro de varejo, mapeamento de vendedores e cálculo do acumulado. Nenhuma escrita real foi feita na planilha durante essa validação.

## Diagnóstico e correção local — 08/09/2026

O log desta data confirma login concluído em `/Companies`. Após o clique no cartão, a página estava em `/Workspace`, com três campos de pesquisa e nenhum texto de data. O resolvedor aceitou pontuação negativa (-80), selecionou `Pesquisar...` com `readonly` e `fill("01/09/2026")` terminou em timeout de 90 segundos. O log não comprova que o formulário do relatório tenha aberto; também não permite atribuir essa falha aos avisos de WebSocket ou ao encerramento posterior por SIGTERM.

A correção local rejeita pesquisa e controles não editáveis, aguarda novos elementos no DOM e exige identificação de início/fim ou seletores explícitos sem ambiguidade. Os dois campos são resolvidos antes de preencher; se forem o mesmo elemento, a execução é interrompida. Inputs nativos de data recebem ISO; controles textuais recebem DD/MM/YYYY. O valor é conferido após Tab. O fallback Groq passa pelas mesmas validações e deixa de reiniciar o contador de tentativas quando recebe `wait`.

Se o formulário não aparecer, o fluxo salva `output/aster_report_date_filters_failed.html` e `.png` e não confirma o período nem segue para extração/publicação. Seletores explícitos podem ser necessários para controles sem rótulos identificáveis. Nenhuma classe gerada `sc-*` foi incorporada como seletor.

Os testes de regressão em `tests/test_report_dates.py` usam HTML local no Chromium, sem acessar ERP, Groq, planilha ou e-mail reais. Para executá-los, é necessário ter o Chromium correspondente ao Playwright instalado. Esta alteração ainda exige validação na interface real e implantação; não restaura o processamento de planilha removido anteriormente.

As seções abaixo registram o histórico anterior desta correção.

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

A última alteração relacionada ao Apps Script e ao modo `date_balance` foi deliberadamente revertida a pedido do usuário. O pacote atual não usa o anexo anterior como contrato da planilha. O foco desta versão é o fluxo Playwright de login, navegação para o relatório e extração dos dados.

O anexo mais recente é um snapshot do DOM da SPA de login. Ele confirma a presença de `#loading`, `#root`, elementos de modal e marcas visuais do Aster, mas não deve ser usado para selecionar classes `sc-*` geradas. Os seletores de login ficam baseados em `input#email`, `input[type="email"]`, `input[placeholder="Senha"]`, `input[type="password"]` e `button[type="submit"]`.

A publicação passou a ser o comportamento padrão de `main.py`; ela ainda pode ser desativada explicitamente com `DAILY_COMPARISON_ENABLED=false`. O Render precisa possuir `SHEETS_API_URL`, `SHEETS_API_TOKEN` e, opcionalmente, `SHEETS_OUTPUT_TAB=1_Lançamentos Diários` configurados como variáveis de ambiente. O token não deve ser enviado no chat nem gravado no repositório.

## Validação realizada

A compilação dos módulos Python e os sete testes unitários existentes passaram. A verificação do Apps Script permanece referente à versão anterior, antes do último anexo, porque a alteração `date_balance` foi revertida conforme solicitado.

O novo log confirmou que o login agora funciona: o bot encontrou e preencheu e-mail e senha, enviou o formulário e chegou a `https://aster.gruposps.com.br/Companies`. A falha restante está no cartão: `text=Resumo Comercial` resolve um `span` que pode existir no DOM sem estar visível ou clicável. O código agora aguarda o painel Reports, reconsulta o DOM por até 30 segundos, sobe até seis níveis de ancestrais procurando um alvo visível e usa `button`, `a`, `role=button`, `tabindex=0` ou o container visível como alvo. Se falhar, salva `aster_report_card_timeout.*`.

O login e a escrita real na planilha ainda não foram executados nesta sessão porque não foram fornecidos valores de credenciais e token de produção. A validação final no Render deve acionar `/run`, consultar `/status` e verificar a nova linha na aba `1_Lançamentos Diários`.
