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

    Key guarantee: exactly len(date_lines) narration strings are returned, and
    no narration text ever leaks into an adjacent transaction's slot.
    """
    if len(date_lines) == len(narration_lines):
        return narration_lines

    num_transactions = len(date_lines)
    cleaned = [l.strip() for l in narration_lines if l.strip()]

    if not cleaned:
        return ["No Description"] * num_transactions

    # Fewer or equal lines than transactions — 1-to-1 map, pad the rest
    if len(cleaned) <= num_transactions:
        result = list(cleaned)
        result.extend(["No Description"] * (num_transactions - len(result)))
        return result

    # More narration lines than transactions — must merge continuations.
    # Extended set of known transaction-start prefixes:
    new_entry_patterns = [
        r'^UPI[-\s]',       r'^NEFT',           r'^RTGS',
        r'^IMPS',           r'^ATM',            r'^SETTLEMENT',
        r'^UPISETTLEMENT',  r'^\d+TERMINAL',    r'^EDCRENTAL',
        r'^SOUNDBOXRENTAL', r'^INT\.?PAY',      r'^CHQ',
        r'^CASH',           r'^FT-',            r'^BY\s',
        r'^TO\s',           r'^CLG',            r'^NACH',
        r'^ECS',            r'^SI-',            r'^PFMS',
        r'^ACHCR',          r'^MBK',            r'^BY\s+INST',
        r'^BY\s+DD',        r'^BY\s+CASH',      r'^CHARGES',
        r'^INT\s',          r'^SME\s',          r'^SALARY',
        r'^TRF',            r'^POS\s',          r'^DR\s',
        r'^\d{10,}',        r'^[A-Z]{3}[A-Z]{3}\d{2}INSTA',
        r'^INTERESTDEBITED', r'^IBFUNDSTRANSFER', r'^[A-Z]{6}\d{2}',
        r'^SELF',           r'^MEDCSI',
    ]

    def looks_like_new_entry(line: str) -> bool:
        for pat in new_entry_patterns:
            if re.match(pat, line, re.IGNORECASE):
                return True
        return False

    result = []         # completed narration slots
    current_parts = []  # parts accumulating for the current slot

    for idx, line in enumerate(cleaned):
        lines_after = len(cleaned) - idx - 1
        slots_left = num_transactions - len(result)

        # Hard constraint: once we're on the last slot, absorb everything into it
        on_last_slot = (slots_left <= 1)

        # Forced new entry: not enough lines remain to give every pending slot
        # at least one line unless we start a new slot right now
        forced_new = (not on_last_slot) and (lines_after < slots_left - 1)

        if not current_parts:
            current_parts.append(line)
        elif on_last_slot:
            current_parts.append(line)
        elif forced_new or looks_like_new_entry(line):
            result.append(" ".join(current_parts))
            current_parts = [line]
        else:
            current_parts.append(line)

    # Close the last open slot
    if current_parts:
        if len(result) < num_transactions:
            result.append(" ".join(current_parts))
        else:
            result[-1] = result[-1] + " " + " ".join(current_parts)

    # Safety: pad or merge tail into last slot (never drop text)
    while len(result) < num_transactions:
        result.append("No Description")
    if len(result) > num_transactions:
        tail = " ".join(result[num_transactions - 1:])
        result = result[:num_transactions - 1] + [tail]

    return result


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
        narrations = split_multiline_narration(narration_lines, valid_dates)

        # Parse balances first (needed for amount alignment)
        balance_cell = get_cell('balance')
        balance_lines = [l.strip() for l in balance_cell.split('\n') if l.strip()] if balance_cell else []
        
        def parse_bal(b_str):
            b_clean = re.sub(r'[₹$€£,]', '', b_str)
            b_clean = re.sub(r'\s*(Cr|CR|cr)\s*$', '', b_clean)
            is_dr = bool(re.search(r'\s*(Dr|DR|dr)\s*$', b_str))
            b_clean = re.sub(r'\s*(Dr|DR|dr)\s*$', '', b_clean)
            b_val = 0.0
            try:
                b_val = float(re.sub(r'[^\d.\-]', '', b_clean))
            except:
                pass
            return -abs(b_val) if is_dr else b_val
            
        balances = [parse_bal(b) for b in balance_lines]

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
            nar_text = narrations[i] if i < len(narrations) else "No Description"
            nar_text = re.sub(r'\s*OpeningBalance DrCount CrCount Debits Credits ClosingBal.*$', '', nar_text, flags=re.DOTALL)
            narration = clean_narration(nar_text)
            
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
                balance=abs(balance),
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


def parse_text_fallback(pdf_path: str, password: str = None, master_ledgers: List[str] = None) -> List[Transaction]:
    """
    Fallback parser that extracts text line-by-line and uses regex to find transactions.
    Useful for PDFs where table extraction fails or yields incorrect results.
    """
    console.print("  🔍 Attempting text-based fallback parsing...")
    transactions = []
    
    try:
        with pdfplumber.open(pdf_path, password=password) as pdf:
            full_text = "\n".join([page.extract_text() or "" for page in pdf.pages])
            
            # Regex to match a transaction line: Date, Narration, Debit, Credit, Balance
            # Example: 01-03-2026 UPI/548234558718/CR/LATA/SBIN/882786955/Paymen 700.00 78,346.06
            # This pattern looks for a date at the start, then captures everything until it finds 
            # two or three numeric amounts at the end.
            pattern = re.compile(
                r'^(\d{2}-\d{2}-\d{4})\s+(.*?)\s+([\d,.]+)\s+([\d,.]+)?\s*$', 
                re.MULTILINE
            )
            
            for match in pattern.finditer(full_text):
                date_str, narration, amt1, amt2 = match.groups()
                date = parse_date(date_str)
                if not date:
                    continue
                
                val1 = clean_amount(amt1)
                val2 = clean_amount(amt2) if amt2 else 0.0
                
                # Logic to determine if it's a Debit or Credit based on context or column position
                # In this specific PDF, the format is: Date, Narration, Credit, Balance
                # Wait, let's look at the dump: "1 01-03-2026 ... 700.00 78,346.06"
                # It seems Credit is the first amount, Balance is the second.
                
                # We will assume if there are 2 amounts: amt1=Credit/Debit, amt2=Balance
                # This is a simplification; we'll use a more robust heuristic if possible.
                # Let's use the total credits vs debits or balance transition to be sure.
                
                # For now, based on abc.pdf dump: Credit is 700.00, Balance is 78,346.06.
                # Let's assume: 
                # If only 2 values: it's either (Debit, Balance) or (Credit, Balance).
                # We can't know for sure without the header. 
                # But the dump says "Debit (₹) Credit (₹) Balance (₹)".
                # Line 10: "...Paymen 700.00 78,346.06" -> Only two values.
                # This means either Debit is empty and we have (Credit, Balance) or vice versa.
                
                # We'll refine this: try to see if the amount is a debit or credit.
                # Since we don't have the columns, we'll check if it's a payment or receipt in narration.
                
                is_debit = "Paymen" in narration or "Debit" in narration
                debit = val1 if is_debit else 0.0
                credit = val1 if not is_debit else 0.0
                balance = val2
                
                if debit == 0.0 and credit == 0.0:
                    continue

                if master_ledgers:
                    voucher_type, contra_ledger = reconcile_transaction(narration, is_debit, master_ledgers, amount=val1)
                else:
                    voucher_type = "Payment" if is_debit else "Receipt"
                    contra_ledger = guess_contra_ledger(narration, is_debit)

                transactions.append(Transaction(
                    date=date,
                    narration=clean_narration(narration),
                    debit=debit,
                    credit=credit,
                    balance=balance,
                    contra_ledger=contra_ledger,
                    voucher_type=voucher_type,
                ))
    except Exception as e:
        console.print(f"  [bold red]Text fallback failed:[/] {e}")

    return transactions

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

    # Detect and route BOB statements
    # NOTE: We must check for Axis Bank indicators FIRST, because Axis Bank
    # statements may contain "BANK OF BARODA" in transaction narrations.
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path, password=password or "") as pdf:
            if pdf.pages:
                text = pdf.pages[0].extract_text() or ""
                header_text = text[:1000].upper()
                # Check if this is an Axis Bank statement (should NOT be routed to BOB)
                is_axis = any(kw in header_text for kw in [
                    'AXIS BANK', 'STATEMENT OF AXIS ACCOUNT', 'UTIB',
                ])
                if not is_axis and ("BANK OF BARODA" in header_text or "SWASTIK TRADERS" in header_text):
                    console.print("  🏦 [bold green]Detected Bank of Baroda Statement format![/]")
                    from bob_parser import parse_bank_statement_bob
                    transactions = parse_bank_statement_bob(pdf_path, password=password, master_ledgers=master_ledgers)
                    console.print(f"  📋 Total transactions extracted: [bold green]{len(transactions)}[/]")
                    return transactions
    except Exception as e:
        console.print(f"  [yellow]Note: Error detecting BOB statement format: {e}[/]")

    tables = extract_tables_from_pdf(pdf_path, password=password)

    if not tables:
        console.print("[bold red]❌ No tables found in the PDF![/]")
        # Fallback to text parsing if no tables are found
        return parse_text_fallback(pdf_path, password, master_ledgers)

    transactions: List[Transaction] = []
    header_found = False
    column_mapping: Dict[str, int] = {}
    is_multiline = False

    last_balance = None

    multiline_rows_to_merge = []
    multiline_mapping = None

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
                if not multiline_mapping:
                    multiline_mapping = column_mapping
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
            if header_found and is_multiline_cell_format(data_rows):
                is_multiline = True

        if not header_found or not column_mapping:
            continue

        if is_multiline:
            # Accumulate multi-line rows to merge them across pages
            for row in data_rows:
                if row:
                    multiline_rows_to_merge.append(row)
        else:
            # Helper defined once per table (not inside the loop)
            def get_cell_raw(r, col_name):
                idx = column_mapping.get(col_name, -1)
                if idx < 0 or idx >= len(r):
                    return ''
                return str(r[idx]).strip() if r[idx] else ''

            for row in data_rows:
                # ── Continuation-row detection ──────────────────────────────
                # A continuation row has no date and no debit/credit amount
                # but carries text in the narration column. pdfplumber creates
                # these when a long narration wraps inside a PDF table cell and
                # the PDF has no explicit row border for that continuation line.
                # Without this merge the party/payee name is silently lost,
                # causing the wrong ledger to be assigned to the transaction.
                if row is not None:
                    row_date   = get_cell_raw(row, 'date')
                    row_debit  = get_cell_raw(row, 'debit')
                    row_credit = get_cell_raw(row, 'credit')
                    row_nar    = get_cell_raw(row, 'narration')

                    is_continuation = (
                        not parse_date(row_date)
                        and clean_amount(row_debit) == 0.0
                        and clean_amount(row_credit) == 0.0
                        and bool(row_nar)
                        and transactions
                    )

                    if is_continuation:
                        prev = transactions[-1]
                        merged_nar = clean_narration(prev.narration + ' ' + row_nar)
                        if master_ledgers:
                            vtype, contra = reconcile_transaction(
                                merged_nar, prev.is_debit, master_ledgers,
                                amount=prev.amount
                            )
                        else:
                            vtype  = prev.voucher_type
                            contra = guess_contra_ledger(merged_nar, prev.is_debit)
                        prev.narration     = merged_nar
                        prev.voucher_type  = vtype
                        prev.contra_ledger = contra
                        continue
                # ────────────────────────────────────────────────────────────

                txn = _parse_standard_row(row, column_mapping, master_ledgers=master_ledgers)
                if txn:
                    transactions.append(txn)
                    last_balance = txn.balance

    # Process accumulated multiline rows as one giant table
    if multiline_rows_to_merge and multiline_mapping:
        merged_row = [''] * (max(multiline_mapping.values()) + 1)
        for row in multiline_rows_to_merge:
            for i, cell in enumerate(row):
                if i < len(merged_row):
                    if cell and str(cell).strip():
                        if merged_row[i]:
                            merged_row[i] += '\n' + str(cell)
                        else:
                            merged_row[i] = str(cell)
        txns, _ = parse_multiline_table([merged_row], multiline_mapping, prev_balance=None, master_ledgers=master_ledgers)
        transactions.extend(txns)

    # Sanity check: if we extracted transactions but they look like a sequence (1, 2, 3...), it's likely the Sr No column
    if transactions:
        sequential_count = 0
        for i, txn in enumerate(transactions[:10]):
            if abs(txn.debit - (i + 1)) < 0.01 or abs(txn.credit - (i + 1)) < 0.01:
                sequential_count += 1
        if sequential_count >= 5:
            console.print("  ⚠️  Detected suspicious sequential amounts. Triggering fallback parser...")
            return parse_text_fallback(pdf_path, password, master_ledgers)

    if not transactions:
        return parse_text_fallback(pdf_path, password, master_ledgers)

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
