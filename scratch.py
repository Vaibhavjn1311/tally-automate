import pdf_parser
import bob_parser

print("Testing bob_parser:")
try:
    txns = bob_parser.parse_bank_statement_bob("bharat (3).pdf")
    print("Extracted", len(txns), "using bob_parser")
except Exception as e:
    print("Error bob:", e)

print("Testing pdf_parser:")
try:
    txns2 = pdf_parser.parse_bank_statement("bharat (3).pdf")
    print("Extracted", len(txns2), "using pdf_parser")
except Exception as e:
    print("Error pdf:", e)
