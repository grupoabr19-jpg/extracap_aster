# Auditoria do sistema Aster

Data: 08/09/2026. Base: `master`, commit `e8f2b91`.

## Escopo e conclusão

Foi lido integralmente o conteúdo dos 22 arquivos versionados presentes: 1.978 linhas, incluindo código, testes, configuração e documentação. Foram inspecionadas todas as pastas do projeto, o estado do Git, os 31 commits acessíveis pelas referências locais e os diffs das mudanças que explicam o comportamento atual. Há 25 commits alcançáveis a partir de HEAD; parte da evolução anterior está em outra linha de histórico.

O sistema ainda não está validado de ponta a ponta. Há falhas reproduzidas na seleção de datas, no parsing, na API e no contrato de publicação. O problema de maior impacto é a possibilidade de apagar o conteúdo da planilha e falhar antes de regravá-lo.

Esta auditoria não alterou a lógica de produção. Foi criado um ambiente `.venv` ignorado pelo Git para verificações locais. O `.env` foi inspecionado somente quanto aos nomes das variáveis; seus valores não foram expostos. Objetos binários e metadados internos de `.git` não foram tratados como código de aplicação. Não foram auditadas linha por linha as dependências de terceiros instaladas durante a análise.

Não houve acesso autenticado ao ERP, chamada real ao Groq, publicação em Google Sheets, envio de e-mail ou implantação. O código efetivamente implantado no Render e no Apps Script pode divergir deste checkout. Portanto, leitura de todos os arquivos próprios não equivale a garantir 100% do funcionamento em produção.

## Fluxo efetivo

`POST /run` → reserva de execução em memória → thread → `main.run()` → configuração e data de referência → Chromium → login → aba Reports → cartão → filtros → primeira tabela HTML → CSV → parser → POST Apps Script → SMTP.

- A data padrão é ontem no fuso de São Paulo; o filtro começa no primeiro dia do mês.
- `DAILY_COMPARISON_ENABLED` controla publicação, apesar do nome sugerir cálculo de metas.
- `daily_comparison.py`, a leitura de metas de `sheets_client.py` e `read_sales_report()` não participam do fluxo atual.
- O download XLSX deixou de ser executado. `ASTER_REPORT_DOWNLOAD_SELECTOR` permanece no exemplo, mas não é consumido.
- O Python aceita `daily_rows` e `cumulative_by_seller`; o Apps Script atual ignora `dataMode` e `referenceDate`.
- Não há agendador implementado no checkout atual. O acionamento depende de um serviço ou gatilho externo.

## Achados prioritários

### A01 — Crítico: limpeza destrutiva antes de uma escrita que pode falhar

Local: `apps_script/Code.gs:27–34`.

`getDataRange().getValues()` lê todas as colunas. As linhas preservadas mantêm essa largura, mas `setValues()` recebe uma faixa com a largura do payload, normalmente quatro. Se existir uma quinta coluna e uma linha histórica preservada, a matriz fica incompatível. Antes dessa validação, o script chama `sheet.clearContents()` na aba inteira.

Reprodução com serviços Google simulados: aba com cinco colunas, uma linha histórica e nova carga de quatro colunas → limpeza executada → erro de quantidade de colunas → aba simulada vazia. Mesmo quando não há incompatibilidade, a limpeza remove fórmulas e conteúdo além de A:D.

Correção proposta: validar completamente a carga e construir a matriz antes de escrever; limitar leitura/escrita automática a A:D; preservar colunas posteriores; garantir capacidade da faixa; limpar apenas sobras necessárias após uma escrita válida. Definir recuperação em caso de falha intermediária. Não há evidência de que uma planilha real tenha sido apagada nesta sessão.

### A02 — Alto: reprocessamento com múltiplas datas duplica histórico

Local: `apps_script/Code.gs:29–32`.

A remoção usa apenas a data da primeira linha recebida. Uma carga com 01/09 e 02/09 substitui 01/09 e mantém o antigo 02/09, inserindo outro registro de 02/09. Na simulação, 200 kg antigos e 220 kg corrigidos permaneceram juntos.

Além disso, `String(Date)` de uma célula tipada como data não é equivalente ao ISO enviado pelo Python. Não existe normalização de datas antes da comparação.

Correção proposta: definir quais datas a carga substitui, normalizá-las e aplicar substituição idempotente para todo o conjunto. Definir também como representar um dia corrigido para zero linhas, pois o parser atual recusa relatórios vazios.

