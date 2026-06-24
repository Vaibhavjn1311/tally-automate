"""
Tally XML Generator.
Generates TallyPrime-compatible XML files for importing bank vouchers.
"""

import xml.etree.ElementTree as ET
from xml.dom import minidom
from typing import List
from transaction import Transaction
from rich.console import Console
import csv

console = Console()


def create_voucher_element(txn: Transaction, bank_ledger: str, vnum: int) -> ET.Element:
    """Create a VOUCHER XML element for a transaction."""
    v = ET.Element("VOUCHER")
    v.set("REMOTEID", f"BankImport-{vnum}")
    v.set("VCHTYPE", txn.voucher_type)
    v.set("ACTION", "Create")
    v.set("OBJVIEW", "Accounting Voucher View")

    ET.SubElement(v, "DATE").text = txn.tally_date
    ET.SubElement(v, "VOUCHERTYPENAME").text = txn.voucher_type

    if txn.reference:
        ET.SubElement(v, "REFERENCE").text = txn.reference
    ET.SubElement(v, "NARRATION").text = txn.narration
    ET.SubElement(v, "EFFECTIVEDATE").text = txn.tally_date

    amt = txn.amount

    if txn.voucher_type == "Payment":
        _add_entry(v, txn.contra_ledger, "Yes", f"-{amt:.2f}")
        _add_entry(v, bank_ledger, "No", f"{amt:.2f}")
    elif txn.voucher_type == "Receipt":
        _add_entry(v, bank_ledger, "Yes", f"-{amt:.2f}")
        _add_entry(v, txn.contra_ledger, "No", f"{amt:.2f}")
    elif txn.voucher_type == "Contra":
        if txn.is_credit:  # Cash Deposit (Bank is Debited)
            _add_entry(v, bank_ledger, "Yes", f"-{amt:.2f}")
            _add_entry(v, txn.contra_ledger, "No", f"{amt:.2f}")
        else:  # Cash Withdrawal (Bank is Credited)
            _add_entry(v, txn.contra_ledger, "Yes", f"-{amt:.2f}")
            _add_entry(v, bank_ledger, "No", f"{amt:.2f}")

    return v


def _add_entry(parent, ledger, is_positive, amount_str):
    entry = ET.SubElement(parent, "ALLLEDGERENTRIES.LIST")
    ET.SubElement(entry, "LEDGERNAME").text = ledger
    ET.SubElement(entry, "ISDEEMEDPOSITIVE").text = is_positive
    ET.SubElement(entry, "AMOUNT").text = amount_str


def generate_tally_xml(transactions: List[Transaction], bank_ledger: str, output_path: str) -> str:
    """Generate a complete Tally XML file from transactions."""
    envelope = ET.Element("ENVELOPE")
    header = ET.SubElement(envelope, "HEADER")
    ET.SubElement(header, "TALLYREQUEST").text = "Import Data"

    body = ET.SubElement(envelope, "BODY")
    imp = ET.SubElement(body, "IMPORTDATA")
    rdesc = ET.SubElement(imp, "REQUESTDESC")
    ET.SubElement(rdesc, "REPORTNAME").text = "Vouchers"
    sv = ET.SubElement(rdesc, "STATICVARIABLES")
    ET.SubElement(sv, "SVCURRENTCOMPANY").text = "##SVCURRENTCOMPANY"

    rdata = ET.SubElement(imp, "REQUESTDATA")
    tmsg = ET.SubElement(rdata, "TALLYMESSAGE")
    tmsg.set("xmlns:UDF", "TallyUDF")

    counts = {"Payment": 0, "Receipt": 0, "Contra": 0}
    for idx, txn in enumerate(transactions, 1):
        tmsg.append(create_voucher_element(txn, bank_ledger, idx))
        counts[txn.voucher_type] = counts.get(txn.voucher_type, 0) + 1

    xml_str = ET.tostring(envelope, encoding='unicode')
    pretty = minidom.parseString(xml_str).toprettyxml(indent="  ")
    lines = pretty.split('\n')
    if lines[0].startswith('<?xml'):
        lines = lines[1:]

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('\n'.join(lines))

    console.print(f"\n  [bold green]✅ Tally XML generated:[/] {output_path}")
    console.print(f"     📤 Payments: [red]{counts['Payment']}[/]  📥 Receipts: [green]{counts['Receipt']}[/]  🔄 Contra: [blue]{counts['Contra']}[/]")
    console.print(f"     📊 Total: [bold]{len(transactions)}[/] vouchers")
    return output_path


def generate_csv_report(transactions: List[Transaction], bank_ledger: str, output_path: str) -> str:
    """Generate a CSV report for review before importing."""
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['Date', 'Narration', 'Voucher Type', 'Debit', 'Credit', 'Amount', 'Bank Ledger', 'Contra Ledger', 'Balance', 'Reference'])
        for txn in transactions:
            w.writerow([
                txn.display_date, txn.narration, txn.voucher_type,
                f"{txn.debit:.2f}" if txn.debit > 0 else "",
                f"{txn.credit:.2f}" if txn.credit > 0 else "",
                f"{txn.amount:.2f}", bank_ledger, txn.contra_ledger,
                f"{txn.balance:.2f}" if txn.balance > 0 else "", txn.reference,
            ])
    console.print(f"  [bold green]📄 CSV report generated:[/] {output_path}")
    return output_path
