"""
Transaction data model and cleaning utilities.
Handles parsing, validation, and normalization of bank statement transactions.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Tuple
import re


@dataclass
class Transaction:
    """Represents a single bank statement transaction."""
    date: datetime
    narration: str
    debit: float  # Money going out (withdrawal)
    credit: float  # Money coming in (deposit)
    balance: float
    reference: str = ""
    voucher_type: str = ""  # Payment, Receipt, or Contra
    contra_ledger: str = ""  # The other side of the entry

    @property
    def amount(self) -> float:
        """Returns the transaction amount (positive value)."""
        return self.debit if self.debit > 0 else self.credit

    @property
    def is_debit(self) -> bool:
        """True if this is a debit (withdrawal/payment) transaction."""
        return self.debit > 0

    @property
    def is_credit(self) -> bool:
        """True if this is a credit (deposit/receipt) transaction."""
        return self.credit > 0

    @property
    def tally_date(self) -> str:
        """Returns date in Tally format: YYYYMMDD"""
        return self.date.strftime("%Y%m%d")

    @property
    def display_date(self) -> str:
        """Returns date in display format: DD-MM-YYYY"""
        return self.date.strftime("%d-%m-%Y")

    def __post_init__(self):
        """Auto-assign voucher type based on transaction nature."""
        if not self.voucher_type:
            if self.is_debit:
                self.voucher_type = "Payment"
            elif self.is_credit:
                self.voucher_type = "Receipt"
        # Ensure it's not empty, default to Payment if all else fails
        if not self.voucher_type:
            self.voucher_type = "Payment"



def clean_amount(amount_str: str) -> float:
    """Clean and parse an amount string to float."""
    if not amount_str or str(amount_str).strip() in ('', '-', 'None', 'nan', 'NaN'):
        return 0.0

    amount_str = str(amount_str).strip()
    amount_str = re.sub(r'[₹$€£,]', '', amount_str)
    amount_str = re.sub(r'\s*(Cr|Dr|CR|DR|cr|dr)\s*$', '', amount_str)
    amount_str = re.sub(r'[^\d.\-]', '', amount_str)

    try:
        return abs(float(amount_str))
    except (ValueError, TypeError):
        return 0.0


def parse_date(date_str: str) -> Optional[datetime]:
    """Parse a date string in various Indian bank statement formats."""
    if not date_str or str(date_str).strip() in ('', '-', 'None', 'nan'):
        return None

    date_str = str(date_str).strip()
    formats = [
        "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y",
        "%d %b %Y", "%d-%b-%Y", "%d/%b/%Y", "%d %B %Y", "%d-%B-%Y",
        "%Y-%m-%d", "%m/%d/%Y", "%d %b %y", "%d-%b-%y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def clean_narration(narration: str) -> str:
    """Clean up the narration text."""
    if not narration or str(narration).strip() in ('', 'None', 'nan'):
        return "No Description"

    narration = str(narration).strip()
    narration = re.sub(r'\s+', ' ', narration)
    narration = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', narration)
    return narration.strip()


def guess_contra_ledger(narration: str, is_debit: bool) -> str:
    """Fallback guesser when no master ledgers are provided."""
    narration_lower = narration.lower()
    # If no specific match is found, default to 'A' (Suspense Account)
    suspense_ledger = 'A'
    
    # These are potential UPI matches. If we are unsure, we should NOT guess these.
    # We remove them from automatic guessing if we want to force suspense.
    patterns = {
        # r'upi|unified payment|phonepe|gpay|google pay|paytm|bhim': 'UPI Transactions', # Disabled to prevent incorrect guessing
        r'neft|rtgs|imps|fund transfer|ft\-': 'Bank Transfer',
        r'atm|cash withdrawal|atm/cash': 'Cash',
        r'chq|cheque|chq\.?\s*no|clg': 'Cheque Transactions',
        r'salary|sal\b|payroll': 'Salary',
        r'interest|int\b|int\.': 'Interest' if is_debit else 'Bank Interest Received',
        r'charges|service charge|sms charge|annual fee|maintenance|maint\b': 'Bank Charges',
        r'emi|loan|equated monthly': 'EMI / Loan Repayment',
        r'insurance|lic|policy|premium': 'Insurance Premium',
        r'tax|tds|gst|income tax': 'Tax Payments',
        r'pos|point of sale|card|swipe': 'POS Transactions',
        r'si\b|standing instruction|auto debit|nach|mandate|ecs': 'Auto Debit / NACH',
        r'dividend': 'Dividend Received',
    }
    for pattern, ledger in patterns.items():
        if re.search(pattern, narration_lower):
            return ledger
    return suspense_ledger


def levenshtein_distance(s1: str, s2: str) -> int:
    """Calculates Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def phonetic_normalize(s: str) -> str:
    """Normalizes string phonetically to handle common spelling variations in Indian names."""
    s = s.upper()
    s = re.sub(r'[^A-Z0-9]', '', s)
    s = s.replace('SH', 'S')
    s = s.replace('CH', 'C')
    s = s.replace('EE', 'I')
    s = s.replace('OO', 'U')
    s = s.replace('W', 'V')
    s = s.replace('Y', 'I')
    s = s.replace('Z', 'J')
    # Remove consecutive duplicate letters
    s = re.sub(r'([A-Z0-9])\1+', r'\1', s)
    return s


