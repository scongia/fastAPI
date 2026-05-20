#!/usr/bin/env python3
"""
Remove password protection from a PDF file.
"""

import argparse
import sys
from pathlib import Path

try:
    import pikepdf
except ImportError:
    print("Error: pikepdf not installed. Run: pip install pikepdf")
    sys.exit(1)


def unlock(input_path: Path, password: str, output_path: Path) -> None:
    try:
        with pikepdf.open(input_path, password=password) as pdf:
            pdf.save(output_path)
    except pikepdf.PasswordError:
        print("Error: incorrect password.")
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: file not found: {input_path}")
        sys.exit(1)

    print(f"Unlocked → {output_path}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Remove password protection from a PDF file.",
        epilog="example:\n  %(prog)s statement.pdf --password secret",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("pdf", type=Path, help="Password-protected PDF file")
    ap.add_argument("--password", "-p", required=True, help="PDF password")
    ap.add_argument(
        "--output", "-o", type=Path,
        help="Output path (default: <name>_unlocked.pdf)",
    )
    args = ap.parse_args()

    if not args.pdf.exists():
        print(f"Error: file not found: {args.pdf}")
        sys.exit(1)

    output = args.output or args.pdf.with_stem(args.pdf.stem + "_unlocked")
    unlock(args.pdf, args.password, output)


if __name__ == "__main__":
    main()
