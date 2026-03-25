from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from typing import List, Dict, Any
import io
import pandas as pd
from difflib import SequenceMatcher

from orchestrator.statement_orchestrator import process_statement
from consolidation import consolidate
from export_service import create_export_file, create_reconciliation_export
from parsers.daily_transaction_parser import parse_pdf as parse_daily_tx
from parsers.maybank_parser import parse_pdf as parse_maybank_tx

app = FastAPI()


def extract_name_from_description(description: str) -> str:
    """Extract name from description (right side of -)"""
    if not description or '-' not in description:
        return ""
    return description.split('-')[-1].strip()


def extract_transaction_id_from_description(description: str) -> str:
    """Extract transaction ID from description (e.g., 57301220481 from Receive#57301220481)"""
    import re
    if not description:
        return ""
    # Look for pattern like "Receive#12345678" or similar
    match = re.search(r'#(\d+)', description)
    if match:
        return match.group(1)
    return ""


def extract_reference_from_description(description: str) -> str:
    """Extract reference from description (left side of -)"""
    if not description or '-' not in description:
        return ""
    return description.split('-')[0].strip()


def fuzzy_match_name(name1: str, name2: str, threshold: float = 0.6) -> bool:
    """Check if two names are similar enough (fuzzy matching)"""
    name1 = name1.upper().strip()
    name2 = name2.upper().strip()
    
    # Direct substring match (most reliable)
    if name1 in name2 or name2 in name1:
        return True
    
    ratio = SequenceMatcher(None, name1, name2).ratio()
    return ratio >= threshold


def extract_transaction_id_from_maybank(description: str) -> str:
    """Extract transaction ID from maybank description if available"""
    import re
    if not description:
        return ""
    # Look for 11-digit transaction IDs (e.g., 57301221119 or 57301221 119 with space)
    # Handle with or without spaces: "57301221119" or "57301221 119"
    match = re.search(r'\b(5\d{10})\b', description)
    if match:
        return match.group(1)
    
    # Try with space: "57301221 119" -> extract and remove space
    match = re.search(r'\b(5\d{5})\s+(\d{5})\b', description)
    if match:
        return match.group(1) + match.group(2)
    
    return ""


