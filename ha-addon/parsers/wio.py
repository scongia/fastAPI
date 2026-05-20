from .base import BankParser


class WioParser(BankParser):
    BANK_ID = "WIO"
    DATE_FORMATS = ["%d %b %Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y"]

    DATE_HEADERS = {"date", "transaction date", "value date"}
    DESC_HEADERS = {
        "description", "details", "transaction details",
        "remarks", "merchant", "payee", "beneficiary",
    }
    DEBIT_HEADERS = {
        "debit", "amount (dr)", "paid out", "withdrawal",
        "money out", "out",
    }
    CREDIT_HEADERS = {
        "credit", "amount (cr)", "paid in", "deposit",
        "money in", "in",
    }
    AMOUNT_HEADERS = {"amount", "transaction amount"}
    BALANCE_HEADERS = {"balance", "available balance", "closing balance"}
