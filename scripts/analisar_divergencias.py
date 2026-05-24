#!/usr/bin/env python3
import argparse
import csv
import re
import sqlite3
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path


DEFAULT_RESULT_PREFIXES = "32"
DEFAULT_IGNORED_WORDS = (
    "NF,NFE,NOTA,FISCAL,DOC,DOCUMENTO,PAGTO,PAGAMENTO,REF,REFERENTE,"
    "FORN,FORNECEDOR,FORNEC,HIS,HIST,HISTORICO,ENTR,ENTRADA,IRRF,INSS,"
    "ISS,RETIDO,RETENCAO,BOLETO,DUPLICATA,FATURA"
)
DEFAULT_EXCLUDED_PATTERNS = (
    "008860,008890,FUNCIONARIOS,AUTONOMOS,FOLHA,FOL/,FO1/,FO2/,FO3/,FO4/,"
    "FO5/,DEPRECIACAO,AMORTIZACAO"
)

ALIASES = {
    "date": ["CT2_DATA", "DATA LCTO", "DATA", "DT", "DT_LANC", "DATA_LANCAMENTO"],
    "debit": ["CT2_DEBITO", "CTA DEBITO", "CTA DEB", "DEBITO", "CONTA_DEBITO", "CTA_DEBITO"],
    "credit": ["CT2_CREDIT", "CT2_CREDITO", "CTA CREDITO", "CTA CRED", "CREDITO", "CONTA_CREDITO", "CTA_CREDITO"],
    "history": ["CT2_HIST", "CT2_HISTOR", "HIST LANC", "HISTORICO LANC", "HISTORICO", "HIST", "DESCRICAO"],
    "type": ["TIPO LCTO", "TIPO_LCTO"],
    "lot": ["NUMERO LOTE", "LOTE", "NUMERO_LOTE"],
    "sub_lot": ["SUB LOTE", "SUB_LOTE"],
    "origin": ["ORIGEM"],
    "branch": ["FILIAL"],
    "document": ["NUMERO DOC", "DOCUMENTO", "NUMERO_DOC"],
    "value": ["VALOR", "VALOR MOEDA1"],
    "debit_occurrence": ["OCORREN DEB", "OCORREN_DEB", "OCORRENCIA DEB", "OCORRENCIA_DEB"],
    "credit_occurrence": ["OCORREN CRD", "OCORREN_CRED", "OCORREN CR", "OCORRENCIA CRD", "OCORRENCIA_CRED"],
}


def main():
    args = parse_args()
    config = {
        "month": args.mes,
        "result_prefixes": [clean_account(item) for item in split_list(args.prefixos_resultado)],
        "ignored_words": {normalize_text(item) for item in split_list(args.palavras_ignoradas)},
        "excluded_patterns": [normalize_text(item) for item in split_list(args.padroes_ignorados)],
    }

    result = analyze_file(
        source=Path(args.arquivo),
        month=args.mes,
        db_path=Path(args.db),
        output_path=Path(args.saida or f"divergencias_{args.mes}.csv"),
        config=config,
        recreate=args.recriar,
        verbose=True,
    )

    print(f"{result['total']:,}".replace(",", ".") + f" divergencias exportadas em {result['output_path']}")


