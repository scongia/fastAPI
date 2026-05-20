#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $(basename "$0") -p <parser> [-i <input_dir>] [-o <output_dir>]"
    echo ""
    echo "  -p, --parser   Parser to use: hsbc, enbd-credit, enbd-current, wio"
    echo "  -i, --input    Input directory (default: in/<parser>)"
    echo "  -o, --output   Output directory (default: ofx/<parser>)"
    echo ""
    echo "Examples:"
    echo "  $(basename "$0") -p hsbc"
    echo "  $(basename "$0") -p hsbc -i ~/Downloads/hsbc"
    echo "  $(basename "$0") -p enbd-credit -i ~/Downloads -o ofx/enbd"
    exit 1
}

PARSER=""
IN_DIR=""
OUT_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -p|--parser) PARSER="$2"; shift 2 ;;
        -i|--input)  IN_DIR="$2"; shift 2 ;;
        -o|--output) OUT_DIR="$2"; shift 2 ;;
        *) usage ;;
    esac
done

[[ -z "$PARSER" ]] && usage

[[ -z "$IN_DIR" ]]  && IN_DIR="in/$PARSER"
[[ -z "$OUT_DIR" ]] && OUT_DIR="ofx/$PARSER"

if [[ ! -d "$IN_DIR" ]]; then
    echo "Error: input directory '$IN_DIR' not found"
    exit 1
fi

mkdir -p "$OUT_DIR"

find "$IN_DIR" -name "*.pdf" | sort | while read -r f; do
    echo "Converting: $f"
    python pdf_to_ofx.py "$f" --parser "$PARSER" --output "$OUT_DIR/$(basename "${f%.pdf}").ofx"
done

echo "Done. Output in $OUT_DIR"
