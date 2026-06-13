import streamlit as st
import os
import tempfile
import pandas as pd
from pathlib import Path
from rich.console import Console

# Import existing logic
from ledger_extractor import extract_ledger_names
from pdf_parser import parse_bank_statement
from bob_parser import parse_bank_statement_bob
from tally_xml import generate_tally_xml, generate_csv_report

st.set_page_config(page_title="Tally Bank Statement Automator", page_icon="🏦", layout="wide")

def main():
    st.title("🏦 Tally Bank Statement Automator")
    st.markdown("""
    Upload your bank statement and master ledger PDF to generate Tally-compatible XML files.
    """)

    with st.sidebar:
        st.header("Settings")
        bank_option = st.selectbox(
            "Select Bank Format",
            ["Generic / HDFC", "Bank of Baroda"],
            index=0
        )
        
        has_password = st.checkbox("PDF is password protected?")
        password = ""
        if has_password:
            password = st.text_input("Enter PDF Password", type="password")

        st.divider()
        st.header("Date Range Filter")
        st.info("Leave empty to process complete statement")
        from_date = st.date_input("From Date", value=None)
        to_date = st.date_input("To Date", value=None)

    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("1. Bank Statement")
        statement_file = st.file_uploader("Upload Bank Statement PDF", type=["pdf"])
        
    with col2:
        st.subheader("2. Master Ledger")
        master_file = st.file_uploader("Upload Master Ledger PDF", type=["pdf"])

    if st.button("🚀 Process Statement", use_container_width=True):
        if not statement_file or not master_file:
            st.error("Please upload both the Bank Statement and Master Ledger files.")
            return

        with st.spinner("Processing..."):
            try:
                # Use temporary files for processing
                with tempfile.TemporaryDirectory() as tmp_dir:
                    stmt_path = os.path.join(tmp_dir, statement_file.name)
                    mstr_path = os.path.join(tmp_dir, master_file.name)
                    
                    with open(stmt_path, "wb") as f:
                        f.write(statement_file.getbuffer())
                    with open(mstr_path, "wb") as f:
                        f.write(master_file.getbuffer())

                    # Step 0: Extract Ledgers
                    master_ledgers = extract_ledger_names(mstr_path)
                    
                    # Step 1: Parse Statement
                    if bank_option == "Bank of Baroda":
                        transactions = parse_bank_statement_bob(stmt_path, password=password, master_ledgers=master_ledgers)
                    else:
                        transactions = parse_bank_statement(stmt_path, password=password, master_ledgers=master_ledgers)

                    if not transactions:
                        st.error("No transactions could be extracted. Please check the PDF format or password.")
                        return

                    # --- Apply Date Filter ---
                    if from_date or to_date:
                        filtered_transactions = []
                        for t in transactions:
                            # t.date is a datetime object
                            txn_date = t.date.date()
                            
                            include = True
                            if from_date and txn_date < from_date:
                                include = False
                            if to_date and txn_date > to_date:
                                include = False
                                
                            if include:
                                filtered_transactions.append(t)
                        
                        if not filtered_transactions:
                            st.warning("No transactions found in the selected date range.")
                            return
                            
                        transactions = filtered_transactions
                        st.info(f"Filtered to **{len(transactions)}** transactions between {from_date or 'Start'} and {to_date or 'End'}")
                    # -------------------------

                    # Step 2: Display Results
                    st.success(f"Successfully processed {len(transactions)} transactions!")
                    
                    # Create DataFrame for display
                    data = []
                    for t in transactions:
                        data.append({
                            "Date": t.display_date,
                            "Narration": t.narration,
                            "Type": t.voucher_type,
                            "Debit": t.debit,
                            "Credit": t.credit,
                            "Contra Ledger": t.contra_ledger
                        })
                    df = pd.DataFrame(data)
                    st.dataframe(df, use_container_width=True)

                    # Step 3: Generate Files
                    xml_path = os.path.join(tmp_dir, "tally_import.xml")
                    csv_path = os.path.join(tmp_dir, "review_report.csv")
                    
                    # --- Enhanced Auto-detect Bank Ledger ---
                    import pdfplumber
                    import re
                    bank_ledger = "Bank Account"
                    try:
                        with pdfplumber.open(stmt_path, password=password) as pdf:
                            # Extract text from the first few pages
                            text = ""
                            for pg in range(min(3, len(pdf.pages))):
                                text += pdf.pages[pg].extract_text() or ""
                            
                            # 1. Find potential account number patterns (Full or Masked)
                            # Matches: 1234567890 OR 123XXXXXX789 OR XXXXXX7890
                            patterns = [
                                r'\b\d{8,18}\b',                 # Full digits
                                r'\b\d{2,6}[X*]{4,12}\d{2,6}\b', # Masked middle
                                r'\b[X*]{4,12}\d{4,8}\b'         # Masked start
                            ]
                            
                            found_patterns = []
                            for p in patterns:
                                found_patterns.extend(re.findall(p, text))
                                
                            # Also check specifically near "Account No"
                            acc_match = re.search(r'Account\s*No\s*[:.\\-]?\s*([0-9X*]{8,20})', text, re.IGNORECASE)
                            if acc_match:
                                found_patterns.insert(0, acc_match.group(1))
                            
                            # 2. Match found patterns against master ledgers
                            matched_ledger = None
                            for p in found_patterns:
                                # Clean pattern to get just the visible digits
                                visible_parts = [v for v in re.split(r'[X*]+', p) if len(v) >= 3]
                                
                                for led in master_ledgers:
                                    led_upper = led.upper()
                                    # If it's a full number (no X), simple check
                                    if 'X' not in p and '*' not in p:
                                        if p in led_upper.replace(" ", ""):
                                            matched_ledger = led
                                            break
                                    # If it's masked, check if ALL visible parts (>=3 chars) are in the ledger
                                    elif visible_parts and all(part in led_upper.replace(" ", "") for part in visible_parts):
                                        matched_ledger = led
                                        break
                                if matched_ledger:
                                    bank_ledger = matched_ledger
                                    break
                    except Exception as e:
                        st.warning(f"Error during ledger detection: {e}")
                    
                    if bank_ledger != "Bank Account":
                        st.info(f"🏦 Auto-detected Bank Ledger: **{bank_ledger}**")
                    else:
                        st.warning("⚠️ Could not auto-detect bank ledger. Using default: **Bank Account**")
                    # -------------------------------

                    
                    generate_tally_xml(transactions, bank_ledger, xml_path)

                    generate_csv_report(transactions, bank_ledger, csv_path)

                    # Step 4: Download Buttons
                    with open(xml_path, "rb") as f:
                        st.download_button(
                            label="📥 Download Tally XML",
                            data=f,
                            file_name=f"{Path(statement_file.name).stem}_tally.xml",
                            mime="application/xml"
                        )
                    
                    with open(csv_path, "rb") as f:
                        st.download_button(
                            label="📥 Download Review CSV",
                            data=f,
                            file_name=f"{Path(statement_file.name).stem}_review.csv",
                            mime="text/csv"
                        )

            except Exception as e:
                st.error(f"An error occurred: {e}")
                st.exception(e)

if __name__ == "__main__":
    main()
