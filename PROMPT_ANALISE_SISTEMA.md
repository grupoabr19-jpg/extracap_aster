# Prompt de entendimento e manutencao do sistema Aster

Use o texto abaixo como contexto para uma IA, desenvolvedor ou agente de manutencao. Ele descreve o sistema conforme o codigo atual. Nao trate funcionalidades descritas como disponiveis se estiverem marcadas como desconectadas, incompletas ou com falha.

## Prompt

Voce esta trabalhando no repositorio `extracao_aster`, uma automacao de integracao comercial. Analise e mantenha o sistema respeitando o comportamento real descrito abaixo.

### Objetivo de negocio

O sistema acessa o ERP Aster com Playwright, autentica usando configuracao externa, abre um relatorio comercial, extrai a tabela HTML, salva uma exportacao CSV, opcionalmente publica lancamentos em uma planilha Google Sheets por meio de Google Apps Script e envia o arquivo extraido por e-mail.

A operacao pode ser executada diretamente pelo modulo principal ou acionada por uma API Flask. A planilha funciona como destino dos lancamentos diarios e tambem possui uma rotina JavaScript para transformar snapshots acumulados em deltas diarios.

O projeto deve ser configurado por `.env`. Nunca inclua credenciais, tokens, senhas, IDs sensiveis ou seletores dependentes de sessao em commits, exemplos ou respostas.

### Fluxo efetivamente executado

1. `main.run()` carrega `.env`.
2. `resolve_reference_date()` determina a data de referencia. Sem data explicita, usa o dia anterior no fuso `America/Sao_Paulo`.
3. `Settings.from_env()` valida configuracoes obrigatorias e monta URLs, seletores, credenciais, SMTP, pastas e modo de dados.
4. `configure_logging()` cria `logs/execucao.log` e um logger de console.
5. Playwright inicia o Chromium em modo headless ou visivel.
6. `login_and_extract()` abre o Aster, preenche usuario e senha, envia o login, verifica se a URL deixou de conter `/login`, acessa o relatorio, aplica filtros e retorna o HTML da tabela.
7. `html_to_csv()` interpreta as tags `tr`, `td` e `th`, normaliza o texto e grava `output/aster_YYYYMMDD_HHMMSS.csv`.
8. Se `DAILY_COMPARISON_ENABLED` for verdadeiro, `read_sales_records()` le o CSV, identifica as colunas, converte quantidades para `Decimal`, descarta datas futuras e prepara os lancamentos.
9. `publish_from_env()` valida e envia um JSON por POST ao Google Apps Script.
10. O Apps Script autentica o token, valida a carga e atualiza a aba `1_Lançamentos Diários`.
11. Um e-mail SMTP e montado com o CSV como anexo e enviado por SSL ou STARTTLS.
12. O navegador e fechado no bloco `finally`.

### Arquivos e responsabilidades

#### `main.py`

E o orquestrador Python.

- `Settings`: dataclass imutavel com todas as configuracoes do Aster, relatorio, SMTP, e-mail, pastas e publicacao.
- `Settings.from_env()`: le variaveis de ambiente; `required()` rejeita valores ausentes; `items()` aceita listas separadas por virgula ou ponto e virgula; datas padrao sao o primeiro dia do mes e o dia atual do ambiente recebido.
- `configure_logging()`: cria diretorios e evita handlers duplicados.
- `login_and_extract()`: configura timeouts, executa login, abre o relatorio, seleciona filtros e devolve o HTML da tabela.
- `html_to_csv()`: usa `HTMLParser` para converter linhas HTML em CSV UTF-8 com BOM.
- `send_email()`: usa `SMTP_SSL` quando `smtp_security == "ssl"`; qualquer outro valor segue por STARTTLS.
- `run()`: encadeia extracao, conversao, parsing, publicacao e envio de e-mail.

Logica importante: o codigo importa `read_sales_records` e `publish_from_env`, mas a rotina `daily_comparison.py` nao e chamada. O campo `daily_comparison_enabled` controla a publicacao bruta, nao o calculo completo do comparativo.