def token_similarity(t1: str, t2: str) -> float:
    """Computes similarity score between two normalized tokens."""
    if t1 == t2:
        return 1.0
    if len(t1) < 3 or len(t2) < 3:
        return 0.0
    
    # Substring matching for longer tokens
    if t1 in t2 or t2 in t1:
        overlap_len = min(len(t1), len(t2))
        if overlap_len >= 4:
            return 0.9
            
    # Edit distance similarity
    dist = levenshtein_distance(t1, t2)
    max_len = max(len(t1), len(t2))
    return 1.0 - (dist / max_len)


def reconcile_transaction(narration: str, is_debit: bool, master_ledgers: list, amount: float = 0.0) -> Tuple[str, str]:
    """
    Reconciles a transaction against the Tally Master ledgers list.
    Only maps to ledgers present in the list, falling back to 'A' (suspense ledger) if unmatched.
    """
    default_voucher = 'Payment' if is_debit else 'Receipt'
    
    # Resolve suspense ledger 'A'
    a_ledger = next((l for l in master_ledgers if l == 'A' or l.upper() == 'A'), 'A')
    
    if not master_ledgers:
        return default_voucher, a_ledger

    nar_upper = narration.upper()
    nar_compact = re.sub(r'[^A-Z0-9]', '', nar_upper)

    # 1. Cash Check -> Contra Voucher
    is_cash = False
    if any(kw in nar_compact for kw in ['CASHWDL', 'CASHDEP', 'CASHPOSIT', 'CASHWITH', 'BYCASH', 'TOCASH', 'ATMCASH', 'ATMWDL', 'CASH8845SELF', 'CASHDEPOSIT', 'CASHDEPOSITBY', 'ATMTXN', 'ATMDEBIT']):
        is_cash = True
    elif 'CASH' in nar_compact:
        is_cash = True
    elif is_debit and 'SELF' in nar_compact:
        is_cash = True

    if is_cash:
        cash_ledger = next((l for l in master_ledgers if l.upper() == 'CASH'), None)
        if cash_ledger:
            return 'Contra', cash_ledger

    # 2. PhonePe / UPI Check -> Map to 'Phone Pyee' ONLY if it is a strong match. 
    # To prevent over-matching, we can make this more restrictive or remove automatic mapping if desired.
    # The user specifically requested not to default payments to these ledgers.
    # We will ONLY map if narration contains 'PHONEPE' OR 'UPI', but restrict it to non-payment types if necessary,
    # or just trust the master ledger check.
    # Given the request, we should make it harder to hit 'Phone Pyee' for Payments.
    
    # Revised logic: Do not auto-map to 'Phone Pyee' for Payments unless very clear.
    if is_debit and any(kw in nar_compact for kw in ['PHONEPE', 'PHONEPAY', 'PHONEPYEE']):
        # If it's a payment, force to suspense 'A' unless we have a strong match later
        pass
    elif any(kw in nar_compact for kw in ['PHONEPE', 'PHONEPAY', 'PHONEPYEE', 'PAYMENTFROMPH', 'TERMINAL1', 'CARDSSETTL', 'UPSETTL', 'UPISETTL', 'DDS615']):
        phone_led = next((l for l in master_ledgers if l.upper() in ['PHONE PYEE', 'PHONEPE', 'PHONE PAY', 'PHONEPE PRIVATELIMI']), None)
        if phone_led:
            return default_voucher, phone_led

    # 3. Enhanced Ledger Matching Logic
    IGNORE_WORDS = {
        'JOBAT', 'AMBUA', 'INDORE', 'RATLAM', 'KHATTALI', 'UDHEGHAD', 
        'KASBA', 'AJNAR', 'MOTAUMAR', 'DAHOD', 'RANAPUR', 'PETLAWAD', 
        'LTD', 'LIMITED', 'PVT', 'PRIVATE', 'SERVICES', 'STORES', 'KIRANA',
        'CGST', 'SGST', 'IGST', 'GST', 'G S T', 'GUJRAT', 'GUJARAT', 'MP', 'M P',
        'INDIA', 'BHABRA', 'ALIRAJPUR', 'UDAIGARH', 'NANPUR', 'KUKSHI', 'DHAMNOD',
        'PAY', 'UPI', 'NEFT', 'RTGS', 'IMPS', 'TRANSFER', 'VOUCHER', 'PAYMENT',
        'HDFC', 'SBIN', 'BKID', 'BARB', 'UTIB', 'ICIC', 'KKBK', 'YESB', 'AIRP', 'FINO',
        'ABH', 'HDF', 'SBI', 'BKID0008845', 'BARB0JOBATX', 'ASSOCIATES', 'ACCOUNT', 'AC', 'COMP', 'CO',
        'BANK', 'BANKS', 'A/C', 'ACCOUNTS', 'LEDGER', 'LEDGERS', 'STATE', 'CENTRAL', 'OF', 'THE', 'AND'
    }

    # Tokenize narration
    nar_tokens = [w for w in re.split(r'[^A-Z0-9]', nar_upper) if w]
    nar_norms = [phonetic_normalize(w) for w in nar_tokens]

    best_match = None
    max_score = 0

    for led in master_ledgers:
        if led.upper() in ['A', 'CASH', 'PROFIT & LOSS', 'PROFIT & LOSS A/C']:
            continue

        led_upper = led.upper()
        # Tokenize ledger name - exclude digit-only and ignored words
        led_tokens = [
            w for w in re.split(r'[^A-Z0-9]', led_upper) 
            if w and w not in IGNORE_WORDS and len(w) >= 3 and not w.isdigit()
        ]
        if not led_tokens:
            continue

        match_count = 0
        total_len = 0

        for lt in led_tokens:
            lt_norm = phonetic_normalize(lt)
            
            # Find best matching token in narration
            best_sim = 0.0
            for nt_norm in nar_norms:
                sim = token_similarity(lt_norm, nt_norm)
                if sim > best_sim:
                    best_sim = sim
            
            if best_sim >= 0.75:
                match_count += 1
                total_len += len(lt)

        if match_count > 0:
            match_ratio = match_count / len(led_tokens)
            score = (match_ratio * 100) + total_len

            if score > max_score:
                max_score = score
                best_match = led

    if best_match and max_score >= 80:
        return default_voucher, best_match

    # 4. Default Fallback -> Ledger "A" (Suspense)
    return default_voucher, a_ledger