def reconciliate_transactions(daily_df: pd.DataFrame, maybank_df: pd.DataFrame) -> Dict[str, Any]:
    """
    Reconciliate transactions between daily transaction and maybank statements.
    Returns matched transactions and unmatched ones.
    Matching strategy:
    1. First try to match by transaction ID (most reliable)
    2. Then try to match by name + amount (fuzzy)
    """
    # Make copies to avoid modifying original dataframes
    daily_df = daily_df.copy()
    maybank_df = maybank_df.copy()
    
    # Ensure Debit and Credit columns are numeric
    daily_df['Debit'] = pd.to_numeric(daily_df['Debit'], errors='coerce').fillna(0)
    maybank_df['Credit'] = pd.to_numeric(maybank_df['Credit'], errors='coerce').fillna(0)
    maybank_df['Debit'] = pd.to_numeric(maybank_df['Debit'], errors='coerce').fillna(0)
    
    # Extract transaction ID and name from daily transactions
    daily_df['Name'] = daily_df['Description'].apply(extract_name_from_description)
    daily_df['TransactionID'] = daily_df['Description'].apply(extract_transaction_id_from_description)
    
    # Group daily transactions by TransactionID and sum debits
    daily_grouped = []
    for tx_id, group in daily_df.groupby('TransactionID'):
        if tx_id:  # Only process if transaction ID exists
            total_debit = group['Debit'].sum()
            name = group['Name'].iloc[0]  # Use first name in group
            daily_grouped.append({
                "transaction_id": tx_id,
                "name": name,
                "total_debit": total_debit,
                "count": len(group),
                "transactions": group.to_dict(orient='records')
            })
    
    # Extract names and transaction IDs from maybank transactions
    maybank_df['Name'] = maybank_df['Transaction Description'].apply(
        lambda x: extract_name_from_maybank_description(x) if isinstance(x, str) else ""
    )
    maybank_df['TransactionID'] = maybank_df['Transaction Description'].apply(
        lambda x: extract_transaction_id_from_maybank(x) if isinstance(x, str) else ""
    )
    
    matched = []
    unmatched_daily = list(daily_grouped)
    matched_maybank_indices = set()
    
    # Try to match daily with maybank
    for daily_tx in daily_grouped:
        for idx, maybank_tx in maybank_df.iterrows():
            match_found = False
            match_reason = ""
            
            # Strategy 1: Match by transaction ID (most reliable)
            if daily_tx['transaction_id'] and maybank_tx['TransactionID']:
                if daily_tx['transaction_id'] == maybank_tx['TransactionID']:
                    match_found = True
                    match_reason = "transaction_id"
            
            # Strategy 2: Match by name and amount
            if not match_found and daily_tx['name'] and maybank_tx['Name']:
                name_matches = fuzzy_match_name(daily_tx['name'], maybank_tx['Name'])
                # Credit column contains the amount for the transaction
                amount_matches = abs(daily_tx['total_debit'] - float(maybank_tx['Credit'])) < 0.01
                
                if name_matches and amount_matches:
                    match_found = True
                    match_reason = "name_and_amount"
            
            if match_found:
                daily_type = "debit" if daily_tx.get('total_debit', 0) > 0 else "credit" if daily_tx.get('total_debit', 0) < 0 else "unknown"
                matched_amount = float(maybank_tx['Credit']) if maybank_tx.get('Credit') is not None else 0.0
                matched_name = daily_tx.get('name') or maybank_tx.get('Name') or ""

                matched.append({
                    "daily_transaction": daily_tx,
                    "maybank_transaction": maybank_tx.to_dict(),
                    "matched": True,
                    "match_confidence": match_reason,
                    "matched_amount": matched_amount,
                    "daily_type": daily_type,
                    "matched_name": matched_name
                })
                unmatched_daily.remove(daily_tx)
                matched_maybank_indices.add(idx)
                break
    
    unmatched_maybank = maybank_df[~maybank_df.index.isin(matched_maybank_indices)].to_dict(orient='records')
    
    return {
        "matched_transactions": matched,
        "unmatched_daily_transactions": unmatched_daily,
        "unmatched_maybank_transactions": unmatched_maybank,
        "summary": {
            "total_daily_grouped": len(daily_grouped),
            "total_matched": len(matched),
            "total_unmatched_daily": len(unmatched_daily),
            "total_unmatched_maybank": len(unmatched_maybank)
        }
    }


