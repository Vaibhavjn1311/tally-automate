"""
Non-interactive runner for the Tally Bank Statement Automator (BOB Format).
"""
import os
import re
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from bob_parser import parse_bank_statement_bob
from tally_xml import generate_tally_xml, generate_csv_report
from ledger_extractor import extract_ledger_names

console = Console()

# ── Parameters ────────────────────────────────────────────────────────────────
PDF_PATH      = "OpTransactionHistoryUX502-06-2026.pdf"
PDF_PASSWORD  = None 
MASTER_PDF    = "Master.pdf"
# ─────────────────────────────────────────────────────────────────────────────

console.print(Panel("🏦  TALLY BANK STATEMENT AUTOMATOR (BOB)  🏦", style="bold blue"))

# 1. Extract master ledgers
console.print(f"\n[bold cyan]Step 0:[/] Extracting ledgers from [bold]{MASTER_PDF}[/]...")
master_ledgers = extract_ledger_names(MASTER_PDF)
console.print(f"  ✅ Extracted [bold green]{len(master_ledgers)}[/] ledgers.")

# 2. Parse bank statement
console.print(Panel("Step 1: Parsing Bank Statement PDF", style="bold cyan"))
transactions = parse_bank_statement_bob(PDF_PATH, password=PDF_PASSWORD, master_ledgers=master_ledgers)

if not transactions:
    console.print("[bold red]❌ No transactions could be extracted from the PDF.[/]")
    raise SystemExit(1)

# 3. Auto-detect bank ledger (Same as main.py)
import pdfplumber
detected_bank_ledger = "Bank Account"
try:
    with pdfplumber.open(PDF_PATH, password=PDF_PASSWORD) as pdf:
        text = pdf.pages[0].extract_text() or ""
        potential_acc_nos = re.findall(r'\b\d{10,16}\b', text)
        acc_match = re.search(r'Account\s*No\s*[:.\\-]?\s*(\d+)', text, re.IGNORECASE)
        if acc_match:
            potential_acc_nos.insert(0, acc_match.group(1))
        for acc_no in potential_acc_nos:
            for led in master_ledgers:
                if acc_no in led:
                    detected_bank_ledger = led
                    break
            if detected_bank_ledger != "Bank Account":
                break
except Exception:
    pass

if detected_bank_ledger != "Bank Account":
    console.print(f"\n  🏦 Auto-detected Bank Ledger: [bold cyan]{detected_bank_ledger}[/]")
else:
    detected_bank_ledger = "Bank Account"
    console.print(f"\n  🏦 Using default Bank Ledger: [bold cyan]{detected_bank_ledger}[/]")

# 4. Display summary
console.print(Panel("Step 2: Transactions Summary", style="bold cyan"))
table = Table(box=box.ROUNDED, show_lines=True, title_style="bold cyan", title="📊 Extracted Transactions")
table.add_column("#",   style="dim", width=4)
table.add_column("Date",   style="cyan", width=12)
table.add_column("Narration", style="white", max_width=40)
table.add_column("Type",  width=8)
table.add_column("Debit",  style="red",   justify="right", width=12)
table.add_column("Credit", style="green", justify="right", width=12)
table.add_column("Contra Ledger", style="yellow", max_width=25)

for idx, txn in enumerate(transactions, 1):
    type_style = {"Payment": "[red]Payment[/]", "Receipt": "[green]Receipt[/]", "Contra": "[blue]Contra[/]"}
    table.add_row(
        str(idx),
        txn.display_date,
        txn.narration[:40],
        type_style.get(txn.voucher_type, txn.voucher_type),
        f"₹{txn.debit:,.2f}"  if txn.debit  > 0 else "",
        f"₹{txn.credit:,.2f}" if txn.credit > 0 else "",
        txn.contra_ledger,
    )
console.print(table)

# 5. Generate output files
console.print(Panel("Step 3: Generate Tally Import Files", style="bold cyan"))

base_name  = Path(PDF_PATH).stem
output_dir = Path(PDF_PATH).parent / "tally_output"
output_dir.mkdir(exist_ok=True)

xml_path = str(output_dir / f"{base_name}_tally.xml")
csv_path = str(output_dir / f"{base_name}_review.csv")

generate_tally_xml(transactions, detected_bank_ledger, xml_path)
generate_csv_report(transactions, detected_bank_ledger, csv_path)

console.print(f"\n  ✅ XML: [bold green]{xml_path}[/]")
console.print(f"  ✅ CSV: [bold green]{csv_path}[/]")
console.print("\n[bold green]✨ Done! Your Tally import file is ready.[/]\n")
