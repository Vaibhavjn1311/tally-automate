import pdfplumber

def inspect_table_structure(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        # Check a middle page, as table layouts might be more complex
        page = pdf.pages[5]
        # Extract tables with a very loose strategy to see the raw cells
        tables = page.extract_tables({
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
        })
        
        if tables:
            print(f"Table on page 6:")
            for i, row in enumerate(tables[0]):
                print(f"Row {i}: {row}")
                if i > 10: break # Just look at the first 10 rows

if __name__ == "__main__":
    import sys
    inspect_table_structure(sys.argv[1])
