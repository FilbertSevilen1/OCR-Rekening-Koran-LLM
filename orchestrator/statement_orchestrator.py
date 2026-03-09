import json
import re
from services.pdf_service import split_pdf_pages
from services.openai_service import ask_model

from parsers.bank_detector import DETECT_PROMPT
from parsers.account_parser import ACCOUNT_PROMPT
from parsers.summary_parser import SUMMARY_PROMPT
from parsers.transaction_parser import TRANSACTION_PROMPT


def parse_json_response(result, default_val=None):
    if default_val is None:
        default_val = {}
    if not result:
        print("Model returned empty result")
        return default_val
        
    match = re.search(r'```(?:json)?(.*?)```', result, re.DOTALL)
    if match:
        result = match.group(1).strip()
    else:
        # If the model didn't use markdown format, it might have added conversational text.
        # Find the first [ or { and the last ] or }
        start_idx = -1
        end_idx = -1
        
        list_start = result.find('[')
        obj_start = result.find('{')
        
        if list_start != -1 and (obj_start == -1 or list_start < obj_start):
            start_idx = list_start
            end_idx = result.rfind(']')
        elif obj_start != -1:
            start_idx = obj_start
            end_idx = result.rfind('}')
            
        if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
            result = result[start_idx:end_idx+1]
        
    try:
        return json.loads(result.strip())
    except Exception as e:
        print(f"Failed to parse JSON. Raw result: {result}")
        return default_val

async def process_statement(file_bytes):
    pages = split_pdf_pages(file_bytes)
    first_page = pages[0]
    last_page = pages[-1]

    bank = parse_json_response(ask_model(DETECT_PROMPT, first_page), default_val={})
    
    account_context = f"Context - Bank: {bank}\n"
    account = parse_json_response(ask_model(account_context + ACCOUNT_PROMPT, first_page), default_val={})
    
    summary_context = f"Context - Bank: {bank}\nAccount: {account}\n"
    summary_pages = [first_page] if len(pages) == 1 else [first_page, last_page]
    summary = parse_json_response(ask_model(summary_context + SUMMARY_PROMPT, summary_pages), default_val={})
    
    transactions = []
    trx_context = f"Context - Bank: {bank}\nAccount: {account}\nSummary: {summary}\n"
    
    for page in pages:
        result = ask_model(trx_context + TRANSACTION_PROMPT, page)
        txs = parse_json_response(result, default_val=[])
        if not isinstance(txs, list):
            txs = []
        for t in txs:
            if isinstance(t, dict):
                t["account_number"] = account.get("account_number")
        transactions.extend(txs)

    # Extract expected figures from summary
    def parse_float(val):
        if val is None:
            return None
        try:
            return float(str(val).replace(",", ""))
        except ValueError:
            return None

    expected_debit = parse_float(summary.get("total_debit_amount"))
    expected_credit = parse_float(summary.get("total_credit_amount"))
    begin_bal = parse_float(summary.get("begin_balance")) or 0.0
    end_bal = parse_float(summary.get("end_balance")) or 0.0

    # Step-by-step Mathematical Transaction Validation & DB/CR Auto-Correction
    running_balance_issues = []
    current_running_balance = begin_bal
    
    calculated_total_debit = 0.0
    calculated_total_credit = 0.0

    for i, t in enumerate(transactions):
        try:
            amt = float(str(t.get("amount", "0")).replace(",", ""))
        except ValueError:
            amt = 0.0
            
        try:
            t_bal = float(str(t.get("balance", "0")).replace(",", ""))
        except ValueError:
            t_bal = 0.0
            
        t_type = str(t.get("type", "")).upper()
        
        # Determine the mathematically required difference to get from previous balance to this balance
        actual_diff = round(abs(t_bal - current_running_balance), 2)
        amt_error = round(abs(actual_diff - amt), 2)
        
        # Advanced Auto-Healer: If the amount is exactly 1 digit off from the required math, it's heavily likely an OCR typo (e.g. 8 vs 6)
        if amt_error > 0 and actual_diff > 0:
            def clean_str(val):
                s = str(val)
                return s[:-2] if s.endswith(".0") else s
                
            amt_str = clean_str(amt)
            req_str = clean_str(actual_diff)
            
            if len(amt_str) == len(req_str):
                diff_count = sum(1 for a, b in zip(amt_str, req_str) if a != b)
                if diff_count == 1:
                    amt = actual_diff
                    t["amount"] = amt # Silently auto-heal the hallucination!
        
        # Mathematically deduce if this was a Credit or a Debit by seeing which brings us closer to the parsed balance!
        cr_prediction = current_running_balance + amt
        db_prediction = current_running_balance - amt
        
        cr_error = abs(cr_prediction - t_bal)
        db_error = abs(db_prediction - t_bal)
        
        if cr_error < db_error:
            t["type"] = "CR"
            calculated_total_credit += amt
            calculated_current = cr_prediction
        elif db_error < cr_error:
            t["type"] = "DB"
            calculated_total_debit += amt
            calculated_current = db_prediction
        else:
            # If errors are exactly equal (e.g. amt = 0), fallback to LLM's parsed type
            if t_type == "DB":
                t["type"] = "DB"
                calculated_total_debit += amt
                calculated_current = db_prediction
            else:
                t["type"] = "CR"
                calculated_total_credit += amt
                calculated_current = cr_prediction
                
        if abs(calculated_current - t_bal) > 0.01:
            running_balance_issues.append({
                "index": i,
                "date": t.get("date"),
                "description": t.get("description"),
                "expected_running_balance": calculated_current,
                "parsed_running_balance": t_bal,
                "parsed_amount": amt
            })
            
        # Update the mathematical tracker (trust the parsed balance to prevent cascading errors)
        current_running_balance = t_bal
        
    calculated_end_bal = begin_bal + calculated_total_credit - calculated_total_debit
    
    if expected_debit is not None:
        debit_match = "True" if abs(calculated_total_debit - expected_debit) < 0.01 else "False"
    else:
        debit_match = "Unproofable"
        
    if expected_credit is not None:
        credit_match = "True" if abs(calculated_total_credit - expected_credit) < 0.01 else "False"
    else:
        credit_match = "Unproofable"

    proof = {
        "calculated_total_debit": calculated_total_debit,
        "calculated_total_credit": calculated_total_credit,
        "expected_total_debit": expected_debit,
        "expected_total_credit": expected_credit,
        "debit_match": debit_match,
        "credit_match": credit_match,
        "balance_match": "True" if abs(calculated_end_bal - end_bal) < 0.01 else "False",
        "calculated_end_balance": calculated_end_bal,
        "running_balance_issues": running_balance_issues
    }

    return {
        "account": account,
        "summary": summary,
        "proof": proof,
        "transactions": transactions
    }