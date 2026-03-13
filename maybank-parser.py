import os
import re
import pdfplumber
import pandas as pd
from openai import OpenAI
from config import AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT_NAME

# Initialize OpenAI client
client = OpenAI(
    base_url=AZURE_OPENAI_ENDPOINT,
    api_key=AZURE_OPENAI_API_KEY
)

DATE_PATTERN = r"\d{2}\s+[A-Za-z]{3}\s+\d{4}"

# Midpoints between header x-ranges for column mapping
COL_BOUNDARIES = {
    "trx_date": (30.0, 93.25),
    "trx_time": (93.25, 153.9),
    "post_date": (153.9, 215.65),
    "proc_time": (215.65, 272.95),
    "desc": (272.95, 335.8),
    "ref": (335.8, 385.0),
    "debit": (385.0, 450),
    "credit": (450, 509.7),
    "src": (509.7, 554.5),
    "teller": (554.5, 604.3),
    "branch": (604.3, 648.65),
    "code": (648.65, 725.55),
    "balance": (725.55, 1000.0)
}

def clean_num(x):
    if not x: return None
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

def clean_descriptions_gpt(desc_list):
    if not desc_list: return []
    valid_indices = [i for i, d in enumerate(desc_list) if len(str(d).strip()) > 2]
    if not valid_indices: return [str(d).strip() for d in desc_list]
    text_block = "\n".join([f"ID_{i}: {desc_list[i]}" for i in valid_indices])
    prompt = f"Clean these bank transaction descriptions for a financial report. Remove dates/times, long IDs, and redundant codes. KEEP critical info like people/vendor names or purposes.\nFormat: ID_n: Cleaned Description\nDescriptions:\n{text_block}"
    try:
        if not AZURE_OPENAI_API_KEY: raise ValueError("No API Key")
        resp = client.chat.completions.create(model=AZURE_OPENAI_DEPLOYMENT_NAME, messages=[{"role": "user", "content": prompt}], temperature=0)
        lines = resp.choices[0].message.content.strip().split("\n")
        results = [str(d).strip() for d in desc_list]
        for line in lines:
            m = re.search(r"ID_(\d+)[:.]\s*(.*)", line)
            if m:
                idx = int(m.group(1))
                if 0 <= idx < len(results): results[idx] = m.group(2).strip()
        return results
    except Exception: return [str(d).strip() for d in desc_list]

def extract_page(page, last_page=False):
    words = page.extract_words()
    
    # Header boundary
    header_bottom = 0
    for w in words:
        if w['top'] < 250:
            content = w["text"].upper()
            if "TRANSACTION" in content or "POSTING" in content or "DESCRIPTION" in content:
                header_bottom = max(header_bottom, w["bottom"])
    if header_bottom == 0: header_bottom = 120.0

    # Footer boundary (look for horizontal lines below header)
    footer_top = page.height
    h_lines = [l for l in page.lines if l["width"] > 100 and l["top"] > header_bottom]
    if h_lines:
        # Use the first horizontal line encountered as the footer boundary
        footer_top = min([l["top"] for l in h_lines])

    # Filter words
    valid_words = [w for w in words if header_bottom + 1 < w["top"] < footer_top]
    valid_words.sort(key=lambda x: (x["top"], x["x0"]))

    # Group into lines (3pt tolerance)
    line_groups = []
    if valid_words:
        cur = [valid_words[0]]
        for i in range(1, len(valid_words)):
            w = valid_words[i]
            if abs(w["top"] - cur[-1]["top"]) < 3.0: cur.append(w)
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
    all_raw_rows = []
    with pdfplumber.open(path) as pdf:
        num_pages = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            rows = extract_page(page, last_page=(i == num_pages - 1))
            all_raw_rows.extend(rows)

    final_txs = []
    current_tx = None
    
    for row in all_raw_rows:
        is_new_tx = clean_num(row["balance"]) is not None
        
        if is_new_tx:
            if current_tx: 
                final_txs.append(current_tx)
            
            # Inheritance: If date or time is missing on a new transaction line, 
            # and we are on the same page, inherit from the previous one.
            if current_tx and row["_page"] == current_tx["_page"]:
                if not row["trx_date"]: row["trx_date"] = current_tx["trx_date"]
                if not row["trx_time"]: row["trx_time"] = current_tx["trx_time"]
                if not row["post_date"]: row["post_date"] = current_tx["post_date"]
                if not row["proc_time"]: row["proc_time"] = current_tx["proc_time"]
            
            current_tx = row.copy()
        else:
            if current_tx and row["_page"] == current_tx["_page"]:
                # Append multi-line fields
                if row["desc"]: 
                    current_tx["desc"] = (current_tx["desc"] + " " + row["desc"]).strip()
                if row["ref"]:
                    current_tx["ref"] = (current_tx["ref"] + " " + row["ref"]).strip()
                
                # Fill other fields if they were missing on the first line
                for k in ["trx_date", "trx_time", "post_date", "proc_time", "debit", "credit", "src", "teller", "branch", "code"]:
                    if not current_tx[k] and row[k]: 
                        current_tx[k] = row[k]
    
    if current_tx:
        final_txs.append(current_tx)

    df = pd.DataFrame(final_txs)
    if df.empty: return pd.DataFrame()

    # Clean up any footer words that might have been sucked in (e.g. Total, Records)
    # We filter out rows that have 'Total' in the description or don't look like transactions
    df = df[~df["desc"].str.contains("Total no. of Records", case=False, na=False)]
    
    print(f"[*] Total detected transactions after cleaning: {len(df)}")
    
    df["debit_clean"] = df["debit"].apply(clean_num)
    df["credit_clean"] = df["credit"].apply(clean_num)
    df["balance_clean"] = df["balance"].apply(clean_num)
    
    # Final date propagation for any remaining NaNs 
    df["trx_date"] = df["trx_date"].ffill()
    df["post_date"] = df["post_date"].ffill()
    df["cleaned_desc"] = df["desc"]
    # df["cleaned_desc"] = clean_descriptions_gpt(df["desc"].tolist())

    out = pd.DataFrame({
        "Transaction Date": df["trx_date"],
        "Transaction Time": df["trx_time"],
        "Posting Date": df["post_date"],
        "Processing Time": df["proc_time"],
        "Transaction Description": df["cleaned_desc"],
        "Transaction Ref": df["ref"],
        "Debit": df["debit_clean"].apply(fmt_currency),
        "Credit": df["credit_clean"].apply(fmt_currency),
        "Source Code": df["src"],
        "Teller ID": df["teller"],
        "Branch/Channel": df["branch"],
        "Transaction Code": df["code"],
        "End Balance": df["balance_clean"].apply(fmt_currency)
    })
    # Replace NaNs with empty strings so they show up as blank in Excel
    return out.fillna("")

def export_excel(df, path):
    if not df.empty:
        try:
            df.to_excel(path, index=False)
            print(f"[+] Exported to {path}")
        except:
            # Fallback if file is open
            alt_path = path.replace(".xlsx", "_1.xlsx")
            df.to_excel(alt_path, index=False)
            print(f"[+] File was open. Exported to {alt_path}")

if __name__ == "__main__":
    pdf_file = r"Templates\RK BII MGD 120226 2.pdf"
    # pdf_file = r"Templates\RK BII PKB 020326.pdf"
    if os.path.exists(pdf_file):
        df_result = parse_pdf(pdf_file)
        export_excel(df_result, "maybank_final_output.xlsx")
    else:
        print(f"File not found: {pdf_file}")