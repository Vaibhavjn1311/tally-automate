import pdfplumber
import sys

def inspect_pdf(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        print(f"Total Pages: {len(pdf.pages)}")
        # Check first page
        page = pdf.pages[0]
        # Just extract text to see if it's selectable/readable
        print(f"Page 1 Text Sample:\n{page.extract_text()[:500]}")
        # Check if tables are detected
        tables = page.extract_tables()
        print(f"\nNumber of tables detected on Page 1: {len(tables)}")
        if tables:
            print(f"First table headers (if applicable): {tables[0][0]}")

if __name__ == "__main__":
    inspect_pdf(sys.argv[1])
