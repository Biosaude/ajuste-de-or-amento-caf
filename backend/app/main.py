import shutil
import tempfile
import uuid
import base64
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .excel_service import SpreadsheetError, parse_spreadsheet
from .pdf_service import PdfError, create_reordered_pdf, integrity_errors, order_items, parse_pdf
from .storage import DATA, audit, get_base, init_db, set_base

app = FastAPI(title="Ordenador de Orçamentos", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
BASE_FILE = DATA / "current_base"
RESULTS: dict[str, Path] = {}


@app.on_event("startup")
def startup() -> None:
    init_db()


def safe_name(name: str | None) -> str:
    return Path(name or "arquivo").name


@app.get("/api/base")
def current_base():
    return get_base()


@app.post("/api/base")
async def upload_base(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".xlsx", ".xls"}:
        raise HTTPException(400, "Envie uma planilha .xlsx ou .xls.")
    target = BASE_FILE.with_suffix(suffix)
    for old in DATA.glob("current_base.*"):
        old.unlink(missing_ok=True)
    with target.open("wb") as output:
        shutil.copyfileobj(file.file, output)
    try:
        products = parse_spreadsheet(target)
    except SpreadsheetError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from exc
    metadata = {"name": safe_name(file.filename), "updated_at": datetime.now(timezone.utc).isoformat(), "count": len(products), "path": str(target)}
    set_base(metadata)
    return metadata


@app.post("/api/pdf/analyze")
async def analyze_pdf(file: UploadFile = File(...)):
    if Path(file.filename or "").suffix.lower() != ".pdf":
        raise HTTPException(400, "Envie um arquivo PDF.")
    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf") as temp:
        temp.write(content); temp.flush()
        try:
            parsed = parse_pdf(Path(temp.name))
        except PdfError as exc:
            raise HTTPException(422, str(exc)) from exc
    return {"budget_number": parsed.budget_number, "patient": parsed.patient, "item_count": len(parsed.items), "total": parsed.total}


@app.post("/api/process")
async def process(file: UploadFile = File(...), spreadsheet: UploadFile | None = File(None)):
    base = get_base()
    if spreadsheet is not None:
        suffix = Path(spreadsheet.filename or "").suffix.lower()
        if suffix not in {".xlsx", ".xls"}:
            raise HTTPException(400, "Envie uma planilha .xlsx ou .xls.")
        uploaded_base = Path(tempfile.mkdtemp(prefix="base_")) / f"base{suffix}"
        with uploaded_base.open("wb") as destination:
            shutil.copyfileobj(spreadsheet.file, destination)
        base = {"name": safe_name(spreadsheet.filename), "path": str(uploaded_base)}
    if not base or not Path(base["path"]).exists():
        raise HTTPException(409, "Carregue uma planilha base antes de processar.")
    token = uuid.uuid4().hex
    work = Path(tempfile.mkdtemp(prefix="orcamento_"))
    source, output = work / "original.pdf", work / "ordenado.pdf"
    try:
        with source.open("wb") as destination:
            shutil.copyfileobj(file.file, destination)
        products = parse_spreadsheet(Path(base["path"]))
        parsed = parse_pdf(source)
        ordered, missing = order_items(parsed.items, [p.normalized_code for p in products])
        errors = integrity_errors(parsed.items, ordered)
        if errors:
            raise HTTPException(422, {"message": "Falha na validação de integridade.", "divergences": errors})
        create_reordered_pdf(source, output, parsed.items, ordered)
        reparsed = parse_pdf(output)
        post_errors = integrity_errors(parsed.items, reparsed.items)
        if len(reparsed.items) != len(parsed.items):
            post_errors.append("O PDF final não contém a mesma quantidade de linhas detectáveis.")
        if post_errors:
            raise HTTPException(422, {"message": "Falha na validação de integridade. O documento final apresentou divergências em relação ao orçamento original.", "divergences": post_errors})
        source.unlink(missing_ok=True)
        RESULTS[token] = output
        audit(spreadsheet_name=base["name"], pdf_name=safe_name(file.filename), budget_number=parsed.budget_number,
              item_count=len(parsed.items), found_count=len(parsed.items)-missing, missing_count=missing, integrity="OK")
        preview = [{"original_order": item.original_order, "new_order": i, "code": item.code, "description": item.description} for i, item in enumerate(ordered, 1)]
        response = {"token": token, "budget_number": parsed.budget_number, "patient": parsed.patient, "total": parsed.total,
                    "item_count": len(ordered), "missing_count": missing, "preview": preview}
        # Serverless invocations do not share memory or /tmp. Return the result
        # with the metadata so download never depends on a later invocation.
        if os.getenv("VERCEL"):
            response["pdf_base64"] = base64.b64encode(output.read_bytes()).decode("ascii")
            shutil.rmtree(work, ignore_errors=True)
            RESULTS.pop(token, None)
        if spreadsheet is not None:
            shutil.rmtree(Path(base["path"]).parent, ignore_errors=True)
        return response
    except (PdfError, SpreadsheetError) as exc:
        shutil.rmtree(work, ignore_errors=True)
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/download/{token}")
def download(token: str):
    path = RESULTS.get(token)
    if not path or not path.exists():
        raise HTTPException(404, "Arquivo expirado ou inexistente.")
    parsed = parse_pdf(path)
    number = ''.join(ch for ch in parsed.budget_number if ch.isalnum()) or "SEM_NUMERO"
    def cleanup() -> None:
        RESULTS.pop(token, None)
        shutil.rmtree(path.parent, ignore_errors=True)

    return FileResponse(path, media_type="application/pdf", filename=f"ORCAMENTO_{number}_ORDENADO.pdf",
                        background=BackgroundTask(cleanup))
