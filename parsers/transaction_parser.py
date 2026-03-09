TRANSACTION_PROMPT = """
Extract ALL transactions.

Fields:
date (DD/MM/YYYY)
description
type (DB or CR)
amount
balance

Return JSON list. If there are no transactions on this page, return an empty JSON list: []

CRITICAL: Double check your transcription for typos! 
Make sure that the running `balance` mathematically matches the amounts perfectly: `previous_balance + CR_amount - DB_amount = current_balance`.
IMPORTANT: You have been provided with BOTH an image and the "extracted raw text" of this document. 
ALWAYS prioritize the numbers (amount and balance) from the "extracted raw text" over the visual image OCR if possible and the results are make sense. The raw text is extracted from the text directly to avoid hallucinations.

Example:

[
 {
  "date":"12/02/2026",
  "description":"TRANSFER MASUK",
  "type":"CR",
  "amount":2000000,
  "balance":5000000
 }
]
"""