### A03 — Alto: modo acumulado anunciado, mas sem processamento no destino

Local: `main.py:647–653`, `sheets_writer.py:9`, `apps_script/Code.gs:16–37`.

O Python soma por vendedor e envia `cumulative_by_seller`. O Apps Script não calcula diferenças nem mantém snapshots. Na simulação, snapshots de 100 e 250 produziram linhas de 100 e 250, em vez de 100 e 150. O cabeçalho do fluxo principal permanece “Peso do dia (kg)” também no modo acumulado.

Correção proposta: alinhar o contrato nos dois lados. Enquanto acumulado não estiver implementado e validado, rejeitá-lo explicitamente. Se for restaurado, especificar virada do mês, primeiro snapshot, dias ausentes, vendedor removido, correções retroativas e proteção de lançamentos manuais. Não restaurar cegamente uma versão histórica: a documentação registra que uma alteração anterior foi revertida deliberadamente.

### A04 — Alto: resumo mensal sem data pode ser publicado como venda de um dia

Local: `main.py:87–92`, `sales_parser.py:81–108`.

O período padrão vai do início do mês até a referência, mas o modo padrão é `daily_rows`. Sem coluna de data, o parser atribui a data de referência a todos os registros. Foi reproduzido que um resumo sem data com vendedor A e peso 100 vira um lançamento de 100 na referência.

Se o relatório for um resumo acumulado, isso transforma total mensal em venda diária. A estrutura real do relatório precisa ser confirmada; o mecanismo de atribuição foi demonstrado, mas sua ocorrência em produção não foi verificada. Uma coluna de data configurada com nome inexistente também resulta no fallback de data, em vez de erro de configuração.

Correção proposta: exigir coluna de data válida no modo diário, ou extrair estritamente um único dia; tratar resumo acumulado com contrato próprio. Rejeitar coluna explicitamente configurada que não exista e verificar período efetivo do relatório.

### A05 — Alto: datas preenchidas em formato incompatível com input nativo

Local: `main.py:91–92`, `main.py:550`, `main.py:564`.

O resolvedor dá preferência a `input[type=date]`, mas `Settings` gera datas `DD/MM/YYYY`. Reprodução em Chromium local: `fill('04/09/2026')` retorna `Malformed value`; `fill('2026-09-04')` funciona. A aplicação não converte o formato conforme o tipo de input. Erros de preenchimento do Playwright também não são cobertos uniformemente pelos blocos que capturam apenas `TimeoutError` nativo e `ValueError`.

Correção proposta: manter datas tipadas internamente, formatar ISO para inputs nativos e formato adequado para controles textuais; verificar valor após preenchimento e mudança de foco; validar início <= fim e coerência com a referência.

### A06 — Alto: campo de pesquisa pode ser selecionado como filtro de data

Local: `main.py:174–224`, especialmente `if score` na linha 212.

Pontuação negativa também é verdadeira em Python. Um input “Pesquisar” recebe -80, entra nos candidatos e pode ser escolhido imediatamente. Reprodução no Chromium: página com apenas esse input → ambos `start_date` e `end_date` resolvem para “Pesquisar”. Não há exigência de pontuação positiva nem garantia de que os dois campos sejam distintos. A busca usa toda a página, e “início” com acento não é normalizado para “inicio”.

Correção proposta: restringir a busca ao formulário ativo; exigir evidência suficiente de data; distinguir início/fim e rejeitar ambiguidade; esperar a montagem do formulário. O fallback genérico `input[type=date]` também pode resolver os dois campos para o primeiro input.

### A07 — Alto: espera não acompanha elementos que aparecem depois

Local: `main.py:149–171`; relacionado a `main.py:132–146`.

`_find_visible_element()` calcula a quantidade uma vez e lança `ValueError` imediatamente quando ela é zero. Se novos elementos aparecem durante a espera, a quantidade original não é atualizada. Reprodução: input programado para aparecer em 500 ms, timeout de 2 segundos → falha imediata.

O helper de login `_visible_locator()` continua usando `.first`, portanto não resolve o caso de primeiro elemento oculto e outro visível. Cada seletor também recebe no máximo cinco segundos, ainda que a configuração declare 90 segundos.

