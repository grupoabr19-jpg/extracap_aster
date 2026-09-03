# Automacao de extracao Aster ERP

Projeto Python para autenticar no Aster ERP com Playwright, extrair uma tabela ou baixar um arquivo e enviar o resultado por SMTP.

O projeto tambem pode operar no Render com duas entradas para a mesma rotina:

- cron de segunda-feira a sexta-feira, as 08:00 UTC (05:00 em Brasilia);
- acionamento manual protegido por token a partir do Google Sheets.

Em ambos os casos, a data processada e o ultimo dia util encerrado. Uma nova
execucao para a mesma data substitui somente aquele dia e preserva o historico.

## Estrutura

```text
extracao_aster/
|-- main.py
|-- business_calendar.py  # ultimo dia util e feriados
|-- web_app.py            # endpoint do botao manual
|-- cron_trigger.py       # acompanha a execucao agendada
|-- sheets_client.py
|-- sync_sheet.py          # diagnostico antigo de leitura
|-- sheets_writer.py       # publica resultados via POST
|-- daily_comparison.py    # preserva o historico e monta os lancamentos diarios
|-- apps_script/
|   |-- SheetApi.gs       # endpoint seguro para leitura/escrita
|   `-- ManualTrigger.gs # funcao atribuida ao botao da planilha
|-- Dockerfile
|-- render.yaml
|-- requirements.txt
|-- .env.example
|-- .env                 # criar localmente; nunca versionar
|-- README.md
|-- output/              # CSV/downloads gerados
`-- logs/                # execucao.log
```

## Instalacao

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .env.example .env
```

Linux/macOS:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
```

Edite `.env` com usuario, senha, SMTP, destinatarios e seletores reais. O valor `ASTER_REPORT_URL` deve ser a URL final do relatorio, ou pode ser a pagina pos-login se ela contiver a tabela.

## Deploy no Render

O `render.yaml` cria:

1. `grupoabr-aster-varejo`: servico web que executa o Playwright e recebe o
   acionamento manual;
2. `grupoabr-aster-varejo-diario`: cron que chama o servico as 08:00 UTC,
   de segunda a sexta, e acompanha o status ate sucesso ou erro.

As variaveis marcadas como `sync: false` devem ser preenchidas no primeiro
deploy. Senhas e tokens nunca devem ser gravados no GitHub.

Os feriados e demais dias sem expediente podem ser informados em
`NON_WORKING_DATES`, separados por virgula:

```text
NON_WORKING_DATES=2026-09-07,2026-10-12,2026-11-02
```

O fuso deve permanecer `America/Sao_Paulo` e o modo normal deve permanecer
`REFERENCE_DATE_MODE=previous_business_day`.

### Botao na planilha

1. No Apps Script vinculado a planilha, crie um arquivo e cole o conteudo de
   `apps_script/ManualTrigger.gs`.
2. Nas propriedades do projeto, cadastre:
   - `ASTER_RENDER_URL=https://grupoabr-aster-varejo.onrender.com`
   - `ASTER_RENDER_TOKEN` com o mesmo valor de `TRIGGER_TOKEN` do Render.
3. Na aba `5_Configurações`, insira um desenho ou imagem com o texto
   **Atualizar agora**.
4. No menu do desenho, escolha **Atribuir script** e informe
   `atualizarVendasAgora`.

Na primeira utilizacao, o Google pedira autorizacao para a chamada externa.

## Execucao

```bash
python main.py
```

O processo roda headless por padrao, aguarda seletores visiveis, gera CSV quando extrai HTML e registra auditoria em `logs/execucao.log`. Para investigar seletores, use temporariamente `ASTER_HEADLESS=false`; a execucao agendada deve permanecer `true`.

## Conexao com a planilha por Apps Script

O arquivo `apps_script/SheetApi.gs` recebe os dados por `POST`. Durante a execucao diaria, o Python le as metas e o historico pelo `GET`, substitui somente os registros do dia corrente e publica novamente a aba `1_Lançamentos Diários` pelo `POST`.

### Publicar o endpoint

1. Abra a planilha e acesse **Extensoes > Apps Script**.
2. Crie um arquivo no editor e cole o conteudo de `apps_script/SheetApi.gs`.
3. Em **Project Settings > Script Properties**, crie as propriedades:
	- Name: `SPREADSHEET_ID`
	- Value: o ID da planilha de vendas
	- Name: `ASTER_SHEETS_TOKEN`
	- Value: o mesmo valor longo e aleatorio que sera colocado em `SHEETS_API_TOKEN`.
4. Clique em **Deploy > New deployment**.
5. Selecione **Web app**, execute como sua conta e permita acesso a **Anyone**. O token continua obrigatorio para ler os dados.
6. Copie a URL terminada em `/exec` para `SHEETS_API_URL` no `.env`.

O endpoint de escrita recebe `token`, `sheetName`, `headers` e `rows`, substitui o conteudo da aba indicada e congela a primeira linha. Antes da publicacao, o Python incorpora todas as linhas historicas ja existentes; assim, executar o processo novamente no mesmo dia atualiza aquele dia sem duplicar nem apagar os dias anteriores. Nao publique o token em Git nem em mensagens. Se um token real foi exposto, gere outro e atualize a propriedade do Apps Script e o `.env`.

### Testar a leitura da planilha

Para testar a leitura legada da planilha, depois de preencher `SHEETS_API_URL` e `SHEETS_API_TOKEN` no `.env`:

```powershell
& .\.venv\Scripts\python.exe sync_sheet.py
```

O comando cria `output/planilha_raw.json` com a resposta completa e `output/metas_vendedores.json` com os blocos normalizados. O parser reconhece `REGIAO`, `LIDER REGIONAL`, `SEGMENTO` e `VENDEDOR` mesmo quando os rótulos possuem acentos.

