import json
import os
import datetime
from services.pdf_service import split_pdf_pages
from services.openai_service import ask_model
from orchestrator.statement_orchestrator import parse_json_response

def parse_float(val):
    if val is None:
        return 0.0
    try:
        return float(str(val).replace(",", ""))
    except ValueError:
        return 0.0

def validate_and_heal_transactions(transactions, start_balance=None, label="", is_branch=False):
    if not transactions:
        return transactions, 0.0

    # If no start balance provided, deduce from first transaction 
    if start_balance is None or start_balance == 0.0:
        first = transactions[0]
        f_bal = parse_float(first.get("balance") or first.get("end_balance"))
        f_deb = parse_float(first.get("debit"))
        f_cre = parse_float(first.get("credit"))
        if is_branch:
            # Branch: balance_after = balance_before + debit - credit
            # So: balance_before = balance_after - debit + credit
            start_balance = f_bal - f_deb + f_cre
        else:
            # Finance: balance_after = balance_before + credit - debit
            # So: balance_before = balance_after - credit + debit
            start_balance = f_bal - f_cre + f_deb

    current_balance = start_balance
    swaps = 0
    for t in transactions:
        deb = parse_float(t.get("debit"))
        cre = parse_float(t.get("credit"))
        bal = parse_float(t.get("balance") or t.get("end_balance"))

        # Formulas:
        # Branch:  bal = prev + deb - cre
        # Finance: bal = prev + cre - deb
        if is_branch:
            pred_correct  = current_balance + deb - cre
            pred_swapped  = current_balance + cre - deb
        else:
            pred_correct  = current_balance + cre - deb
            pred_swapped  = current_balance + deb - cre

        err_correct = abs(pred_correct - bal)
        err_swapped = abs(pred_swapped - bal)

        if err_swapped < err_correct and err_swapped < 1.0:
            # Swap fixes the math — apply it
            t["debit"], t["credit"] = cre, deb
            t["_healed"] = True
            current_balance = bal
            swaps += 1
        else:
            current_balance = bal

    if swaps:
        print(f"[Heal:{label}] Corrected {swaps} swapped debit/credit entries.")

    return transactions, current_balance