Correção proposta: usar um prazo global e reconsultar o DOM; selecionar elementos visíveis sem assumir a primeira correspondência; distinguir ausência temporária de seletor inválido.

### A08 — Alto: fallback Groq pode ultrapassar o limite de rodadas

Local: `main.py:295–301`, `groq_client.py:160–190`, `groq_client.py:302`.

Quando a primeira ação é `wait`, o código reinicia `rounds` e chama a si próprio. Isso neutraliza `MAX_ROUNDS=3`. Reprodução com resposta simulada `wait`: cinco consultas sucessivas, todas vendo contador 1; a auditoria interrompeu a quinta chamada. Não houve chamada ao serviço real.

Correção proposta: laço com limite global de rodadas e duração, sem reset dentro de uma recuperação. Validar status, quantidade de ações, tipo de alvo e campo esperado antes de aceitar a resposta. Hoje o limite de três ações está apenas no prompt e a confiança tem limiares parcialmente duplicados.

### A09 — Alto: extração não comprova atualização nem completude da tabela

Local: `main.py:572–587`.

Após clicar em Confirmar, o código espera apenas que a primeira tabela esteja visível. Uma tabela já visível pode conter dados anteriores enquanto a atualização é assíncrona. Não há verificação de período aplicado, conclusão da requisição, paginação, virtualização ou total de linhas. O CSV contém apenas o DOM extraído.

Achado por inspeção: depende da interface real para confirmar quais riscos ocorrem. Correção proposta: sinal observável de término do relatório, conferência do período e totais e extração completa, eventualmente via exportação XLSX se esse for o caminho confiável do ERP.

### A10 — Médio: zero numérico é rejeitado e formatos são convertidos silenciosamente

Local: `sales_parser.py:13–24`; lógica semelhante em `sheets_client.py:21–27`.

`value or ''` converte zero numérico em vazio. Reproduzido com `0`, `0.0`, `Decimal('0')` e um XLSX com peso zero: `Quantidade vazia`. A string `'0'` funciona.

Outros resultados reproduzidos: `'1,234.56'` → `1.23456`; `'abc123'` → `123`; `'1e3'` → `13`; `'1.234'` → `1.234`, que diverge de uma interpretação brasileira de milhar. O reconhecimento de “toneladas” como coluna também não converte unidade para kg.

Correção proposta: preservar valores numéricos nativos, definir formato e unidade de entrada, rejeitar lixo e conversões ambíguas. Cobrir CSV e XLSX, zeros, decimais, negativos, separadores e totais de rodapé. Nomes de vendedores não são consolidados no fluxo de registros e linhas de total podem ser tratadas como vendedor se não forem filtradas.

### A11 — Médio: validação do contrato é insuficiente

Local: `sheets_writer.py:11–35`, `apps_script/Code.gs:20–23`.

O validador Python aceitou em reprodução: data impossível `2026-02-31`, vendedor `None` e peso `True`. Compara datas como strings e não exige nomes/ordem dos quatro cabeçalhos. O Apps Script valida somente token, aba e presença de arrays; um cliente direto pode contornar as validações Python.

Correção proposta: validar datas reais, tipos estritos, cabeçalhos, modo, referência e largura de todas as linhas nos dois lados, antes de qualquer mutação. Definir limites e comportamento para carga vazia. Exigir respostas JSON com estrutura válida no cliente, não apenas assumir um objeto com `.get()`.

### A12 — Médio: API pode responder 500 para entrada inválida

Local: `web_app.py:54–61`.

Reprodução via cliente Flask e thread simulada: `reference_date: 123` → HTTP 500; data impossível textual → 400; data futura `2099-01-01` → 202. O parsing captura `ValueError`, mas não `TypeError`. Acesso a `request.json` também torna o comportamento dependente do Content-Type e do JSON recebido.

Correção proposta: validar objeto JSON e tipo de data antes do parsing; definir política explícita para datas futuras. A reserva em lock já protege concorrência dentro de um processo. Persistem estado volátil, ausência de recuperação após reinício e possibilidade de estado `running` ficar preso se `Thread.start()` falhar. Mais workers/réplicas exigiriam coordenação compartilhada.

### A13 — Médio: sanitização não remove dados sensíveis dentro de strings

Local: `groq_client.py:119–153`, `main.py:227–255`, `main.py:120–128`.

