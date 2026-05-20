#!/usr/bin/env python3
"""
UAE Bank PDF Statement → OFX Converter for Banktivity
Supports: Emirates NBD (credit), Wio, HSBC UAE
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Suppress noisy pdfminer font warnings
logging.getLogger("pdfminer").setLevel(logging.ERROR)

from parsers import PARSERS, detect_bank, detect_account_type
from parsers.base import Transaction, _escape


# ---------------------------------------------------------------------------
# OFX writer
# ---------------------------------------------------------------------------

def write_ofx(
    transactions: list[Transaction],
    bank_id: str,
    currency: str,
    output_path: Path,
    account_id: str = "",
    account_type: str = "bank",   # "credit" or "bank"
) -> None:
    if not transactions:
        print("Warning: No transactions to write.")
        return

    transactions = sorted(transactions, key=lambda t: t.date)
    dt_start = transactions[0].date.strftime("%Y%m%d")
    dt_end   = transactions[-1].date.strftime("%Y%m%d")
    dt_now   = datetime.now().strftime("%Y%m%d%H%M%S")
    acct     = account_id or f"{bank_id}_ACCOUNT"
    is_cc    = (account_type == "credit")

    lines = [
        "OFXHEADER:100",
        "DATA:OFXSGML",
        "VERSION:102",
        "SECURITY:NONE",
        "ENCODING:UTF-8",
        "CHARSET:1252",
        "COMPRESSION:NONE",
        "OLDFILEUID:NONE",
        "NEWFILEUID:NONE",
        "",
        "<OFX>",
        "<SIGNONMSGSRSV1>",
        "<SONRS>",
        "<STATUS>",
        "<CODE>0",
        "<SEVERITY>INFO",
        "</STATUS>",
        f"<DTSERVER>{dt_now}",
        "<LANGUAGE>ENG",
        "</SONRS>",
        "</SIGNONMSGSRSV1>",
    ]

    if is_cc:
        lines += [
            "<CREDITCARDMSGSRSV1>",
            "<CCSTMTTRNRS>",
            "<TRNUID>1001",
            "<STATUS>",
            "<CODE>0",
            "<SEVERITY>INFO",
            "</STATUS>",
            "<CCSTMTRS>",
            f"<CURDEF>{currency}",
            "<CCACCTFROM>",
            f"<ACCTID>{acct}",
            "</CCACCTFROM>",
        ]
    else:
        lines += [
            "<BANKMSGSRSV1>",
            "<STMTTRNRS>",
            "<TRNUID>1001",
            "<STATUS>",
            "<CODE>0",
            "<SEVERITY>INFO",
            "</STATUS>",
            "<STMTRS>",
            f"<CURDEF>{currency}",
            "<BANKACCTFROM>",
            f"<BANKID>{bank_id}",
            f"<ACCTID>{acct}",
            "<ACCTTYPE>CHECKING",
            "</BANKACCTFROM>",
        ]

    lines += [
        "<BANKTRANLIST>",
        f"<DTSTART>{dt_start}",
        f"<DTEND>{dt_end}",
    ]

    for txn in transactions:
        lines += [
            "<STMTTRN>",
            f"<TRNTYPE>{txn.trntype}",
            f"<DTPOSTED>{txn.date.strftime('%Y%m%d')}",
            f"<TRNAMT>{txn.amount:.2f}",
            f"<FITID>{txn.fitid}",
            f"<NAME>{_escape(txn.description[:32])}",
            f"<MEMO>{_escape(txn.description)}",
            "</STMTTRN>",
        ]

    lines.append("</BANKTRANLIST>")

    last_bal = next(
        (t for t in reversed(transactions) if t.balance is not None), None
    )
    if last_bal:
        lines += [
            "<LEDGERBAL>",
            f"<BALAMT>{last_bal.balance:.2f}",
            f"<DTASOF>{last_bal.date.strftime('%Y%m%d')}",
            "</LEDGERBAL>",
        ]

    if is_cc:
        lines += ["</CCSTMTRS>", "</CCSTMTTRNRS>", "</CREDITCARDMSGSRSV1>", "</OFX>"]
    else:
        lines += ["</STMTRS>", "</STMTTRNRS>", "</BANKMSGSRSV1>", "</OFX>"]

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(transactions)} transactions → {output_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def convert(
    pdf: Path,
    parser_key: str = "",
    output: Path = None,
    output_dir: Path = None,
    account: str = "",
    password: str = "",
) -> None:
    """Convert a single PDF statement to OFX. Raises SystemExit on failure.

    output:     explicit OFX file path.
    output_dir: directory to write into, using the default <bank>-<dates>.ofx filename.
    If neither is given, defaults to out/<bank>/<bank>-<dates>.ofx.
    """
    if not pdf.exists():
        print(f"Error: file not found: {pdf}")
        sys.exit(1)

    if not parser_key:
        parser_key = detect_bank(pdf, password=password)
        if parser_key:
            print(f"Auto-detected parser: {parser_key.upper()}")
        else:
            print(
                "Could not auto-detect parser from PDF.\n"
                f"Please specify with --parser {' | '.join(PARSERS.keys())}"
            )
            sys.exit(1)

    bank_parser = PARSERS[parser_key]()
    bank_parser.password = password or None

    print(f"Parsing {pdf.name} ({parser_key.upper()})…")
    transactions = bank_parser.parse(pdf)

    if not transactions:
        print(
            "No transactions found.\n"
            "Run with --inspect to see what pdfplumber extracts from your PDF,\n"
            "then open a GitHub issue or share the output so the parser can be tuned."
        )
        sys.exit(1)

    first_date   = min(t.date for t in transactions).strftime("%Y%m%d")
    last_date    = max(t.date for t in transactions).strftime("%Y%m%d")
    account_type = detect_account_type(bank_parser, pdf)

    # Use account number from parser metadata if not explicitly provided
    if not account:
        acct_info = getattr(bank_parser, '_account_info', [])
        if acct_info:
            account = acct_info[0][1] or acct_info[0][2] or ""

    print(f"Found {len(transactions)} transactions ({first_date} → {last_date})")
    print(f"Account type: {'credit card' if account_type == 'credit' else 'bank account'}")

    if output:
        output_path = output
    elif output_dir:
        bank_name   = bank_parser.BANK_ID.lower()
        output_path = output_dir / f"{bank_name}-{first_date}-{last_date}.ofx"
    else:
        bank_name = bank_parser.BANK_ID.lower()
        out_dir   = Path("out") / bank_name
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"{bank_name}-{first_date}-{last_date}.ofx"

    write_ofx(transactions, bank_parser.BANK_ID, bank_parser.CURRENCY, output_path, account, account_type)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert UAE bank PDF statements to OFX for Banktivity",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  %(prog)s statement.pdf                          # auto-detect parser
  %(prog)s statement.pdf --parser enbd-current
  %(prog)s statement.pdf --parser wio -o wio_jan.ofx
  %(prog)s statement.pdf --parser hsbc --account 012345678
  %(prog)s statement.pdf --inspect                # debug: show extracted data
        """,
    )
    ap.add_argument("pdf", type=Path, help="PDF statement file")
    ap.add_argument(
        "--parser", choices=list(PARSERS.keys()),
        help="Parser to use (auto-detected if omitted)",
    )
    ap.add_argument(
        "--output", "-o", type=Path,
        help="Output OFX file (default: out/<bank>/<bank>-<first_date>-<last_date>.ofx)",
    )
    ap.add_argument(
        "--account", default="",
        help="Account ID/number to embed in the OFX file",
    )
    ap.add_argument(
        "--password", default="",
        help="Password for encrypted PDF files",
    )
    ap.add_argument(
        "--inspect", action="store_true",
        help="Print raw extracted table/text data for debugging — does not produce OFX",
    )

    args = ap.parse_args()

    if args.inspect:
        if not args.pdf.exists():
            print(f"Error: file not found: {args.pdf}")
            sys.exit(1)
        parser_key = args.parser or detect_bank(args.pdf, password=args.password)
        if not parser_key:
            print(f"Could not auto-detect parser from PDF.\nPlease specify with --parser {' | '.join(PARSERS.keys())}")
            sys.exit(1)
        bank_parser = PARSERS[parser_key]()
        bank_parser.password = args.password or None
        transactions = bank_parser.parse(args.pdf)
        if not transactions:
            print("No transactions found.")
            return
        account_type = detect_account_type(bank_parser, args.pdf)
        import pandas as pd
        df = pd.DataFrame([{
            "date":        t.date.strftime("%d/%m/%Y"),
            "description": t.description,
            "amount":      t.amount,
            "balance":     t.balance,
        } for t in transactions])
        pd.set_option("display.max_rows", None)
        pd.set_option("display.max_colwidth", 60)
        pd.set_option("display.width", 0)
        print(f"\n{args.pdf.name}  [{parser_key.upper()}]  —  {len(transactions)} transactions")
        print(f"Account type: {'Credit Card' if account_type == 'credit' else 'Bank Account'}")
        # Show per-account metadata if the parser captured it
        for acct_type, acct_num, iban in getattr(bank_parser, '_account_info', []):
            if acct_num:
                print(f"  {acct_num}")
        print()
        print(df.to_string(index=False))
        return

    convert(args.pdf, parser_key=args.parser or "", output=args.output, account=args.account, password=args.password)


if __name__ == "__main__":
    main()