def analyze_file(source, month, db_path, output_path, config=None, recreate=False, verbose=False):
    source = Path(source)
    db_path = Path(db_path)
    output_path = Path(output_path)

    if not source.exists():
        raise FileNotFoundError(f"Arquivo nao encontrado: {source}")

    if recreate and db_path.exists():
        db_path.unlink()

    config = config or {
        "month": month,
        "result_prefixes": [clean_account(item) for item in split_list(DEFAULT_RESULT_PREFIXES)],
        "ignored_words": {normalize_text(item) for item in split_list(DEFAULT_IGNORED_WORDS)},
        "excluded_patterns": [normalize_text(item) for item in split_list(DEFAULT_EXCLUDED_PATTERNS)],
    }
    config["month"] = month

    with sqlite3.connect(db_path, timeout=60) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA temp_store = MEMORY")
        create_schema(conn)
        create_import_schema(conn)
        create_supplier_schema(conn)

        imported_source = get_metadata(conn, "arquivo")
        source_key = str(source.resolve())
        can_reuse = has_imported_rows(conn) and imported_source == source_key

        if has_imported_rows(conn) and imported_source and imported_source != source_key and not recreate:
            raise SystemExit(
                "O SQLite informado ja contem dados de outro arquivo. "
                "Use --recriar ou informe outro --db."
            )

        if recreate or not can_reuse:
            clear_data(conn)
            if verbose:
                print("Lendo fornecedores para melhorar a identificacao...")
            config["supplier_catalog"] = load_supplier_catalog(conn)
            supplier_lookup = build_supplier_lookup(source, config)
            if verbose:
                print("Importando lancamentos de resultado para o SQLite...")
            imported = import_result_entries(conn, source, config, supplier_lookup)
            save_metadata(conn, source, imported)
            if verbose:
                print(f"{imported:,}".replace(",", ".") + " lancamentos de resultado importados.")
        else:
            imported = int(get_metadata(conn, "linhas_resultado") or 0)
            if verbose:
                print("Usando dados ja importados no SQLite.")

        if verbose:
            print("Gerando divergencias...")
        total = export_divergences(conn, config["month"], output_path)

    return {
        "total": total,
        "imported": imported,
        "db_path": str(db_path),
        "output_path": str(output_path),
    }


def import_file_into_base(source, db_path, config=None, verbose=False):
    source = Path(source)
    db_path = Path(db_path)

    if not source.exists():
        raise FileNotFoundError(f"Arquivo nao encontrado: {source}")

    config = config or {
        "month": "",
        "result_prefixes": [clean_account(item) for item in split_list(DEFAULT_RESULT_PREFIXES)],
        "ignored_words": {normalize_text(item) for item in split_list(DEFAULT_IGNORED_WORDS)},
        "excluded_patterns": [normalize_text(item) for item in split_list(DEFAULT_EXCLUDED_PATTERNS)],
    }

    with sqlite3.connect(db_path, timeout=60) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA temp_store = MEMORY")
        create_schema(conn)
        create_import_schema(conn)
        create_supplier_schema(conn)

        if verbose:
            print("Identificando meses do arquivo...")
        months = collect_months(source)
        if not months:
            raise ValueError("Nenhum mes valido encontrado no arquivo.")

        placeholders = ",".join("?" for _ in months)
        conn.execute(f"DELETE FROM lancamentos WHERE mes IN ({placeholders})", months)
        conn.execute(f"DELETE FROM importacoes WHERE mes IN ({placeholders})", months)
        conn.commit()

        if verbose:
            print("Lendo fornecedores para melhorar a identificacao...")
        config["supplier_catalog"] = load_supplier_catalog(conn)
        supplier_lookup = build_supplier_lookup(source, config)

        if verbose:
            print("Importando lancamentos de resultado para a base fixa...")
        imported = import_result_entries(conn, source, config, supplier_lookup)

        imported_at = datetime.now().isoformat(timespec="seconds")
        conn.executemany(
            """
            INSERT OR REPLACE INTO importacoes (mes, arquivo, importado_em, lancamentos_resultado)
            VALUES (?, ?, ?, ?)
            """,
            [(month, str(source.resolve()), imported_at, count_month_entries(conn, month)) for month in months],
        )
        conn.commit()

    return {
        "months": months,
        "imported": imported,
        "db_path": str(db_path),
    }


def export_divergences_from_base(db_path, month, output_path):
    db_path = Path(db_path)
    output_path = Path(output_path)
    if not db_path.exists():
        raise FileNotFoundError("Base SQLite ainda nao foi criada.")

    with sqlite3.connect(db_path, timeout=60) as conn:
        create_schema(conn)
        create_supplier_schema(conn)
        current_entries = count_month_entries(conn, month)
        total = export_divergences(conn, month, output_path)

    return {
        "total": total,
        "current_entries": current_entries,
        "output_path": str(output_path),
    }


