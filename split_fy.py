import os
import csv
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime

def parse_csv_date(date_str):
    try:
        return datetime.strptime(date_str.strip(), "%d-%m-%Y")
    except:
        return None

def split_csv_by_fy(file_path):
    print(f"Processing CSV {file_path}...")
    if not os.path.exists(file_path):
        return
        
    fy2526_rows = []
    fy2627_rows = []
    header = None
    
    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            if not row: continue
            date_obj = parse_csv_date(row[0])
            if date_obj:
                if date_obj <= datetime(2026, 3, 31):
                    fy2526_rows.append(row)
                else:
                    fy2627_rows.append(row)
    
    base_name = os.path.splitext(file_path)[0]
    
    def save_csv(rows, suffix):
        if not rows: return
        output_path = f"{base_name}_{suffix}.csv"
        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)
        print(f"Saved: {output_path}")

    save_csv(fy2526_rows, "FY25-26")
    save_csv(fy2627_rows, "FY26-27")

def split_xml_by_fy(file_path):
    print(f"Processing XML {file_path}...")
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
    except Exception as e:
        print(f"Error parsing {file_path}: {e}")
        return

    vouchers = list(tree.iter("VOUCHER"))
    if not vouchers:
        print(f"No VOUCHER found in {file_path}")
        return

    fy2526_vouchers = []
    fy2627_vouchers = []

    for v in vouchers:
        date_elem = v.find("DATE")
        if date_elem is None or not date_elem.text:
            continue
            
        date_str = date_elem.text.strip()
        if date_str <= "20260331":
            fy2526_vouchers.append(v)
        else:
            fy2627_vouchers.append(v)

    base_name = os.path.splitext(file_path)[0]
    
    def save_xml(vouchers_list, suffix):
        if not vouchers_list: return
        new_envelope = ET.Element("ENVELOPE")
        header = ET.SubElement(new_envelope, "HEADER")
        ET.SubElement(header, "TALLYREQUEST").text = "Import Data"
        body = ET.SubElement(new_envelope, "BODY")
        imp = ET.SubElement(body, "IMPORTDATA")
        rdesc = ET.SubElement(imp, "REQUESTDESC")
        ET.SubElement(rdesc, "REPORTNAME").text = "Vouchers"
        sv = ET.SubElement(rdesc, "STATICVARIABLES")
        ET.SubElement(sv, "SVCURRENTCOMPANY").text = "##SVCURRENTCOMPANY"
        rdata = ET.SubElement(imp, "REQUESTDATA")
        tmsg = ET.SubElement(rdata, "TALLYMESSAGE")
        tmsg.set("xmlns:UDF", "TallyUDF")
        for v in vouchers_list:
            tmsg.append(v)
        xml_str = ET.tostring(new_envelope, encoding='unicode')
        pretty = minidom.parseString(xml_str).toprettyxml(indent="  ")
        lines = pretty.split('\n')
        if lines[0].startswith('<?xml'): lines = lines[1:]
        output_path = f"{base_name}_{suffix}.xml"
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write('\n'.join(lines))
        print(f"Saved: {output_path}")

    save_xml(fy2526_vouchers, "FY25-26")
    save_xml(fy2627_vouchers, "FY26-27")

for root_dir, dirs, files in os.walk("tally_output"):
    for file in files:
        file_path = os.path.join(root_dir, file)
        if file.endswith("_tally.xml") and "_FY" not in file:
            split_xml_by_fy(file_path)
        elif file.endswith("_review.csv") and "_FY" not in file:
            split_csv_by_fy(file_path)
