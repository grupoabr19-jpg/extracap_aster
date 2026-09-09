# Implementação da correção Aster → Planilha

## Alterações aplicadas

A versão corrigida contém as seguintes mudanças:

1. `main.py` detecta automaticamente `/usr/bin/chromium` ou `google-chrome` quando `ASTER_CHROMIUM_EXECUTABLE` não é informado.
2. `main.py` permite configurar explicitamente `ASTER_CHROMIUM_EXECUTABLE`.
3. O fluxo deixou de usar `networkidle` como condição principal após login e abertura do relatório.
4. O fluxo aguarda estados funcionais: formulário, corpo da tela autenticada, workspace, rota do relatório, campos editáveis, tabela/mensagem ou download.
5. Service Workers do Aster são permitidos por padrão. O bloqueio permanece disponível somente por `ASTER_BLOCK_SERVICE_WORKERS=true` para diagnóstico.
6. `sheets_writer.py` permite `SHEETS_PUBLISH_TIMEOUT_SECONDS`, registra a duração de HTTP e retorna mensagem explícita quando ocorre timeout, sem repetir cegamente uma publicação cuja confirmação foi perdida.
7. O Apps Script passou a usar `_Histórico de Cargas` com acento, alinhado ao nome da aba existente.
8. O Apps Script rejeita pesos negativos antes da soma.
9. Os testes Playwright locais detectam Chromium instalado no sistema.
10. O teste JavaScript foi atualizado para usar o nome correto da aba de histórico.
11. `.env.example` documenta as novas configurações.

## Validação

- Compilação Python: passou.
- Testes Python: **27 passaram**.
- Smoke test Playwright com Chromium real: passou usando `/usr/bin/chromium`.
- Teste do contrato Apps Script: passou.
- Sintaxe dos três arquivos Apps Script: passou quando validada como JavaScript.

## Configuração de produção

Na aplicação Render, definir ou confirmar:

```text
ASTER_BLOCK_SERVICE_WORKERS=false
SHEETS_PUBLISH_TIMEOUT_SECONDS=45
```

Não é necessário definir `ASTER_CHROMIUM_EXECUTABLE` na imagem oficial Playwright se o navegador empacotado estiver disponível. Se o host usar Chromium do sistema, definir o caminho real, por exemplo:

```text
ASTER_CHROMIUM_EXECUTABLE=/usr/bin/chromium
```

## Publicação necessária

A versão corrigida ainda precisa ser publicada nos serviços externos:

1. fazer deploy do código Python atualizado no Render;
2. publicar simultaneamente `apps_script/Code.gs` e `apps_script/ResumoComercial.gs` no mesmo projeto Apps Script;
3. criar uma nova implantação/versionamento do Apps Script;
4. atualizar `SHEETS_API_URL` para a implantação correta, se a URL mudar;
5. executar uma única data de teste e conferir publicação, histórico, coluna E, totais e status.

Esta publicação não foi executada nesta sessão porque não há autorização/conexão operacional disponível para alterar o Render ou publicar o projeto Apps Script real. O pacote corrigido é seguro para revisão e publicação controlada.
