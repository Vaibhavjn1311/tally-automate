"""
PDF Bank Statement Parser.
Extracts transaction tables from bank statement PDFs using pdfplumber.
Supports multiple Indian bank statement formats including multi-line cell formats
(e.g., Bank of Baroda, SBI, etc.).
"""

import pdfplumber
import re
from typing import List, Optional, Tuple, Dict
from transaction import Transaction, clean_amount, parse_date, clean_narration, guess_contra_ledger, reconcile_transaction
from rich.console import Console

console = Console()


# ─── Column Name Patterns ────────────────────────────────────────────────────
COLUMN_PATTERNS: Dict[str, List[str]] = {
    'date': [
        'date', 'txn date', 'transaction date', 'value date', 'val date',
        'posting date', 'trans date', 'dt', 'txn dt', 'value dt',
        'tran date', 'trans. date', 'txn. date', 'trandate',
    ],
    'narration': [
        'narration', 'description', 'particulars', 'details', 'remarks',
        'transaction details', 'txn details', 'transaction description',
        'transaction particulars', 'trans particulars', 'trans description',
        'naration', 'desc', 'detail', 'particular', 'arration',
    ],
    'debit': [
        'debit', 'withdrawal', 'withdrawals', 'debit amount', 'dr',
        'debit amt', 'dr amount', 'dr amt', 'withdrawn', 'debit(dr)',
        'withdrawal amount', 'dr.', 'debit (dr)', 'withdrawalamt', 'withdraw',
        'rawal',
    ],
    'credit': [
        'credit', 'deposit', 'deposits', 'credit amount', 'cr',
        'credit amt', 'cr amount', 'cr amt', 'deposited', 'credit(cr)',
        'deposit amount', 'cr.', 'credit (cr)', 'depositamt', 'posit',
    ],
    'balance': [
        'balance', 'closing balance', 'running balance', 'bal',
        'available balance', 'closing bal', 'balance amount',
        'running bal', 'bal.', 'balance(inr)', 'closingbalance', 'alance',
    ],
    'reference': [
        'reference', 'ref', 'ref no', 'ref. no', 'reference no',
        'reference number', 'chq no', 'cheque no', 'chq/ref no',
        'txn ref', 'utr', 'utr no', 'chq./ref.no', 'chq./ref.no.', 'chq',
    ],
    'value_date': [
        'valuedt', 'value dt', 'val dt', 'value date',
    ],
}

# Date pattern for DD/MM/YY or DD/MM/YYYY
DATE_PATTERN = re.compile(r'^\d{2}/\d{2}/\d{2,4}$')


def normalize_column_name(col_name: str) -> Optional[str]:
    """Match a column header to a standardized name using pattern matching."""
    if not col_name:
        return None

    # Skip actual multi-line values or extremely long text blocks
    if len(col_name) > 50 or col_name.count('\n') > 1:
        return None

    col_lower = col_name.lower().strip().replace(' ', '').replace('.', '').replace('-', '').replace('_', '')

    for standard_name, patterns in COLUMN_PATTERNS.items():
        for pattern in patterns:
            pattern_clean = pattern.replace(' ', '').replace('.', '').replace('-', '').replace('_', '')
            # Match if equal, starts with, or if pattern is contained in col_lower (for split headers)
            # Reduced min length for 'in' check to 2 to catch 'dr' and 'cr' in split cells
            if col_lower == pattern_clean or col_lower.startswith(pattern_clean) or (len(pattern_clean) >= 2 and pattern_clean in col_lower):
                return standard_name

    return None


def detect_columns(headers: List[str]) -> Dict[str, int]:
    """Detect which columns correspond to standard names. Returns mapping of names to indices."""
    mapping = {}
    for idx, header in enumerate(headers):
        if header is None:
            continue
        standard = normalize_column_name(str(header))
        if standard and standard not in mapping:
            mapping[standard] = idx
    return mapping


