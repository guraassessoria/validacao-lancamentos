#!/usr/bin/env python3
import csv
import json
import mimetypes
import re
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from analisar_divergencias import (
    export_divergences_from_base,
    get_base_summary,
    import_file_into_base,
    import_supplier_registry,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "saida"
DB_DIR = ROOT / "bancos"
UPLOAD_DIR = ROOT / "uploads"
BASE_DB = DB_DIR / "ct2_base.db"
HOST = "127.0.0.1"
PORT = 8000
MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    ),
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


class LocalHandler(BaseHTTPRequestHandler):
    server_version = "ValidadorCT2/1.0"

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/files":
            return self.send_json({"files": list_csv_files()})

        if path == "/api/base":
            return self.send_json(get_base_summary(BASE_DB))

        if path.startswith("/api/preview/"):
            return self.send_preview(path.removeprefix("/api/preview/"))

        if path.startswith("/saida/"):
            return self.send_static(OUTPUT_DIR / unquote(path.removeprefix("/saida/")), allow_root=OUTPUT_DIR)

        if path == "/":
            return self.send_static(ROOT / "index.html", allow_root=ROOT)

        requested = ROOT / unquote(path.lstrip("/"))
        return self.send_static(requested, allow_root=ROOT)

    def do_POST(self):
        if urlparse(self.path).path != "/api/analyze":
            if urlparse(self.path).path == "/api/upload":
                return self.handle_upload()
            return self.send_error(404)

        try:
            payload = self.read_json()
            result = run_analysis(payload)
            return self.send_json(result)
        except Exception as exc:
            return self.send_json({"error": str(exc)}, status=400)

    def handle_upload(self):
        try:
            file_name = self.headers.get("X-File-Name", "")
            source = save_upload(file_name, self.rfile, int(self.headers.get("Content-Length", "0")))
            if source.suffix.lower() == ".xml":
                result = import_supplier_registry(source, BASE_DB)
                summary = get_base_summary(BASE_DB)
                return self.send_json(
                    {
                        "file": source.name,
                        "supplierCount": result["imported"],
                        "months": [],
                        "imported": result["imported"],
                        "base": summary,
                    }
                )

            result = import_file_into_base(source, BASE_DB, verbose=True)
            summary = get_base_summary(BASE_DB)
            return self.send_json(
                {
                    "file": source.name,
                    "months": result["months"],
                    "imported": result["imported"],
                    "base": summary,
                }
            )
        except Exception as exc:
            return self.send_json({"error": str(exc)}, status=400)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8") or "{}")

    def send_json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for header, value in _SECURITY_HEADERS.items():
            self.send_header(header, value)
        self.end_headers()
        self.wfile.write(data)

    def send_preview(self, encoded_name):
        output = safe_output_path(encoded_name)
        if not output.exists():
            return self.send_json({"error": "Arquivo de saida nao encontrado."}, status=404)

        with output.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            rows = []
            for index, row in enumerate(reader):
                if index >= 200:
                    break
                rows.append(row)

        return self.send_json({"rows": rows})

    def send_static(self, path, allow_root):
        try:
            resolved = path.resolve()
            root = allow_root.resolve()
            if root != resolved and root not in resolved.parents:
                return self.send_error(403)
            if not resolved.exists() or not resolved.is_file():
                return self.send_error(404)

            content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
            data = resolved.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            for header, value in _SECURITY_HEADERS.items():
                self.send_header(header, value)
            self.end_headers()
            self.wfile.write(data)
        except OSError as exc:
            return self.send_error(500, str(exc))

    def log_message(self, format, *args):
        print("%s - %s" % (self.address_string(), format % args))


def list_csv_files():
    files = []
    for path in ROOT.glob("*.csv"):
        if path.name.startswith("divergencias_"):
            continue
        if not is_ct2_csv(path):
            continue
        files.append(
            {
                "name": path.name,
                "size": path.stat().st_size,
            }
        )
    return sorted(files, key=lambda item: item["name"].lower())


def is_ct2_csv(path):
    try:
        with path.open("rb") as handle:
            sample = handle.read(65536)
        text = sample.decode("utf-8-sig", errors="ignore")
        normalized = text.upper()
        return "DATA LCTO" in normalized and "HIST LANC" in normalized
    except OSError:
        return False


def run_analysis(payload):
    month = str(payload.get("mes") or "").strip()

    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("Informe o mes no formato AAAA-MM.")

    OUTPUT_DIR.mkdir(exist_ok=True)
    DB_DIR.mkdir(exist_ok=True)
    output = OUTPUT_DIR / f"divergencias_base_{month}.csv"

    result = export_divergences_from_base(BASE_DB, month, output)
    summary = get_base_summary(BASE_DB)

    relative_output = output.relative_to(OUTPUT_DIR).as_posix()
    preview = load_preview(output)
    return {
        "total": result["total"],
        "imported": summary["total_entries"],
        "currentEntries": result["current_entries"],
        "previewCount": len(preview),
        "output": relative_output,
        "downloadUrl": f"/saida/{relative_output}",
        "preview": preview,
    }


def load_preview(output):
    with output.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        return [row for _, row in zip(range(50), reader)]


def safe_root_file(name):
    path = (ROOT / name).resolve()
    if path.parent != ROOT.resolve() or not path.exists():
        raise ValueError("Arquivo invalido.")
    return path


def save_upload(file_name, input_stream, length):
    if length <= 0:
        raise ValueError("Arquivo vazio.")

    if length > MAX_UPLOAD_BYTES:
        raise ValueError(f"Arquivo excede o limite de {MAX_UPLOAD_BYTES // 1024 // 1024} MB.")

    decoded = unquote(file_name or "")
    name = Path(decoded).name
    if not name.lower().endswith((".csv", ".xml")):
        raise ValueError("Envie um arquivo CSV da CT2 ou XML do MATA020.")

    UPLOAD_DIR.mkdir(exist_ok=True)
    import uuid
    target = UPLOAD_DIR / f"{safe_slug(Path(name).stem)}_{uuid.uuid4().hex}{Path(name).suffix.lower()}"

    copied = 0
    with target.open("wb") as handle:
        while copied < length:
            chunk = input_stream.read(min(1024 * 1024, length - copied))
            if not chunk:
                break
            copied += len(chunk)
            handle.write(chunk)

    if copied != length:
        target.unlink(missing_ok=True)
        raise ValueError("Upload incompleto.")

    return target


def safe_output_path(name):
    path = (OUTPUT_DIR / unquote(name)).resolve()
    if path.parent != OUTPUT_DIR.resolve():
        raise ValueError("Arquivo de saida invalido.")
    return path


def safe_slug(value):
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return slug or "ct2"


def main():
    server = ThreadingHTTPServer((HOST, PORT), LocalHandler)
    print(f"Servidor local em http://{HOST}:{PORT}")
    print("Pressione Ctrl+C para parar.")
    server.serve_forever()


if __name__ == "__main__":
    main()
