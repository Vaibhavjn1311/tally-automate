import pdfplumber
import re

def inspect_date_range(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                # Find lines with dates in March 2026
                lines = text.split('\n')
                for line in lines:
                    if re.search(r'\d{2}/03/2026', line):
                        print(f"Page {page.page_number}: {line}")

if __name__ == "__main__":
    import sys
    inspect_date_range(sys.argv[1])