def extract_tables_from_pdf(pdf_path: str, password: str = None) -> List[List[List[str]]]:
    """Extract all tables from a PDF file using pdfplumber."""
    all_tables = []

    try:
        open_kwargs = {}
        if password:
            open_kwargs["password"] = password
        with pdfplumber.open(pdf_path, **open_kwargs) as pdf:
            console.print(f"  📄 PDF has [bold cyan]{len(pdf.pages)}[/] page(s)")

            for page_num, page in enumerate(pdf.pages, 1):
                # Strategy 1: Standard lines-based extraction
                tables = page.extract_tables({
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                    "snap_tolerance": 5,
                    "join_tolerance": 5,
                    "edge_min_length": 10,
                })

                # Strategy 2: Text-based vertical strategy (for borderless tables with horizontal lines)
                if not tables or all(len(t[0]) < 3 for t in tables if t):
                    more_tables = page.extract_tables({
                        "vertical_strategy": "text",
                        "horizontal_strategy": "lines",
                        "snap_tolerance": 5,
                        "join_tolerance": 5,
                    })
                    if more_tables:
                        tables = more_tables

                # Strategy 3: Pure text-based (for fully borderless tables)
                if not tables or all(len(t[0]) < 3 for t in tables if t):
                    more_tables = page.extract_tables({
                        "vertical_strategy": "text",
                        "horizontal_strategy": "text",
                        "snap_tolerance": 5,
                        "join_tolerance": 5,
                    })
                    if more_tables:
                        tables = more_tables

                if tables:
                    console.print(f"  📊 Page {page_num}: Found [bold green]{len(tables)}[/] table(s)")
                    all_tables.extend(tables)
                else:
                    console.print(f"  ⚠️  Page {page_num}: No tables found")

    except Exception as e:
        console.print(f"  [bold red]Error reading PDF:[/] {e}")
        raise

    return all_tables


def is_multiline_cell_format(table: List[List[str]]) -> bool:
    """
    Detect if the table uses multi-line cells (e.g., Bank of Baroda format)
    where multiple transactions are packed into a single row with newline separators.
    """
    for row in table:
        if row is None:
            continue
        for cell in row:
            if cell and '\n' in str(cell):
                lines = str(cell).strip().split('\n')
                # If the cell has many lines and some look like dates, it's multi-line
                date_count = sum(1 for l in lines if DATE_PATTERN.match(l.strip()))
                if date_count >= 2: # Reduced from 3 to 2 for better sensitivity
                    return True
    return False


def split_multiline_narration(narration_lines: List[str], date_lines: List[str]) -> List[str]:
    """
    Match narration lines to dates. Narrations can span multiple lines due to 
    wrapping, so we need to merge continuation lines with their parent narration.
    
    A narration line is a "new" narration if the corresponding date line exists.
    Otherwise, it's a continuation of the previous narration.
    """
    if len(date_lines) == len(narration_lines):
        return narration_lines

    # We need to figure out which narration lines belong together
    # Strategy: Use dates as anchors. Each date starts a new transaction.
    # Count dates to know expected number of transactions.
    num_transactions = len(date_lines)

    # Try to match narrations by checking if line starts look like new entries
    # (not continuation of wrapped text)
    merged = []
    current = ""

    for line in narration_lines:
        line = line.strip()
        if not line:
            continue

        # Heuristic: a new narration typically starts with known patterns
        is_new_entry = False

        # Check if this looks like the start of a new transaction narration
        new_entry_patterns = [
            r'^UPI[-\s]',           # UPI transaction
            r'^NEFT',               # NEFT
            r'^RTGS',               # RTGS
            r'^IMPS',               # IMPS
            r'^ATM',                # ATM
            r'^SETTLEMENT',         # Settlement
            r'^UPISETTLEMENT',      # UPI Settlement
            r'^\d+TERMINAL',        # Card settlement
            r'^EDCRENTAL',          # EDC Rental
            r'^SOUNDBOXRENTAL',     # Soundbox Rental
            r'^INT\.?PAY',          # Interest payment
            r'^CHQ',                # Cheque
            r'^CASH',               # Cash
            r'^FT-',                # Fund transfer
            r'^BY\s',               # By transfer
            r'^TO\s',               # To transfer
            r'^CLG',                # Clearing
            r'^NACH',               # NACH
            r'^ECS',                # ECS
            r'^SI-',                # Standing instruction
        ]

        for pat in new_entry_patterns:
            if re.match(pat, line, re.IGNORECASE):
                is_new_entry = True
                break

        # If we haven't reached expected count and this looks new, start new entry
        if is_new_entry or len(merged) == 0:
            if current:
                merged.append(current)
            current = line
        else:
            # Continuation of previous narration
            current = current + " " + line if current else line

    if current:
        merged.append(current)

    # If merged count matches dates, great!
    if len(merged) == num_transactions:
        return merged

    # Fallback: if count doesn't match, just return what we have
    # and pad/truncate to match date count
    if len(merged) < num_transactions:
        merged.extend(["No Description"] * (num_transactions - len(merged)))
    elif len(merged) > num_transactions:
        merged = merged[:num_transactions]

    return merged