def save_log(prefix, content):
    os.makedirs("logs", exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filepath = os.path.join("logs", f"{prefix}_{timestamp}.txt")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

def clear_logs():
    log_dir = "logs"
    if os.path.exists(log_dir):
        for filename in os.listdir(log_dir):
            if filename.endswith(".txt"):
                file_path = os.path.join(log_dir, filename)
                try:
                    os.remove(file_path)
                except Exception as e:
                    print(f"Failed to delete {file_path}. Reason: {e}")



BRANCH_EXTRACT_PROMPT = """
You are a highly accurate data extraction system. Extract ALL daily transactions from the provided Branch Daily Transactions document.

The document visual columns are: Voucher No. | Chart of Account | Description | Reference No. | Debit | Credit | Balance

CRITICAL - RAW TEXT COLUMN ORDER:
In the raw text extracted from the PDF, the two amount columns appear REVERSED compared to the visual layout:
  <voucher_no> <date> <credit_amount> <debit_amount> <chart_of_account> <description>

The FIRST numeric value after the date = CREDIT
The SECOND numeric value after the date = DEBIT

Verified examples from this document:
  Raw: "999IPKB097626030018 2/3/2026 0.00 30,613,000.00 21991120-AP Contract..."
  -> credit=0.0, debit=30613000.0   [30,613,000.00 is in DEBIT column in the image]

  Raw: "999IPKB097626030053 2/3/2026 2,085,000.00 0.00 11120101 Payment Receive#57301220481-ARIS PERMADI"
  -> credit=2085000.0, debit=0.0   [2,085,000.00 is in CREDIT column in the image]

  Raw: "999IPKB097626030075 03/03/2026 0.00 778,653,400.00 11012901 Transfer Fu..."
  -> credit=0.0, debit=778653400.0

Fields to extract for each transaction:
- voucher_no: Voucher No.
- chart_of_account: Chart of Account (just the code, e.g. 21991120)
- description: Full description text (the part after chart of account, including Receive#, PDC Clearing# etc. and any name)
- reference_no: Reference No. (the date-like value after Voucher)
- debit: Debit amount (float, 0.0 if none) — SECOND number in raw text
- credit: Credit amount (float, 0.0 if none) — FIRST number in raw text
- balance: Running Balance (float, can be negative)

CRITICAL:
- Amounts use Indonesian format '30,613,000.00' — commas are thousand separators, dot is decimal. Parse 30,613,000.00 as 30613000.0
- Trust the visual image column headers: the column labeled "Debit" is Debit, "Credit" is Credit.
- Return raw JSON ONLY. No markdown.
"""

SUMMARY_EXTRACT_PROMPT = """
Extract summary information:
- begin_balance: Opening/Beginning Balance
- end_balance: Closing/Ending Balance
Return JSON: {"begin_balance": 0.0, "end_balance": 0.0}
"""


FINANCE_EXTRACT_PROMPT = """
You are a highly accurate data extraction system. Extract ALL bank transactions from the provided Finance Bank Statement (Rekening Koran) document.

Fields to extract for each transaction:
- transaction_date: Date
- transaction_description: Description
- transaction_ref: Ref
- debit: Debit amount (float)
- credit: Credit amount (float)
- end_balance: End Balance (float)

CRITICAL INSTRUCTIONS:
- IDENTIFY DEBIT AND CREDIT COLUMNS CAREFULLY. Check headers (Debit/Credit or DB/CR).
- Amounts use format '30,613,000.00'. Ignore commas.
- Return raw JSON ONLY. Do NOT enclose in markdown.
"""


RECONCILIATION_PROMPT = """
You are a highly skilled financial analyst performing bank reconciliation.
You are given two lists of transactions in JSON format:
1. Branch Transactions (Saldo CONFINS)
2. Finance Transactions (Saldo RK)

Your task is to reconcile these two sets of data using fuzzy Name, Reference Number, and Amount matching.

Matching Ground Rules:
1. Fuzzy Name Match: Look for names in the descriptions.
   - Branch: 'Receive#57301221119-MARIHOT BR SITUNGKIR'
   - Finance: 'PEL MARIHOT SITUNGK/BM11 03FI/57301221119'
   - They match on 'MARIHOT' and the reference '57301221119'.
2. Reference Match: Look for matching numeric strings (e.g., '57301221119', '57501241011') in both descriptions.
3. Amount Match (with aggregation):
   - Multiple Branch entries for the same identifier may sum to one Finance entry.
   - Example: ARIS PERMADI (Br: 2,085,000 + 16,800 + 200) = (Fin: 2,102,000).

Classification Rules:
- "kurang" = Matching where Finance is CREDIT and Branch is DEBIT
  Logic: Money coming in to the bank vs recorded as outgoing in Branch.
- "tambah" = Matching where Finance is DEBIT and Branch is CREDIT
  Logic: Money going out from the bank vs recorded as incoming in Branch.

Output Format:
Return ONLY a JSON object:
{
  "reconciliation_summary": {
     "saldo_rk": 0.0,
     "saldo_confins": 0.0,
     "saldo_akhir_rk": 0.0,
     "saldo_akhir_confins": 0.0
  },
  "tambah": [
     // Finance=Debit, Branch=Credit
     {
       "finance_transaction": { "date": "", "description": "", "amount": 0.0 },
       "branch_transactions": [ { "date": "", "description": "", "amount": 0.0 } ],
       "total_amount": 0.0,
       "common_identifier": "..."
     }
  ],
  "kurang": [
     // Finance=Credit, Branch=Debit
     {
       "finance_transaction": { "date": "", "description": "", "amount": 0.0 },
       "branch_transactions": [ { "date": "", "description": "", "amount": 0.0 } ],
       "total_amount": 0.0,
       "common_identifier": "..."
     }
  ],
  "unmatched_transactions": {
     "branch": [],
     "finance": []
  }
}

CRITICAL:
- Ensure LENI MONIKA and MARIHOT match even if descriptions differ slightly. 
- Match by Reference Number (e.g. 57301221119) if present.
- Return raw JSON ONLY.
"""


async def process_reconciliation(branch_bytes, finance_bytes):
    clear_logs()
    branch_pages = split_pdf_pages(branch_bytes)
    finance_pages = split_pdf_pages(finance_bytes)
    
    # Extract Summaries
    branch_summary = parse_json_response(ask_model(SUMMARY_EXTRACT_PROMPT, branch_pages[0]), default_val={})
    finance_summary = parse_json_response(ask_model(SUMMARY_EXTRACT_PROMPT, finance_pages[0]), default_val={})
    
    branch_txs = []
    for i, page in enumerate(branch_pages):
        page_prompt = BRANCH_EXTRACT_PROMPT + f"\n\nCRITICAL: Processing PAGE {i+1} of {len(branch_pages)}."
        res = ask_model(page_prompt, page)
        save_log(f"branch_extract_page_{i+1}", res)
        txs = parse_json_response(res, default_val=[])
        if isinstance(txs, list):
            branch_txs.extend(txs)
    
    # Heal Branch
    branch_txs, _ = validate_and_heal_transactions(branch_txs, parse_float(branch_summary.get("begin_balance")), label="branch", is_branch=True)
            
    finance_txs = []
    for i, page in enumerate(finance_pages):
        page_prompt = FINANCE_EXTRACT_PROMPT + f"\n\nCRITICAL: Processing PAGE {i+1} of {len(finance_pages)}."
        res = ask_model(page_prompt, page)
        save_log(f"finance_extract_page_{i+1}", res)
        txs = parse_json_response(res, default_val=[])
        if isinstance(txs, list):
            finance_txs.extend(txs)

    # Heal Finance
    finance_txs, _ = validate_and_heal_transactions(finance_txs, parse_float(finance_summary.get("begin_balance")), label="finance", is_branch=False)

    data_for_reconciliation = {
        "branch_transactions": branch_txs,
        "finance_transactions": finance_txs
    }
    
    data_str = json.dumps(data_for_reconciliation, indent=2)
    reconcile_prompt = f"{RECONCILIATION_PROMPT}\n\nDATA:\n{data_str}"
    
    res = ask_model(reconcile_prompt, [])
    save_log("reconciliation_final", res)
    reconciliation = parse_json_response(res, default_val={})
    
    return reconciliation

