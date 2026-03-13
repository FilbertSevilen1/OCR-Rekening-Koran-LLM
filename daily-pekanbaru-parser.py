import os
import re
import pdfplumber
import pandas as pd

# Precise boundaries for Daily Pekanbaru based on initial inspection
COL_BOUNDARIES = {
    "voucher": (0.0, 100.0),
    "coa": (100.0, 220.0),
    "desc": (220.0, 410.0),
    "ref": (410.0, 520.0),
    "debit": (520.0, 645.0),
    "credit": (645.0, 740.0),
    "balance": (740.0, 1000.0)
}

def clean_num(x):
    if not x: return None
    # Handle negative signs and commas
    s = str(x).replace(",", "").replace(" ", "").strip()
    try:
        return float(s)
    except:
        return None

def fmt_currency(x):
    if x is None or (isinstance(x, float) and pd.isna(x)) or x == "": 
        return None
    try:
        return float(str(x).replace(",", ""))
    except:
        return None

def extract_page(page, last_page=False):
    words = page.extract_words()
    
    # Header boundary
    header_bottom = 0
    for w in words:
        if w['top'] < 150:
            content = w["text"].upper()
            if any(k in content for k in ["VOUCHER", "CHART", "DESCRIPTION", "REFERENCE"]):
                header_bottom = max(header_bottom, w["bottom"])
    
    if header_bottom == 0: header_bottom = 85.0

    # Footer boundary (look for horizontal lines below header)
    footer_top = page.height
    h_lines = [l for l in page.lines if l["width"] > 100 and l["top"] > header_bottom]
    if h_lines:
        # Skip lines that are too close to the header (likely table border)
        actual_footer_lines = [l for l in h_lines if l["top"] > header_bottom + 50]
        if actual_footer_lines:
            footer_top = min([l["top"] for l in actual_footer_lines])
    
    # Filter words
    valid_words = [w for w in words if header_bottom + 1 < w["top"] < footer_top]
    valid_words.sort(key=lambda x: (x["top"], x["x0"]))

    # Group into lines (3pt tolerance)
    line_groups = []
    if valid_words:
        cur = [valid_words[0]]
        for i in range(1, len(valid_words)):
            w = valid_words[i]
            if abs(w["top"] - cur[-1]["top"]) < 3.0: 
                cur.append(w)
            else:
                line_groups.append(cur)
                cur = [w]
        line_groups.append(cur)

    rows = []
    for line in line_groups:
        r = {k: "" for k in COL_BOUNDARIES}
        r["_page"] = page.page_number
        for w in line:
            x0 = w["x0"]
            for k, (b_start, b_end) in COL_BOUNDARIES.items():
                if b_start <= x0 < b_end: r[k] += " " + w["text"]
        for k, v in r.items():
            if isinstance(v, str): r[k] = v.strip()
        rows.append(r)
    return rows

def parse_pdf(path):
    print(f"[*] Analyzing PDF: {path}")
    all_raw_rows = []
    with pdfplumber.open(path) as pdf:
        num_pages = len(pdf.pages)
        print(f"[*] Total Pages: {num_pages}")
        for i, page in enumerate(pdf.pages):
            rows = extract_page(page, last_page=(i == num_pages - 1))
            all_raw_rows.extend(rows)
            print(f"[*] Page {i+1} extracted: {len(rows)} lines")

    final_txs = []
    current_tx = None
    
    print("[*] Grouping lines into transactions...")
    for row in all_raw_rows:
        # Anchor: Balance at the end of the row marks a new transaction
        is_new_tx = clean_num(row["balance"]) is not None
        
        if is_new_tx:
            if current_tx: final_txs.append(current_tx)
            current_tx = row.copy()
        else:
            if current_tx and row["_page"] == current_tx["_page"]:
                # Append multi-line fields
                if row["desc"]: 
                    current_tx["desc"] = (current_tx["desc"] + " " + row["desc"]).strip()
                if row["ref"]:
                    current_tx["ref"] = (current_tx["ref"] + " " + row["ref"]).strip()
                
                # Fill other fields if they were missing on the first line
                for k in ["voucher", "coa", "debit", "credit"]:
                    if not current_tx[k] and row[k]: 
                        current_tx[k] = row[k]
    
    if current_tx:
        final_txs.append(current_tx)

    df = pd.DataFrame(final_txs)
    if df.empty: 
        print("[!] No transactions found.")
        return pd.DataFrame()

    # Filter out header/summary rows that might have balances but aren't transactions
    # e.g. "Beginning Balance"
    df = df[~df["desc"].str.contains("Beginning Balance", case=False, na=False)]

    df["debit_clean"] = df["debit"].apply(clean_num)
    df["credit_clean"] = df["credit"].apply(clean_num)
    df["balance_clean"] = df["balance"].apply(clean_num)

    print(f"[*] Total detected transactions: {len(df)}")
    
    out = pd.DataFrame({
        "Voucher No.": df["voucher"],
        "Chart of Account": df["coa"],
        "Description": df["desc"],
        "Reference No.": df["ref"],
        "Debit": df["debit_clean"].apply(fmt_currency),
        "Credit": df["credit_clean"].apply(fmt_currency),
        "Balance": df["balance_clean"].apply(fmt_currency)
    })
    
    return out.fillna("")

def export_excel(df, path):
    if not df.empty:
        try:
            df.to_excel(path, index=False)
            print(f"[+] Exported to {path}")
        except:
            alt_path = path.replace(".xlsx", "_1.xlsx")
            df.to_excel(alt_path, index=False)
            print(f"[+] File was open. Exported to {alt_path}")

if __name__ == "__main__":
    print("[*] Starting Daily Pekanbaru Parser...")
    pdf_file = os.path.join("Templates", "Daily pekanbaru tgl.030326.pdf")
    if os.path.exists(pdf_file):
        df_result = parse_pdf(pdf_file)
        export_excel(df_result, "daily_pekanbaru_output.xlsx")
    else:
        print(f"[!] File not found: {pdf_file}")
