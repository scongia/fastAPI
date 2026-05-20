import re
from pathlib import Path

from .base import BankParser, Transaction


class EmiratesNBDCreditParser(BankParser):
    """
    ENBD Skywards / credit-card e-statements.

    The bilingual (Arabic + English) layout defeats pdfplumber's table detection
    — the Amount column ends up as None in every extracted row. We work directly
    with word bounding boxes instead:

        x0 ≈  40–110  → Transaction Date  (DD/MM/YYYY)
        x0 ≈ 125–200  → Posting Date      (DD/MM/YYYY)
        x0 ≈ 200–505  → Description
        x0 ≈ 505–570  → Amount            (digits; "CR" suffix = credit/return)
    """

    BANK_ID = "ENBD"
    DATE_FORMATS = ["%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y", "%d/%m/%y"]

    _TXN_DATE_X  = (40,  110)
    _POST_DATE_X = (125, 200)
    _DESC_X      = (200, 505)
    _AMOUNT_X    = (505, 570)

    _DATE_RE   = re.compile(r'^\d{2}/\d{2}/\d{4}$')
    _AMOUNT_RE = re.compile(r'^-?[\d,]+\.\d{2}(CR)?$', re.IGNORECASE)

    def _parse_page(self, page) -> list[Transaction]:
        return self._parse_page_words(page)

    def _parse_page_words(self, page) -> list[Transaction]:
        words = page.extract_words(x_tolerance=3, y_tolerance=3, keep_blank_chars=False)
        if not words:
            return []

        lines: dict[int, list[dict]] = {}
        for w in words:
            key = round(w['top'] / 2) * 2
            lines.setdefault(key, []).append(w)

        transactions: list[Transaction] = []
        for y_key in sorted(lines):
            row = sorted(lines[y_key], key=lambda w: w['x0'])

            txn_date_word = next(
                (w for w in row
                 if self._TXN_DATE_X[0] <= w['x0'] <= self._TXN_DATE_X[1]
                 and self._DATE_RE.match(w['text'])),
                None,
            )
            if not txn_date_word:
                continue

            date = self._parse_date(txn_date_word['text'])
            if not date:
                continue

            desc_words = [
                w['text'] for w in row
                if self._DESC_X[0] <= w['x0'] <= self._DESC_X[1]
            ]
            desc = ' '.join(desc_words).strip()
            desc = re.sub(r'\s+DUBAI\s+ARE?\s*$', '', desc).strip()
            if not desc:
                continue
            if self.SKIP_DESC_PATTERNS.search(desc):
                continue

            amt_word = next(
                (w for w in row
                 if self._AMOUNT_X[0] <= w['x0'] <= self._AMOUNT_X[1]
                 and self._AMOUNT_RE.match(w['text'])),
                None,
            )
            if not amt_word:
                continue

            raw = amt_word['text'].upper()
            is_credit = raw.endswith('CR')
            numeric = raw.replace('CR', '').replace(',', '')
            try:
                value = float(numeric)
            except ValueError:
                continue

            # Plain amount = purchase = debit from user's view = negative OFX
            # Amount + CR  = payment/return = credit = positive OFX
            amount = value if is_credit else -value

            transactions.append(Transaction(date=date, description=desc, amount=amount))

        return transactions

    def inspect(self, pdf_path: Path) -> None:
        print(f"\n=== Inspecting {pdf_path.name} (ENBD credit word-based parser) ===\n")
        for line in self._statement_header(pdf_path):
            print(line)
        print(f"\n  Columns:  date x∈{self._TXN_DATE_X}  |  "
              f"desc x∈{self._DESC_X}  |  amount x∈{self._AMOUNT_X}\n")

        with self._open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                words = page.extract_words(x_tolerance=3, y_tolerance=3, keep_blank_chars=False)
                lines: dict[int, list[dict]] = {}
                for w in words:
                    key = round(w['top'] / 2) * 2
                    lines.setdefault(key, []).append(w)

                txn_rows = []
                for y_key in sorted(lines):
                    row = sorted(lines[y_key], key=lambda w: w['x0'])
                    txn_date = next(
                        (w for w in row
                         if self._TXN_DATE_X[0] <= w['x0'] <= self._TXN_DATE_X[1]
                         and self._DATE_RE.match(w['text'])),
                        None,
                    )
                    if not txn_date:
                        continue
                    desc = ' '.join(
                        w['text'] for w in row
                        if self._DESC_X[0] <= w['x0'] <= self._DESC_X[1]
                    )
                    amt = next(
                        (w['text'] for w in row
                         if self._AMOUNT_X[0] <= w['x0'] <= self._AMOUNT_X[1]
                         and self._AMOUNT_RE.match(w['text'])),
                        "MISSING",
                    )
                    txn_rows.append((txn_date['text'], desc, amt))

                if txn_rows:
                    print(f"  Page {page_num}  ({len(txn_rows)} transaction rows)")
                    print(f"  {'Date':<12}  {'Amount':>12}  Description")
                    print(f"  {'-'*12}  {'-'*12}  {'-'*40}")
                    for date, desc, amt in txn_rows:
                        print(f"  {date:<12}  {amt:>12}  {desc}")
                    print()
