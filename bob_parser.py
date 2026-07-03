import pdfplumber
import re
from typing import List, Optional
from transaction import Transaction, clean_amount, parse_date, clean_narration, reconcile_transaction
from rich.console import Console

console = Console()

def clean_narration_amount(narration: str, amount: float = None, balance: float = None) -> str:
    """Clean transaction amount and balance patterns from narration."""
    # Remove balance and amount strings
    pattern = r'\b[\d,]+\.\d{2}(?:[Cc][Rr]|[Dd][Rr])?\b'
    if amount is not None:
        amount_str_1 = f"{amount:,.2f}"
        amount_str_2 = f"{amount:.2f}"
        narration = narration.replace(amount_str_1, '').replace(amount_str_2, '')
        
    if balance is not None:
        balance_str_1 = f"{balance:,.2f}"
        balance_str_2 = f"{balance:.2f}"
        narration = narration.replace(balance_str_1, '').replace(balance_str_2, '')
        
    narration = re.sub(pattern, '', narration)
    
    # Clean UTR or check endings or colons
    narration = re.sub(r'\s*:\s*\d{12}\s*$', '', narration)
    narration = re.sub(r'\s*:\s*$', '', narration)
    narration = re.sub(r'\s+', ' ', narration).strip()
    return narration


# ─── Date pattern for DD-MM-YY (columnar BOB format) ─────────────────────────
_DATE_COL_RE = re.compile(r'^(\d{2}-\d{2}-\d{2,4})\s+')

# Lines to skip (headers, footers, separators)
_SKIP_KEYWORDS = [
    'BANK OF BARODA', 'HELPLINE NO', 'BRANCH PHONE', 'MICR CODE', 'IFSC CODE',
    'A/C Name', 'Address', 'City', 'CKYC', 'Tel No', 'Nomination Flag',
    'Scheme Description', 'Joint Holders', 'A/C Number', 'Account Open Date',
    'Statement of account', 'DATE PARTICULARS', 'Page Total', 'Grand Total',
    'Note:', 'Unless the constituent', 'returning on the basis',
    'transaction(s) in the statement', 'ClrBal:', 'Unclr Bal', 'Lien:',
    'We are committed', 'For details please', 'Please contact',
    'ABBREVIATIONS', 'Retd -', 'EC -', 'SP -', 'INT -', 'OBC -',
    'DAUE -', 'Pending penal', 'This is a computer',
    '****END', 'Page No:', 'within 15 days',
]


def _is_separator_line(line: str) -> bool:
    """Check if a line is a dashed separator."""
    return bool(re.match(r'^-{10,}$', line.strip()))


def _is_skip_line(line: str) -> bool:
    """Check if a line is a header/footer/metadata line to skip."""
    stripped = line.strip()
    if not stripped:
        return True
    if _is_separator_line(stripped):
        return True
    for kw in _SKIP_KEYWORDS:
        if kw in stripped:
            return True
    return False


