# Validador de lancamentos contabeis

Aplicacao web estatica para validar lancamentos da tabela CT2 do Protheus.

## Como usar

### Backend web

Este e o caminho para rodar a aplicacao completa online ou localmente com API,
upload, SQLite e download do CSV de divergencias.

```powershell
pip install -r requirements.txt
uvicorn scripts.backend:app --reload --host 127.0.0.1 --port 8000
```

Depois abra:

```text
http://127.0.0.1:8000
```

O backend usa a pasta `data/` por padrao em ambiente local. Em hospedagem sem
disco persistente, configure `DATABASE_URL` com uma conexao Postgres da Neon. A
aplicacao usa o Postgres para guardar a base SQLite de trabalho de forma
persistente, e recria o arquivo local quando o servidor inicia.

Defina tambem `APP_PASSWORD` no ambiente online. Quando essa variavel existe,
a aplicacao exige senha via autenticacao basica do navegador.

Variaveis para Render + Neon:

- `DATABASE_URL`: connection string da Neon.
- `APP_PASSWORD`: senha de acesso ao sistema.
- `DATA_DIR`: pode ficar como `/tmp/validador-ct2`.
- `MAX_UPLOAD_MB`: limite maximo para uploads, padrao `100`.

Em ambiente Render, `APP_PASSWORD` e obrigatoria. Se ela nao estiver definida, o
backend nao inicia aberto por acidente.

### Analise local com SQLite

Este e o caminho recomendado para arquivos grandes. O script le a CT2 em streaming,
grava os lancamentos de resultado em um SQLite local e exporta somente as
divergencias do mes analisado.

Para usar no navegador com base fixa, inicie o servidor local:

```powershell
python scripts\servidor_local.py
```

Depois abra:

```text
http://127.0.0.1:8000
```

A tela possui dois pontos de importacao:

1. `Importar cadastro MATA020`: recebe o `mata020.xml` e carrega razao social e
   nome fantasia dos fornecedores.
2. `Importar CT2 para base fixa`: recebe o CSV da CT2 e atualiza os lancamentos.

Importe o `mata020.xml` antes da CT2 para melhorar a identificacao dos
fornecedores. Durante a importacao da CT2, todos os meses presentes no arquivo
sao substituidos na base; meses novos sao integrados aos dados ja existentes.
Depois disso, informe o mes analisado e gere somente o CSV de divergencias.

> No GitHub Pages, a aplicacao roda apenas como pagina estatica. O modo com
> base fixa SQLite, importacao do MATA020 e processamento de arquivos grandes
> exige o servidor local Python.

Tambem e possivel rodar direto pelo terminal:

```powershell
python scripts\analisar_divergencias.py --arquivo ct2_dados_2025-01-01_2026-04-30.csv --mes 2026-04 --db ct2.db --saida divergencias_2026-04.csv --recriar
```

Para rodar novamente no mesmo arquivo e banco, o `--recriar` pode ser omitido:

```powershell
python scripts\analisar_divergencias.py --arquivo ct2_dados_2025-01-01_2026-04-30.csv --mes 2026-04 --db ct2.db --saida divergencias_2026-04.csv
```

O CSV gerado contem apenas divergencias: lancamentos do mes analisado em que o
fornecedor usa uma conta de resultado que nao apareceu para ele nos meses
anteriores.

Para contas iniciadas por `321` e `322`, a comparacao considera a ocorrencia do
lancamento. Quando `Ocorren Deb` ou `Ocorren Crd` for `18`, a natureza esperada
e `322`; quando for diferente de `18`, a natureza esperada e `321`. Se a unica
diferenca entre historico e mes atual for essa natureza de custo/despesa, a linha
nao e tratada como divergencia.

### Tela estatica

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
