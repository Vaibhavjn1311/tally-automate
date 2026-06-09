import pdfplumber
import re
from typing import List
from transaction import Transaction, clean_amount, parse_date, clean_narration, reconcile_transaction
from rich.console import Console

console = Console()

def parse_bank_statement_bob(pdf_path: str, password: str = None, master_ledgers: List[str] = None) -> List[Transaction]:
    """Robust line-by-line block parser for Bank of Baroda statement format."""
    console.print(f"\n[bold]📂 Parsing BOB PDF (Block Mode):[/] {pdf_path}")
    
    all_lines = []
    with pdfplumber.open(pdf_path, password=password or "") as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                all_lines.extend(text.split('\n'))
    
    # 1. Identify lines starting with two dates (Transaction Headers)
    header_indices = []
    for i, line in enumerate(all_lines):
        # BOB specific: Starts with DD/MM/YYYY DD/MM/YYYY
        if re.match(r'^\d{2}/\d{2}/\d{4}\s+\d{2}/\d{2}/\d{4}', line.strip()):
            header_indices.append(i)
            
    if not header_indices:
        console.print("[bold red]❌ No transaction headers found in BOB PDF.[/]")
        return []

    transactions = []
    
    # Process each header block
    for i in range(len(header_indices)):
        start_idx = header_indices[i]
        # The block for transaction i ends at the next header line
        end_idx = header_indices[i+1] if i+1 < len(header_indices) else len(all_lines)
        
        block = all_lines[start_idx:end_idx]
        header_line = block[0]
        
        # Basic info from header line
        match = re.match(r'^(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+(.*)', header_line.strip())
        if not match: continue
        
        date_str, val_date_str, narration_start = match.groups()
        date = parse_date(date_str)
        
        # Balance BEFORE/AFTER logic:
        # Balance on current header line is actually the closing balance of the PREVIOUS transaction.
        # Balance of CURRENT transaction is on the NEXT header line.
        
        curr_line_amounts = re.findall(r'[\d,]+\.\d{2}Cr?|[\d,]+\.\d{2}Dr?', header_line)
        opening_bal = clean_amount(curr_line_amounts[-1]) if curr_line_amounts else 0.0
        
        closing_bal = 0.0
        if i + 1 < len(header_indices):
            next_header_line = all_lines[header_indices[i+1]]
            next_amounts = re.findall(r'[\d,]+\.\d{2}Cr?|[\d,]+\.\d{2}Dr?', next_header_line)
            if next_amounts:
                closing_bal = clean_amount(next_amounts[-1])
        
        # Transaction amount is found in the block (excluding the balance in the header line)
        txn_amount = 0.0
        for line_idx, line in enumerate(block):
            # Skip the known balance in the first line
            search_line = line
            if line_idx == 0 and curr_line_amounts:
                search_line = line.replace(curr_line_amounts[-1], '')
            
            nums = re.findall(r'[\d,]+\.\d{2}', search_line)
            if nums:
                # The first number found that is not the opening balance
                txn_amount = clean_amount(nums[0])
                break
        
        # If txn_amount is still 0, maybe it's on the header line but we replaced it?
        # Re-check header line for ANY other numbers if txn_amount is 0
        if txn_amount == 0.0 and len(curr_line_amounts) > 1:
            txn_amount = clean_amount(curr_line_amounts[0])

        # Determine type
        # The statement is in REVERSE CHRONOLOGICAL order.
        # current header line balance (opening_bal) is the closing balance of the LATEST transaction.
        # next header line balance (closing_bal) is the closing balance of the PRECEDING transaction.
        # So: if current_bal < preceding_bal -> DECREASE -> Payment (Debit).
        # if current_bal > preceding_bal -> INCREASE -> Receipt (Credit).
        
        is_debit = False
        if closing_bal != 0.0 and opening_bal != 0.0:
            is_debit = opening_bal < closing_bal
            # Use delta for amount accuracy
            txn_amount = abs(opening_bal - closing_bal)
        else:
            # Fallback keywords if one balance is missing
            debit_keywords = ['DR', 'WITHDRAWAL', 'CHARGES', 'PAID', 'BILL', 'NEFT-DR', 'RTGS-DR', 'IMPS-DR']
            full_block_text = " ".join(block).upper()
            is_debit = any(kw in full_block_text for kw in debit_keywords)
            
        debit = txn_amount if is_debit else 0.0
        credit = txn_amount if not is_debit else 0.0
        
        narration = clean_narration(" ".join(block))
        # Remove amounts from narration to keep it clean
        for amt_str in re.findall(r'[\d,]+\.\d{2}Cr?|[\d,]+\.\d{2}Dr?', narration):
            narration = narration.replace(amt_str, '')
        narration = clean_narration(narration)

        if master_ledgers:
            voucher_type, contra_ledger = reconcile_transaction(narration, is_debit, master_ledgers, amount=txn_amount)
        else:
            voucher_type = "Payment" if is_debit else "Receipt"
            contra_ledger = "A"
            
        transactions.append(Transaction(
            date=date,
            narration=narration,
            debit=debit,
            credit=credit,
            balance=closing_bal or opening_bal, # Fallback
            voucher_type=voucher_type,
            contra_ledger=contra_ledger
        ))
        
    return transactions