Reprodução com dados fictícios: chave `password` foi ocultada, mas token dentro de `url` e e-mail dentro de `visible_text` permaneceram. A função redige nomes de chaves conhecidos, não o conteúdo de strings. O erro também é interpolado separadamente no prompt. HTML e screenshots são gravados integralmente, e logs recebem console e URLs de requisições do ERP.

Correção proposta: enviar somente metadados necessários dos campos; remover query strings, texto de negócio e identificadores; controlar retenção e acesso a diagnósticos. `doGet()` retorna todas as abas, inclusive ocultas caso existam; o token na query string de `sheets_client.py` também merece substituição quando o contrato for revisto. Não foi constatado vazamento real nesta sessão.

### A14 — Médio: configuração, implantação e observabilidade incompletas

- `MAIL_TO` pode estar vazio; isso só será percebido na entrega, depois da publicação. SMTP não define timeout explícito. Falha de e-mail após uma escrita bem-sucedida marca a execução inteira como erro, sem indicar sucesso parcial.
- `/health` verifica algumas variáveis que possuem defaults, exige Sheets mesmo quando publicação está desligada e omite validações como destinatário, números, modo e navegabilidade. Retorna 200 mesmo degradado; não comprova funcionamento externo.
- `ASTER_REPORT_DATA_MODE` não aparece no `.env.example` nem nos nomes inspecionados do `.env` local. A configuração local cai no modo diário na ausência de override do processo.
- `ASTER_REPORT_DOWNLOAD_SELECTOR`, `ASTER_SALES_ACCUMULATED_COLUMN`, `ASTER_RENDER_URL` e `ASTER_RENDER_TOKEN` não controlam o fluxo atual. `post_login_wait_ms` e `working_days_remaining` são carregados, mas não utilizados nele.
- `GROQ_ENABLED`, chave, timeout e limiar não estão documentados integralmente no exemplo. Datas fixas no ambiente podem contrariar a referência solicitada; não há validação de coerência.
- Docker fixa imagem Playwright 1.62.0, mas o pacote permite qualquer versão >=1.45 e <2. Nesta instalação foi resolvida a mesma 1.62.0; não foi observado conflito atual. Builds futuros podem divergir. Não há lockfile.
- Docker/Gunicorn usam 8080 fixo; a variável PORT só é usada no servidor de desenvolvimento. O comando precisa estar alinhado ao ambiente de implantação. Docker não estava disponível para testar a imagem.
- CSVs têm nome com precisão de segundos; diagnósticos usam nomes fixos e são sobrescritos. Logs não possuem rotação. O logger reutiliza handlers mesmo se a pasta de configuração mudar.
- `.github` contém instruções, mas não um workflow de CI. Compilação isolada não detecta os problemas funcionais reproduzidos.

## Inventário de todos os arquivos versionados

| Arquivo | Linhas | Responsabilidade / resultado da leitura |
|---|---:|---|
| `main.py` | 665 | Orquestração; concentra A04–A09 e limitações operacionais. |
| `groq_client.py` | 304 | Cliente e validação de recuperação; A08 e A13. Enums e alguns campos não são consumidos integralmente. |
| `sales_parser.py` | 108 | CSV/XLSX; A04 e A10. Datas aceitam prefixo de dez caracteres; HTML complexo e cabeçalhos exigem cuidado. |
| `sheets_writer.py` | 52 | POST de quatro colunas; A03 e A11. |
| `sheets_client.py` | 66 | Leitura de metas desconectada; inferência depende de layout e primeira linha posterior com número; contexto pode vir de outro bloco. |
| `daily_comparison.py` | 20 | Cálculo desconectado; rejeita dias restantes <1, mas não metas negativas; requer regra para fim do mês. |
| `business_calendar.py` | 20 | Ontem em São Paulo; helper de dia útil separado usa data local e não é chamado. Data explícita não tem validação de tipo. |
| `web_app.py` | 80 | Gatilho, estado e health; A12 e A14. |
| `apps_script/Code.gs` | 47 | Backend atual de leitura/escrita; A01–A03, A11 e A13. ID de planilha está fixo no código. |
| `copy_page_html.js` | 23 | Utilitário manual de cópia; fallback informa sucesso sem verificar retorno de `execCommand`. |
| `tests/test_business_calendar.py` | 24 | Dois testes; passaram. Não cobre helper de dias úteis, feriados e tipos inválidos. |
| `tests/test_ingestion_contract.py` | 67 | Cinco testes; passaram. Não cobre contrato real do backend nem as entradas inválidas reproduzidas. |
| `tests/test_code_gs_contract.js` | 99 | Falha na primeira chamada a função removida; não valida a implementação atual. |
| `Dockerfile` | 7 | Container e Gunicorn; build não executado. |
| `requirements.txt` | 7 | Sete dependências diretas; instalação concluída em `.venv`. |
| `.env.example` | 62 | Exemplo parcialmente desatualizado; A14. |
| `.gitignore` | 9 | Ignora `.env`, `.venv`, caches, logs e output. |
| `.dockerignore` | 11 | Existe e exclui `.env`, variantes, Git, caches, logs e output. |
| `.github/copilot-instructions.md` | 5 | Instruções de segurança e compilação, respeitadas. |
| `CORRECAO_LOGIN_RENDER.md` | 43 | Registra reversão deliberada do Apps Script e estado anterior do login. |
| `Correção do login ASTER no Render.md` | 41 | Diverge do arquivo acima: afirma que funções de snapshot e testes estão disponíveis. |
| `PROMPT_ANALISE_SISTEMA.md` | 218 | Contexto histórico, não fonte confiável do estado atual: descreve funções removidas e problemas já corrigidos. |

