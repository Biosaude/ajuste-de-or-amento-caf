import shutil
import tempfile
import uuid
import base64
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .excel_service import SpreadsheetError, parse_spreadsheet
from .normalize import normalize_code
from .pdf_service import PdfError, ValidationError, create_reordered_pdf, document_integrity_errors, integrity_errors, order_items, parse_pdf
from .storage import DATA, audit, get_base, init_db, set_base

LOGGER = logging.getLogger(__name__)
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
        LOGGER.info("EXCEL BASE ORDER:")
        for position, product in enumerate(products, 1):
            LOGGER.info("%s -> %s", position, product.code)
        parsed = parse_pdf(source)
        base_order = [product.code for product in products]
        LOGGER.info("PDF ORIGINAL:")
        for position, item in enumerate(parsed.items, 1):
            LOGGER.info("%s -> %s (raw=%r)", position, item.code, item.code)
        ordered, unmatched_pdf = order_items(parsed.items, base_order)
        excel_normalized = [normalize_code(code) for code in base_order]
        pdf_normalized = [normalize_code(item.code) for item in parsed.items]
        matched_codes = [item.normalized_code for item in ordered]
        unmatched_excel = len(excel_normalized) - len(matched_codes)
        LOGGER.info("Excel codes: %d; PDF codes: %d; Matched: %d; Unmatched Excel: %d; Unmatched PDF: %d",
                    len(excel_normalized), len(pdf_normalized), len(ordered), unmatched_excel, unmatched_pdf)
        for product in products:
            LOGGER.info("Excel raw=%r parsed=%r normalized=%r match=%s", product.raw_value,
                        product.code, product.normalized_code, product.normalized_code in pdf_normalized)
        if not ordered:
            LOGGER.error("EXCEL RAW: %s", [product.raw_value for product in products])
            LOGGER.error("EXCEL NORMALIZED: %s", excel_normalized)
            LOGGER.error("PDF RAW: %s", [item.code for item in parsed.items])
            LOGGER.error("PDF NORMALIZED: %s", pdf_normalized)
            raise HTTPException(422, "Nenhum código do PDF corresponde à ordem da planilha base.")
        if unmatched_excel or unmatched_pdf or len(ordered) != len(parsed.items):
            raise HTTPException(422, {"message": "Nem todos os códigos puderam ser relacionados; o PDF não foi gerado.",
                                      "excel_codes": len(excel_normalized), "pdf_codes": len(pdf_normalized),
                                      "matched": len(ordered), "unmatched_excel": unmatched_excel,
                                      "unmatched_pdf": unmatched_pdf})
        errors = integrity_errors(parsed.items, ordered)
        if errors:
            raise HTTPException(422, {"message": "Falha na validação de integridade.", "divergences": errors})
        create_reordered_pdf(source, output, parsed.items, ordered)
        reparsed = parse_pdf(output)
        LOGGER.info("ORDEM EXTRAÍDA DO PDF FINAL: %s", [item.code for item in reparsed.items])
        if [item.normalized_code for item in reparsed.items] != excel_normalized:
            raise ValidationError("O PDF gerado não corresponde à ordem da planilha base.")
        post_errors = document_integrity_errors(parsed, reparsed, ordered)
        if post_errors:
            raise HTTPException(422, {"message": "Falha na validação de integridade. O documento final apresentou divergências em relação ao orçamento original.", "divergences": post_errors})
        LOGGER.info("VALIDAÇÃO FINANCEIRA: itens=%d unidades=%s subtotal=%s total_produtos=%s total_geral=%s",
                    len(reparsed.items), reparsed.total_units, reparsed.subtotal,
                    reparsed.total_products, reparsed.total)
        source.unlink(missing_ok=True)
        RESULTS[token] = output
        audit(spreadsheet_name=base["name"], pdf_name=safe_name(file.filename), budget_number=parsed.budget_number,
              item_count=len(parsed.items), found_count=len(ordered), missing_count=unmatched_pdf, integrity="OK")
        preview = [{"original_order": item.original_order, "new_order": i, "code": item.code, "description": item.description} for i, item in enumerate(ordered, 1)]
        response = {"token": token, "budget_number": parsed.budget_number, "patient": parsed.patient, "total": parsed.total,
                    "item_count": len(ordered), "missing_count": unmatched_pdf, "preview": preview}
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
