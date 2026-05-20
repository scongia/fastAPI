"""
HSBC UAE current account e-statements.

The transaction pages are rendered entirely as bitmap images — pdfplumber
cannot extract any text from them.  We therefore use pytesseract OCR at
200 DPI to recover word positions and then parse the columnar layout.

Pre-processing: each page is thresholded at pixel value 230 (grayscale)
before OCR.  This brightens the lightly-shaded account header rows so that
tesseract can read them, while also boosting contrast in the amount columns.

Layout (all coordinates as % of page width at 200 DPI):
    Date                x  <  15 %   format DDMmmYYYY  e.g. "22Nov2024"
    Transaction details x 15-50 %
    Deposits            x 50-65 %
    Withdrawals         x 65-75 %
    Balance             x 75-90 %

Each transaction spans multiple OCR lines:
  - Zero or one date line (date carried forward when absent)
  - One or more description lines
  - A "REF …" line that is always the last line of the transaction and
    carries the deposit/withdrawal amount and the new running balance

HSBC statements contain multiple account sections within the same PDF
(Current Account, Statement Savings A/C, Auto Loan, etc.).  We only parse
sections headed by account types listed in _WANTED_ACCOUNT_TYPES.  The
section header row contains the account type, the account number, and the
IBAN on a single line.  On continuation pages there is NO repeat of the
section header — the page begins directly with transactions.  The parser
therefore tracks in_wanted_section across pages at the parse() level.

Skip rows: BALANCE BROUGHT FORWARD, BALANCE CARRIED FORWARD, CLOSING
BALANCE, Transaction Summary, Transaction Count.
"""

import re
import logging
from pathlib import Path
from typing import Optional

from .base import BankParser, Transaction, _amount

log = logging.getLogger(__name__)

try:
    import pytesseract
    from pdf2image import convert_from_path
    import numpy as np
    from PIL import Image as _PILImage
    _OCR_AVAILABLE = True
except ImportError:
    _OCR_AVAILABLE = False


# ---------------------------------------------------------------------------
# Column x-ranges as fractions of page width (empirical, 200 DPI)
# ---------------------------------------------------------------------------
_DATE_X_MAX    = 0.15
_DESC_X_MIN    = 0.15
_DESC_X_MAX    = 0.50
_DEP_X_MIN     = 0.50
_DEP_X_MAX     = 0.65
_WDR_X_MIN     = 0.65
_WDR_X_MAX     = 0.75
_BAL_X_MIN     = 0.75
_BAL_X_MAX     = 0.92

# Date: DDMmmYYYY — OCR sometimes reads "0" as "O"
_DATE_RE = re.compile(
    r'^(\d{1,2}|[Oo]\d)([A-Za-z]{3})(\d{4})$'
)

_AMOUNT_RE = re.compile(r'^[\d,]+\.\d{2}(DR)?$', re.IGNORECASE)

# Account section types we want to parse (case-insensitive substring match)
_WANTED_ACCOUNT_TYPES = {
    "current account",
    "statement sav",  # "STATEMENT SAV A/C"
    "savings account",
    "saving account",
    "global money account",
}

# Account section types that end the wanted section (stop parsing)
_UNWANTED_ACCOUNT_TYPES = {
    "auto loan",
    "other loan",
    "home loan",
    "personal loan",
    "credit card",
    "hsbc premier",   # credit card / advance section
    "advance",
    "overdraft",
}

# Rows to discard
_SKIP_RE = re.compile(
    r'(balance\s*brought\s*forward|balance\s*carried\s*forward|'
    r'\bcarried\s*forward\b|\bbrought\s*forward\b|'
    r'closing\s+balance|opening\s+balance|'
    r'transaction\s+summary|transaction\s+count)',
    re.IGNORECASE,
)

# Pixel threshold for pre-processing: values above this become 255 (white).
# This brightens the gray-shaded account header rows enough for tesseract.
_THRESHOLD = 230


def _ocr_page(pil_image):
    """Return list of word dicts with keys: text, x_frac, y_px, conf."""
    # Convert to grayscale and apply threshold to improve contrast on
    # shaded header rows and amount columns.
    gray = pil_image.convert('L')
    arr = np.array(gray)
    arr = np.where(arr > _THRESHOLD, 255, arr).astype(np.uint8)
    img = _PILImage.fromarray(arr)

    w, _ = img.size
    data = pytesseract.image_to_data(
        img,
        config='--psm 6',
        output_type=pytesseract.Output.DICT,
    )
    words = []
    for i in range(len(data['text'])):
        txt = data['text'][i].strip()
        if not txt:
            continue
        conf = int(data['conf'][i])
        if conf < 15:
            continue
        words.append({
            'text':   txt,
            'x_frac': data['left'][i] / w,
            'y_px':   data['top'][i],
            'conf':   conf,
        })
    return words


