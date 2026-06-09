import pdfplumber

def dump_page_2(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[1]
        text = page.extract_text()
        if text:
            lines = text.split('\n')
            for i, line in enumerate(lines[:20]):
                print(f"L{i:03}: {line}")

if __name__ == "__main__":
    import sys
    dump_page_2(sys.argv[1])
