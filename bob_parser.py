import pdfplumber
import re
from typing import List
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

def parse_bank_statement_bob(pdf_path: str, password: str = None, master_ledgers: List[str] = None) -> List[Transaction]:
    """Robust line-by-line block parser for Bank of Baroda statement format."""
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
