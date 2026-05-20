import hashlib
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import pdfplumber
except ImportError:
    print("Error: pdfplumber not installed. Run: pip install pdfplumber")
    sys.exit(1)


@dataclass
class Transaction:
    date: datetime
    description: str
    amount: float          # negative = debit/out, positive = credit/in
    balance: Optional[float] = None

    @property
    def fitid(self) -> str:
        raw = f"{self.date.isoformat()}|{self.description}|{self.amount:.2f}"
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    @property
    def trntype(self) -> str:
        return "CREDIT" if self.amount >= 0 else "DEBIT"


class BankParser:
    BANK_ID = "BANK"
    CURRENCY = "AED"
    DATE_FORMATS = [
        "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y",
        "%d/%m/%y", "%d-%m-%y", "%Y-%m-%d",
    ]

    DATE_HEADERS    = {"date", "transaction date", "trans date", "value date", "posting date", "txn date"}
    DESC_HEADERS    = {"description", "narrative", "details", "transaction details", "particulars",
                       "remarks", "payment details", "merchant", "payee"}
    DEBIT_HEADERS   = {"debit", "dr", "withdrawal", "withdrawals", "amount debited",
                       "debit amount", "paid out", "money out", "amount (dr)"}
    CREDIT_HEADERS  = {"credit", "cr", "deposit", "deposits", "amount credited",
                       "credit amount", "paid in", "money in", "amount (cr)"}
    AMOUNT_HEADERS  = {"amount", "transaction amount", "txn amount"}
    BALANCE_HEADERS = {"balance", "running balance", "closing balance", "available balance",
                       "balance (aed)"}

    SKIP_DESC_PATTERNS = re.compile(
        r"(opening|closing|brought forward|carried forward|b/f|c/f|balance b/d|balance c/d)",
        re.IGNORECASE,
    )

    password: Optional[str] = None

    def _open(self, pdf_path: Path):
        try:
            return pdfplumber.open(pdf_path, password=self.password or "")
        except Exception as e:
            # pdfplumber wraps PDFPasswordIncorrect as PdfminerException(original_exc),
            # so str(e) is empty — check the wrapped cause's type name instead.
            cause = e.args[0] if e.args else e
            if "password" in type(cause).__name__.lower():
                hint = "" if self.password else " Use --password to provide it."
                raise SystemExit(f"Error: PDF is password-protected.{hint}") from None
            raise

    def parse(self, pdf_path: Path) -> list[Transaction]:
        transactions: list[Transaction] = []
        with self._open(pdf_path) as pdf:
            for page in pdf.pages:
                transactions.extend(self._parse_page(page))
        return self._deduplicate(transactions)

    def _parse_page(self, page) -> list[Transaction]:
        transactions: list[Transaction] = []
        tables = page.extract_tables()
        for table in (tables or []):
            if table and len(table) >= 2:
                transactions.extend(self._parse_table(table))
        if not transactions:
            text = page.extract_text() or ""
            transactions.extend(self._parse_text(text))
        return transactions

    def _parse_table(self, table: list[list]) -> list[Transaction]:
        col_map: dict = {}
        header_idx: Optional[int] = None

        for i, row in enumerate(table):
            if row is None:
                continue
            normalized = [_norm(cell) for cell in row]
            mapping = self._identify_columns(normalized)
            if mapping:
                header_idx = i
                col_map = mapping
                break

        if header_idx is None or not col_map:
            return []

        transactions: list[Transaction] = []
        pending_txn: Optional[Transaction] = None

        for row in table[header_idx + 1:]:
            if row is None:
                continue
            txn = self._parse_row(row, col_map)
            if txn:
                if pending_txn:
                    transactions.append(pending_txn)
                pending_txn = txn
            else:
                desc_cell = row[col_map["desc"]] if "desc" in col_map and col_map["desc"] < len(row) else None
                extra = _norm(desc_cell)
                if extra and pending_txn:
                    pending_txn.description = f"{pending_txn.description} {extra}".strip()

        if pending_txn:
            transactions.append(pending_txn)

        return transactions

    def _identify_columns(self, headers: list[str]) -> dict:
        col_map: dict = {}
        for i, h in enumerate(headers):
            if not h:
                continue
            if h in self.DATE_HEADERS and "date" not in col_map:
                col_map["date"] = i
            elif h in self.DESC_HEADERS and "desc" not in col_map:
                col_map["desc"] = i
            elif h in self.DEBIT_HEADERS and "debit" not in col_map:
                col_map["debit"] = i
            elif h in self.CREDIT_HEADERS and "credit" not in col_map:
                col_map["credit"] = i
            elif h in self.AMOUNT_HEADERS and "amount" not in col_map:
                col_map["amount"] = i
            elif h in self.BALANCE_HEADERS and "balance" not in col_map:
                col_map["balance"] = i

        has_date = "date" in col_map
        has_desc = "desc" in col_map
        has_money = "debit" in col_map or "credit" in col_map or "amount" in col_map
        if has_date and has_desc and has_money:
            return col_map
        return {}

    def _parse_row(self, row: list, col_map: dict) -> Optional[Transaction]:
        try:
            date_str = _cell(row, col_map.get("date"))
            if not date_str:
                return None
            date = self._parse_date(date_str)
            if not date:
                return None

            desc = _cell(row, col_map.get("desc")) or ""
            if not desc:
                return None
            if self.SKIP_DESC_PATTERNS.search(desc):
                return None

            debit   = _amount(_cell(row, col_map.get("debit")))
            credit  = _amount(_cell(row, col_map.get("credit")))
            signed  = _amount(_cell(row, col_map.get("amount")))
            balance = _amount(_cell(row, col_map.get("balance")))

            if signed is not None:
                amount = signed
            elif debit is not None and debit != 0:
                amount = -abs(debit)
            elif credit is not None and credit != 0:
                amount = abs(credit)
            else:
                return None

            return Transaction(date=date, description=desc, amount=amount, balance=balance)

        except (IndexError, ValueError, TypeError):
            return None

    def _parse_text(self, text: str) -> list[Transaction]:
        return []

    def _parse_date(self, s: str) -> Optional[datetime]:
        s = s.strip()
        for fmt in self.DATE_FORMATS:
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    def _deduplicate(self, txns: list[Transaction]) -> list[Transaction]:
        seen: set[str] = set()
        result: list[Transaction] = []
        for t in txns:
            if t.fitid not in seen:
                seen.add(t.fitid)
                result.append(t)
        return result

    def _statement_header(self, pdf_path: Path) -> list[str]:
        try:
            with self._open(pdf_path) as pdf:
                text = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
        except Exception:
            return []

        text_lower = text.lower()
        CREDIT_KEYWORDS = {"credit card", "credit limit", "payment due", "minimum payment"}
        stmt_type = "Credit Card Statement" if any(k in text_lower for k in CREDIT_KEYWORDS) \
                    else "Bank Account Statement"
        lines = [f"  Statement type : {stmt_type}"]

        for pattern in [
            r'card\s+number\s*[:\-]?\s*([0-9X*x\s]{10,30})',
            r'account\s+(?:number|no\.?)\s*[:\-]?\s*([0-9X*x\s]{8,30})',
        ]:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                label = "Card number   " if "card" in pattern else "Account number"
                lines.append(f"  {label} : {m.group(1).strip()}")
                break

        for pattern in [
            r'card\s+type\s*[:\-]?\s*(.+?)(?:\n|$)',
            r'account\s+type\s*[:\-]?\s*(.+?)(?:\n|$)',
            r'product\s*[:\-]?\s*(.+?)(?:\n|$)',
        ]:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                val = m.group(1).strip()
                if val and len(val) < 60:
                    lines.append(f"  Card/acct type : {val}")
                    break

        for pattern in [
            r'statement\s+period\s*[:\-]?\s*(.+?)(?:\n|$)',
            r'period\s*[:\-]?\s*(\d.+?)(?:\n|$)',
            r'from\s+(\d{2}[\/\-]\d{2}[\/\-]\d{2,4})\s+to\s+(\d{2}[\/\-]\d{2}[\/\-]\d{2,4})',
        ]:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                period = f"{m.group(1).strip()} to {m.group(2).strip()}" \
                         if m.lastindex and m.lastindex >= 2 else m.group(1).strip()
                if period and len(period) < 60:
                    lines.append(f"  Period         : {period}")
                    break

        return lines

    def inspect(self, pdf_path: Path) -> None:
        print(f"\n=== Inspecting {pdf_path.name} ===\n")
        for line in self._statement_header(pdf_path):
            print(line)
        print()
        with self._open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                print(f"\n{'='*60}")
                print(f"PAGE {page_num}")
                print('='*60)

                tables = page.extract_tables() or []
                if tables:
                    for t_idx, table in enumerate(tables):
                        print(f"\n  [Table {t_idx + 1}]  {len(table)} rows")
                        for r_idx, row in enumerate(table[:8]):
                            print(f"    {r_idx:>2}: {row}")
                        if len(table) > 8:
                            print(f"    ... ({len(table) - 8} more rows)")
                else:
                    print("\n  No tables detected — showing raw text:")
                    text = page.extract_text() or ""
                    for line in text.splitlines()[:30]:
                        print(f"    {line}")
                    if len(text.splitlines()) > 30:
                        print(f"    ... ({len(text.splitlines()) - 30} more lines)")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _norm(val) -> str:
    return str(val or "").lower().strip()

def _cell(row: list, idx: Optional[int]) -> Optional[str]:
    if idx is None or idx >= len(row):
        return None
    v = row[idx]
    return str(v).strip() if v is not None else None

def _amount(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    s = s.strip()
    if not s or s in ("-", "—", "–", "N/A", "n/a", ""):
        return None
    negative = s.startswith("-")
    s = re.sub(r"[^\d.,]", "", s).replace(",", "")
    if not s:
        return None
    try:
        val = float(s)
        return -val if negative else val
    except ValueError:
        return None

def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