def parse_multiline_amounts(amount_cell: str, num_transactions: int) -> List[float]:
    """
    Parse a multi-line amount cell. Each line is an amount for one transaction.
    Empty lines mean no amount (0.0) for that transaction.
    Returns a list of floats matching the number of transactions.
    """
    if not amount_cell or str(amount_cell).strip() == '':
        return []

    lines = str(amount_cell).strip().split('\n')
    amounts = [clean_amount(l.strip()) for l in lines if l.strip()]

    # Amounts are listed only for transactions that have them
    # We need to figure out which transactions have amounts
    # Filter out zero values — they aren't real amounts
    return [a for a in amounts if a > 0.0]


def match_amounts_to_balances(
    debits_raw: List[float],
    credits_raw: List[float],
    balances: List[float],
    prev_balance: Optional[float] = None
) -> Tuple[List[float], List[float]]:
    """
    Align debits and credits to balances using mathematical balance transitions.
    Returns (debits, credits) as lists of length equal to len(balances).
    """
    n = len(balances)
    debits = [0.0] * n
    credits = [0.0] * n

    if n == 0:
        return debits, credits

    # Fallback if we don't have enough balances
    if n < 2 and prev_balance is None:
        for i in range(min(n, len(debits_raw))):
            debits[i] = debits_raw[i]
        for i in range(min(n, len(credits_raw))):
            credits[i] = credits_raw[i]
        return debits, credits

    # Keep track of available debit/credit amounts (filter out zeros)
    avail_debits = [x for x in debits_raw if x > 0.0]
    avail_credits = [x for x in credits_raw if x > 0.0]

    # Helper to find and pop the closest amount
    def pop_closest(val: float, lst: List[float]) -> float:
        if not lst:
            return val
        # Find index of closest value
        idx = min(range(len(lst)), key=lambda j: abs(lst[j] - val))
        closest = lst[idx]
        # If the closest value is reasonably close (e.g. within 5% or 10.0), use it
        if abs(closest - val) < max(10.0, 0.05 * val):
            return lst.pop(idx)
        return val

    # Match transactions i from 1 to n-1
    for i in range(1, n):
        diff = balances[i] - balances[i - 1]
        if diff < -0.005:
            # Debit
            amt = pop_closest(abs(diff), avail_debits)
            debits[i] = amt
        elif diff > 0.005:
            # Credit
            amt = pop_closest(diff, avail_credits)
            credits[i] = amt

    # Now for index 0
    if prev_balance is not None:
        diff = balances[0] - prev_balance
        if diff < -0.005:
            amt = pop_closest(abs(diff), avail_debits)
            debits[0] = amt
        elif diff > 0.005:
            amt = pop_closest(diff, avail_credits)
            credits[0] = amt
    else:
        # No prev_balance (first transaction of the statement)
        # Use whatever is left in avail_debits or avail_credits
        if avail_debits and not avail_credits:
            debits[0] = avail_debits.pop(0)
        elif avail_credits and not avail_debits:
            credits[0] = avail_credits.pop(0)
        elif avail_debits and avail_credits:
            # Both are available, decide based on which list has more items
            if len(avail_debits) > len(avail_credits):
                debits[0] = avail_debits.pop(0)
            else:
                credits[0] = avail_credits.pop(0)

    return debits, credits


