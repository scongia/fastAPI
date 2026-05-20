"""
TRN extractor — uses Claude vision to find the UAE Tax Registration Number
on an invoice document (PDF or image).

UAE TRN: a 15-digit number issued by the Federal Tax Authority (FTA).
It always starts with 1 and appears on VAT-registered vendor invoices as
"TRN", "Tax Registration No.", "VAT Reg. No.", etc.
"""

import base64
import re

import anthropic
from trn_assistant import config

_TRN_PATTERN = re.compile(r"\b1\d{14}\b")

_EXTRACTION_PROMPT = """You are extracting a UAE Tax Registration Number (TRN) from an invoice.

The TRN is a 15-digit number issued by the UAE Federal Tax Authority (FTA).
It always starts with 1 and may be labelled as:
- TRN
- Tax Registration No. / Number
- VAT Registration No. / Number
- Tax Reg No

Look carefully at the entire document including headers, footers, and small print.

If you find a TRN, respond with ONLY the 15-digit number and nothing else.
If no TRN is present (e.g. the vendor is not VAT-registered), respond with exactly: NOT_FOUND"""


def _extract_trn_from_text(text: str) -> str | None:
    """Fallback: pull a 15-digit UAE TRN from plain text using regex."""
    match = _TRN_PATTERN.search(text)
    return match.group(0) if match else None


def _media_type_for(content_type: str) -> str:
    ct = content_type.lower()
    if "pdf" in ct:
        return "application/pdf"
    if "png" in ct:
        return "image/png"
    if "jpg" in ct or "jpeg" in ct:
        return "image/jpeg"
    if "webp" in ct:
        return "image/webp"
    if "gif" in ct:
        return "image/gif"
    return "application/pdf"


async def extract_trn(document_bytes: bytes, content_type: str) -> str | None:
    """
    Send the invoice document to Claude and return the extracted TRN, or None
    if the vendor has no TRN (not VAT-registered).
    """
    client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY())

    encoded = base64.standard_b64encode(document_bytes).decode("utf-8")
    media_type = _media_type_for(content_type)

    message = await client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=64,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document" if "pdf" in media_type else "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": encoded,
                        },
                    },
                    {"type": "text", "text": _EXTRACTION_PROMPT},
                ],
            }
        ],
    )

    raw = message.content[0].text.strip()

    if raw == "NOT_FOUND":
        return None

    # Validate: must be exactly 15 digits starting with 1
    if re.fullmatch(r"1\d{14}", raw):
        return raw

    # Claude returned something noisy — try regex on its response
    return _extract_trn_from_text(raw)
