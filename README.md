# Validador de lancamentos contabeis

Aplicacao web estatica para validar lancamentos da tabela CT2 do Protheus.

## Como usar

1. Abra `index.html` no navegador.
2. Selecione um arquivo CSV, XLSX ou XLS da CT2.
3. Informe o mes analisado.
4. Confira as colunas detectadas: data, debito, credito e historico.
5. Ajuste os prefixos das contas de resultado e os lotes/historicos ignorados, se necessario.
6. Clique em `Analisar lancamentos`.

A tela mostra apenas divergencias: fornecedores cuja conta de resultado no mes analisado ainda nao apareceu nos meses anteriores. Quando houver historico anterior, a aplicacao lista as ultimas contas usadas para aquele fornecedor.

No arquivo analisado neste projeto, a CT2 usa `;` como separador e as principais colunas sao `Data Lcto`, `Cta Debito`, `Cta Credito` e `Hist Lanc`. O filtro padrao considera contas de resultado iniciadas por `32`, sem filtrar pela contrapartida.
Quando o arquivo tiver linhas `Cont.Hist`, a aplicacao junta essas continuacoes ao historico da linha principal antes de extrair o fornecedor.

## Colunas comuns da CT2

- `CT2_DATA`
- `CT2_DEBITO`
- `CT2_CREDIT`
- `CT2_HIST`

## Observacoes

- A extracao do fornecedor e feita a partir do historico, priorizando textos depois de marcadores como `NF` e `FORN`, e removendo numeros de documentos, datas, CNPJ/CPF e sufixos tecnicos como `HIST`.
- Historicos sem marcador de fornecedor ou nota fiscal nao entram na extracao de fornecedores.
- Por padrao, a analise ignora lotes/historicos de folha, autonomos, depreciacao e amortizacao.
- Como o historico contabil varia muito por empresa, ajuste a lista de palavras ignoradas para melhorar a identificacao dos fornecedores.
- Para XLSX/XLS, a pagina usa a biblioteca SheetJS via CDN.