def _group_lines(words, y_tolerance=12):
    """Group words into horizontal lines by y-position."""
    lines: dict[int, list[dict]] = {}
    for w in words:
        key = round(w['y_px'] / y_tolerance) * y_tolerance
        lines.setdefault(key, []).append(w)
    return [(y, sorted(lines[y], key=lambda w: w['x_frac'])) for y in sorted(lines)]


def _line_text(line_words, x_min=0.0, x_max=1.0):
    return ' '.join(w['text'] for w in line_words if x_min <= w['x_frac'] < x_max).strip()


def _parse_date_str(s: str) -> Optional[str]:
    """
    Normalise a DDMmmYYYY OCR string to "DD Mmm YYYY" for strptime.
    Handles OCR confusion of O→0.
    Returns None if it does not match the pattern.
    """
    m = _DATE_RE.match(s)
    if not m:
        return None
    day_s, mon_s, yr_s = m.groups()
    day_s = day_s.replace('O', '0').replace('o', '0')
    return f"{day_s} {mon_s.capitalize()} {yr_s}"


def _is_account_header(line_words) -> bool:
    """True when the line is a transaction table header for a wanted account.
    Must contain a wanted account type keyword AND either an IBAN (AExx...) or
    account number (NNN-NNNNNN-NNN). Summary rows in the account overview table
    are excluded because they contain extra numeric columns after the account number.
    """
    text = ' '.join(w['text'] for w in line_words)
    text_lower = text.lower()
    if not any(kw in text_lower for kw in _WANTED_ACCOUNT_TYPES):
        return False
    # Must have IBAN or account number format
    has_iban = bool(re.search(r'\bAE\w{20,23}\b', text))
    has_acct = bool(re.search(r'\b\d{3}-\d{6}-\d{3}\b', text))
    if not (has_iban or has_acct):
        return False
    # Exclude summary rows: they have 3+ standalone numbers after the account
    # number (credit limit, balance, AED equivalent) — transaction headers don't
    trailing = re.sub(r'.*\d{3}-\d{6}-\d{3}', '', text)
    if len(re.findall(r'[\d,]+\.\d{2}', trailing)) >= 2:
        return False
    return True


def _is_unwanted_section(line_words) -> bool:
    """True when the line starts an account section we do NOT want to parse."""
    text = ' '.join(w['text'] for w in line_words).lower()
    return any(kw in text for kw in _UNWANTED_ACCOUNT_TYPES)


def _parse_amount(text: str) -> Optional[float]:
    """Parse a number like '2,461.93' or '177.32DR' — DR suffix = negative."""
    if not text:
        return None
    text = text.strip()
    m = re.match(r'^([\d,]+\.\d{2})(DR)?$', text, re.IGNORECASE)
    if not m:
        return None
    val = float(m.group(1).replace(',', ''))
    if m.group(2):
        val = -val
    return val


