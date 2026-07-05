"""
Axis Bank Statement Parser.
Handles Axis Bank PDF statements which use a structured table format with columns:
  Tran Date | Chq No | Particulars | Debit | Credit | Balance | Init.Br

Supports both table-based extraction (primary) and text-line-based fallback.
"""

import pdfplumber
import re
from typing import List, Optional, Dict
from transaction import Transaction, clean_amount, parse_date, clean_narration, reconcile_transaction, guess_contra_ledger
from rich.console import Console

console = Console()

# ─── Lines to skip (headers, footers, metadata) ──────────────────────────────
_SKIP_KEYWORDS = [
    'AXIS BANK', 'Joint Holder', 'Customer ID', 'IFSC Code', 'MICR Code',
    'Nominee Registered', 'Registered Mobile', 'Registered Email', 'Scheme:',
    'Currency:', 'Statement of Axis', 'OPENING BALANCE', 'CLOSING BALANCE',
    'TRANSACTION TOTAL', 'Unless the constituent', 'The closing balance',
    'We would like', 'With effect from', 'Deposit Insurance',
    'In compliance', 'To ensure you', 'REGISTERED OFFICE',
    'BRANCH ADDRESS', 'Legends :', 'ICONN-', 'VMT-', 'AUTOSWEEP',
    'REV SWEEP', 'SWEEP TRF', 'CWDR-', 'PUR-', 'TIP/', 'RATE.DIFF',
    'CLG-', 'EDC-', 'SETU ', 'Int.pd-', 'Int.Coll-',
    'This is a system generated', '++++ End of Statement',
    'Please co-operate', 'emails, if received',
]

# Date pattern for DD-MM-YYYY (Axis Bank format)
_DATE_RE = re.compile(r'^(\d{2}-\d{2}-\d{4})\s+')


def _is_skip_line(line: str) -> bool:
    """Check if a line should be skipped (header/footer/metadata)."""
    stripped = line.strip()
    if not stripped:
        return True
    for kw in _SKIP_KEYWORDS:
        if kw in stripped:
            return True
    return False


