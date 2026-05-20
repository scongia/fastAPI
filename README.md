# Banktivity

Convert UAE bank PDF statements to OFX format for import into [Banktivity](https://www.banktivity.com/).

## Supported Banks

| Bank | Key | Account Types |
|------|-----|---------------|
| Emirates NBD | `enbd-current` | Current account |
| Emirates NBD | `enbd-credit` | Credit card (Skywards, etc.) |
| Wio | `wio` | Current account |
| HSBC UAE | `hsbc` | Current account |

## Requirements

```bash
pip install -r requirements.txt
```

For password-protected PDFs, also install:

```bash
pip install pikepdf
```

## Usage

### Convert a statement

```bash
# Auto-detect parser
python pdf_to_ofx.py statement.pdf

# Specify parser explicitly
python pdf_to_ofx.py statement.pdf --parser enbd-current
python pdf_to_ofx.py statement.pdf --parser enbd-credit
python pdf_to_ofx.py statement.pdf --parser wio
python pdf_to_ofx.py statement.pdf --parser hsbc

# Custom output path
python pdf_to_ofx.py statement.pdf -o ~/Desktop/transactions.ofx

# Set account ID (written into the OFX file)
python pdf_to_ofx.py statement.pdf --account 012345678

# Password-protected PDF
python pdf_to_ofx.py statement.pdf --password your_password
```

Output is saved to `out/<bank>/<bank>-<first_date>-<last_date>.ofx` by default.

### Remove PDF password

```bash
python pdf_unlock.py statement.pdf --password your_password
# Saves as statement_unlocked.pdf

python pdf_unlock.py statement.pdf --password secret -o unlocked.pdf
```

### Debug / inspect mode

Shows raw extracted tables and text without producing an OFX file — useful when a statement isn't parsing correctly:

```bash
python pdf_to_ofx.py statement.pdf --inspect
```

## Project Structure

```
parsers/
├── __init__.py       # Parser registry and bank/account-type auto-detection
├── base.py           # Core BankParser class and Transaction dataclass
├── enbd_current.py   # Emirates NBD current account
├── enbd_credit.py    # Emirates NBD credit card (bounding-box extraction)
├── hsbc.py           # HSBC UAE
└── wio.py            # Wio Bank
pdf_to_ofx.py         # Main CLI entry point
pdf_unlock.py         # PDF password removal utility
in/                   # Sample input PDFs (by bank)
out/                  # Generated OFX files
```

## Adding a New Bank

1. Create `parsers/<bank>.py` subclassing `BankParser` from `parsers/base.py`.
2. Override header lists (`DATE_HEADERS`, `DESC_HEADERS`, `DEBIT_HEADERS`, `CREDIT_HEADERS`, etc.) to match the bank's column names.
3. Register the parser in `parsers/__init__.py` under a new key in `PARSERS`.
4. Add a bank identifier string to `detect_bank()` in `parsers/__init__.py`.

For statements where table extraction fails (e.g. bilingual layouts), see `parsers/enbd_credit.py` for an example of word bounding-box extraction.


python batch_convert.py ./in/enbd-credit --output ./ofx/enbd-credit --parser enbd-credit
python pdf_to_ofx.py "in/enbd-credit/E-STATEMENT_4677450380862111_25012025_2111_Skywards_20250126-093926645_E3301725_26012025.pdf" --parser enbd-credit --inspect

python pdf_to_ofx.py "in/enbd-current/E-Statement_20241102-20260505_095347965_6901_unlocked.pdf" --parser enbd-current --inspect

python pdf_to_ofx.py "in/enbd-credit/E-STATEMENT_4677450380862111_25012025_2111_Skywards_20250126-093926645_E3301725_26012025.pdf" --parser enbd-credit --inspect

python3 pdf_to_ofx.py in/hsbc/2024-12-21_Statement.pdf --parser hsbc  

./convert.sh -i ./in/hsbc -o ./ofx/hsbc -p hsbc


rebuild only the api container after changes
docker compose up -d --build api 