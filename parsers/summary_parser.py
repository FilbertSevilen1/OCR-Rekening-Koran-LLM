SUMMARY_PROMPT = """
Extract bank statement summary.
CRITICAL: Double check your transcription for typos. A single wrong digit will break the math! Carefully transcribe these specific values exactly as written.

NOTE: 
- `total_debit` and `total_credit` should reflect the COUNT (number of transactions).
- `total_debit_amount` and `total_credit_amount` should reflect the actual monetary value sum. 
- If a value does not exist in the summary section of the document, return `null` for that field rather than 0!

Return JSON:

{
 "begin_balance": 0.0,
 "end_balance": 0.0,
 "total_debit": 0,
 "total_credit": 0,
 "total_debit_amount": 0.0,
 "total_credit_amount": 0.0
}
"""