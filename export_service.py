import pandas as pd
import io

def create_export_file(statement_data):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Sheet 1: Account Details
        account = statement_data.get("account", {})
        account_df = pd.DataFrame([account] if account else [])
        account_df.to_excel(writer, sheet_name="Account Details", index=False)
        
        # Sheet 2: Summary + Proof
        summary = statement_data.get("summary", {})
        proof = statement_data.get("proof", {})
        
        # Merge summary and proof into one row to show easily, or as two tables
        summary_row = {**summary, **{"proof_" + k: v for k, v in proof.items()}}
        summary_df = pd.DataFrame([summary_row] if summary_row else [])
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        
        # Sheet 3: Transactions
        transactions = statement_data.get("transactions", [])
        tx_df = pd.DataFrame(transactions)
        tx_df.to_excel(writer, sheet_name="Transactions", index=False)
        
        # Sheet 4: Consolidation 
        # For now, it copies the transactions but can be extended if multiple statements are parsed
        if transactions:
            consolidation_df = pd.DataFrame(transactions)
        else:
            consolidation_df = pd.DataFrame([])
        consolidation_df.to_excel(writer, sheet_name="Consolidation", index=False)
            
    output.seek(0)
    return output


def create_reconciliation_export(reconciliation_data, daily_df=None, maybank_df=None):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Sheet 1: Matched Transactions
        matched = reconciliation_data.get("matched_transactions", [])
        if matched:
            # Flatten matched transactions
            matched_rows = []
            for m in matched:
                daily_tx = m.get("daily_transaction", {})
                maybank_tx = m.get("maybank_transaction", {})
                row = {
                    "Match Confidence": m.get("match_confidence", ""),
                    "Matched Amount": m.get("matched_amount", ""),
                    "Daily Type": m.get("daily_type", ""),
                    "Maybank Type": m.get("maybank_type", ""),
                    "Matched Name": m.get("matched_name", ""),
                    # Daily transaction fields
                    "Daily Transaction ID": daily_tx.get("transaction_id", ""),
                    "Daily Name": daily_tx.get("name", ""),
                    "Daily Total Debit": daily_tx.get("total_debit", ""),
                    "Daily Total Credit": daily_tx.get("total_credit", ""),
                    "Daily Count": daily_tx.get("count", ""),
                    # Maybank transaction fields
                    "Maybank Transaction Date": maybank_tx.get("Transaction Date", ""),
                    "Maybank Posting Date": maybank_tx.get("Posting Date", ""),
                    "Maybank Description": maybank_tx.get("Transaction Description", ""),
                    "Maybank Debit": maybank_tx.get("Debit", ""),
                    "Maybank Credit": maybank_tx.get("Credit", ""),
                    "Maybank Balance": maybank_tx.get("End Balance", ""),
                }
                matched_rows.append(row)
            matched_df = pd.DataFrame(matched_rows)
        else:
            matched_df = pd.DataFrame()
        matched_df.to_excel(writer, sheet_name="Matched Transactions", index=False)
        
        # Sheet 2: Unmatched Daily Transactions
        unmatched_daily = reconciliation_data.get("unmatched_daily_transactions", [])
        if unmatched_daily:
            daily_rows = []
            for ud in unmatched_daily:
                row = {
                    "Transaction ID": ud.get("transaction_id", ""),
                    "Name": ud.get("name", ""),
                    "Total Debit": ud.get("total_debit", ""),
                    "Total Credit": ud.get("total_credit", ""),
                    "Count": ud.get("count", ""),
                }
                daily_rows.append(row)
            daily_df_sheet = pd.DataFrame(daily_rows)
        else:
            daily_df_sheet = pd.DataFrame()
        daily_df_sheet.to_excel(writer, sheet_name="Unmatched Daily", index=False)
        
        # Sheet 3: Unmatched Maybank Transactions
        unmatched_maybank = reconciliation_data.get("unmatched_maybank_transactions", [])
        if unmatched_maybank:
            maybank_df_sheet = pd.DataFrame(unmatched_maybank)
        else:
            maybank_df_sheet = pd.DataFrame()
        maybank_df_sheet.to_excel(writer, sheet_name="Unmatched Maybank", index=False)
        
        # Sheet 4: Raw Daily Transactions
        if daily_df is not None and not daily_df.empty:
            daily_df.to_excel(writer, sheet_name="Raw Daily Transactions", index=False)
        else:
            pd.DataFrame().to_excel(writer, sheet_name="Raw Daily Transactions", index=False)
        
        # Sheet 5: Raw Maybank Transactions
        if maybank_df is not None and not maybank_df.empty:
            maybank_df.to_excel(writer, sheet_name="Raw Maybank Transactions", index=False)
        else:
            pd.DataFrame().to_excel(writer, sheet_name="Raw Maybank Transactions", index=False)
            
    output.seek(0)
    return output
