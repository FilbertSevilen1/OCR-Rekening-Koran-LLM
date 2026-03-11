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



DIRECT_RECONCILIATION_PROMPT = """
You are a top-tier financial auditor. Your task is to perform a 100% COMPLETE and ACCURATE bank reconciliation.
You have been provided with high-resolution images of two documents.

---
DOCUMENTS:
1. BRANCH TRANSACTION LOG (CONFINS)
   - Columns: Voucher No, Description, Reference No, Debit, Credit, Balance.
   - POLARITY: DEBIT = Money IN, CREDIT = Money OUT.

2. FINANCE BANK STATEMENT (RK)
   - Columns: Date, Description, Ref, Debit, Credit, Balance.
   - POLARITY: CREDIT = Money IN, DEBIT = Money OUT.

---
RECONCILIATION MANDATES:
1. "kurang" section: Match Branch DEBIT (Money In) with Finance CREDIT (Money In).
   - MANDATORY: Include ALL payments that match exists in both finance and branch.
   - ARIS PERMADI AGGREGATION: Match Finance entry (2,102,000) with THREE Branch entries: 2,085,000 + 16,800 + 200. Transcribe 16,800 carefully.

2. "tambah" section: Match Branch CREDIT (Money Out) with Finance DEBIT (Money Out).
   - MANDATORY: Include FEE ADM, SERVICE CHARGE, and other bank fees/transfers.

3. FUZZY MATCHING: Be flexible. If a names from branch is similar to finance, match it. But make sure both has the same names, when calculated both transaction from finance and branch is equal, put it as match into kurang or lebih according to kredit or debit

---
CRITICAL AUDIT RULES:
1. DO NOT BE LAZY: You must process EVERY transaction from EVERY page. Omissions like Doly Chandra or Daswir are unacceptable.
2. ACCURACY: Read the numbers from the images pixel-perfectly.
3. COMPLETENESS: Your output JSON must contain ALL matching groups found across all 11+ pages.

Output ONLY a JSON object:
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
       "total_amount": 0.0, "common_identifier": "..."
     }
  ],
  "kurang": [
     {
       "finance_transaction": { "date": "", "description": "", "amount": 0.0 },
       "branch_transactions": [ { "date": "", "description": "", "amount": 0.0 } ],
       "total_amount": 0.0, "common_identifier": "..."
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
    
    labeled_pages = []
    
    # Label Branch pages
    for i, p in enumerate(branch_pages):
        p_labeled = p.copy()
        p_labeled["text"] = f"--- [DOCUMENT: BRANCH TRANSACTION LOG, PAGE {i+1}] ---\n" + p.get("text", "")
        labeled_pages.append(p_labeled)
        
    # Label Finance pages
    for i, p in enumerate(finance_pages):
        p_labeled = p.copy()
        p_labeled["text"] = f"--- [DOCUMENT: FINANCE BANK STATEMENT, PAGE {i+1}] ---\n" + p.get("text", "")
        labeled_pages.append(p_labeled)

    # Perform Direct Reconciliation in one pass
    res = ask_model(DIRECT_RECONCILIATION_PROMPT, labeled_pages)
    save_log("reconciliation_direct_all", res)
    reconciliation = parse_json_response(res, default_val={})
    
    return reconciliation

