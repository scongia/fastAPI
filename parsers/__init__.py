from pathlib import Path
from typing import Optional

import pdfplumber

from .base import BankParser
from .enbd_credit import EmiratesNBDCreditParser
from .enbd_current import EmiratesNBDCurrentParser
from .wio import WioParser
from .hsbc import HSBCParser

PARSERS: dict[str, type[BankParser]] = {
    "enbd-current": EmiratesNBDCurrentParser,
    "enbd-credit":  EmiratesNBDCreditParser,
    "wio":          WioParser,
    "hsbc":         HSBCParser,
}


def detect_bank(pdf_path: Path, password: str = "") -> Optional[str]:
    try:
        with pdfplumber.open(pdf_path, password=password) as pdf:
            text = ""
            for page in pdf.pages[:2]:
                text += (page.extract_text() or "").lower()
            if "emirates nbd" in text and "credit card statement" in text:
                return "enbd-credit"
            if "emirates nbd" in text and "current account" in text:
                return "enbd-current"
            if "wio" in text:
                return "wio"
    except Exception:
        pass

    # HSBC statements are image-based — pdfplumber extracts no text.
    # Fall back to OCR on the first non-cover page.
    ocr_text = HSBCParser.ocr_first_page_text(pdf_path)
    if "hsbc" in ocr_text:
        return "hsbc"

    return None


def detect_account_type(parser: BankParser, pdf_path: Path) -> str:
    """Returns 'credit' for credit cards, 'bank' for current/savings accounts."""
    CREDIT_KEYWORDS = {"credit card", "credit limit", "payment due", "minimum payment", "creditline"}
    try:
        with pdfplumber.open(pdf_path, password=parser.password or "") as pdf:
            text = ""
            for page in pdf.pages[:2]:
                text += (page.extract_text() or "").lower()
            if any(kw in text for kw in CREDIT_KEYWORDS):
                return "credit"
    except Exception:
        pass
    return "bank"
