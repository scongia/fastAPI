"""
TRN Assistant router — endpoints called by n8n.

Mounted at /trn by main.py so all paths become:
  POST /trn/process-invoice
  POST /trn/extract-trn
  GET  /trn/vendor/{vendor_name}
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from trn_assistant.dext_client import DextClient
from trn_assistant.trn_extractor import extract_trn
from trn_assistant.zoho_client import ZohoClient

log = logging.getLogger("trn_assistant")

router = APIRouter(prefix="/trn", tags=["TRN Assistant"])

_dext = DextClient()
_zoho = ZohoClient()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ProcessInvoiceRequest(BaseModel):
    item_id: str
    supplier_name: str
    # Optional: pre-signed document URL from the Dext webhook payload.
    # If omitted the service fetches the document via the Dext API.
    document_url: str | None = None


class ProcessInvoiceResponse(BaseModel):
    item_id: str
    supplier_name: str
    trn_found: bool
    trn: str | None
    zoho_action: str | None  # "created" | "updated" | "unchanged" | None
    zoho_contact_id: str | None
    message: str


class ExtractTrnRequest(BaseModel):
    item_id: str | None = None
    document_url: str | None = None


class ExtractTrnResponse(BaseModel):
    trn_found: bool
    trn: str | None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/process-invoice", response_model=ProcessInvoiceResponse)
async def process_invoice(body: ProcessInvoiceRequest):
    """
    Full pipeline:
      1. Download invoice document (from URL or Dext API)
      2. Extract TRN using Claude vision
      3. Create or update vendor in Zoho Books with TRN

    n8n calls this endpoint after receiving a Dext item.published webhook.
    """
    # Step 1: get the document bytes
    try:
        if body.document_url:
            doc_bytes, content_type = await _dext.get_document_bytes_from_url(body.document_url)
        else:
            doc_bytes, content_type = await _dext.get_document_bytes(body.item_id)
    except Exception as exc:
        log.error("Failed to fetch document for item %s: %s", body.item_id, exc)
        raise HTTPException(status_code=502, detail=f"Could not fetch invoice document: {exc}")

    # Step 2: extract TRN
    try:
        trn = await extract_trn(doc_bytes, content_type)
    except Exception as exc:
        log.error("TRN extraction failed for item %s: %s", body.item_id, exc)
        raise HTTPException(status_code=502, detail=f"TRN extraction failed: {exc}")

    if trn is None:
        return ProcessInvoiceResponse(
            item_id=body.item_id,
            supplier_name=body.supplier_name,
            trn_found=False,
            trn=None,
            zoho_action=None,
            zoho_contact_id=None,
            message="No TRN found — vendor is likely not VAT-registered. No Zoho update made.",
        )

    # Step 3: ensure vendor exists in Zoho Books with TRN
    try:
        contact, action = await _zoho.ensure_vendor_has_trn(body.supplier_name, trn)
    except Exception as exc:
        log.error("Zoho Books update failed for '%s': %s", body.supplier_name, exc)
        raise HTTPException(status_code=502, detail=f"Zoho Books update failed: {exc}")

    return ProcessInvoiceResponse(
        item_id=body.item_id,
        supplier_name=body.supplier_name,
        trn_found=True,
        trn=trn,
        zoho_action=action,
        zoho_contact_id=contact.get("contact_id"),
        message=f"Vendor '{body.supplier_name}' {action} in Zoho Books with TRN {trn}.",
    )


@router.post("/extract-trn", response_model=ExtractTrnResponse)
async def extract_trn_only(body: ExtractTrnRequest):
    """
    Extract TRN from an invoice without touching Zoho Books.
    Useful for testing or for workflows that handle Zoho separately.
    """
    if not body.item_id and not body.document_url:
        raise HTTPException(status_code=400, detail="Provide item_id or document_url.")

    try:
        if body.document_url:
            doc_bytes, content_type = await _dext.get_document_bytes_from_url(body.document_url)
        else:
            doc_bytes, content_type = await _dext.get_document_bytes(body.item_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch document: {exc}")

    trn = await extract_trn(doc_bytes, content_type)
    return ExtractTrnResponse(trn_found=trn is not None, trn=trn)


@router.get("/vendor/{vendor_name}")
async def get_vendor(vendor_name: str):
    """
    Look up a vendor in Zoho Books by name.
    Useful for n8n to check whether a vendor already has a TRN before processing.
    """
    contact = await _zoho.find_vendor(vendor_name)
    if not contact:
        return {"found": False, "vendor_name": vendor_name}

    return {
        "found": True,
        "vendor_name": vendor_name,
        "contact_id": contact.get("contact_id"),
        "tax_treatment": contact.get("tax_treatment"),
        "tax_registration_number": contact.get("tax_registration_number"),
        "has_trn": bool(contact.get("tax_registration_number")),
    }