def extract_name_from_maybank_description(description: str) -> str:
    """Extract name from maybank transaction description"""
    import re
    if not description:
        return ""
    
    description_upper = description.upper()
    
    # Common transaction words to exclude or skip
    exclude_words = {
        'TRF', 'DR', 'CR', 'ADM', 'FEE', 'BI', 'FAST', 'TRANSFER', 'LAINNYA',
        'PELUNASAN', 'ANGSURAN', 'PERIODE', 'APRIL', 'MEI', 'REBO', 'INOVA',
        'PUTRI', 'NOVRIN', 'HELEN', 'ANGSURAN', 'INOVA', 'REBO', 'PERIODE',
        'PRINTED', 'STATEMENT', 'FEBRUARI', 'SERVICE', 'CHARGE', 'FEE', 'ADM',
        'PEL', 'BM', 'SB', 'BRI', 'BPD', 'BCA', 'MANDIRI', 'OCBC', 'UOB', 'FAST'
    }
    
    # Strategy 1: Extract name before "/" or specific delimiters
    # Pattern: "PEL MARIHOT SITUNGK/BM11 03FI/57301221 119"
    match = re.search(r'(?:^|\s)([A-Z][A-Z\s]+?)(?:/|BM\d|03FI)', description)
    if match:
        potential_name = match.group(1).strip()
        words = potential_name.split()
        filtered = [w for w in words if w not in exclude_words and len(w) > 2 and not w.isdigit()]
        if filtered:
            return ' '.join(filtered)
    
    # Strategy 2: Look for keywords like "Pelunasan an" to capture receiver name
    # Pattern: "... Pelunasan an Abdul rahman BM ..."
    match = re.search(r'pelunasan\s+an\s+([A-Za-z\s]+?)(?:\s+BM|\s+\d|\s+ZZ|$)', description, flags=re.IGNORECASE)
    if match:
        potential_name = match.group(1).strip()
        words = potential_name.split()
        filtered = [w for w in words if w.upper() not in exclude_words and len(w) > 2 and not w.isdigit()]
        if filtered:
            return ' '.join(filtered)

    # Strategy 3: Look for IDs and extract names after numeric codes
    # Pattern: "20260302CEN AIDJA010O02 33896132 SUCI DIAN SARI Pelunasan an Abdul rahman"
    match = re.search(r'(?:\d+\s+)+([A-Z][A-Z\s]+?)(?:\s+Pelunasan|\s+angsuran|\s+BI\s|$)', description)
    if match:
        potential_name = match.group(1).strip()
        words = potential_name.split()
        filtered = [w for w in words if w not in exclude_words and len(w) > 2]
        if filtered:
            return ' '.join(filtered)
    
    # Strategy 3: Extract capitalized words intelligently
    parts = description.split()
    
    name_parts = []
    for i, part in enumerate(parts):
        # Skip if it's a number, code, or very short
        if part.isdigit() or len(part) <= 2:
            continue
        
        # Stop if we hit a "/" or codes like "BM11", "03FI", etc.
        if '/' in part or re.match(r'^[A-Z]{2}\d+', part):
            break
        
        # Look for capitalized words (likely names)
        if part and (part[0].isupper() or part.isupper()):
            # Skip transaction codes and exclude words
            if part not in exclude_words and not re.match(r'^\d+[A-Z]', part):
                # Remove special characters but keep the word
                clean_part = re.sub(r'[^\w]', '', part)
                if clean_part and not clean_part.isdigit():
                    name_parts.append(clean_part)
    
    # Return concatenated names, but limit to avoid garbage
    if name_parts:
        result = ' '.join(name_parts[:5])  # Limit to first 5 words
        return result.strip()
    
    return ""


@app.get("/")
def health():
    return {"status": "ok"}


@app.post("/parse-statement")
async def parse_statement(file: UploadFile = File(...), export_excel: bool = Form(False)):
    content = await file.read()
    result = await process_statement(content)
    
    if export_excel:
        excel_io = create_export_file(result)
        return StreamingResponse(
            excel_io,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=statement_{result.get('account', {}).get('account_number', 'export')}.xlsx"}
        )
        
    return result

@app.post("/parse-daily-transaction")
async def parse_daily_transaction(file: UploadFile = File(...), export_excel: bool = Form(False)):
    content = await file.read()
    df = parse_daily_tx(io.BytesIO(content))
    
    if export_excel:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        output.seek(0)
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=daily_transaction_export.xlsx"}
        )
        
    return df.to_dict(orient='records')

@app.post("/parse-maybank")
async def parse_maybank(file: UploadFile = File(...), export_excel: bool = Form(False)):
    content = await file.read()
    df = parse_maybank_tx(io.BytesIO(content))
    
    if export_excel:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        output.seek(0)
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=maybank_export.xlsx"}
        )
        
    return df.to_dict(orient='records')

@app.post("/reconciliate")
async def reconciliate_statements(daily_file: UploadFile = File(...), maybank_file: UploadFile = File(...), export_file: bool = Form(False)):
    """
    Reconciliate daily transaction and Maybank statement files.
    Matches transactions by name and amount.
    
    Args:
        daily_file: Daily transaction PDF file
        maybank_file: Maybank statement PDF file
        export_file: If true, returns Excel file with 3 sheets instead of JSON
        
    Returns:
        If export_file is False: Dictionary containing matched and unmatched transactions
        If export_file is True: Excel file with sheets for matched, unmatched daily, unmatched maybank
    """
    daily_content = await daily_file.read()
    maybank_content = await maybank_file.read()
    
    daily_df = parse_daily_tx(io.BytesIO(daily_content))
    maybank_df = parse_maybank_tx(io.BytesIO(maybank_content))
    
    reconciliation = reconciliate_transactions(daily_df, maybank_df)
    
    if export_file:
        output = create_reconciliation_export(reconciliation, daily_df, maybank_df)
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=reconciliation_export.xlsx"}
        )
    
    return reconciliation