Falha atual importante: no ramo `cumulative_by_seller`, `Decimal("0")` e utilizado sem importar `Decimal` em `main.py`. O modo acumulado causa `NameError` em tempo de execucao, embora a compilacao sintatica passe.

Outros pontos: `date` e `html` estao importados sem uso; a deteccao de login considera apenas a URL, e o nome do arquivo CSV tem precisao de segundos, podendo colidir em execucoes simultaneas.

#### `sales_parser.py`

Le CSV, TXT, XLSX e XLSM.

- `key()`: remove acentos, normaliza espacos e aplica comparacao sem diferenca entre maiusculas e minusculas.
- `number()`: remove unidade `kg` e caracteres estranhos; converte formatos brasileiros como `1.234,56` em `Decimal("1234.56")`.
- `row_date()`: aceita `DD/MM/YYYY`, `YYYY-MM-DD`, `DD-MM-YYYY` e `DD/MM/YY`.
- `read_rows()`: usa `csv.Sniffer` para texto e `openpyxl` para planilhas; para XLSX usa a primeira aba ativa.
- `read_sales_report()`: agrupa por uma lista conhecida de vendedores e retorna valores diarios e acumulados.
- `read_sales_records()`: retorna registros individuais `(data, vendedor, quantidade)`, rejeita relatorio vazio, vendedor vazio e quantidade invalida, e ignora registros futuros.

O parser resolve variacoes de nomes de colunas, mas nao consolida vendedores equivalentes com nomes diferentes. A funcao `read_sales_report()` existe, porem nao e usada pelo fluxo atual.

#### `sheets_writer.py`

Publica cargas no Apps Script.

- `VALID_DATA_MODES`: permite `daily_rows` e `cumulative_by_seller`.
- `_json_value()`: converte `Decimal` para `float` e `date` para ISO.
- `validate_payload()`: valida data de referencia, modo, cabecalhos, quantidade de colunas, vendedor, peso finito e data nao posterior.
- `publish_rows()`: monta o JSON, executa POST, trata erros HTTP/rede e exige `status == "ok"`.
- `publish_from_env()`: obtem endpoint, token e nome da aba do ambiente.

A validacao Python e estrutural e nao confirma se a data textual e uma data real. Tambem nao verifica nomes ou ordem dos cabecalhos e pode gerar erros de tipo para entradas malformadas.

#### `sheets_client.py`

Le metas e historico via endpoint GET do Apps Script.

- `VendorTarget`: representa regiao, lider, segmento, vendedor e meta em kg.
- `fetch_workbook()`: envia token na query string e espera uma resposta com lista `sheets`.
- `extract_vendor_targets()`: procura uma linha de vendedores, encontra uma linha numerica posterior e tenta reconstruir contexto de regiao, lider e segmento.
- `load_targets_from_env()`: exige `SHEETS_API_URL` e `SHEETS_API_TOKEN`.

Esse modulo esta desconectado de `main.py`. O token na URL pode aparecer em logs de proxy, historico ou monitoramento. A inferencia depende fortemente do layout da planilha.

#### `daily_comparison.py`

Implementa o calculo de comparativo por vendedor, mas nao e chamado no fluxo principal.

`calculate_rows()` calcula meta, venda do dia, venda acumulada, percentual da meta, saldo, dias uteis restantes e necessidade diaria. Usa `Decimal`, arredondamento `ROUND_HALF_UP` e impede zero dias uteis restantes. Metas negativas nao sao rejeitadas.

#### `business_calendar.py`

Centraliza datas de negocio.

- `resolve_reference_date()`: respeita data explicita; caso contrario usa o dia anterior no fuso configurado.
- `previous_calendar_day()`: delega para `resolve_reference_date()`.
- `last_business_day()`: ignora fins de semana e feriados ISO.

O endpoint `/run` usa `previous_calendar_day()`, nao `last_business_day()`. Portanto, uma execucao na segunda-feira pode escolher domingo.

