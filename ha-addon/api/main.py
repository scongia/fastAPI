"""
FastAPI service wrapping pdf_unlock.py and pdf_to_ofx.py as HTTP endpoints.

POST /unlock        — accepts PDF + password, returns unlocked PDF bytes
POST /convert       — accepts PDF + optional parser key, writes OFX to /data/ofx,
                      returns OFX bytes
POST /pdf-to-image  — accepts PDF, returns page 1 as base64-encoded JPEG
GET  /health        — liveness check
"""

import base64
import io
import logging
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pdf2image import convert_from_bytes

import sys
import os

# Make root package importable when running from /app inside Docker
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsers import PARSERS, detect_bank  # noqa: E402
from pdf_to_ofx import convert  # noqa: E402
from pdf_unlock import unlock  # noqa: E402

log = logging.getLogger("api")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="PDF→OFX API")

OFX_OUTPUT_ROOT = Path(os.environ.get("OFX_OUTPUT_ROOT", "/data/ofx"))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/unlock")
async def unlock_pdf(
    file: UploadFile = File(...),
    password: str = Form(...),
) -> Response:
    pdf_bytes = await file.read()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        original_stem = Path(file.filename or "statement").stem
        out_name = f"{original_stem}_unlocked.pdf"
        in_path = tmp_path / "input.pdf"
        out_path = tmp_path / out_name
        in_path.write_bytes(pdf_bytes)
        try:
            unlock(in_path, password, out_path)
        except SystemExit:
            raise HTTPException(status_code=400, detail="Unlock failed — wrong password or invalid PDF")
        except Exception as exc:
            log.exception("unlock error")
            raise HTTPException(status_code=500, detail=str(exc))
        return Response(
            content=out_path.read_bytes(),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
        )


@app.post("/convert")
async def convert_pdf(
    file: UploadFile = File(...),
    parser: str = Form(""),
) -> Response:
    if parser and parser not in PARSERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown parser '{parser}'. Valid: {sorted(PARSERS)}",
        )

    pdf_bytes = await file.read()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        in_path = tmp_path / (file.filename or "input.pdf")
        in_path.write_bytes(pdf_bytes)

        # Auto-detect bank if no parser supplied
        parser_key = parser
        if not parser_key:
            parser_key = detect_bank(in_path) or ""
            if not parser_key:
                raise HTTPException(
                    status_code=422,
                    detail="Could not detect bank. Supply a 'parser' field.",
                )
            log.info("Auto-detected bank: %s", parser_key)

        # Write OFX to shared volume under /data/ofx/<parser>/
        ofx_dir = OFX_OUTPUT_ROOT / parser_key
        ofx_dir.mkdir(parents=True, exist_ok=True)

        try:
            convert(in_path, parser_key=parser_key, output_dir=ofx_dir)
        except SystemExit:
            raise HTTPException(status_code=500, detail="Conversion failed — check server logs")
        except Exception as exc:
            log.exception("convert error")
            raise HTTPException(status_code=500, detail=str(exc))

        # Return the OFX file that was just written
        ofx_files = sorted(ofx_dir.glob("*.ofx"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not ofx_files:
            raise HTTPException(status_code=500, detail="OFX file not found after conversion")

        latest = ofx_files[0]
        return Response(
            content=latest.read_bytes(),
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="{latest.name}"'},
        )


@app.post("/pdf-to-image")
async def pdf_to_image(file: UploadFile = File(...), page: int = 1):
    """Convert a PDF page to a base64-encoded JPEG for AI vision processing."""
    pdf_bytes = await file.read()
    images = convert_from_bytes(pdf_bytes, first_page=page, last_page=page, fmt="jpeg")

    if not images:
        raise HTTPException(status_code=422, detail="Could not convert PDF to image")

    buffer = io.BytesIO()
    images[0].save(buffer, format="JPEG")
    b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

    return {"base64": b64, "media_type": "image/jpeg"}