def parse_multiline_table(
    table: List[List[str]],
    column_mapping: Dict[str, int],
    prev_balance: Optional[float] = None,
    master_ledgers: List[str] = None
) -> Tuple[List[Transaction], Optional[float]]:
    """
    Parse a table where multiple transactions are packed into multi-line cells.
    This is common in Bank of Baroda and similar bank statement formats.
    """
    transactions = []
    current_balance = prev_balance

    for row in table:
        if row is None:
            continue

        def get_cell(col_name: str) -> str:
            idx = column_mapping.get(col_name, -1)
            if idx < 0 or idx >= len(row):
                return ''
            return str(row[idx]) if row[idx] else ''

        # Get the date cell - this determines number of transactions
        date_cell = get_cell('date')
        if not date_cell:
            continue

        date_lines = [l.strip() for l in date_cell.split('\n') if l.strip()]

        # Validate that these are actual dates
        valid_dates = []
        for dl in date_lines:
            d = parse_date(dl)
            if d:
                valid_dates.append(d)

        if not valid_dates:
            continue

        num_txns = len(valid_dates)

        # Parse narrations
        narration_cell = get_cell('narration')
        narration_lines = narration_cell.split('\n') if narration_cell else []
        narrations = split_multiline_narration(narration_lines, date_lines)

        # Parse balances first (needed for amount alignment)
        balance_cell = get_cell('balance')
        balance_lines = [l.strip() for l in balance_cell.split('\n') if l.strip()] if balance_cell else []
        balances = [clean_amount(b) for b in balance_lines]

        # Parse amounts
        debit_cell = get_cell('debit')
        credit_cell = get_cell('credit')

        debit_amounts_raw = parse_multiline_amounts(debit_cell, num_txns)
        credit_amounts_raw = parse_multiline_amounts(credit_cell, num_txns)

        # Align amounts to transactions using balance changes
        debits, credits = match_amounts_to_balances(
            debit_amounts_raw,
            credit_amounts_raw,
            balances,
            prev_balance=current_balance
        )

        # Parse references
        ref_cell = get_cell('reference')
        ref_lines = [l.strip() for l in ref_cell.split('\n') if l.strip()] if ref_cell else []

        # Clean/merge wrapped reference lines
        k = 0
        while k < len(ref_lines) - 1:
            if ref_lines[k] and ref_lines[k+1] and ref_lines[k][0].isalpha() and len(ref_lines[k]) < 22 and ref_lines[k+1].isdigit() and len(ref_lines[k+1]) <= 8:
                ref_lines[k] = ref_lines[k] + ref_lines[k+1]
                ref_lines.pop(k+1)
            else:
                k += 1

        # Build transactions
        for i in range(num_txns):
            date = valid_dates[i]
            narration = clean_narration(narrations[i] if i < len(narrations) else "No Description")
            debit = debits[i] if i < len(debits) else 0.0
            credit = credits[i] if i < len(credits) else 0.0
            balance = balances[i] if i < len(balances) else 0.0

            # Skip entries with no amount
            if debit == 0.0 and credit == 0.0:
                continue

            is_debit = debit > 0
            
            if master_ledgers:
                voucher_type, contra_ledger = reconcile_transaction(narration, is_debit, master_ledgers, amount=(debit or credit))
            else:
                voucher_type = "Payment" if is_debit else "Receipt"
                contra_ledger = guess_contra_ledger(narration, is_debit)

            ref = ""
            if i < len(ref_lines):
                ref = ref_lines[i]

            txn = Transaction(
                date=date,
                narration=narration,
                debit=debit,
                credit=credit,
                balance=balance,
                reference=ref,
                contra_ledger=contra_ledger,
                voucher_type=voucher_type,
            )
            transactions.append(txn)

        if transactions:
            current_balance = transactions[-1].balance

    return transactions, current_balance