def get_base_summary(db_path):
    db_path = Path(db_path)
    if not db_path.exists():
        return {"months": [], "total_entries": 0, "supplier_count": 0}

    with sqlite3.connect(db_path, timeout=60) as conn:
        create_schema(conn)
        create_import_schema(conn)
        create_supplier_schema(conn)
        total_entries = conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0]
        supplier_count = conn.execute("SELECT COUNT(*) FROM fornecedores").fetchone()[0]
        rows = conn.execute(
            """
            SELECT l.mes, COUNT(l.id) AS lancamentos_resultado,
                   COALESCE(i.arquivo, '') AS arquivo,
                   COALESCE(i.importado_em, '') AS importado_em
            FROM lancamentos l
            LEFT JOIN importacoes i ON i.mes = l.mes
            GROUP BY l.mes
            ORDER BY l.mes
            """
        ).fetchall()

    return {
        "total_entries": total_entries,
        "supplier_count": supplier_count,
        "months": [
            {
                "month": month,
                "entries": entries,
                "file": Path(file_name).name if file_name else "",
                "importedAt": imported_at,
            }
            for month, entries, file_name, imported_at in rows
        ],
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Importa uma CT2 para SQLite e exporta somente divergencias de conta por fornecedor."
    )
    parser.add_argument("--arquivo", required=True, help="CSV da CT2.")
    parser.add_argument("--mes", required=True, help="Mes analisado no formato AAAA-MM, ex.: 2026-04.")
    parser.add_argument("--db", default="ct2.db", help="Arquivo SQLite de trabalho.")
    parser.add_argument("--saida", help="CSV de saida. Padrao: divergencias_AAAA-MM.csv.")
    parser.add_argument("--prefixos-resultado", default=DEFAULT_RESULT_PREFIXES)
    parser.add_argument("--palavras-ignoradas", default=DEFAULT_IGNORED_WORDS)
    parser.add_argument("--padroes-ignorados", default=DEFAULT_EXCLUDED_PATTERNS)
    parser.add_argument("--recriar", action="store_true", help="Remove e recria os dados do SQLite antes de analisar.")
    return parser.parse_args()


def create_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS lancamentos (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          linha_origem INTEGER NOT NULL,
          filial TEXT,
          data_lcto TEXT NOT NULL,
          mes TEXT NOT NULL,
          numero_lote TEXT,
          sub_lote TEXT,
          numero_doc TEXT,
          fornecedor_extraido TEXT NOT NULL,
          fornecedor_chave TEXT NOT NULL,
          conta_resultado TEXT NOT NULL,
          conta_comparacao TEXT,
          lado_resultado TEXT NOT NULL,
          contrapartida TEXT,
          ocorren_deb TEXT,
          ocorren_crd TEXT,
          ocorrencia_resultado TEXT,
          valor TEXT,
          historico TEXT,
          origem TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_lancamentos_mes
          ON lancamentos (mes);
        CREATE INDEX IF NOT EXISTS idx_lancamentos_fornecedor_conta_mes
          ON lancamentos (fornecedor_chave, conta_resultado, mes);
        """
    )
    ensure_column(conn, "lancamentos", "conta_comparacao", "TEXT")
    ensure_column(conn, "lancamentos", "ocorren_deb", "TEXT")
    ensure_column(conn, "lancamentos", "ocorren_crd", "TEXT")
    ensure_column(conn, "lancamentos", "ocorrencia_resultado", "TEXT")


def ensure_column(conn, table, column, definition):
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def create_import_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS importacoes (
          mes TEXT PRIMARY KEY,
          arquivo TEXT NOT NULL,
          importado_em TEXT NOT NULL,
          lancamentos_resultado INTEGER NOT NULL
        );
        """
    )


