# 🏦 Tally Bank Statement Automator

Convert your **Bank Statement PDFs** into **Tally-importable XML files** with a single command.

## Features

- 📄 **PDF Parsing** — Extracts transactions from bank statement PDFs
- 🏦 **Tally XML** — Generates TallyPrime-compatible voucher XML
- 🔍 **Smart Detection** — Auto-detects columns (Date, Narration, Debit, Credit, Balance)
- 🏷️ **Ledger Guessing** — Intelligently maps narrations to contra ledgers (UPI, NEFT, ATM, etc.)
- ✏️ **Editable Mappings** — Review and edit ledger assignments before export
- 📊 **CSV Report** — Generates a review CSV alongside the XML

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the Tool
```bash
# Interactive mode
python main.py

# Or provide the PDF path directly
python main.py /path/to/bank_statement.pdf
```

### 3. Import in TallyPrime
1. Open your Company in TallyPrime
2. Press **Alt+O** → **Import** → **Transactions**
3. Enter the path to the generated XML file
4. Press Enter to import

## Supported Bank Formats

The tool auto-detects columns from most Indian bank statement PDFs:
- SBI, HDFC, ICICI, Axis, Kotak, PNB, Bank of Baroda, etc.
- Any PDF with standard table columns (Date, Narration, Debit, Credit, Balance)

## Output Files

After processing, files are saved in a `tally_output/` folder:
- `{filename}_tally.xml` — Import this into TallyPrime
- `{filename}_review.csv` — Review transactions before importing

## Important Notes

⚠️ **Ledger names** in the XML must exactly match the ledger names in your Tally company.  
⚠️ The PDF must have **selectable text** (not a scanned image).  
⚠️ Always **backup your Tally data** before importing.

## Project Structure

```
automate_tally/
├── main.py          # CLI entry point
├── pdf_parser.py    # PDF table extraction
├── transaction.py   # Transaction data model
├── tally_xml.py     # Tally XML generation
├── requirements.txt # Python dependencies
└── README.md        # This file
```
