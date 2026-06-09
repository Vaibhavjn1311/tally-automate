import pdfplumber

def find_header(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        # Use simple table extraction to find rows
        tables = page.extract_tables()
        for table in tables:
            for row in table:
                # Look for typical BOB header keywords
                row_str = ' '.join([str(c) for c in row if c]).lower()
                if 'date' in row_str and 'debit' in row_str:
                    print(f"Found header row: {row}")
                    return row
    print("Header not found.")

if __name__ == "__main__":
    import sys
    find_header(sys.argv[1])
