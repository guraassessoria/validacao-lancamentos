#!/usr/bin/env python3
import os
import base64
import secrets
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .analisar_divergencias import (
    export_divergences_from_base,
    get_base_summary,
    import_file_into_base,
    import_supplier_registry,
)


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "saida"
DB_PATH = DATA_DIR / "ct2_base.db"

app = FastAPI(title="Validador CT2", version="1.0.0")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    password = os.getenv("APP_PASSWORD", "")
    if not password:
        return await call_next(request)

    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Basic "):
        try:
            decoded = base64.b64decode(authorization.removeprefix("Basic ")).decode("utf-8")
            _user, supplied_password = decoded.split(":", 1)
            if secrets.compare_digest(supplied_password, password):
                return await call_next(request)
        except Exception:
            pass

    return Response(
        "Autenticacao requerida.",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Validador CT2"'},
    )


@app.on_event("startup")
def startup():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/base")
def base_summary():
    return get_base_summary(DB_PATH)


@app.post("/api/upload")
def upload_base(file: UploadFile = File(...)):
    target = save_upload(file)

    try:
        if target.suffix.lower() == ".xml":
            result = import_supplier_registry(target, DB_PATH)
            return {
                "file": target.name,
                "supplierCount": result["imported"],
                "months": [],
                "imported": result["imported"],
                "base": get_base_summary(DB_PATH),
            }

        if target.suffix.lower() != ".csv":
            raise HTTPException(status_code=400, detail="Envie um CSV da CT2 ou XML do MATA020.")

        result = import_file_into_base(target, DB_PATH, verbose=True)
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
    if not mes or len(mes) != 7:
        raise HTTPException(status_code=400, detail="Informe o mes no formato AAAA-MM.")

    output = OUTPUT_DIR / f"divergencias_base_{mes}.csv"
    try:
        result = export_divergences_from_base(DB_PATH, mes, output)
        preview = load_preview(output)
        summary = get_base_summary(DB_PATH)
        return {
            "total": result["total"],
            "imported": summary["total_entries"],
            "currentEntries": result["current_entries"],
            "previewCount": len(preview),
            "output": output.name,
            "downloadUrl": f"/api/download/{output.name}",
            "preview": preview,
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
        raise HTTPException(status_code=400, detail="Envie um CSV da CT2 ou XML do MATA020.")

    name = safe_slug(Path(file.filename or "upload").stem) + suffix
    target = UPLOAD_DIR / name

    with target.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)

    return target


def load_preview(output):
    import csv

    with output.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        return [row for _, row in zip(range(50), reader)]


def safe_output_path(file_name):
    path = (OUTPUT_DIR / Path(file_name).name).resolve()
    if path.parent != OUTPUT_DIR:
        raise HTTPException(status_code=400, detail="Arquivo invalido.")
    return path


def safe_slug(value):
    import re

    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return slug or "upload"


app.mount("/", StaticFiles(directory=ROOT, html=True), name="static")