def _parse_columnar_transaction_line(line: str):
    """
    Parse a columnar BOB transaction line with format:
    DD-MM-YY  PARTICULARS  CHQ.NO.  WITHDRAWALS  DEPOSITS  BALANCE
    
    Returns dict with date_str, narration, chq_no, withdrawal, deposit, balance
    or None if not a transaction line.
    """
    m = _DATE_COL_RE.match(line)
    if not m:
        return None
    
    date_str = m.group(1)
    rest = line[m.end():].strip()
    
    # Extract balance at the end (with Cr/Dr suffix)
    bal_match = re.search(r'([\d,]+\.\d{2})(Cr|Dr|CR|DR)\s*$', rest, re.IGNORECASE)
    balance = 0.0
    balance_type = 'Cr'
    if bal_match:
        balance = clean_amount(bal_match.group(1))
        balance_type = bal_match.group(2).upper()
        rest = rest[:bal_match.start()].strip()
    
    # Now rest contains: PARTICULARS  [CHQ.NO.]  [WITHDRAWAL]  [DEPOSIT]
    # Extract numeric amounts from the right side
    # Pattern: amounts are comma-separated numbers with decimals at the end
    amounts = []
    while True:
        amt_match = re.search(r'([\d,]+\.\d{2})\s*$', rest)
        if amt_match:
            amounts.insert(0, clean_amount(amt_match.group(1)))
            rest = rest[:amt_match.start()].strip()
        else:
            break
    
    # Parse amounts:
    # If only balance was found (B/F line), amounts might be empty
    withdrawal = 0.0
    deposit = 0.0
    
    if len(amounts) >= 2:
        # Two amounts: withdrawal, deposit (one of them will be the actual amount)
        withdrawal = amounts[0]
        deposit = amounts[1]
    elif len(amounts) == 1:
        # Single amount: could be either withdrawal or deposit
        # We'll determine later using balance transitions
        # For now, store it and use balance logic
        withdrawal = amounts[0]
    
    # The remaining text is the narration + possibly cheque number
    # CHQ.NO. is typically a standalone number after the narration text
    narration = rest.strip()
    chq_no = ''
    
    # Try to extract cheque number from end of narration
    chq_match = re.search(r'\s+(\d{4,12})\s*$', narration)
    if chq_match:
        chq_no = chq_match.group(1)
        narration = narration[:chq_match.start()].strip()
    
    return {
        'date_str': date_str,
        'narration': narration,
        'chq_no': chq_no,
        'withdrawal': withdrawal,
        'deposit': deposit,
        'balance': balance,
        'balance_type': balance_type,
    }


