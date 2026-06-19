"""
Tally Bank Statement Automator - Main CLI
Converts bank statement PDFs into Tally-importable XML files.
"""

import sys
import os
import re
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich import box

from pdf_parser import parse_bank_statement
from tally_xml import generate_tally_xml, generate_csv_report
from transaction import Transaction
from ledger_extractor import extract_ledger_names

console = Console()

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║          🏦  TALLY BANK STATEMENT AUTOMATOR  🏦            ║
║                                                              ║
║   Convert Bank Statement PDFs → Tally Import XML             ║
║                                                              ║
║   Version 1.2.2 (Advanced Reconciliation)                    ║
╚══════════════════════════════════════════════════════════════╝
"""

def display_transactions(transactions: list[Transaction]):
    """Display transactions in a rich table."""
    table = Table(
        title="📊 Extracted Transactions",
        box=box.ROUNDED,
        show_lines=True,
        title_style="bold cyan",
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("Date", style="cyan", width=12)
    table.add_column("Narration", style="white", max_width=40)
    table.add_column("Type", width=8)
    table.add_column("Debit", style="red", justify="right", width=12)
    table.add_column("Credit", style="green", justify="right", width=12)
    table.add_column("Contra Ledger", style="yellow", max_width=25)

    for idx, txn in enumerate(transactions, 1):
        type_style = {"Payment": "[red]Payment[/]", "Receipt": "[green]Receipt[/]", "Contra": "[blue]Contra[/]"}
        table.add_row(
            str(idx),
            txn.display_date,
            txn.narration[:40],
            type_style.get(txn.voucher_type, txn.voucher_type),
            f"₹{txn.debit:,.2f}" if txn.debit > 0 else "",
            f"₹{txn.credit:,.2f}" if txn.credit > 0 else "",
            txn.contra_ledger,
        )

    console.print(table)

    # Summary
    total_debit = sum(t.debit for t in transactions)
    total_credit = sum(t.credit for t in transactions)
    console.print(f"\n  💰 Total Debits:  [red]₹{total_debit:,.2f}[/]")
    console.print(f"  💰 Total Credits: [green]₹{total_credit:,.2f}[/]")
    console.print(f"  📊 Net:           [bold]₹{total_credit - total_debit:,.2f}[/]")


def edit_ledger_mappings(transactions: list[Transaction]):
    """Allow user to edit contra ledger assignments."""
    unique_ledgers = sorted(set(t.contra_ledger for t in transactions))
    console.print(f"\n[bold]📝 Current Ledger Mappings:[/]")
    for i, ledger in enumerate(unique_ledgers, 1):
        count = sum(1 for t in transactions if t.contra_ledger == ledger)
        console.print(f"  {i}. [yellow]{ledger}[/] ({count} transactions)")

    console.print("\n[dim]Enter the number to edit, or press Enter to skip:[/]")
    while True:
        choice = Prompt.ask("Edit ledger #", default="")
        if not choice:
            break
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(unique_ledgers):
                old_name = unique_ledgers[idx]
                new_name = Prompt.ask(f"  New name for '[yellow]{old_name}[/]'")
                if new_name.strip():
                    for txn in transactions:
                        if txn.contra_ledger == old_name:
                            txn.contra_ledger = new_name.strip()
                    console.print(f"  [green]✅ Updated '{old_name}' → '{new_name}'[/]")
                    unique_ledgers[idx] = new_name.strip()
        except ValueError:
            break


def main():
    """Main CLI entry point."""
    console.print(BANNER, style="bold blue")

    # ─── Get PDF path ────────────────────────────────────────────────────
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
    else:
        pdf_path = Prompt.ask("📂 Enter path to bank statement PDF")

    pdf_path = os.path.expanduser(pdf_path.strip().strip('"').strip("'"))

    if not os.path.exists(pdf_path):
        console.print(f"[bold red]❌ File not found:[/] {pdf_path}")
        sys.exit(1)

    if not pdf_path.lower().endswith('.pdf'):
        console.print("[bold red]❌ File must be a PDF![/]")
        sys.exit(1)

    # ─── Get Master Ledger PDF (Optional) ───────────────────────────────
    master_pdf_path = None
    master_ledgers = None
    
    if Confirm.ask("📚 Do you have a Tally Master Ledger PDF?", default=True):
        master_pdf_path = Prompt.ask("📂 Enter path to Master Ledger PDF", default="Master.pdf")
        master_pdf_path = os.path.expanduser(master_pdf_path.strip().strip('"').strip("'"))
        
        if os.path.exists(master_pdf_path):
            console.print(f"  🔍 Extracting ledgers from [bold cyan]{master_pdf_path}[/]...")
            master_ledgers = extract_ledger_names(master_pdf_path)
            console.print(f"  ✅ Extracted [bold green]{len(master_ledgers)}[/] ledgers.")
        else:
            console.print(f"  [bold red]⚠️  Master PDF not found at {master_pdf_path}. Proceeding without it.[/]")

    # ─── Get PDF Password (if needed) ───────────────────────────────────
    default_password = Path(pdf_path).stem
    pdf_password = None

    if Confirm.ask(f"🔐 Is the PDF password-protected?", default=True):
        pdf_password = Prompt.ask(
            "🔑 Enter PDF password",
            default=default_password,
            password=True,
        )

    # ─── Step 1: Parse PDF ───────────────────────────────────────────────────────
    console.print(Panel("Step 1: Parsing Bank Statement PDF", style="bold cyan"))
    transactions = parse_bank_statement(pdf_path, password=pdf_password, master_ledgers=master_ledgers)

    if not transactions:
        console.print("[bold red]❌ No transactions could be extracted from the PDF.[/]")
        console.print("[yellow]Possible reasons:[/]")
        console.print("  1. The PDF might be a scanned image (needs OCR)")
        console.print("  2. The table format is not recognized")
        console.print("  3. The PDF password might be incorrect")
        sys.exit(1)

    # ─── Step 1.5: Auto-detect Bank Ledger ────────────────────────────
    from ledger_extractor import detect_bank_ledger
    detected_bank_ledger = detect_bank_ledger(pdf_path, master_ledgers, password=pdf_password)

    if detected_bank_ledger != "Bank Account":
        console.print(f"  🏦 [bold green]Success![/] Auto-detected Bank Ledger: [bold cyan]{detected_bank_ledger}[/]")
        bank_ledger = detected_bank_ledger
    else:
        bank_ledger = Prompt.ask(
            "🏦 Enter your Bank Ledger name (as in Tally)",
            default="Bank Account"
        )

    # ─── Step 2: Display Transactions ────────────────────────────────────────────
    console.print(Panel("Step 2: Review Extracted Transactions", style="bold cyan"))
    display_transactions(transactions)

    # ─── Edit Ledger Mappings ────────────────────────────────────────────
    if Confirm.ask("\n📝 Would you like to edit ledger mappings?", default=False):
        edit_ledger_mappings(transactions)
        console.print("\n[bold]Updated transactions:[/]")
        display_transactions(transactions)

    # ─── Step 3: Generate Output ─────────────────────────────────────────────────
    console.print(Panel("Step 3: Generate Tally Import Files", style="bold cyan"))

    base_name = Path(pdf_path).stem
    output_dir = Path(pdf_path).parent / "tally_output"
    output_dir.mkdir(exist_ok=True)

    xml_path = str(output_dir / f"{base_name}_tally.xml")
    csv_path = str(output_dir / f"{base_name}_review.csv")

    console.print(f"  🛠️  Using Bank Ledger: [bold green]{bank_ledger}[/]")
    generate_tally_xml(transactions, bank_ledger, xml_path)
    generate_csv_report(transactions, bank_ledger, csv_path)

    # ─── Import Instructions ─────────────────────────────────────────────
    console.print(Panel(
        "[bold]How to Import in TallyPrime:[/]\n\n"
        "1. Open your Company in TallyPrime\n"
        "2. Press [bold cyan]Alt+O[/] → Select [bold]Import[/] → [bold]Transactions[/]\n"
        f"3. Enter the file path: [bold green]{xml_path}[/]\n"
        "4. Press Enter to import\n\n"
        f"[yellow]⚠️  Make sure all ledger names in the XML match your Tally ledgers![/]\n"
        f"   (Importing to Bank Ledger: [bold cyan]{bank_ledger}[/])\n"
        f"[dim]📄 Review CSV: {csv_path}[/]",
        title="📋 Import Instructions",
        style="green",
        box=box.DOUBLE,
    ))

    console.print("\n[bold green]✨ Done! Your Tally import file is ready.[/]\n")


if __name__ == "__main__":
    main()
