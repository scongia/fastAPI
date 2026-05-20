#!/usr/bin/env python3
"""
Batch convert all PDF bank statements in a folder (recursively) to OFX.
"""

import argparse
import sys
from pathlib import Path

from parsers import PARSERS, detect_bank
from pdf_to_ofx import convert


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Batch-convert all PDF statements in a folder to OFX",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  %(prog)s ./statements/ --output ./ofx/
  %(prog)s ~/Downloads/bank_pdfs/ -o ~/Desktop/ofx/ --password secret
        """,
    )
    ap.add_argument("folder", type=Path, help="Folder to search recursively for PDFs")
    ap.add_argument("--output", "-o", type=Path, required=True, help="Output folder for OFX files")
    ap.add_argument("--parser", choices=list(PARSERS.keys()), help="Force a specific parser for all files (auto-detected if omitted)")
    ap.add_argument("--password", default="", help="Password for encrypted PDFs")
    args = ap.parse_args()

    if not args.folder.is_dir():
        print(f"Error: not a directory: {args.folder}")
        sys.exit(1)

    args.output.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(args.folder.rglob("*.pdf"))
    if not pdfs:
        print(f"No PDF files found in {args.folder}")
        sys.exit(0)

    print(f"Found {len(pdfs)} PDF(s) in {args.folder}\n")

    ok = skipped = 0
    for pdf in pdfs:
        resolved_key = args.parser or detect_bank(pdf, password=args.password)
        if not resolved_key:
            print(f"  [SKIP] {pdf.name} — could not detect parser")
            skipped += 1
            continue
        try:
            convert(pdf, parser_key=resolved_key, output_dir=args.output, password=args.password)
            ok += 1
        except SystemExit:
            skipped += 1
        except Exception as e:
            print(f"  [ERROR] {pdf.name}: {e}")
            skipped += 1

    print(f"\nDone: {ok} converted, {skipped} skipped/failed")


if __name__ == "__main__":
    main()
