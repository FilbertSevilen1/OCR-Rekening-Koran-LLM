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



BRANCH_EXTRACTION_PROMPT = """
You are a financial data extractor. Your task is to extract every transaction from a BRANCH TRANSACTION LOG (CONFINS).

DOCUMENT STRUCTURE:
Columns: Transaction Date, Voucher No, Chart of Account, Description, Reference No., Debit, Credit, Balance.
POLARITY: DEBIT = Money IN, CREDIT = Money OUT.

EXTRACTION RULES:
1. Extract ALL rows with a numeric Debit or Credit.
2. For each row, extract:
   - date: The transaction date (DD/MM/YYYY)
   - voucher_no
   - coa (Chart of Account)
   - description
   - debit: absolute number
   - credit: absolute number
   - balance
3. Description extraction: Extract Description as Complete as possible, description may have more than one lines.
4. Account Name Rule: If Description contains "-", the text after "-" is the account name. Otherwise, use the whole description.

Return ONLY a JSON list of objects:
[
  {
    "date": "DD/MM/YYYY",
    "voucher_no": "",
    "coa": "",
    "description": "",
    "account_name": "",
    "debit": 0.0,
    "credit": 0.0,
    "balance": 0.0
  }
]
"""

FINANCE_EXTRACTION_PROMPT = """
You are a financial data extractor. Your task is to extract every transaction from a FINANCE BANK STATEMENT (RK).

DOCUMENT STRUCTURE:
Columns: Transaction Date, Description, Amount (Debit/Credit), Balance.

EXTRACTION RULES:
1. Extract ALL rows with a numeric value in ANY amount column.
2. For each row, extract:
   - date: The transaction date (DD/MM/YYYY)
   - description: The full transaction description (handle multi-line)
   - amount: The absolute numeric value
   - balance: The numeric value in 'End Balance' column
3. Each row usually contains either a Debit or Credit value.

Return ONLY a JSON list of objects:
[
  {
    "date": "DD/MM/YYYY",
    "description": "...",
    "amount": 0.0,
    "balance": 0.0
  }
]
"""

CLEANSE_BRANCH_PROMPT = """
You are a financial data cleanser. Your task is to consolidate multiple entries in a BRANCH TRANSACTION LOG (CONFINS) that belong to the same logical transaction.

CONSOLIDATION RULES:
1. Group rows where:
   - they have the same date
   - they have very similar descriptions or the same account_name
   - they appear sequentially
2. For each group:
   - SUM the 'debit' and 'credit' values.
   - Keep the 'account_name' and 'date'.
   - Use the description from the largest amount row.
   - Use the 'balance' from the LAST row in the group.

Return ONLY a JSON list of consolidated objects:
[
  {
    "date": "DD/MM/YYYY",
    "description": "...",
    "account_name": "...",
    "debit": 0.0,
    "credit": 0.0,
    "balance": 0.0
  }
]
"""

RECONCILIATION_MATCHING_PROMPT = """
You are a forensic auditor. Your task is to reconcile TWO lists of transactions: BRANCH (CONFINS) and FINANCE (RK).

SOURCE OF TRUTH FOR POLARITY (TAMBAH/KURANG):
Use the BRANCH list as the source of truth for categorization.

MATCHING LOGIC:
1. Date Match (IMPORTANT): 
   - Dates should ideally match exactly.
   - Allow for ±1 day difference as bank processing might lag.
2. Name Match (FLEXIBLE): 
   - Branch 'account_name' is often a substring of Finance 'description'.
   - Be flexible with wording (e.g., "Budi" vs "Budi Santoso").
3. Amount Match:
   - Amounts MUST be identical.
4. Category:
   - Branch DEBIT (IN) matches Finance -> Category "KURANG".
   - Branch CREDIT (OUT) matches Finance -> Category "TAMBAH".

OUTPUT FORMAT:
Return ONLY a JSON object:
{
  "reconciliation_summary": {
    "saldo_rk": 0.0,
    "saldo_confins": 0.0,
    "saldo_akhir_rk": 0.0,
    "saldo_akhir_confins": 0.0
  },
  "tambah": [
    {
      "finance_transaction": { "date": "", "description": "", "amount": 0.0 },
      "branch_transactions": [ { "date": "", "description": "", "amount": 0.0 } ],
      "total_amount": 0.0,
      "common_identifier": ""
    }
  ],
  "kurang": [
    {
      "finance_transaction": { "date": "", "description": "", "amount": 0.0 },
      "branch_transactions": [ { "date": "", "description": "", "amount": 0.0 } ],
      "total_amount": 0.0,
      "common_identifier": ""
    }
  ],
  "unmatched_transactions": {
    "branch": [],
    "finance": []
  }
}
"""

async def process_reconciliation(branch_bytes, finance_bytes):
    clear_logs()
    branch_pages = split_pdf_pages(branch_bytes)
    finance_pages = split_pdf_pages(finance_bytes)
    
    all_branch_txs_raw = []
    all_finance_txs = []

    # Step 1: Extract Branch Transactions
    for i, p in enumerate(branch_pages):
        print(f"Extracting Branch Page {i+1}...")
        res = ask_model(BRANCH_EXTRACTION_PROMPT, p)
        txs = parse_json_response(res, default_val=[])
        if isinstance(txs, list):
            all_branch_txs_raw.extend(txs)

    # Step 2: Extract Finance Transactions
    for i, p in enumerate(finance_pages):
        print(f"Extracting Finance Page {i+1}...")
        res = ask_model(FINANCE_EXTRACTION_PROMPT, p)
        txs = parse_json_response(res, default_val=[])
        if isinstance(txs, list):
            all_finance_txs.extend(txs)
            
    save_log("branch_extract_raw", json.dumps(all_branch_txs_raw, indent=2))
    save_log("finance_extract_all", json.dumps(all_finance_txs, indent=2))

    # Step 3: Cleanse Branch Transactions (Consolidation)
    print("Cleansing and Consolidating Branch Transactions...")
    cleansing_input = json.dumps(all_branch_txs_raw, indent=2)
    res_cleansed = ask_model(CLEANSE_BRANCH_PROMPT + "\n\nDATA:\n" + cleansing_input, {})
    all_branch_txs = parse_json_response(res_cleansed, default_val=all_branch_txs_raw)
    save_log("branch_extract_cleansed", json.dumps(all_branch_txs, indent=2))

    # Step 4: Reconciliation Matching
    match_data = f"\n\n### BRANCH TRANSACTIONS (CLEANSED):\n{json.dumps(all_branch_txs, indent=2)}\n\n### FINANCE TRANSACTIONS:\n{json.dumps(all_finance_txs, indent=2)}"
    print("Performing Reconciliation Matching...")
    res = ask_model(RECONCILIATION_MATCHING_PROMPT + match_data, {})
    save_log("reconciliation_final", res)
    reconciliation = parse_json_response(res, default_val={})
    
    return reconciliation

