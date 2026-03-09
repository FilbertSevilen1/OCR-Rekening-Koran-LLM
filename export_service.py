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
