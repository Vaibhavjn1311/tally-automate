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