class HSBCParser(BankParser):
    """
    HSBC UAE Premier / Current Account statements (OCR-based).

    Overrides parse() entirely — pdfplumber cannot extract text from these
    image-based PDFs.
    """

    BANK_ID = "HSBC"
    DATE_FORMATS = ["%d %b %Y"]   # after normalisation: "22 Nov 2024"

    def parse(self, pdf_path: Path) -> list[Transaction]:
        if not _OCR_AVAILABLE:
            raise ImportError(
                "HSBC parser requires pytesseract, pdf2image, numpy, and Pillow.\n"
                "Install with: pip install pytesseract pdf2image numpy Pillow\n"
                "and: brew install tesseract  (macOS)"
            )

        images = convert_from_path(
            str(pdf_path),
            dpi=200,
            grayscale=True,
        )

        transactions: list[Transaction] = []
        # State that must survive across page boundaries:
        #   in_wanted_section — True once we've seen a wanted account header
        #   date_cell         — the last date seen (carried forward)
        #   desc_parts        — accumulated description lines for the current txn
        #   pending_*         — amounts waiting to be flushed
        #   account_info      — list of (account_type, account_number, iban) tuples
        state = {
            'in_wanted_section': False,
            'date_cell': None,
            'desc_parts': [],
            'pending_dep': None,
            'pending_wdr': None,
            'pending_bal': None,
            'account_info': None,   # (acct_type, acct_num, iban) of the first wanted table
            'active_acct_num': None,  # account number currently being parsed
            'done': False,            # True once a second account table is seen — stop all parsing
        }

        for page_img in images:
            if state['done']:
                break
            page_txns, state = self._parse_ocr_page(page_img, state)
            transactions.extend(page_txns)

        # Flush any transaction pending at end of document
        txn = self._flush(state)
        if txn:
            transactions.append(txn)

        # Store the single account's metadata for --inspect
        self._account_info = [state['account_info']] if state['account_info'] else []

        return self._deduplicate(transactions)

    # ------------------------------------------------------------------
    # Core OCR page parser
    # ------------------------------------------------------------------

    def _flush(self, state: dict) -> Optional[Transaction]:
        """Emit a Transaction from accumulated state, or None."""
        desc_parts = state['desc_parts']
        if not desc_parts or state['date_cell'] is None:
            state['desc_parts'] = []
            state['pending_dep'] = state['pending_wdr'] = state['pending_bal'] = None
            return None

        desc = ' '.join(desc_parts).strip()
        state['desc_parts'] = []

        if _SKIP_RE.search(desc) or self.SKIP_DESC_PATTERNS.search(desc):
            state['pending_dep'] = state['pending_wdr'] = state['pending_bal'] = None
            return None

        amount: Optional[float] = None
        if state['pending_dep'] is not None and state['pending_dep'] != 0:
            amount = abs(state['pending_dep'])
        elif state['pending_wdr'] is not None and state['pending_wdr'] != 0:
            amount = -abs(state['pending_wdr'])

        bal = state['pending_bal']
        state['pending_dep'] = state['pending_wdr'] = state['pending_bal'] = None

        if amount is None:
            return None

        desc = re.sub(r'\s+REF\s+[A-Z0-9]{4}-\d+\s*$', '', desc).strip()

        return Transaction(
            date=state['date_cell'],
            description=desc,
            amount=amount,
            balance=bal,
        )

    def _parse_ocr_page(
        self, page_img, state: dict
    ) -> tuple[list[Transaction], dict]:
        words = _ocr_page(page_img)
        lines = _group_lines(words)

        transactions: list[Transaction] = []
        # in_txn_zone: True once we've seen the column header row or account
        # header on this page.  Prevents page boilerplate (address, customer
        # number, etc.) from bleeding into the transaction description buffer
        # on continuation pages where in_wanted_section is already True.
        in_txn_zone = False
        prev_line_full = ''  # used to look back for account number on header detection

        for _y, line_words in lines:
            if not line_words:
                continue

            line_full = ' '.join(w['text'] for w in line_words)
            # Track previous line for look-back (e.g. account number before header)
            last_line_full = prev_line_full
            prev_line_full = line_full

            # ── Page footer — stop processing this page ──────────────────
            if 'system generated' in line_full.lower() or 'does not require' in line_full.lower():
                break

            # ── Account section header ───────────────────────────────────
            if _is_account_header(line_words):
                # Extract account number and IBAN from this header line.
                # Account number may also be on the immediately preceding line.
                line_text = ' '.join(w['text'] for w in line_words)
                acct_num = ''
                iban = ''
                acct_type = ''
                iban_m = re.search(r'\bAE[\w\s]{21,23}\b', line_text)
                if iban_m:
                    iban = iban_m.group(0).replace(' ', '')
                acct_m = re.search(r'\b(\d{3}-\d{6}-\d{3})\b', line_text)
                if acct_m:
                    acct_num = acct_m.group(1)
                if not acct_num:
                    prev_m = re.search(r'\b(\d{3}-\d{6}-\d{3})\b', last_line_full)
                    if prev_m:
                        acct_num = prev_m.group(1)
                for kw in _WANTED_ACCOUNT_TYPES:
                    if kw in line_text.lower():
                        type_m = re.match(r'^([\w\s/]+?)\s+\d{3}-', line_text)
                        acct_type = type_m.group(1).strip() if type_m else kw.title()
                        break

                if state['account_info'] is None:
                    # First wanted table — start parsing it
                    state['account_info'] = (acct_type, acct_num, iban)
                    state['active_acct_num'] = acct_num
                    txn = self._flush(state)
                    if txn:
                        transactions.append(txn)
                    state['in_wanted_section'] = True
                    state['date_cell'] = None
                    in_txn_zone = True
                elif acct_num and acct_num != state['active_acct_num']:
                    # Different account number — stop all processing
                    txn = self._flush(state)
                    if txn:
                        transactions.append(txn)
                    state['done'] = True
                    return transactions, state
                else:
                    # Same account repeated on a continuation page — keep going
                    in_txn_zone = True
                continue

            # ── Unwanted section (loan, credit card, etc.) — stop parsing ─
            # Only relevant once we've already started parsing a wanted section
            if state['in_wanted_section'] and _is_unwanted_section(line_words):
                txn = self._flush(state)
                if txn:
                    transactions.append(txn)
                state['done'] = True
                return transactions, state

            if not state['in_wanted_section']:
                continue

            # ── Column header row (Date / Transaction Details / …) ───────
            date_col_text = _line_text(line_words, 0, _DATE_X_MAX).lower()
            desc_col_text = _line_text(line_words, _DESC_X_MIN, _DESC_X_MAX).lower()
            if 'date' in date_col_text and ('transaction' in desc_col_text or 'detail' in desc_col_text):
                in_txn_zone = True
                continue

            # ── "DR=Debit" marker row — signals start of transaction area
            # on continuation pages (no account header on these pages)
            if 'dr=debit' in line_full.lower() or 'dr = debit' in line_full.lower():
                in_txn_zone = True
                continue

            if not in_txn_zone:
                continue

            # ── Skip rows (balance forward, closing balance, etc.) ───────
            if _SKIP_RE.search(line_full):
                continue

            # ── Currency header line (AED / EUR / …) ────────────────────
            if re.match(r'^[A-Z]{3}$', line_full.strip()):
                continue

            # ── Date field ───────────────────────────────────────────────
            date_words = [w for w in line_words if w['x_frac'] < _DATE_X_MAX]
            new_date = None
            if date_words:
                date_str = _parse_date_str(date_words[0]['text'])
                if date_str:
                    new_date = self._parse_date(date_str)

            # ── Amount fields ────────────────────────────────────────────
            dep_text = _line_text(line_words, _DEP_X_MIN, _DEP_X_MAX)
            wdr_text = _line_text(line_words, _WDR_X_MIN, _WDR_X_MAX)
            bal_text = _line_text(line_words, _BAL_X_MIN, _BAL_X_MAX)

            dep_val = _parse_amount(dep_text)
            wdr_val = _parse_amount(wdr_text)
            bal_val = _parse_amount(bal_text)

            # ── Description words ────────────────────────────────────────
            desc_words_text = _line_text(line_words, _DESC_X_MIN, _DESC_X_MAX)

            # ── Classify the line ────────────────────────────────────────
            has_amount  = dep_val is not None or wdr_val is not None
            has_balance = bal_val is not None

            # A REF line closes the current transaction
            is_ref_line = (
                has_amount and has_balance
            ) or (
                has_balance and desc_words_text and
                re.match(r'^REF\b', desc_words_text, re.IGNORECASE)
            )

            if new_date is not None:
                state['date_cell'] = new_date

            if is_ref_line:
                if desc_words_text:
                    state['desc_parts'].append(desc_words_text)
                if dep_val is not None:
                    state['pending_dep'] = dep_val
                if wdr_val is not None:
                    state['pending_wdr'] = wdr_val
                if bal_val is not None:
                    state['pending_bal'] = bal_val
                txn = self._flush(state)
                if txn:
                    transactions.append(txn)
            else:
                if has_balance and not has_amount and not desc_words_text:
                    # Pure balance line with no desc (e.g. opening bal line)
                    continue
                if desc_words_text:
                    # Skip noise fragments: lone pipes, brackets, single chars,
                    # or partial numbers like "09.59" that are misread balance values
                    if re.match(r'^[\|\[\]<>\d\.\s]+$', desc_words_text) and len(desc_words_text) < 8:
                        continue
                    state['desc_parts'].append(desc_words_text)

        return transactions, state

    # ------------------------------------------------------------------
    # detect_bank helper — expose page 2 text via OCR
    # ------------------------------------------------------------------

    @staticmethod
    def ocr_first_page_text(pdf_path: Path) -> str:
        """Return OCR text of the first non-blank page (for bank detection)."""
        if not _OCR_AVAILABLE:
            return ""
        try:
            images = convert_from_path(str(pdf_path), dpi=100,
                                       grayscale=True, first_page=2, last_page=2)
            if not images:
                return ""
            words = _ocr_page(images[0])
            return ' '.join(w['text'] for w in words).lower()
        except Exception:
            return ""
