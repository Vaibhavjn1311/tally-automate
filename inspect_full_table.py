import pdfplumber

def inspect_full_table(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        tables = page.extract_tables()
        if tables:
            for i, row in enumerate(tables[0]):
                print(f"Row {i}: {row}")
                if i > 20: break

if __name__ == "__main__":
    import sys
    inspect_full_table(sys.argv[1])