def find_header_row(table: List[List[str]]) -> Tuple[int, Dict[str, int]]:
    """Find the header row in a table by looking for known column name patterns."""
    for row_idx, row in enumerate(table):
        if row is None:
            continue

        headers = [str(cell).strip() if cell else '' for cell in row]
        mapping = detect_columns(headers)

        if 'date' in mapping and ('debit' in mapping or 'credit' in mapping):
            return row_idx, mapping

    return -1, {}


def guess_mapping_from_data(table: List[List[str]]) -> Dict[str, int]:
    """Guess column mapping by inspecting data rows when no header is found."""
    if not table:
        return {}
    
    mapping = {}
    
    # Sample rows that look like they might be transactions
    sample_rows = []
    for row in table:
        if not row: continue
        # Row should have at least some data and ideally a date
        if any(row) and any(parse_date(str(c)) for c in row if c):
            sample_rows.append(row)
        if len(sample_rows) >= 10:
            break
            
    if not sample_rows:
        return {}
    
    num_cols = len(sample_rows[0])
    
    # 1. Find Date column
    for idx in range(num_cols):
        date_count = sum(1 for row in sample_rows if idx < len(row) and parse_date(str(row[idx])))
        if date_count >= len(sample_rows) * 0.6:
            mapping['date'] = idx
            break
            
    # 2. Find Amount columns (Debit, Credit, Balance)
    amount_cols = []
    for idx in range(num_cols):
        if idx == mapping.get('date'): continue
        
        # Check if column contains mostly numbers and is NOT a date
        amount_count = 0
        valid_rows = 0
        for row in sample_rows:
            if idx < len(row):
                val = str(row[idx]).strip()
                if not val: continue
                valid_rows += 1
                
                # Skip if it's a date
                if parse_date(val):
                    continue
                
                # Skip if it has too much non-numeric text (likely narration)
                non_numeric = re.sub(r'[\d.,\-₹\sCrDrcrdr]', '', val).strip()
                if len(non_numeric) > 2:
                    continue
                    
                if clean_amount(val) > 0:
                    amount_count += 1
        
        if valid_rows > 0 and amount_count >= valid_rows * 0.4:
            amount_cols.append(idx)
            
    if len(amount_cols) >= 3:
        # If we have 3 or more, assume they are Debit, Credit, Balance in order
        mapping['debit'] = amount_cols[0]
        mapping['credit'] = amount_cols[1]
        mapping['balance'] = amount_cols[-1]
    elif len(amount_cols) == 2:
        # Often Debit/Credit are merged or one is empty. 
        # Usually it's Amount and Balance or Debit and Balance.
        mapping['debit'] = amount_cols[0]
        mapping['balance'] = amount_cols[1]
    elif len(amount_cols) == 1:
        mapping['balance'] = amount_cols[0]

    # 3. Find Narration (column with most text that is not date or amount)
    best_nar_col = -1
    max_text_len = 0
    for idx in range(num_cols):
        if idx in mapping.values(): continue
        
        total_len = sum(len(str(row[idx])) for row in sample_rows if idx < len(row))
        if total_len > max_text_len:
            max_text_len = total_len
            best_nar_col = idx
            
    if best_nar_col >= 0:
        mapping['narration'] = best_nar_col
        
    return mapping