def parse_bank_statement_bob_columnar(pdf_path: str, password: str = None, master_ledgers: List[str] = None) -> List[Transaction]:
    """
    Parser for BOB columnar text-based statements with format:
    DATE  PARTICULARS  CHQ.NO.  WITHDRAWALS  DEPOSITS  BALANCE
    
    Uses DD-MM-YY date format, Cr/Dr balance suffixes, and continuation 
    narration lines without dates.
    """
    console.print(f"\n[bold]📂 Parsing BOB PDF (Columnar Mode):[/] {pdf_path}")
    
    all_lines = []
    with pdfplumber.open(pdf_path, password=password or "") as pdf:
        console.print(f"  📄 PDF has [bold cyan]{len(pdf.pages)}[/] page(s)")
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                all_lines.extend(text.split('\n'))
    
    # Build transaction blocks: each block starts with a date line, followed
    # by 0+ continuation lines (narration overflow from the PDF)
    blocks = []
    current_block = None
    
    for line in all_lines:
        stripped = line.strip()
        if not stripped:
            continue
        
        if _is_skip_line(stripped):
            # Exception: if this line starts with a valid date + has transaction
            # data, it should not be skipped (e.g., "01-04-25 B/F 2,635.02Cr")
            if not _DATE_COL_RE.match(stripped):
                continue
            # Even with a date, some skip lines match (like "Statement of account for the period of 01-04-2025 to 31-03-2026")
            # Double-check: does it contain actual skip keywords?
            has_skip = any(kw in stripped for kw in ['Statement of account', 'Page Total', 'Grand Total'])
            if has_skip:
                continue
        
        parsed = _parse_columnar_transaction_line(stripped)
        if parsed:
            # New transaction line
            if current_block:
                blocks.append(current_block)
            current_block = {
                'parsed': parsed,
                'continuation_lines': [],
            }
        else:
            # Continuation line (narration overflow or extra detail)
            if current_block:
                current_block['continuation_lines'].append(stripped)
    
    if current_block:
        blocks.append(current_block)
    
    console.print(f"  📊 Found [bold green]{len(blocks)}[/] transaction blocks")
    
    # Build raw transactions with balance info
    transactions_raw = []
    for block in blocks:
        p = block['parsed']
        date = parse_date(p['date_str'])
        if not date:
            continue
        
        # Build full narration from main line + continuation lines
        narration_parts = [p['narration']]
        narration_parts.extend(block['continuation_lines'])
        full_narration = ' '.join(narration_parts)
        
        transactions_raw.append({
            'date': date,
            'narration': full_narration,
            'chq_no': p['chq_no'],
            'withdrawal': p['withdrawal'],
            'deposit': p['deposit'],
            'balance': p['balance'],
            'balance_type': p['balance_type'],
        })
    
    # Now determine debit/credit using balance transitions
    # This BOB format lists transactions in chronological order
    parsed_transactions = []
    
    for i, txn in enumerate(transactions_raw):
        withdrawal = txn['withdrawal']
        deposit = txn['deposit']
        balance = txn['balance']
        
        # If both withdrawal and deposit are given as separate values, use them
        if withdrawal > 0 and deposit > 0:
            # Both have values, which shouldn't normally happen for a single txn
            # Use balance transition to determine which is the real one
            pass
        elif withdrawal > 0 and deposit == 0:
            # Could be either withdrawal or deposit depending on column parsing
            # Use balance transition to verify
            pass
        
        # Determine debit vs credit using balance transition
        is_debit = False
        txn_amount = 0.0
        
        if i > 0:
            prev_balance = transactions_raw[i - 1]['balance']
            diff = balance - prev_balance
            
            if abs(diff) > 0.005:
                if diff < 0:
                    # Balance decreased → withdrawal (debit)
                    is_debit = True
                    txn_amount = abs(diff)
                else:
                    # Balance increased → deposit (credit) 
                    is_debit = False
                    txn_amount = abs(diff)
            else:
                # No balance change (shouldn't normally happen)
                txn_amount = withdrawal if withdrawal > 0 else deposit
                is_debit = withdrawal > 0
        else:
            # First transaction — no previous balance to compare
            # Check if there's an explicit withdrawal and deposit column
            if deposit > 0 and withdrawal == 0:
                is_debit = False
                txn_amount = deposit
            elif withdrawal > 0 and deposit == 0:
                is_debit = True
                txn_amount = withdrawal
            else:
                # Try to use the single amount we parsed
                txn_amount = withdrawal if withdrawal > 0 else deposit
                # Guess based on narration keywords
                nar_upper = txn['narration'].upper()
                is_debit = any(kw in nar_upper for kw in ['UPI/', 'NEFT-', 'WITHDRAWAL', 'CHARGES', 'SMS'])
                if 'B/F' in nar_upper:
                    # Opening balance, skip
                    continue
        
        # Skip zero-amount transactions (like B/F lines with no movement)
        if txn_amount < 0.005:
            # Check if it's a B/F (brought forward) line
            if 'B/F' in txn['narration'].upper():
                continue
            # Also skip if genuinely zero
            continue
        
        debit = txn_amount if is_debit else 0.0
        credit = txn_amount if not is_debit else 0.0
        
        # Clean narration
        cleaned_nar = clean_narration_amount(txn['narration'], amount=txn_amount, balance=balance)
        cleaned_nar = clean_narration(cleaned_nar)
        
        # Reconcile with master ledgers
        if master_ledgers:
            voucher_type, contra_ledger = reconcile_transaction(
                cleaned_nar, is_debit, master_ledgers, amount=txn_amount
            )
        else:
            voucher_type = "Payment" if is_debit else "Receipt"
            contra_ledger = "A"
        
        parsed_transactions.append(Transaction(
            date=txn['date'],
            narration=cleaned_nar,
            debit=debit,
            credit=credit,
            balance=balance,
            reference=txn['chq_no'],
            voucher_type=voucher_type,
            contra_ledger=contra_ledger,
        ))
    
    console.print(f"  📋 Total transactions extracted: [bold green]{len(parsed_transactions)}[/]")
    return parsed_transactions


