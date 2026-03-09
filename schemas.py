from pydantic import BaseModel
from typing import List, Optional

class AccountInfo(BaseModel):
    bank: str
    account_number: str
    account_holder: str
    currency: str

class Summary(BaseModel):
    begin_balance: float
    end_balance: float
    total_debit: Optional[int] = None
    total_credit: Optional[int] = None
    total_debit_amount: Optional[float] = None
    total_credit_amount: Optional[float] = None

class Transaction(BaseModel):
    date: str
    description: str
    type: str
    amount: float
    balance: float
    account_number: str

class Proof(BaseModel):
    calculated_total_debit: float
    calculated_total_credit: float
    expected_total_debit: Optional[float] = None
    expected_total_credit: Optional[float] = None
    debit_match: str
    credit_match: str
    balance_match: str
    calculated_end_balance: float
    running_balance_issues: list = []

class Statement(BaseModel):
    account: AccountInfo
    summary: Summary
    proof: Proof
    transactions: List[Transaction]

class ConsolidatedLedger(BaseModel):
    accounts: List[AccountInfo]
    transactions: List[Transaction]