#### `web_app.py`

Expõe a API de acionamento no Render.

- `/health`: retorna `ok` ou `degraded` e lista nomes de variaveis ausentes.
- `/status`: exige Bearer token e devolve estado em memoria.
- `/run`: exige Bearer token, aceita `reference_date` ISO, inicia uma thread daemon e responde HTTP 202.
- `worker()`: altera estado, chama `run()` e registra sucesso ou erro.
- `authorized()`: compara o Bearer token recebido com `TRIGGER_TOKEN`.

O estado nao e persistente. Existe condicao de corrida entre a verificacao de estado e a criacao da thread: duas requisicoes simultaneas podem iniciar duas execucoes. `datetime.utcnow()` produz timestamps UTC sem usar uma API explicita de timezone. A API aceita datas futuras se forem sintaticamente validas.

#### `apps_script/Code.gs`

E o backend Google Apps Script que protege e atualiza a planilha.

- Constantes definem planilha, propriedade do token, aba de saida, aba oculta de snapshots e cabecalhos.
- `doGet()`: valida token e devolve todas as abas e dados visiveis.
- `doPost()`: usa `LockService`, interpreta JSON, valida token e payload, aplica modo diario ou acumulado e retorna JSON.
- `validatePayload_()`: exige aba correta, data com formato ISO, modo valido, quatro cabecalhos, quatro celulas por linha, vendedor e peso validos.
- `applyDaily_()`: remove registros da mesma data e insere a nova carga; e um upsert por data, nao por vendedor.
- `applyCumulative_()`: salva snapshots, recalcula deltas do mes inteiro e reconstrui a aba diaria.
- `readRows_()`: le apenas A:D.
- `writeColumnsAD_()`: escreve A:D, aumenta linhas/colunas quando necessario, limpa sobras e preserva colunas posteriores, como formulas em E.
- `getSnapshotSheet_()`: cria e oculta a aba de snapshots quando necessario.
- `normalizeDate_()`: aceita objetos `Date`, ISO textual e `DD/MM/YYYY`.
- `sumKg_()`, `validate_()` e `json_()`: totalizacao, autenticacao e resposta JSON.

A estrategia acumulada guarda snapshots para que correcoes de um dia recalcularem os deltas posteriores. Registros manuais do mes podem ser substituidos durante a reconstrucao. A validacao de data confere formato textual, mas nao garante que dia e mes existam. `doGet()` expoe o conteudo completo das abas para qualquer portador do token.

#### `copy_page_html.js`

Script manual para colar no Console do navegador. Copia o HTML completo da pagina com `navigator.clipboard` e possui fallback por `textarea` e `document.execCommand`. O resultado do fallback nao e verificado, mas a mensagem de sucesso e exibida mesmo se a copia falhar.

#### `Dockerfile`

Usa imagem Playwright Python, instala `requirements.txt`, copia o contexto inteiro, define a porta 8080 e inicia Gunicorn com um worker e timeout de 1800 segundos. O worker unico reduz problemas de estado em memoria, mas nao elimina riscos de reinicio ou concorrencia.

Risco operacional: nao existe `.dockerignore`; `COPY . .` pode levar `.env`, logs, saidas e caches para a imagem, mesmo que o Git os ignore.

#### `requirements.txt`

Dependencias: Playwright, python-dotenv, tzdata, openpyxl, Flask e Gunicorn. Ha limites de versao, mas nao existe lockfile; builds diferentes podem resolver versoes diferentes dentro desses intervalos.

#### `.gitignore`

Ignora `.env`, ambiente virtual, caches Python/pytest, logs e arquivos de output. Isso reduz vazamento no Git, mas nao substitui `.dockerignore`.

#### `.github/copilot-instructions.md`

Informa que o projeto usa Python, Playwright e `.env`, proibe credenciais reais e recomenda `python -m py_compile main.py`. Essa verificacao cobre sintaxe, nao nomes indefinidos em todos os caminhos de execucao.

#### `tests/test_business_calendar.py`