def _parse_axis_table(tables: list, master_ledgers: List[str] = None) -> List[Transaction]:
    """
    Parse Axis Bank statement from pdfplumber table extraction.
    The table has columns: Tran Date, Chq No, Particulars, Debit, Credit, Balance, Init.Br
    """
    transactions = []

    for table in tables:
        if not table:
            continue

        # Detect header row
        header_idx = -1
        col_map = {}

        for row_idx, row in enumerate(table):
            if row is None:
                continue
            row_text = ' '.join(str(c).lower() if c else '' for c in row)
            if 'tran date' in row_text or ('date' in row_text and 'particular' in row_text):
                header_idx = row_idx
                for ci, cell in enumerate(row):
                    cell_str = (str(cell) if cell else '').strip().lower().replace('\n', ' ')
                    if 'date' in cell_str and 'tran' in cell_str:
                        col_map['date'] = ci
                    elif 'date' in cell_str and 'tran' not in cell_str and 'date' not in col_map:
                        col_map['date'] = ci
                    elif 'chq' in cell_str or 'cheque' in cell_str:
                        col_map['chq'] = ci
                    elif 'particular' in cell_str or 'narration' in cell_str or 'description' in cell_str:
                        col_map['narration'] = ci
                    elif 'debit' in cell_str or 'withdrawal' in cell_str:
                        col_map['debit'] = ci
                    elif 'credit' in cell_str or 'deposit' in cell_str:
                        col_map['credit'] = ci
                    elif 'balance' in cell_str:
                        col_map['balance'] = ci
                break

        if header_idx < 0:
            # Try to auto-detect based on data patterns
            # Look for rows with DD-MM-YYYY dates
            for row_idx, row in enumerate(table):
                if row is None:
                    continue
                for ci, cell in enumerate(row):
                    cell_str = str(cell).strip() if cell else ''
                    if parse_date(cell_str):
                        col_map['date'] = ci
                        break
                if 'date' in col_map:
                    # Assume standard Axis layout: Date, ChqNo, Particulars, Debit, Credit, Balance, InitBr
                    num_cols = len(row)
                    if num_cols >= 7:
                        col_map = {'date': 0, 'chq': 1, 'narration': 2, 'debit': 3, 'credit': 4, 'balance': 5}
                    elif num_cols >= 6:
                        col_map = {'date': 0, 'narration': 1, 'debit': 2, 'credit': 3, 'balance': 4}
                    header_idx = -1  # No header row to skip
                    break

        if 'date' not in col_map:
            continue

        data_rows = table[header_idx + 1:] if header_idx >= 0 else table

        def get_cell(row, col_name):
            idx = col_map.get(col_name, -1)
            if idx < 0 or idx >= len(row):
                return ''
            return str(row[idx]).strip() if row[idx] else ''

        for row in data_rows:
            if row is None:
                continue

            date_str = get_cell(row, 'date')
            date = parse_date(date_str)
            if not date:
                # This might be a continuation row — append narration to previous txn
                nar_text = get_cell(row, 'narration')
                if nar_text and transactions:
                    # Skip OPENING/CLOSING BALANCE and TRANSACTION TOTAL
                    nar_upper = nar_text.upper().strip()
                    if any(kw in nar_upper for kw in ['OPENING BALANCE', 'CLOSING BALANCE', 'TRANSACTION TOTAL']):
                        continue
                    prev = transactions[-1]
                    merged_nar = clean_narration(prev.narration + ' ' + nar_text)
                    if master_ledgers:
                        vtype, contra = reconcile_transaction(
                            merged_nar, prev.is_debit, master_ledgers, amount=prev.amount
                        )
                    else:
                        vtype = prev.voucher_type
                        contra = guess_contra_ledger(merged_nar, prev.is_debit)
                    prev.narration = merged_nar
                    prev.voucher_type = vtype
                    prev.contra_ledger = contra
                continue

            # Get narration (may contain \n for multi-line cells)
            narration_raw = get_cell(row, 'narration')
            narration = clean_narration(narration_raw.replace('\n', ' '))

            # Skip special rows
            if any(kw in narration.upper() for kw in ['OPENING BALANCE', 'CLOSING BALANCE', 'TRANSACTION TOTAL']):
                continue

            debit = clean_amount(get_cell(row, 'debit'))
            credit = clean_amount(get_cell(row, 'credit'))
            balance = clean_amount(get_cell(row, 'balance'))
            chq_no = get_cell(row, 'chq')

            # Skip rows with no amount
            if debit == 0.0 and credit == 0.0:
                continue

            is_debit = debit > 0

            if master_ledgers:
                voucher_type, contra_ledger = reconcile_transaction(
                    narration, is_debit, master_ledgers, amount=(debit or credit)
                )
            else:
                voucher_type = "Payment" if is_debit else "Receipt"
                contra_ledger = guess_contra_ledger(narration, is_debit)

            transactions.append(Transaction(
                date=date,
                narration=narration,
                debit=debit,
                credit=credit,
                balance=balance,
                reference=chq_no,
                voucher_type=voucher_type,
                contra_ledger=contra_ledger,
            ))

    return transactions