O cliente preserva corretamente o ponto decimal das metas em toneladas.

### Fluxo diario correto

O fluxo de producao e:

```text
Aster -> download CSV -> parser de vendas -> metas + historico da planilha
	 -> daily_comparison.build_daily_log_rows(...) -> publish_from_env(...)
	 -> 1_Lançamentos Diários -> rankings individual e regional -> e-mail com o CSV
```

Para ativar esse fluxo, configure no `.env`:

```text
DAILY_COMPARISON_ENABLED=true
SHEETS_OUTPUT_TAB=1_Lançamentos Diários
ASTER_SALES_VENDOR_COLUMN=Vendedor
ASTER_SALES_QUANTITY_COLUMN=Quantidade
ASTER_SALES_DATE_COLUMN=Data
ASTER_SALES_ACCUMULATED_COLUMN=
```

Os nomes dos campos podem ser alterados para os cabecalhos exatos da exportacao. O parser aceita acentos, maiusculas/minusculas e decimal brasileiro. Ele soma as vendas ate a data atual, separa a venda do dia e ignora vendedores que nao pertencem as metas. O relatorio do Aster e lido em toneladas e o valor publicado na aba 1 e convertido para quilogramas.

O Aster precisa estar configurado para baixar CSV ou XLSX. A coluna `Data` deve estar presente quando a exportacao cobrir mais de um dia, pois ela e usada para separar corretamente a venda do dia.

O `main.py` ja executa essa publicacao automaticamente quando `DAILY_COMPARISON_ENABLED=true`. Exemplo equivalente:

```python
from datetime import date
from decimal import Decimal
from daily_comparison import DAILY_LOG_HEADERS, build_daily_log_rows
from sheets_writer import publish_from_env

rows = build_daily_log_rows(
	existing_rows=existing_rows,
	targets=targets,
	sales_by_vendor={"VENDEDOR_EXEMPLO": Decimal("4.2")},
	reference_date=date.today(),
)
publish_from_env(DAILY_LOG_HEADERS, rows)
```

O resultado inclui uma linha por vendedor no dia, com data numerica compativel com o Google Sheets, peso em kg, observacao de auditoria e regiao. Os percentuais, comparativos e rankings sao calculados na propria planilha a partir das abas `1_Lançamentos Diários` e `4_Metas`.

## Mapeamento no DevTools

1. Abra o portal no Chrome e pressione F12; na aba **Elements**, use o seletor de elemento (Ctrl+Shift+C).
2. Selecione usuario, senha e o botao de login. Clique com o botao direito no elemento, escolha **Copy > Copy selector** e valide no Console com `document.querySelector('SELETOR')`.
3. Depois do login, abra o relatorio manualmente. Identifique a tabela (`table`) ou o botao de exportacao e copie um seletor estavel. Prefira `id`, `name`, `data-*` ou texto/atributo sem classes geradas dinamicamente.
4. Teste se a tabela aparece apos AJAX: no Console, execute `document.querySelectorAll('SELETOR').length`. O resultado esperado e maior que zero.
5. Preencha `ASTER_USERNAME_SELECTOR`, `ASTER_PASSWORD_SELECTOR`, `ASTER_LOGIN_BUTTON_SELECTOR`, `ASTER_REPORT_READY_SELECTOR`, `ASTER_REPORT_TABLE_SELECTOR` e, opcionalmente, `ASTER_REPORT_DOWNLOAD_SELECTOR`.
6. Se o botao abrir um download, informe `ASTER_REPORT_DOWNLOAD_SELECTOR`; caso contrario, deixe vazio e o script convertera a tabela HTML em CSV.

XPath tambem funciona, por exemplo `xpath=//input[@name='usuario']`, embora CSS seja preferivel. Nao registre valores de senha nem compartilhe o `.env`.

## Agendamento

### Windows Task Scheduler

Crie uma tarefa diaria com **Create Basic Task**, escolha `05:00`, selecione **Start a program** e informe:

- Program/script: `C:\caminho\extracao_aster\.venv\Scripts\python.exe`
- Add arguments: `C:\caminho\extracao_aster\main.py`
- Start in: `C:\caminho\extracao_aster`

Marque **Run whether user is logged on or not** e confirme que a conta tem acesso a rede, ao `.env` e ao SMTP.

Alternativa via PowerShell (ajuste caminhos e horario):

```powershell
$action = New-ScheduledTaskAction -Execute 'C:\caminho\extracao_aster\.venv\Scripts\python.exe' -Argument 'C:\caminho\extracao_aster\main.py' -WorkingDirectory 'C:\caminho\extracao_aster'
$trigger = New-ScheduledTaskTrigger -Daily -At 05:00
Register-ScheduledTask -TaskName 'Extracao Aster ERP' -Action $action -Trigger $trigger -User 'DOMINIO\usuario'
```

### Linux Cron

Edite o crontab com `crontab -e` e agende diariamente as 06:00:

```cron
0 5 * * * cd /caminho/extracao_aster && /caminho/extracao_aster/.venv/bin/python main.py >> logs/cron.log 2>&1
```

## Operacao e seguranca

- Mantenha `.env` fora do Git e limite as permissoes do arquivo.
- Use uma conta de servico com o menor privilegio necessario no Aster.
- Prefira SMTP com STARTTLS na porta 587 ou SSL na 465; nao use credenciais no codigo.
- O script retorna codigo 1 em falhas para que o Task Scheduler/Cron e monitoramento detectem o erro.
- Ajuste timeouts e seletores se o portal mudar; nao desabilite a espera explicita para contornar AJAX.