Pastas presentes inicialmente: raiz, `.github`, `apps_script`, `tests`, `.git` e suas estruturas internas. Não havia logs nem saídas de produção disponíveis na árvore inicial. `.venv`, caches e `output` foram criados pelas verificações; o XLSX fictício foi removido após o teste.

## Histórico das modificações

As referências locais `origin/main` e `origin/master` apontam para linhas de evolução distintas. Não houve fetch; as referências são o que já existia neste checkout, não uma confirmação do remoto atual. O repositório não é shallow, mas `40b24cb` é uma raiz independente, e não descendente da primeira linha de evolução.

| Commit(s) | Mudança e efeito relevante |
|---|---|
| `10a0c29` | Inicialização com exclusões de arquivos sensíveis. |
| `0e0d5ce` | Primeira linha de implementação: aplicação, metas, calendário, Render, scripts de API e acionamento, documentação e testes. |
| `8aff683` | Execução passa a privilegiar dia corrido anterior. |
| `ae30328` | Remove cron local e configuração de agendamento no Render. |
| `3195d4d` | Adiciona menu de automação no Google Sheets. |
| `f749ac0` | Adiciona agendamento via Apps Script. Esses arquivos de menu/gatilho não estão no checkout atual. |
| `40b24cb` | Outra raiz: versão compacta do serviço com comparação de metas e download XLSX. Não interpretar ausência de arquivos da outra raiz como uma exclusão registrada neste commit. |
| `b2fde21`, `329f81f` | Diagnóstico de login e checagem de token no health. |
| `d239594`, `d00b2a6` | Espera explícita do login e adaptação à navegação SPA. |
| `59b4f8a` | Confirmação dos filtros antes da extração. |
| `2b23c48` | Reconhecimento de “Peso total”. |
| `80a8288` | Seletor estável para XLSX, posteriormente desconectado. |
| `0743095`, `eaa95b8` | Exemplo de ambiente e ferramenta manual de captura de HTML. |
| `5e611c5` | Substitui comparativo por cargas datadas, remove download XLSX do fluxo e introduz modo acumulado. Deixa módulos de metas desconectados. |
| `88f7484`, `97372a1`, `3f29dfa` | Diagnósticos/esperas adicionais e proteção contra execuções simultâneas. |
| `ace3082` | Importa conjunto local com `.dockerignore`, documentação, Apps Script mais amplo, testes e mudanças de login. |
| `c1f082c` | Merge entre conjunto local e linha do serviço; não integra automaticamente a outra raiz antiga. |
| `c5a1996` | Revisa login/cartão e processamento diário/acumulado do Apps Script. A lógica histórica também precisa de testes adicionais, especialmente entre meses. |
| `0aac704` | Reduz `Code.gs` para 47 linhas, removendo validações, snapshots, escrita restrita e funções expostas aos testes; altera clique do cartão. A documentação informa reversão deliberada. Python/testes não foram alinhados integralmente. |
| `872811f`, `9f4d0f3` | Tentativa de correção dos seletores seguida de reversão explícita. |
| `20f1243` | Introduz helper de primeiro elemento visível; contém A07. |
| `8b6d191` | Amplia diagnóstico após clique no cartão. |
| `976a21c` | Adiciona cliente Groq, fallback e configuração; contém A08 e sanitização incompleta. |
| `9224d91` | Inclui dependência requests, ausente no commit anterior. |
| `e8f2b91` | Altera default/fallback de modelo e adiciona pontuação DOM para filtros. Introduz A06 e mantém incompatibilidade de formato de datas. |

