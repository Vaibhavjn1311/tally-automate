import pdfplumber

def dump_raw_text(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        text = page.extract_text()
        if text:
            lines = text.split('\n')
            for i, line in enumerate(lines[:100]):
                print(f"L{i:03}: {line}")

if __name__ == "__main__":
    import sys
    dump_raw_text(sys.argv[1])