def parse_bank_statement(pdf_path: str, password: str = None, master_ledgers: List[str] = None) -> List[Transaction]:
    """
    Parse a bank statement PDF and return a list of Transaction objects.
    Handles both standard row-per-transaction and multi-line cell formats.
    """
    console.print(f"\n[bold]📂 Parsing PDF:[/] {pdf_path}")
    if password:
        console.print("  🔐 Using password to unlock PDF")

    if master_ledgers:
        console.print(f"  📚 Using [bold cyan]{len(master_ledgers)}[/] master ledgers for reconciliation")

    tables = extract_tables_from_pdf(pdf_path, password=password)

    if not tables:
        console.print("[bold red]❌ No tables found in the PDF![/]")
        console.print("[yellow]Tip: Make sure the PDF has selectable text (not a scanned image).[/]")
        return []

    transactions: List[Transaction] = []
    header_found = False
    column_mapping: Dict[str, int] = {}
    is_multiline = False

    last_balance = None

    for table_idx, table in enumerate(tables):
        h_idx, h_map = find_header_row(table)
        
        if h_idx >= 0:
            column_mapping = h_map
            header_found = True
            console.print(f"  ✅ Header detected in Table {table_idx+1}: {list(h_map.keys())}")
            
            data_rows = table[h_idx + 1:]
            # Check if this is a multi-line cell format
            is_multiline = is_multiline_cell_format(data_rows)
            if is_multiline:
                console.print(f"  📋 Table {table_idx+1}: Detected [bold yellow]multi-line cell format[/]")
            else:
                console.print(f"  📋 Table {table_idx+1}: Detected [bold green]standard row format[/]")
        else:
            if not header_found or (column_mapping and len(table[0]) != max(column_mapping.values()) + 1 if table and table[0] else False):
                # Try to guess mapping if no header found yet or column count changed significantly
                guessed_map = guess_mapping_from_data(table)
                if 'date' in guessed_map and ('debit' in guessed_map or 'credit' in guessed_map or 'balance' in guessed_map):
                    column_mapping = guessed_map
                    header_found = True
                    console.print(f"  🔍 Table {table_idx+1}: Auto-guessed mapping: {list(guessed_map.keys())}")
            
            data_rows = table

        if not header_found or not column_mapping:
            continue

        if is_multiline:
            txns, last_balance = parse_multiline_table(data_rows, column_mapping, prev_balance=last_balance, master_ledgers=master_ledgers)
            transactions.extend(txns)
        else:
            for row in data_rows:
                txn = _parse_standard_row(row, column_mapping, master_ledgers=master_ledgers)
                if txn:
                    transactions.append(txn)
                    last_balance = txn.balance

    console.print(f"\n  📋 Total transactions extracted: [bold green]{len(transactions)}[/]")
    return transactions


def _parse_standard_row(row: List[str], mapping: Dict[str, int], master_ledgers: List[str] = None) -> Optional[Transaction]:
    """Parse a single standard table row into a Transaction object."""
    if row is None or all(cell is None or str(cell).strip() == '' for cell in row):
        return None

    def get_cell(col_name: str) -> str:
        idx = mapping.get(col_name, -1)
        if idx < 0 or idx >= len(row):
            return ''
        return str(row[idx]).strip() if row[idx] else ''

    date_str = get_cell('date')
    date = parse_date(date_str)
    if not date:
        return None

    debit = clean_amount(get_cell('debit'))
    credit = clean_amount(get_cell('credit'))

    if debit == 0.0 and credit == 0.0:
        return None

    narration = clean_narration(get_cell('narration'))
    balance = clean_amount(get_cell('balance'))
    reference = get_cell('reference')
    is_debit = debit > 0
    
    if master_ledgers:
        voucher_type, contra_ledger = reconcile_transaction(narration, is_debit, master_ledgers, amount=(debit or credit))
    else:
        voucher_type = "Payment" if is_debit else "Receipt"
        contra_ledger = guess_contra_ledger(narration, is_debit)

    return Transaction(
        date=date,
        narration=narration,
        debit=debit,
        credit=credit,
        balance=balance,
        reference=reference,
        contra_ledger=contra_ledger,
        voucher_type=voucher_type,
    )