def create_supplier_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS fornecedores (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          codigo TEXT,
          loja TEXT,
          razao_social TEXT,
          nome_fantasia TEXT,
          nome_empresarial TEXT,
          cnpj TEXT,
          chave_razao TEXT,
          chave_fantasia TEXT,
          chave_empresarial TEXT,
          compact_razao TEXT,
          compact_fantasia TEXT,
          compact_empresarial TEXT,
          importado_em TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_fornecedores_compact_razao
          ON fornecedores (compact_razao);
        CREATE INDEX IF NOT EXISTS idx_fornecedores_compact_fantasia
          ON fornecedores (compact_fantasia);
        CREATE INDEX IF NOT EXISTS idx_fornecedores_compact_empresarial
          ON fornecedores (compact_empresarial);
        """
    )


def import_supplier_registry(xml_path, db_path):
    xml_path = Path(xml_path)
    db_path = Path(db_path)
    if not xml_path.exists():
        raise FileNotFoundError(f"Arquivo nao encontrado: {xml_path}")

    imported_at = datetime.now().isoformat(timespec="seconds")
    batch = []
    header = None

    for values in iter_spreadsheet_rows(xml_path):
        if len(values) >= 4 and normalize_text(values[0]) == "CODIGO" and normalize_text(values[2]) == "RAZAO SOCIAL":
            header = values
            continue
        if not header or len(values) < 4:
            continue

        row = {header[index]: values[index] if index < len(values) else "" for index in range(len(header))}
        razao = row.get("Razao Social", "").strip()
        fantasia = row.get("N Fantasia", "").strip()
        empresarial = row.get("Nome Empres.", "").strip()
        if not razao and not fantasia and not empresarial:
            continue

        batch.append(
            (
                row.get("Codigo", "").strip(),
                row.get("Loja", "").strip(),
                razao,
                fantasia,
                empresarial,
                clean_account(row.get("CNPJ Empr.Ex", "")),
                normalize_text(razao),
                normalize_text(fantasia),
                normalize_text(empresarial),
                compact_text(razao),
                compact_text(fantasia),
                compact_text(empresarial),
                imported_at,
            )
        )

    with sqlite3.connect(db_path, timeout=60) as conn:
        create_schema(conn)
        create_import_schema(conn)
        create_supplier_schema(conn)
        conn.execute("DELETE FROM fornecedores")
        conn.executemany(
            """
            INSERT INTO fornecedores (
              codigo, loja, razao_social, nome_fantasia, nome_empresarial, cnpj,
              chave_razao, chave_fantasia, chave_empresarial,
              compact_razao, compact_fantasia, compact_empresarial, importado_em
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        conn.commit()

    return {"imported": len(batch), "db_path": str(db_path)}


def iter_spreadsheet_rows(path):
    for _event, elem in ET.iterparse(path, events=("end",)):
        if elem.tag.endswith("Row"):
            values = []
            for cell in list(elem):
                if not cell.tag.endswith("Cell"):
                    continue
                data = next((child for child in list(cell) if child.tag.endswith("Data")), None)
                values.append("".join(data.itertext()).strip() if data is not None else "")
            if values and any(values):
                yield values
            elem.clear()


def load_supplier_catalog(conn):
    create_supplier_schema(conn)
    rows = conn.execute(
        """
        SELECT codigo, loja, razao_social, nome_fantasia, nome_empresarial,
               chave_razao, chave_fantasia, chave_empresarial,
               compact_razao, compact_fantasia, compact_empresarial
        FROM fornecedores
        """
    ).fetchall()

    suppliers = []
    index = defaultdict(list)
    for row in rows:
        supplier = {
            "codigo": row[0],
            "loja": row[1],
            "label": to_title_case(row[2] or row[3] or row[4]),
            "key": normalize_text(row[2] or row[3] or row[4]),
            "names": [name for name in row[5:8] if name],
            "compacts": [name for name in row[8:11] if name],
        }
        suppliers.append(supplier)
        for compact in supplier["compacts"]:
            if len(compact) >= 4:
                index[compact[:4]].append(supplier)

    return {"suppliers": suppliers, "index": index, "cache": {}}


def clear_data(conn):
    conn.execute("DELETE FROM lancamentos")
    conn.execute("DELETE FROM metadata")
    conn.execute("DELETE FROM importacoes")
    conn.commit()


def has_imported_rows(conn):
    row = conn.execute("SELECT COUNT(*) FROM lancamentos").fetchone()
    return bool(row and row[0])


def save_metadata(conn, source, imported):
    conn.executemany(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        [
            ("arquivo", str(source.resolve())),
            ("linhas_resultado", str(imported)),
            ("importado_em", datetime.now().isoformat(timespec="seconds")),
        ],
    )
    conn.commit()


def count_month_entries(conn, month):
    return conn.execute("SELECT COUNT(*) FROM lancamentos WHERE mes = ?", (month,)).fetchone()[0]


def collect_months(source):
    columns = None
    months = set()
    for row in iter_main_rows(source):
        if columns is None:
            columns = resolve_columns(row.keys())
        date = parse_date(row.get(columns["date"]))
        if date:
            months.add(date[:7])
    return sorted(months)


def get_metadata(conn, key):
    row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return row[0] if row else ""


def build_supplier_lookup(source, config):
    by_invoice = defaultdict(list)
    legal_names = []

    for row in iter_main_rows(source):
        history = get_history(row)
        invoice = extract_invoice_number(history)
        supplier = extract_supplier_from_history(history, config["ignored_words"])
        if not supplier["key"] or len(supplier["key"]) < 5:
            continue

        if invoice and not any(item["key"] == supplier["key"] for item in by_invoice[invoice]):
            by_invoice[invoice].append(supplier)

        if has_legal_suffix(supplier["key"]) and not any(item["key"] == supplier["key"] for item in legal_names):
            legal_names.append(supplier)

    return {
        "by_invoice": by_invoice,
        "legal_names": legal_names,
        "supplier_catalog": config.get("supplier_catalog"),
    }


def import_result_entries(conn, source, config, supplier_lookup):
    columns = None
    batch = []
    total = 0

    for row in iter_main_rows(source):
        if columns is None:
            columns = resolve_columns(row.keys())

        if is_excluded_row(row, config, columns):
            continue

        date = parse_date(row.get(columns["date"]))
        if not date:
            continue

        history = get_history(row, columns)
        supplier = extract_supplier(history, config["ignored_words"], supplier_lookup)
        if not supplier["key"]:
            continue

        candidates = [
            (
                "D",
                clean_account(row.get(columns["debit"])),
                clean_account(row.get(columns["credit"])),
                clean_account(row.get(columns.get("debit_occurrence", ""))),
            ),
            (
                "C",
                clean_account(row.get(columns["credit"])),
                clean_account(row.get(columns["debit"])),
                clean_account(row.get(columns.get("credit_occurrence", ""))),
            ),
        ]
        debit_occurrence = clean_account(row.get(columns.get("debit_occurrence", "")))
        credit_occurrence = clean_account(row.get(columns.get("credit_occurrence", "")))

        for side, account, counterpart, occurrence in candidates:
            if not account or not is_result_account(account, config["result_prefixes"]):
                continue

            comparison_account = comparable_account(account, occurrence)
            batch.append(
                (
                    row["__line_number"],
                    row.get(columns["branch"], ""),
                    date,
                    date[:7],
                    row.get(columns["lot"], ""),
                    row.get(columns["sub_lot"], ""),
                    row.get(columns["document"], ""),
                    supplier["label"],
                    supplier["key"],
                    account,
                    comparison_account,
                    side,
                    counterpart,
                    debit_occurrence,
                    credit_occurrence,
                    occurrence,
                    row.get(columns["value"], ""),
                    history,
                    row.get(columns["origin"], ""),
                )
            )

        if len(batch) >= 5000:
            total += insert_batch(conn, batch)
            batch.clear()

    if batch:
        total += insert_batch(conn, batch)

    conn.commit()
    return total


def insert_batch(conn, batch):
    conn.executemany(
        """
        INSERT INTO lancamentos (
          linha_origem, filial, data_lcto, mes, numero_lote, sub_lote, numero_doc,
          fornecedor_extraido, fornecedor_chave, conta_resultado, conta_comparacao,
          lado_resultado, contrapartida, ocorren_deb, ocorren_crd, ocorrencia_resultado,
          valor, historico, origem
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        batch,
    )
    return len(batch)


def export_divergences(conn, month, output_path):
    previous = load_previous_accounts(conn, month)
    current_rows = conn.execute(
        """
        SELECT linha_origem, filial, data_lcto, numero_lote, sub_lote, numero_doc,
               fornecedor_extraido, fornecedor_chave, conta_resultado, lado_resultado,
               contrapartida, valor, ocorren_deb, ocorren_crd, ocorrencia_resultado,
               historico, origem, conta_comparacao
        FROM lancamentos
        WHERE mes = ?
        ORDER BY data_lcto, linha_origem, id
        """,
        (month,),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0

    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(
            [
                "linha_origem",
                "filial",
                "data_lcto",
                "numero_lote",
                "sub_lote",
                "numero_doc",
                "fornecedor_extraido",
                "fornecedor_chave",
                "conta_atual",
                "lado_resultado",
                "contrapartida",
                "valor",
                "ocorren_deb",
                "ocorren_crd",
                "ocorrencia_resultado",
                "ultimas_contas_anteriores",
                "historico",
                "origem",
            ]
        )

        for row in current_rows:
            supplier_key = row[7]
            comparison_account = row[17] or comparable_account(row[8], row[14])
            previous_comparisons = previous["comparisons"].get(supplier_key, set())
            if comparison_account in previous_comparisons:
                continue

            previous_accounts = format_previous_accounts(previous["accounts"].get(supplier_key, {}))
            writer.writerow([*row[:15], previous_accounts, row[15], row[16]])
            total += 1

    return total


def load_previous_accounts(conn, month):
    rows = conn.execute(
        """
        SELECT fornecedor_chave, conta_resultado, conta_comparacao, ocorrencia_resultado, MAX(data_lcto) AS ultima_data
        FROM lancamentos
        WHERE mes < ?
        GROUP BY fornecedor_chave, conta_resultado, conta_comparacao, ocorrencia_resultado
        ORDER BY fornecedor_chave, ultima_data DESC
        """,
        (month,),
    )

    previous = {"comparisons": defaultdict(set), "accounts": defaultdict(dict)}
    for supplier_key, account, comparison_account, occurrence, last_date in rows:
        comparison_account = comparison_account or comparable_account(account, occurrence)
        previous["comparisons"][supplier_key].add(comparison_account)
        previous["accounts"][supplier_key][account] = last_date

    return previous


def format_previous_accounts(accounts):
    if not accounts:
        return "Sem historico anterior"

    ordered = sorted(accounts.items(), key=lambda item: item[1], reverse=True)
    return " | ".join(f"{account} ({date})" for account, date in ordered[:5])


def iter_main_rows(source):
    with source.open("r", encoding=detect_encoding(source), newline="") as handle:
        header, dialect, header_line_number = find_header_and_dialect(handle)
        columns = [item.strip().strip('"') for item in header]
        reader = csv.DictReader(handle, fieldnames=columns, dialect=dialect)
        resolved = resolve_columns(columns)
        active = None

        for row in reader:
            row = normalize_row(row)
            row["__line_number"] = header_line_number + reader.line_num

            if is_continuation_history(row, resolved):
                if active is not None:
                    active["__fullHistory"] = f"{active.get('__fullHistory', '')} {row.get(resolved['history'], '')}".strip()
                continue

            if active is not None:
                yield active

            active = row
            active["__fullHistory"] = row.get(resolved["history"], "")

        if active is not None:
            yield active


def find_header_and_dialect(handle):
    sample = handle.read(8192)
    handle.seek(0)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"

    reader = csv.reader(handle, dialect)
    for row in reader:
        normalized = normalize_text(" ".join(row))
        if "DATA LCTO" in normalized and "HIST LANC" in normalized:
            return row, dialect, reader.line_num

    raise ValueError("Cabecalho da CT2 nao encontrado. Esperado: Data Lcto e Hist Lanc.")


def detect_encoding(source):
    with source.open("rb") as handle:
        sample = handle.read(65536)
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "utf-8-sig"


def normalize_row(row):
    return {
        str(key).strip().strip('"'): "" if value is None else str(value).strip()
        for key, value in row.items()
        if key is not None
    }


def resolve_columns(columns):
    names = list(columns)
    normalized = {normalize_text(name): name for name in names}
    resolved = {}

    for key, candidates in ALIASES.items():
        found = ""
        for candidate in candidates:
            found = normalized.get(normalize_text(candidate), "")
            if found:
                break
        resolved[key] = found

    required = ["date", "debit", "credit", "history"]
    missing = [key for key in required if not resolved[key]]
    if missing:
        raise ValueError("Colunas obrigatorias nao encontradas: " + ", ".join(missing))

    return resolved


def is_continuation_history(row, columns):
    type_column = columns.get("type")
    return bool(type_column and "CONT HIST" in normalize_text(row.get(type_column)))


def is_excluded_row(row, config, columns):
    searchable = normalize_text(
        " ".join(
            [
                row.get(columns.get("lot", ""), ""),
                row.get(columns.get("sub_lot", ""), ""),
                row.get(columns.get("origin", ""), ""),
                get_history(row, columns),
            ]
        )
    )
    return any(pattern and pattern in searchable for pattern in config["excluded_patterns"])


def get_history(row, columns=None):
    if "__fullHistory" in row:
        return row.get("__fullHistory", "")
    if columns:
        return row.get(columns["history"], "")
    return row.get("Hist Lanc", "") or row.get("HIST LANC", "")


def parse_date(value):
    raw = str(value or "").strip()
    if not raw:
        return ""

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass

    if re.fullmatch(r"\d{8}", raw):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"

    return ""


def clean_account(value):
    return re.sub(r"\D", "", str(value or ""))


def is_result_account(account, prefixes):
    return not prefixes or any(account.startswith(prefix) for prefix in prefixes)


def comparable_account(account, occurrence):
    clean = clean_account(account)
    occurrence = clean_account(occurrence)
    if len(clean) >= 4 and clean[:3] in {"321", "322"} and occurrence:
        expected_prefix = "322" if occurrence == "18" else "321"
        if clean.startswith(expected_prefix):
            return clean[3:]
        return clean
    if len(clean) >= 4 and clean[:3] in {"321", "322"}:
        return clean[3:]
    return clean


def extract_supplier(history, ignored_words, supplier_lookup):
    direct = extract_supplier_from_history(history, ignored_words)
    invoice = extract_invoice_number(history)
    mapped = find_mapped_supplier(supplier_lookup["by_invoice"].get(invoice), direct["key"]) if invoice else None

    if mapped and mapped["key"] != direct["key"] and (
        not direct["key"] or len(direct["key"]) <= 5 or is_fragment_of(direct["key"], mapped["key"])
    ):
        return mapped

    legal_mapped = find_mapped_supplier(supplier_lookup["legal_names"], direct["key"])
    if legal_mapped and legal_mapped["key"] != direct["key"]:
        direct = legal_mapped

    catalog_mapped = find_catalog_supplier(supplier_lookup.get("supplier_catalog"), direct["key"])
    if catalog_mapped:
        return catalog_mapped

    return direct


def find_catalog_supplier(catalog, supplier_key):
    if not catalog or not supplier_key:
        return None

    if supplier_key in catalog["cache"]:
        return catalog["cache"][supplier_key]

    compact = compact_text(supplier_key)
    if len(compact) < 5:
        return None

    candidates = []
    seen = set()
    for prefix in {compact[:4], compact[:5] if len(compact) >= 5 else compact[:4]}:
        for supplier in catalog["index"].get(prefix, []):
            marker = (supplier["codigo"], supplier["loja"], supplier["key"])
            if marker not in seen:
                candidates.append(supplier)
                seen.add(marker)

    if not candidates:
        catalog["cache"][supplier_key] = None
        return None

    best = None
    best_score = 0
    for supplier in candidates:
        for candidate_compact in supplier["compacts"]:
            score = supplier_match_score(compact, candidate_compact)
            if score > best_score:
                best = supplier
                best_score = score

    if best and best_score >= 0.88:
        result = {"key": best["key"], "label": best["label"]}
        catalog["cache"][supplier_key] = result
        return result
    catalog["cache"][supplier_key] = None
    return None


def supplier_match_score(extracted, registered):
    if not extracted or not registered:
        return 0
    if extracted == registered:
        return 1
    if len(extracted) >= 8 and (extracted in registered or registered in extracted):
        return 0.97
    return SequenceMatcher(None, extracted, registered).ratio()


def find_mapped_supplier(candidates, fragment):
    if not candidates:
        return None

    usable = [candidate for candidate in candidates if is_fragment_of(fragment, candidate["key"])] if fragment else candidates
    if fragment and len(usable) != 1:
        return None
    if not usable:
        return None

    return sorted(usable, key=lambda item: len(item["key"]), reverse=True)[0]


def extract_supplier_from_history(history, ignored_words):
    candidate = trim_technical_suffix(clean_supplier_candidate(pick_supplier_candidate(history)))
    tokens = [token for token in candidate.split() if token and token not in ignored_words]
    key = trim_technical_suffix(" ".join(tokens))
    return {"key": key, "label": to_title_case(key)}


def clean_supplier_candidate(value):
    return re.sub(
        r"\s+",
        " ",
        re.sub(
            r"\b\d+[,.]?\d*\b",
            " ",
            re.sub(
                r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b",
                " ",
                re.sub(
                    r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b",
                    " ",
                    re.sub(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b", " ", normalize_text(value)),
                ),
            ),
        ),
    ).strip()


def trim_technical_suffix(value):
    value = re.sub(r"\bHIS(?:T|TORICO)?\b.*$", "", value, flags=re.I)
    value = re.sub(r"\bENTR(?:ADA)?$", "", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip()


def extract_invoice_number(history):
    match = re.search(r"\b(?:NF|NFE|NOTA\s+FISCAL)\.?\s*([0-9./-]+)", str(history or ""), flags=re.I)
    return clean_account(match.group(1)) if match else ""


def is_fragment_of(fragment, full_value):
    if not fragment or not full_value:
        return False
    return full_value.startswith(fragment) or fragment.startswith(full_value[: len(fragment)])


def has_legal_suffix(value):
    return bool(re.search(r"\b(LTDA|S A|SA|ME|EPP|EIRELI|INC|LLC)\b$", value, flags=re.I))


def pick_supplier_candidate(history):
    raw = str(history or "").strip()
    after_invoice = re.search(r"\b(?:NF|NFE|NOTA\s+FISCAL)\.?\s*[\d./-]+\s*[-:]?\s*(.+)$", raw, flags=re.I)
    if after_invoice:
        invoice_candidate = after_invoice.group(1)
        after_supplier = re.search(r"\bFORN\.?\s+(.+)$", invoice_candidate, flags=re.I)
        return clean_supplier_marker_candidate(after_supplier.group(1) if after_supplier else invoice_candidate)

    after_supplier = re.search(r"\bFORN\.?\s+(.+)$", raw, flags=re.I)
    if after_supplier:
        return clean_supplier_marker_candidate(after_supplier.group(1))

    return ""


def clean_supplier_marker_candidate(value):
    return re.split(r"\s+-\s*|-\s+", str(value or "").strip())[0].strip()


def normalize_text(value):
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = re.sub(r"[^A-Za-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip().upper()


def compact_text(value):
    return re.sub(r"[^A-Z0-9]", "", normalize_text(value))


def to_title_case(value):
    return str(value or "").lower().title()


def split_list(value):
    return [item.strip() for item in re.split(r"[,;\n]", str(value or "")) if item.strip()]


if __name__ == "__main__":
    main()
