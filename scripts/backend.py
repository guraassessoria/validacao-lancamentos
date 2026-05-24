#!/usr/bin/env python3
import os
import base64
import secrets
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

from .analisar_divergencias import (
    export_divergences_from_base,
    get_settings,
    get_base_summary,
    import_account_plan,
    import_file_into_base,
    import_supplier_registry,
    save_settings,
)
from .neon_state import enabled as neon_enabled
from .neon_state import hydrate_sqlite, persist_sqlite


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "saida"
DB_PATH = DATA_DIR / "ct2_base.db"
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "100")) * 1024 * 1024
app = FastAPI(title="Validador CT2", version="1.0.0")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    password = os.getenv("APP_PASSWORD", "")
    if running_online() and not password:
        return Response("APP_PASSWORD nao configurado.", status_code=503)

    if not password:
        return add_security_headers(await call_next(request))

    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Basic "):
        try:
            decoded = base64.b64decode(authorization.removeprefix("Basic ")).decode("utf-8")
            _user, supplied_password = decoded.split(":", 1)
            if secrets.compare_digest(supplied_password, password):
                return add_security_headers(await call_next(request))
        except Exception:
            pass

    return add_security_headers(Response(
        "Autenticacao requerida.",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Validador CT2"'},
    ))


UPLOAD_MAX_AGE_HOURS = int(os.getenv("UPLOAD_MAX_AGE_HOURS", "24"))


@app.on_event("startup")
def startup():
    if running_online() and not os.getenv("APP_PASSWORD"):
        raise RuntimeError("APP_PASSWORD precisa estar configurado no ambiente online.")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    purge_old_uploads()
    hydrate_sqlite(DB_PATH)


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html", media_type="text/html; charset=utf-8")


@app.get("/favicon.ico")
def favicon():
    raise HTTPException(status_code=404, detail="Arquivo nao encontrado.")


@app.get("/app.js")
def app_js():
    return FileResponse(WEB_DIR / "app.js", media_type="application/javascript; charset=utf-8")


@app.get("/styles.css")
def styles_css():
    return FileResponse(WEB_DIR / "styles.css", media_type="text/css; charset=utf-8")


@app.get("/api/health")
def health():
    return {"status": "ok", "neon": neon_enabled()}


@app.get("/api/base")
def base_summary():
    return get_base_summary(DB_PATH)


@app.get("/api/settings")
def read_settings():
    hydrate_sqlite(DB_PATH)
    return get_settings(DB_PATH)


@app.post("/api/settings")
def write_settings(
    resultPrefixes: str = Form(...),
    ignoredWords: str = Form(...),
    excludedPatterns: str = Form(...),
):
    save_settings(DB_PATH, resultPrefixes, ignoredWords, excludedPatterns)
    persist_sqlite(DB_PATH)
    return get_settings(DB_PATH)


@app.post("/api/upload")
def upload_base(file: UploadFile = File(...), kind: str = Form("ct2")):
    target = save_upload(file)

    try:
        if kind == "supplier":
            if target.suffix.lower() != ".xml":
                raise HTTPException(status_code=400, detail="Envie o cadastro de fornecedores em XML.")
            result = import_supplier_registry(target, DB_PATH)
            persist_sqlite(DB_PATH)
            return {
                "file": target.name,
                "supplierCount": result["imported"],
                "months": [],
                "imported": result["imported"],
                "base": get_base_summary(DB_PATH),
            }

        if kind == "accountPlan":
            if target.suffix.lower() not in {".csv", ".xml"}:
                raise HTTPException(status_code=400, detail="Envie o plano de contas em CSV ou XML.")
            result = import_account_plan(target, DB_PATH)
            persist_sqlite(DB_PATH)
            return {
                "file": target.name,
                "accountCount": result["imported"],
                "months": [],
                "imported": result["imported"],
                "base": get_base_summary(DB_PATH),
            }

        if kind != "ct2":
            raise HTTPException(status_code=400, detail="Tipo de importacao invalido.")

        if target.suffix.lower() != ".csv":
            raise HTTPException(status_code=400, detail="Envie um CSV da CT2.")

        result = import_file_into_base(target, DB_PATH, verbose=True)
        persist_sqlite(DB_PATH)
        return {
            "file": target.name,
            "months": result["months"],
            "imported": result["imported"],
            "base": get_base_summary(DB_PATH),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/analyze")
def analyze_month(mes: str = Form(...)):
    import re
    if not mes or not re.fullmatch(r"\d{4}-\d{2}", mes):
        raise HTTPException(status_code=400, detail="Informe o mes no formato AAAA-MM.")

    output = OUTPUT_DIR / f"divergencias_base_{mes}.csv"
    try:
        result = export_divergences_from_base(DB_PATH, mes, output)
        divergences = load_divergences(output)
        summary = get_base_summary(DB_PATH)
        return {
            "total": result["total"],
            "imported": summary["total_entries"],
            "currentEntries": result["current_entries"],
            "previewCount": len(divergences),
            "output": output.name,
            "downloadUrl": f"/api/download/{output.name}",
            "preview": divergences,
            "divergences": divergences,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/download/{file_name}")
def download(file_name: str):
    path = safe_output_path(file_name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Arquivo nao encontrado.")
    return FileResponse(path, media_type="text/csv", filename=path.name)


def save_upload(file: UploadFile):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".csv", ".xml"}:
        raise HTTPException(status_code=400, detail="Envie um arquivo CSV ou XML.")

    name = f"{safe_slug(Path(file.filename or 'upload').stem)}_{uuid.uuid4().hex}{suffix}"
    target = UPLOAD_DIR / name

    with target.open("wb") as handle:
        copy_upload_with_limit(file.file, handle, MAX_UPLOAD_BYTES)

    return target


def copy_upload_with_limit(source, target, max_bytes):
    copied = 0
    while True:
        chunk = source.read(1024 * 1024)
        if not chunk:
            break
        copied += len(chunk)
        if copied > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Arquivo excede o limite de {MAX_UPLOAD_BYTES // 1024 // 1024} MB.",
            )
        target.write(chunk)


def load_divergences(output):
    import csv

    with output.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        return list(reader)


def safe_output_path(file_name):
    path = (OUTPUT_DIR / Path(file_name).name).resolve()
    if path.parent != OUTPUT_DIR.resolve():
        raise HTTPException(status_code=400, detail="Arquivo invalido.")
    return path


def safe_slug(value):
    import re

    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return slug or "upload"


def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
    )
    if running_online():
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


def purge_old_uploads():
    import time

    cutoff = time.time() - UPLOAD_MAX_AGE_HOURS * 3600
    for path in UPLOAD_DIR.iterdir():
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)


def running_online():
    return bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID") or os.getenv("RENDER_EXTERNAL_URL"))