def _detect_bob_format(pdf_path: str, password: str = None) -> str:
    """
    Detect which BOB statement format the PDF uses.
    Returns: 'columnar' for DD-MM-YY text-based format, 'block' for DD/MM/YYYY format.
    """
    try:
        with pdfplumber.open(pdf_path, password=password or "") as pdf:
            if pdf.pages:
                text = pdf.pages[0].extract_text() or ""
                # Check for columnar format indicators
                if 'WITHDRAWALS' in text.upper() and 'DEPOSITS' in text.upper():
                    return 'columnar'
                if re.search(r'\d{2}-\d{2}-\d{2}\s+\S', text):
                    # Has DD-MM-YY dates followed by text (columnar style)
                    return 'columnar'
    except Exception:
        pass
    return 'block'


def parse_bank_statement_bob(pdf_path: str, password: str = None, master_ledgers: List[str] = None) -> List[Transaction]:
    """Robust line-by-line block parser for Bank of Baroda statement format.
    Auto-detects between columnar (DD-MM-YY) and block (DD/MM/YYYY) formats."""
    
    # Auto-detect format
    fmt = _detect_bob_format(pdf_path, password)
    
    if fmt == 'columnar':
        console.print("  🏦 [bold green]Detected BOB Columnar format (DD-MM-YY)[/]")
        return parse_bank_statement_bob_columnar(pdf_path, password, master_ledgers)
    
    console.print(f"\n[bold]📂 Parsing BOB PDF (Block Mode):[/] {pdf_path}")
    
    all_lines = []
    with pdfplumber.open(pdf_path, password=password or "") as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                all_lines.extend(text.split('\n'))
                
    blocks = []
    current_block = []
    
    footer_keywords = ['PAGE', 'CONTACT-US', 'COMPUTER-GENERATED', 'PAGE 1 OF', 'PAGE 2 OF', 'PAGE 3 OF', 'PAGE 4 OF']
    header_keywords = [
        'MAIN ACCOUNT', 'JOINT ACCOUNT', 'CUSTOMER ID:', 'BRANCH NAME:', 
        'YOUR ACCOUNT STATEMENT', 'STATEMENT OF TRANSACTIONS', 
        'TRAN DATE VALUE DATE', 'ACCOUNT -'
    ]
    
    for line in all_lines:
        line_strip = line.strip()
        if not line_strip:
            continue
            
        if line_strip.startswith('*'):
            continue
            
        date_match = re.match(r'^(\d{2}/\d{2}/\d{4})', line_strip)
        if date_match:
            upper_line = line_strip.upper()
            if any(kw in upper_line for kw in footer_keywords) or any(kw in upper_line for kw in header_keywords):
                continue
                
            rest = line_strip[10:].strip()

            # Check for a closing balance at the end of the line
            has_closing_balance = bool(
                re.search(r'[\d,]+\.\d{2}(?:Cr|Dr|CR|DR)$', line_strip, re.IGNORECASE)
            )

            # A pure value-date continuation: date followed only by an amount
            # (no letters) and no closing balance marker
            rest_is_only_amount = bool(rest) and not re.search(r'[A-Za-z]', rest)

            if has_closing_balance:
                # Genuine new-transaction header line
                if current_block:
                    blocks.append(current_block)
                current_block = [line_strip]
            elif rest_is_only_amount or not rest:
                # Value-date line or bare date: continuation of current block
                if current_block:
                    current_block.append(line_strip)
            else:
                # Date-prefixed narration continuation (no closing balance)
                # → belongs to the CURRENT block, not a new one
                if current_block:
                    current_block.append(line_strip)
                # If there is no current block yet, start one (edge case)
                else:
                    current_block = [line_strip]
        else:
            if current_block:
                upper_line = line_strip.upper()
                if any(kw in upper_line for kw in footer_keywords) or any(kw in upper_line for kw in header_keywords):
                    continue
                current_block.append(line_strip)
                
    if current_block:
        blocks.append(current_block)
        
    transactions_raw = []
    
    for idx, block in enumerate(blocks):
        first_line = block[0]
        date_str = first_line[:10]
        date = parse_date(date_str)
        
        bal_match = re.search(r'([\d,]+\.\d{2}Cr|[\d,]+\.\d{2}Dr|[\d,]+\.\d{2}CR|[\d,]+\.\d{2}DR)$', first_line.strip(), re.IGNORECASE)
        balance = 0.0
        if bal_match:
            balance = clean_amount(bal_match.group(1))
        else:
            all_numbers = re.findall(r'[\d,]+\.\d{2}', first_line)
            if all_numbers:
                balance = clean_amount(all_numbers[-1])
                
        narration_parts = []
        for line_idx, line in enumerate(block):
            line_clean = line
            line_clean = re.sub(r'\b\d{2}/\d{2}/\d{4}\b', '', line_clean)
            if line_idx == 0 and bal_match:
                line_clean = line_clean.replace(bal_match.group(1), '')
            line_clean = line_clean.strip()
            if line_clean:
                narration_parts.append(line_clean)
                
        narration = " ".join(narration_parts)
        
        transactions_raw.append({
            'date': date,
            'narration': narration,
            'balance': balance,
            'block': block
        })

    # Now calculate Debit/Credit using balance transition (reverse chronological order)
    parsed_transactions = []
    for i in range(len(transactions_raw)):
        txn = transactions_raw[i]
        block = txn['block']
        
        all_block_numbers = []
        for line_idx, line in enumerate(block):
            search_line = line
            search_line = re.sub(r'\b\d{2}/\d{2}/\d{4}\b', '', search_line)
            if line_idx == 0:
                bal_match = re.search(r'([\d,]+\.\d{2}Cr|[\d,]+\.\d{2}Dr|[\d,]+\.\d{2}CR|[\d,]+\.\d{2}DR)$', line.strip(), re.IGNORECASE)
                if bal_match:
                    search_line = search_line.replace(bal_match.group(1), '')
                else:
                    nums = re.findall(r'[\d,]+\.\d{2}', search_line)
                    if nums:
                        search_line = search_line.replace(nums[-1], '')
            
            nums = re.findall(r'[\d,]+\.\d{2}', search_line)
            for n in nums:
                all_block_numbers.append(clean_amount(n))
                
        block_amount = all_block_numbers[0] if all_block_numbers else 0.0
        
        if i < len(transactions_raw) - 1:
            next_txn = transactions_raw[i + 1]
            diff = txn['balance'] - next_txn['balance']
            if abs(diff) > 0.01:
                is_debit = diff < 0
                txn_amount = abs(diff)
            else:
                is_debit = block_amount > 0 and len(block) > 1
                txn_amount = block_amount
        else:
            is_debit = False
            has_num_on_later_lines = False
            for line_idx, line in enumerate(block[1:], 1):
                clean_line = re.sub(r'\b\d{2}/\d{2}/\d{4}\b', '', line)
                if re.findall(r'[\d,]+\.\d{2}', clean_line):
                    has_num_on_later_lines = True
                    break
            
            if has_num_on_later_lines:
                is_debit = True
            else:
                is_debit = any(kw in txn['narration'].upper() for kw in ['NEFT', 'RTGS', 'WITHDRAWAL', 'DEBIT', 'CHARGES'])
                
            txn_amount = block_amount
            
        debit = txn_amount if is_debit else 0.0
        credit = txn_amount if not is_debit else 0.0
        
        cleaned_nar = clean_narration_amount(txn['narration'], amount=txn_amount, balance=txn['balance'])
        
        if master_ledgers:
            voucher_type, contra_ledger = reconcile_transaction(cleaned_nar, is_debit, master_ledgers, amount=txn_amount)
        else:
            voucher_type = "Payment" if is_debit else "Receipt"
            contra_ledger = "A"
            
        parsed_transactions.append(Transaction(
            date=txn['date'],
            narration=cleaned_nar,
            debit=debit,
            credit=credit,
            balance=txn['balance'],
            voucher_type=voucher_type,
            contra_ledger=contra_ledger
        ))
        
    return parsed_transactions
