import pdfplumber
import sys

def check_pdf(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        print(f"Number of pages: {len(pdf.pages)}")

if __name__ == "__main__":
    check_pdf(sys.argv[1])