def _parse_axis_text_lines(all_lines: List[str], master_ledgers: List[str] = None) -> List[Transaction]:
    """
    Fallback text-line parser for Axis Bank statements.
    Parses line by line, merging continuation narration lines.
    
    Transaction line format:
      DD-MM-YYYY [ChqNo] Narration Amount(s) Balance BranchCode
    """
    transactions = []
    blocks = []
    current_block = None

    for line in all_lines:
        stripped = line.strip()
        if not stripped:
            continue

        if _is_skip_line(stripped):
            # Exception: if the line starts with a date and has transaction data
            if not _DATE_RE.match(stripped):
                continue

        m = _DATE_RE.match(stripped)
        if m:
            # Check if this is OPENING/CLOSING/TOTAL line
            rest = stripped[m.end():].strip().upper()
            if any(kw in rest for kw in ['OPENING BALANCE', 'CLOSING BALANCE', 'TRANSACTION TOTAL']):
                continue

            if current_block:
                blocks.append(current_block)
            current_block = {
                'date_str': m.group(1),
                'first_line': stripped[m.end():].strip(),
                'continuation': [],
            }
        else:
            # Continuation line
            if current_block:
                current_block['continuation'].append(stripped)

    if current_block:
        blocks.append(current_block)

    # Parse each block
    prev_balance = None
    for block in blocks:
        date = parse_date(block['date_str'])
        if not date:
            continue

        # Combine first line + continuation
        full_text = block['first_line']
        for cont in block['continuation']:
            full_text += ' ' + cont

        # Extract amounts from the right side
        # Pattern: amounts are at the end of the line, possibly with a branch code (numeric, 3-4 digits)
        # Format: ... Debit/Credit Balance BranchCode
        # Remove branch code (last 3-4 digit number at the end)
        full_text = re.sub(r'\s+\d{3,4}\s*$', '', full_text)

        # Extract balance (last number)
        amounts = re.findall(r'[\d,]+\.\d{2}', full_text)
        if not amounts:
            continue

        balance = clean_amount(amounts[-1])

        # Extract cheque number if present (appears right after the date, before narration)
        chq_no = ''
        chq_match = re.match(r'^(\d{4,6})\s+', full_text)
        if chq_match:
            chq_no = chq_match.group(1)
            full_text = full_text[chq_match.end():]

        # Remove all amounts from narration
        narration = full_text
        for amt_str in amounts:
            narration = narration.replace(amt_str, '')
        narration = clean_narration(narration)

        # Determine debit/credit using balance transition
        debit = 0.0
        credit = 0.0

        if len(amounts) >= 3:
            # Three amounts: first=debit or credit, second=balance (already captured)
            # Actually for Axis: debit, credit, balance — one of debit/credit is empty
            # Since we extracted from text, we have the non-empty amount + balance
            txn_amount = clean_amount(amounts[0])
            if prev_balance is not None:
                diff = balance - prev_balance
                if diff < -0.005:
                    debit = txn_amount
                else:
                    credit = txn_amount
            else:
                # First transaction — use heuristic
                debit = txn_amount
        elif len(amounts) >= 2:
            txn_amount = clean_amount(amounts[0])
            if prev_balance is not None:
                diff = balance - prev_balance
                if diff < -0.005:
                    debit = abs(diff)
                else:
                    credit = abs(diff)
            else:
                debit = txn_amount
        else:
            continue

        prev_balance = balance

        if debit == 0.0 and credit == 0.0:
            continue

        is_debit = debit > 0

        if master_ledgers:
            voucher_type, contra_ledger = reconcile_transaction(
                narration, is_debit, master_ledgers, amount=(debit or credit)
            )
        else:
            voucher_type = "Payment" if is_debit else "Receipt"
            contra_ledger = guess_contra_ledger(narration, is_debit)

        transactions.append(Transaction(
            date=date,
            narration=narration,
            debit=debit,
            credit=credit,
            balance=balance,
            reference=chq_no,
            voucher_type=voucher_type,
            contra_ledger=contra_ledger,
        ))

    return transactions


def parse_bank_statement_axis(pdf_path: str, password: str = None, master_ledgers: List[str] = None) -> List[Transaction]:
    """
    Parse an Axis Bank statement PDF.
    Uses table extraction first (primary), falls back to text-line parsing.
    """
    console.print(f"\n[bold]📂 Parsing Axis Bank PDF:[/] {pdf_path}")
    if password:
        console.print("  🔐 Using password to unlock PDF")

    all_tables = []
    all_lines = []

    try:
        open_kwargs = {}
        if password:
            open_kwargs["password"] = password

        with pdfplumber.open(pdf_path, **open_kwargs) as pdf:
            console.print(f"  📄 PDF has [bold cyan]{len(pdf.pages)}[/] page(s)")

            for page_num, page in enumerate(pdf.pages, 1):
                # Try table extraction (lines/lines strategy works best for Axis)
                tables = page.extract_tables({
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                    "snap_tolerance": 5,
                    "join_tolerance": 5,
                    "edge_min_length": 10,
                })

                if tables:
                    console.print(f"  📊 Page {page_num}: Found [bold green]{len(tables)}[/] table(s)")
                    all_tables.extend(tables)

                # Also extract text for fallback
                text = page.extract_text()
                if text:
                    all_lines.extend(text.split('\n'))

    except Exception as e:
        console.print(f"  [bold red]Error reading PDF:[/] {e}")
        raise

    # Primary: parse from tables
    transactions = []
    if all_tables:
        console.print("  🏦 [bold green]Using table-based extraction for Axis Bank[/]")
        transactions = _parse_axis_table(all_tables, master_ledgers=master_ledgers)

    # Fallback: parse from text lines
    if not transactions and all_lines:
        console.print("  🔍 Table extraction yielded no results, trying text-line fallback...")
        transactions = _parse_axis_text_lines(all_lines, master_ledgers=master_ledgers)

    console.print(f"  📋 Total transactions extracted: [bold green]{len(transactions)}[/]")
    return transactions
