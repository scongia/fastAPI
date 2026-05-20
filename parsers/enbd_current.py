import re
from pathlib import Path
from typing import Optional

from .base import BankParser, Transaction, _amount

try:
    import pytesseract
    from pdf2image import convert_from_path
    import numpy as np
    from PIL import Image as _PILImage
    _OCR_AVAILABLE = True
except ImportError:
    _OCR_AVAILABLE = False


class EmiratesNBDCurrentParser(BankParser):
    """
    ENBD current account e-statements.

    Two PDF variants are handled:

    Variant A — text-based (normal statements):
        The bilingual (Arabic + English) layout is parsed using word bounding boxes.
        pdfplumber extracts both English and Arabic text; we look for the English
        column header row ("Date" + "Details") and parse rows below it.

    Variant B — vector-curves PDFs (some older statements where text is rendered
        as outlines, not embedded as text characters):
        pdfplumber cannot extract any text from the transaction area.  Detected
        when _parse_page_words() returns no transactions on the first page.
        Falls back to pytesseract OCR at 200 DPI.

    Transaction layout (both variants):
        Layout A (single-line description):
            [date] [description text ...]  [debit|credit]  [balance] [Cr|Dr]

        Layout B (multi-line description):
            [description line 1 ...]                        (no date, no balance)
            [date]                          [debit|credit]  [balance] [Cr|Dr]
            [continuation / reference line] (optional)

    Column x-ranges for text-based variant (points):
        date    x0 ∈ (20,  80)
        desc    x0 ∈ (80,  340)
        debit   x0 ∈ (330, 420)
        credit  x0 ∈ (420, 510)
        balance x0 ∈ (510, 600)

    Column x-ranges for OCR variant (as fraction of page width at 200 DPI):
        date    x_frac < 0.13
        desc    x_frac ∈ (0.13, 0.55)
        debit   x_frac ∈ (0.55, 0.70)
        credit  x_frac ∈ (0.70, 0.855)
        balance x_frac ∈ (0.855, 0.915)
        Cr/Dr   x_frac >= 0.915
    """

    BANK_ID = "ENBD"
    DATE_FORMATS = ["%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y", "%d/%m/%y"]

    # ── Text-based column ranges (points) ────────────────────────────────────
    _DATE_X   = (20,  80)
    _DESC_X   = (80,  340)
    _DEBIT_X  = (330, 420)
    _CREDIT_X = (420, 510)
    _BAL_X    = (510, 600)

    # ── OCR column ranges (x_frac) ────────────────────────────────────────────
    _OCR_DATE_MAX   = 0.13
    _OCR_DESC_MIN   = 0.13
    _OCR_DESC_MAX   = 0.55
    _OCR_DEBIT_MIN  = 0.55
    _OCR_DEBIT_MAX  = 0.70
    _OCR_CREDIT_MIN = 0.70
    _OCR_CREDIT_MAX = 0.855
    _OCR_BAL_MIN    = 0.855
    _OCR_BAL_MAX    = 0.915

    _DATE_RE    = re.compile(r'^\d{2}/\d{2}/\d{4}$')
    _AMOUNT_RE  = re.compile(r'^[\d,]+\.\d{2}$')
    _BAL_SUFFIX = re.compile(r'\s+(cr|dr)\s*$', re.IGNORECASE)
    _ARABIC_RE  = re.compile(r'[\u0600-\u06FF]')

    # ── Entry point ───────────────────────────────────────────────────────────

    def parse(self, pdf_path: Path) -> list[Transaction]:
        """Override parse() to detect vector-curves PDFs and switch to OCR."""
        # Try the normal text-based path first via the base class
        transactions = super().parse(pdf_path)
        if transactions:
            self._account_info = self._extract_account_info_text(pdf_path)
            return transactions

        # No transactions found via text extraction — check if this is a
        # vector-curves PDF by looking for the "Date"/"Details" header on page 1.
        # If the header exists in text, there genuinely are no transactions.
        # If the header is absent, the PDF needs OCR.
        try:
            import pdfplumber
            with pdfplumber.open(str(pdf_path)) as pdf:
                page = pdf.pages[0]
                words = page.extract_words(x_tolerance=3, y_tolerance=3, keep_blank_chars=False)
                texts = {w['text'] for w in words}
                header_found = 'Date' in texts and 'Details' in texts
        except Exception:
            header_found = False

        if header_found:
            # Header present but no transactions → genuinely empty statement
            self._account_info = self._extract_account_info_text(pdf_path)
            return []

        # No header in text → vector-curves PDF, fall back to OCR
        return self._parse_ocr(pdf_path)

    # ── Text-based page parser (called by base class) ─────────────────────────

    def _parse_page(self, page) -> list[Transaction]:
        return self._parse_page_words(page)

    def _parse_page_words(self, page) -> list[Transaction]:
        words = page.extract_words(x_tolerance=3, y_tolerance=3, keep_blank_chars=False)
        if not words:
            return []

        # Group words into horizontal lines
        lines: dict[int, list[dict]] = {}
        for w in words:
            key = round(w['top'] / 2) * 2
            lines.setdefault(key, []).append(w)

        # Locate the header row ("Date" + "Details")
        header_y: Optional[int] = None
        for y in sorted(lines):
            texts = {w['text'] for w in lines[y]}
            if 'Date' in texts and 'Details' in texts:
                header_y = y
                break
        if header_y is None:
            return []

        parsed_rows: list[tuple[int, dict]] = []

        for y in sorted(lines):
            if y <= header_y:
                continue
            row_words = sorted(lines[y], key=lambda w: w['x0'])

            date_word = next(
                (w for w in row_words
                 if self._DATE_X[0] <= w['x0'] <= self._DATE_X[1]
                 and self._DATE_RE.match(w['text'])),
                None,
            )
            desc_words = [
                w['text'] for w in row_words
                if self._DESC_X[0] <= w['x0'] <= self._DESC_X[1]
            ]
            desc_text = ' '.join(desc_words).strip()

            # Stop at footer (Arabic text in the description column)
            if self._ARABIC_RE.search(desc_text):
                break

            debit_word = next(
                (w for w in row_words
                 if self._DEBIT_X[0] <= w['x0'] <= self._DEBIT_X[1]
                 and self._AMOUNT_RE.match(w['text'])),
                None,
            )
            credit_word = next(
                (w for w in row_words
                 if self._CREDIT_X[0] <= w['x0'] <= self._CREDIT_X[1]
                 and self._AMOUNT_RE.match(w['text'])),
                None,
            )
            bal_words = [w for w in row_words if self._BAL_X[0] <= w['x0'] <= self._BAL_X[1]]
            bal_text = ' '.join(w['text'] for w in bal_words).strip()
            bal_numeric = self._BAL_SUFFIX.sub('', bal_text).strip()
            bal_val = _amount(bal_numeric) if bal_numeric else None

            parsed_rows.append((y, {
                'date':   date_word['text'] if date_word else None,
                'desc':   desc_text,
                'debit':  debit_word['text'] if debit_word else None,
                'credit': credit_word['text'] if credit_word else None,
                'bal':    bal_val,
            }))

        return self._assemble_transactions(parsed_rows)

    # ── OCR path ──────────────────────────────────────────────────────────────

    def _parse_ocr(self, pdf_path: Path) -> list[Transaction]:
        if not _OCR_AVAILABLE:
            raise ImportError(
                "OCR fallback requires pytesseract, pdf2image, numpy, and Pillow.\n"
                "Install with: pip install pytesseract pdf2image numpy Pillow\n"
                "and: brew install tesseract  (macOS)"
            )

        images = convert_from_path(str(pdf_path), dpi=200, grayscale=True)
        all_rows: list[tuple[int, dict]] = []  # (global_y, row)
        y_offset = 0

        for page_img in images:
            page_rows = self._ocr_page_rows(page_img)
            for y, row in page_rows:
                all_rows.append((y + y_offset, row))
            # Offset next page's y values so rows stay globally ordered
            _, h = page_img.size
            y_offset += h + 10000  # large gap between pages

        self._account_info = self._extract_account_info_ocr(images[0])
        transactions = self._assemble_transactions(all_rows)
        return self._deduplicate(transactions)

    def _extract_account_info_text(self, pdf_path: Path) -> list:
        """Extract account number from text-based PDFs. Returns [(type, acct_num, iban)]."""
        try:
            import pdfplumber
            with pdfplumber.open(str(pdf_path)) as pdf:
                page = pdf.pages[0]
                chars = page.chars
                from collections import defaultdict
                by_top: dict = defaultdict(list)
                for c in chars:
                    by_top[round(c['top'])].append(c)
                text = ' '.join(
                    ''.join(c['text'] for c in sorted(row, key=lambda c: c['x0']))
                    for row in by_top.values()
                )
                return self._parse_account_info(text)
        except Exception:
            return []

    def _extract_account_info_ocr(self, pil_image) -> list:
        """Extract account number from OCR of the first page image. Returns [(type, acct_num, iban)]."""
        try:
            text = pytesseract.image_to_string(pil_image, config='--psm 6')
            return self._parse_account_info(text)
        except Exception:
            return []

    @staticmethod
    def _parse_account_info(text: str) -> list:
        """Parse 'Account number XXXXXXXXXX' and optional IBAN from raw text."""
        acct_num = ''
        iban = ''
        m = re.search(r'Account\s+number\s*(\d{10,16})', text)
        if m:
            acct_num = m.group(1)
        m = re.search(r'IBAN\s*(AE\w{21})', text)
        if m:
            iban = m.group(1)
        if acct_num or iban:
            return [('CURRENT ACCOUNT', acct_num, iban)]
        return []

    def _ocr_page_rows(self, pil_image) -> list[tuple[int, dict]]:
        """OCR one page image and return parsed row dicts."""
        w, _ = pil_image.size
        data = pytesseract.image_to_data(
            pil_image,
            config='--psm 6',
            output_type=pytesseract.Output.DICT,
        )

        words = []
        for i in range(len(data['text'])):
            txt = data['text'][i].strip()
            if not txt or int(data['conf'][i]) < 15:
                continue
            words.append({
                'text':   txt,
                'x_frac': data['left'][i] / w,
                'y_px':   data['top'][i],
            })

        # Group into lines
        lines: dict[int, list[dict]] = {}
        for wd in words:
            key = round(wd['y_px'] / 12) * 12
            lines.setdefault(key, []).append(wd)

        # Find header row ("Date" and "Details" present)
        header_y: Optional[int] = None
        for y in sorted(lines):
            texts = {w['text'] for w in lines[y]}
            if 'Date' in texts and 'Details' in texts:
                header_y = y
                break
            # Sometimes OCR splits "Date" and "Details" onto adjacent y-keys
            full = ' '.join(w['text'] for w in lines[y])
            if 'Date' in full and 'Details' in full:
                header_y = y
                break

        if header_y is None:
            # Continuation page — no header, process entire page
            header_y = -1

        parsed_rows: list[tuple[int, dict]] = []

        for y in sorted(lines):
            if y <= header_y:
                continue

            line_words = sorted(lines[y], key=lambda w: w['x_frac'])
            full_text = ' '.join(w['text'] for w in line_words)

            # Stop at Arabic footer
            if self._ARABIC_RE.search(full_text):
                break

            # Skip "BROUGHT FORWARD" / "CARRIED FORWARD" rows
            if re.search(r'BROUGHT\s+FORWARD|CARRIED\s+FORWARD', full_text, re.IGNORECASE):
                continue

            # Date
            date_words = [w for w in line_words if w['x_frac'] < self._OCR_DATE_MAX]
            date_str = None
            for dw in date_words:
                if self._DATE_RE.match(dw['text']):
                    date_str = dw['text']
                    break

            # Description
            desc_words = [
                w['text'] for w in line_words
                if self._OCR_DESC_MIN <= w['x_frac'] < self._OCR_DESC_MAX
            ]
            desc_text = ' '.join(desc_words).strip()

            # Debit / Credit / Balance
            debit_str  = self._ocr_col_amount(line_words, self._OCR_DEBIT_MIN,  self._OCR_DEBIT_MAX)
            credit_str = self._ocr_col_amount(line_words, self._OCR_CREDIT_MIN, self._OCR_CREDIT_MAX)
            bal_str    = self._ocr_col_amount(line_words, self._OCR_BAL_MIN,    self._OCR_BAL_MAX)

            # Cr/Dr marker (determines sign when amount is in the ambiguous middle column)
            crdr_words = [w['text'] for w in line_words if w['x_frac'] >= self._OCR_BAL_MAX]
            crdr = ' '.join(crdr_words).strip().upper()

            bal_val = _amount(bal_str) if bal_str else None

            parsed_rows.append((y, {
                'date':   date_str,
                'desc':   desc_text,
                'debit':  debit_str,
                'credit': credit_str,
                'bal':    bal_val,
                'crdr':   crdr,
            }))

        return parsed_rows

    @staticmethod
    def _ocr_col_amount(line_words, x_min: float, x_max: float) -> Optional[str]:
        """Return the first amount-like token in the given x_frac column range."""
        for w in line_words:
            if x_min <= w['x_frac'] < x_max:
                # Normalise OCR artefacts: comma instead of dot in decimal
                txt = w['text'].replace(',', '.', 1) if w['text'].count(',') == 1 and '.' not in w['text'] else w['text']
                if re.match(r'^[\d,]+\.\d{1,2}$', txt):
                    return txt
        return None

    # ── Shared transaction assembly ───────────────────────────────────────────

    def _assemble_transactions(self, parsed_rows: list[tuple[int, dict]]) -> list[Transaction]:
        """
        Convert a list of (y, row) dicts into Transaction objects.
        Works for both text-based and OCR rows.
        """
        transactions: list[Transaction] = []
        desc_buffer: list[str] = []

        i = 0
        while i < len(parsed_rows):
            _, row = parsed_rows[i]

            if row['date'] and row['bal'] is not None:
                date = self._parse_date(row['date'])
                j = i + 1
                if date:
                    desc_parts = list(desc_buffer)
                    if row['desc']:
                        desc_parts.append(row['desc'])

                    # Consume one following desc-only row for Layout B
                    if not row['desc'] and j < len(parsed_rows):
                        _, nrow = parsed_rows[j]
                        if not nrow['date'] and nrow['bal'] is None and nrow['desc']:
                            desc_parts.append(nrow['desc'])
                            j += 1

                    desc = ' '.join(desc_parts).strip()
                    desc = re.sub(r'\s+DUBAI\s+ARE?\s*$', '', desc).strip()
                    desc = re.sub(r'\s+DUBAI:AE\s*$', '', desc).strip()

                    amount: Optional[float] = None
                    if row.get('debit'):
                        v = _amount(row['debit'])
                        if v is not None:
                            amount = -abs(v)
                    elif row.get('credit'):
                        v = _amount(row['credit'])
                        if v is not None:
                            amount = abs(v)

                    if desc and amount is not None and not self.SKIP_DESC_PATTERNS.search(desc):
                        transactions.append(Transaction(
                            date=date,
                            description=desc,
                            amount=amount,
                            balance=row['bal'],
                        ))

                desc_buffer = []
                i = j
                continue

            elif not row['date'] and row['bal'] is None and row['desc']:
                desc_buffer.append(row['desc'])
            else:
                if not row['desc']:
                    desc_buffer = []

            i += 1

        return transactions
