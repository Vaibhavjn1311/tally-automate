import pdfplumber
import re

def inspect_lines(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        # Inspect a few pages where we know the transactions are
        for page in pdf.pages[1:5]:
            text = page.extract_text()
            if text:
                print(f"--- Page {page.page_number} ---")
                for line in text.split('\n'):
                    # Look for lines that contain NEFT
                    if 'NEFT' in line:
                        print(f"Line: {line}")

if __name__ == "__main__":
    import sys
    inspect_lines(sys.argv[1])
