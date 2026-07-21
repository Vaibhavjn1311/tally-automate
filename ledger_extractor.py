import pdfplumber
import re

def extract_ledger_names(pdf_path: str) -> list[str]:
    """
    Extracts ledger names from a Tally 'List of Ledgers' PDF.
    Uses horizontal indentation (x0 coordinate) and a group exclusion list
    to accurately identify and return only actual ledgers, matching Tally's count exactly.
    """
    GROUPS_TO_EXCLUDE = {
        'assets', 'liabilities', 'expenses', 'income',
        'current assets', 'fixed assets', 'investments', 'misc. expenses (asset)',
        'branch / divisions', 'capital account', 'current liabilities', 'loans (liability)',
        'suspense a/c', 'profit & loss a/c', 'direct expenses', 'indirect expenses',
        'purchase accounts', 'direct incomes', 'indirect incomes', 'sales accounts',
        'bank accounts', 'cash-in-hand', 'deposits (asset)', 'loans & advances (asset)',
        'stock-in-hand', 'sundry debtors', 'reserves & surplus', 'duties & taxes',
        'provisions', 'sundry creditors', 'bank od a/c', 'secured loans', 'unsecured loans',
        'profit & loss'
    }

    ledgers = []
    active_groups = {}  # Tracks group hierarchy based on x0

    try:
        with pdfplumber.open(pdf_path) as pdf:
            # Detect company name from the very first line of the first page
            company_name = ""
            first_page_text = pdf.pages[0].extract_text()
            if first_page_text:
                company_name = first_page_text.split('\n')[0].strip()

            for page in pdf.pages:
                words = page.extract_words()
                if not words:
                    continue

                # Group words into lines based on top coordinate rounded to nearest integer
                lines_dict = {}
                for w in words:
                    top = round(w['top'])
                    if top not in lines_dict:
                        lines_dict[top] = []
                    lines_dict[top].append(w)

                for top in sorted(lines_dict.keys()):
                    line_words = sorted(lines_dict[top], key=lambda w: w['x0'])
                    line_text = ' '.join([w['text'] for w in line_words]).strip()
                    if not line_text:
                        continue

                    # Leftmost coordinate
                    first_x0 = line_words[0]['x0']

                    # Skip headers and metadata usually placed on the far right
                    if first_x0 > 150.0:
                        continue
                        
                    # Update active groups based on x0 indentation
                    active_groups[first_x0] = line_text
                    keys_to_remove = [k for k in active_groups if k > first_x0]
                    for k in keys_to_remove:
                        del active_groups[k]
                        
                    # Determine parent groups for the current line
                    parents = [active_groups[k].lower() for k in active_groups if k < first_x0]

                    # Skip company name and other header/footer metadata
                    if company_name and line_text.lower() == company_name.lower():
                        continue
                    if 'Virendr Kumar' in line_text or 'List of Ledgers' in line_text or 'Page ' in line_text:
                        continue
                    if 'continued' in line_text.lower():
                        continue
                    if re.search(r'\d+-[A-Za-z]+-\d+ to \d+-[A-Za-z]+-\d+', line_text):
                        continue
                    if 'Group(s) and' in line_text or 'Ledger(s)' in line_text:
                        continue

                    # Ledgers are at x0 >= 40.0. Top/Level 1 group headings are at x0 < 40.0
                    if first_x0 < 40.0:
                        continue

                    # If it's a known group at x0=49.0, exclude it
                    if line_text.lower() in GROUPS_TO_EXCLUDE:
                        continue
                        
                    # Skip ledgers under Purchase Accounts or Sales Accounts
                    if any(p in {'purchase accounts', 'sales accounts'} for p in parents):
                        continue

                    # Skip short trash lines
                    if len(line_text) < 2 and line_text != 'A':
                        continue

                    # Skip standard conjunctions/prepositions extracted as independent lines
                    if line_text.lower() in {'and', 'to', 'for', 'of', 'in', 'on', 'at', 'by', 'an', 'a'}:
                        continue

                    ledgers.append(line_text)

    except Exception as e:
        print(f"Error extracting ledgers: {e}")

    return sorted(list(set(ledgers)))


def detect_bank_ledger(pdf_path: str, master_ledgers: list[str], password: str = None) -> str:
    """
    Detects the bank account number from the statement PDF
    and matches it to one of the master ledgers.
    """
    if not master_ledgers:
        return "Bank Account"
        
    try:
        with pdfplumber.open(pdf_path, password=password or "") as pdf:
            text = pdf.pages[0].extract_text() or ""
            
            acc_match = re.search(r'Account\s*(?:No)?\s*[:.\-]?\s*([0-9Xx\*\-]+)', text, re.IGNORECASE)
            potential_acc_nos = []
            if acc_match:
                potential_acc_nos.append(acc_match.group(1).strip())
                
            masked_matches = re.findall(r'\b[0-9Xx\*\-]{10,18}\b', text)
            for m in masked_matches:
                if m not in potential_acc_nos:
                    potential_acc_nos.append(m)
                    
            standard_digits = re.findall(r'\b\d{10,16}\b', text)
            for d in standard_digits:
                if d not in potential_acc_nos:
                    potential_acc_nos.append(d)
            
            for raw_acc in potential_acc_nos:
                matches = []
                acc_clean = raw_acc.upper().replace('X', r'\d').replace('*', r'\d').replace('-', '')
                
                try:
                    regex = re.compile(acc_clean)
                    for led in master_ledgers:
                        led_clean = re.sub(r'[^0-9A-Za-z]', '', led)
                        if regex.search(led_clean):
                            matches.append(led)
                except Exception:
                    pass
                    
                if not matches:
                    digits_only = ''.join(re.findall(r'\d+', raw_acc))
                    if len(digits_only) >= 6:
                        start_dig = digits_only[:3]
                        end_dig = digits_only[-3:]
                        for led in master_ledgers:
                            led_digs = ''.join(re.findall(r'\d+', led))
                            if led_digs.startswith(start_dig) and led_digs.endswith(end_dig):
                                matches.append(led)
                                
                if not matches:
                    if digits_only:
                        for led in master_ledgers:
                            led_digs = ''.join(re.findall(r'\d+', led))
                            if led_digs and (digits_only in led_digs or led_digs in digits_only):
                                matches.append(led)
            
                if matches:
                    # Sort matches: prefer those starting with 'BOB ' (with space) or without '-'
                    # to avoid choosing header metadata like 'BOB -11960200000154'
                    matches_sorted = sorted(list(set(matches)), key=lambda x: (not x.startswith('BOB '), '-' in x, len(x)))
                    return matches_sorted[0]
                            
    except Exception as e:
        print(f"Error detecting bank ledger: {e}")
        
    return "Bank Account"