Problemas antigos já resolvidos: `Decimal` está importado; `.dockerignore` existe; `/run` reserva execução sob o mesmo lock da verificação. Não devem ser reapresentados como correções pendentes. Alegações antigas de login funcionando não substituem validação atual do ERP.

## Verificações executadas

Ambiente de auditoria: Windows, Python 3.14.6; dependências instaladas a partir de `requirements.txt`. Playwright 1.62.0, Flask 3.1.3, openpyxl 3.1.5. O Node incluído no driver Playwright foi usado porque não havia Node no PATH.

| Verificação | Resultado |
|---|---|
| `python -m py_compile` nos oito módulos de produção | Passou. |
| `python -m unittest discover -s tests -p "test_*.py" -v` | Sete testes passaram após instalar dependências/tzdata. A tentativa inicial sem tzdata pulou um teste. |
| `node tests/test_code_gs_contract.js` | Falhou na linha 59: `exported.applyDaily is not a function`. |
| Execução de `Code.gs` em V8 com serviços e planilha simulados | Demonstrou limpeza antes de erro de largura, duplicação de datas e acumulado sem delta. Não substitui integração Google real. |
| Chromium com HTML local fictício | Demonstrou formato inválido em input date, escolha de pesquisa para datas e falha imediata antes de input tardio. |
| Cliente Flask com thread simulada | Entrada numérica de data → 500; data impossível textual → 400; data futura → 202. Nenhuma automação foi iniciada. |
| Parser/validador com dados fictícios | Demonstrou zero XLSX rejeitado, conversões permissivas e payload inválido aceito. |
| Groq simulado | Demonstrou cinco tentativas apesar do limite de três e redição insuficiente em strings. |
| Docker / serviços externos / agendamento real | Não executados. |

Comandos locais reproduzíveis para a suíte existente:

```powershell
.venv/Scripts/python.exe -m py_compile main.py business_calendar.py daily_comparison.py groq_client.py sales_parser.py sheets_client.py sheets_writer.py web_app.py
.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_*.py' -v
& .venv/Lib/site-packages/playwright/driver/node.exe tests/test_code_gs_contract.js
```

Os experimentos adicionais foram executados durante a auditoria; suas entradas e resultados estão descritos nos achados. Não foram adicionados à suíte como testes que cristalizariam o comportamento defeituoso.

## Ordem de correção e critérios de aceite

1. **Proteger a planilha e alinhar o contrato.** Corrigir A01/A02/A11 antes de nova carga real. Aceite: quinta coluna e fórmulas preservadas, reenvio de múltiplas datas sem duplicação e carga inválida sem mutação.
2. **Definir significado dos dados.** Confirmar se o Resumo Comercial é diário, transacional ou acumulado; resolver A03/A04. Aceite: dados de referência conhecidos geram pesos diários corretos, inclusive na virada do mês.
3. **Tornar a navegação verificável.** Corrigir A05–A09. Aceite: campos tardios/ocultos, inputs textuais/nativos, ambiguidade e tabela em atualização tratados sem publicação de período errado.
4. **Fortalecer parsing e API.** Corrigir A10/A12, regras de unidade, datas, zero e estado da execução. Aceite: XLSX zero funciona, entradas inválidas recebem 400 e falhas parciais são identificáveis.
5. **Limitar exposição e operação.** Corrigir A13/A14, dependências, diagnósticos, destinatários, timeouts e documentação. Recuperar agendamento apenas após confirmar o mecanismo em uso.
6. **Validar integração em ambiente controlado.** Conferir versão implantada, relatório real, publicação em cópia da planilha e entrega de e-mail de teste com destinatário definido. Comparar contagem e soma do relatório com o destino antes de liberar execução recorrente.

O próximo trabalho de implementação já tem causas e critérios de validação identificados. O bloqueio funcional não é apenas login: envolve extração, semântica dos pesos e preservação dos dados no destino.