Testa preservacao de data explicita e uso do fuso de Sao Paulo. Nao cobre fins de semana, feriados, tipos invalidos ou `last_business_day()`.

#### `tests/test_ingestion_contract.py`

Testa descarte de registros futuros, validacao de cargas diaria/acumulada, vendedor/peso invalidos e rejeicao de resposta Apps Script com erro. Nao cobre XLSX, colunas ausentes, datas invalidas, parsing numerico amplo, `main.run()` ou POST bem-sucedido.

#### `tests/test_code_gs_contract.js`

Carrega `Code.gs` em um contexto falso e testa insercao diaria, substituicao de data, preservacao de formulas fora de A:D, deltas acumulados e correcao de snapshot. Nao cobre endpoints, tokens, validacao completa, datas invalidas, erros HTTP ou dados manuais no acumulado.

#### `logs/` e `output/`

Sao artefatos de execucao. O log indica tentativas historicas de login e mensagens de uma versao anterior. Arquivos binarios de output nao fazem parte da logica do sistema e nao devem ser usados como fonte de comportamento sem inspecao especifica.

### Tecnicas e logicas de programacao utilizadas

- Automacao de navegador com Playwright e seletores configuraveis.
- Configuracao por ambiente para separar codigo de credenciais e infraestrutura.
- Dataclasses imutaveis para configuracoes e metas.
- Parsing de HTML, CSV e XLSX.
- Normalizacao Unicode para comparar cabecalhos e nomes.
- `Decimal` para reduzir erros de precisao em quantidades comerciais.
- Validacao de contratos antes de integrar com rede e planilha.
- HTTP JSON com tratamento de erros de transporte e resposta.
- SMTP com TLS para envio de relatorio.
- Lock de script no Apps Script e estado protegido por lock na API.
- Snapshot, ordenacao temporal e calculo de delta para transformar acumulados em lancamentos diarios.
- Thread daemon para tornar o disparo HTTP assincrono.

Essas tecnicas resolvem o problema de extrair dados de um sistema sem API de exportacao direta, transportar os dados para uma planilha compartilhada, manter idempotencia por data, recalcular correcoes historicas e notificar os destinatarios.

### Problemas conhecidos que devem ser tratados com cuidado

1. Importar `Decimal` em `main.py` antes de usar `cumulative_by_seller`.
2. Decidir se `daily_comparison.py` deve ser integrado a `main.py` ou permanecer como modulo futuro.
3. Corrigir a reserva atomica de execucao em `/run` para eliminar a condicao de corrida.
4. Criar `.dockerignore` para impedir que `.env`, logs e outputs entrem na imagem.
5. Validar datas com parser real, nao apenas regex ou comparacao textual.
6. Avaliar envio de token por query string em `sheets_client.py`.
7. Definir comportamento de segunda-feira e feriados no acionamento automatico.
8. Aumentar testes para login, parsing XLSX, datas invalidas, concorrencia, endpoints, SMTP e modos de publicacao.
9. Confirmar se o snapshot acumulado pode substituir registros manuais do mes.
10. Evitar afirmar que o sistema calcula metas e comparativo no fluxo atual sem integrar `sheets_client.py` e `daily_comparison.py`.

### Protocolo para futuras alteracoes

Antes de editar, identifique o modulo que realmente controla o comportamento e leia seus testes ou consumidores. Preserve o contrato de quatro colunas do Apps Script quando a alteracao for de publicacao. Nao exponha valores de `.env`. Depois de editar, execute primeiro o teste mais especifico e entao a validacao geral:

```text
python -m py_compile main.py business_calendar.py daily_comparison.py sales_parser.py sheets_client.py sheets_writer.py web_app.py
python -m unittest discover -s tests -p "test_*.py"
node tests/test_code_gs_contract.js
```

Ao relatar resultados, diferencie sempre:

- comportamento implementado e exercitado;
- comportamento implementado mas desconectado;
- falha conhecida;
- risco nao coberto por testes;
- mudanca proposta, sem afirmar que ela ja existe.
