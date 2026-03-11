from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from typing import List

from orchestrator.statement_orchestrator import process_statement
from orchestrator.reconciliation_orchestrator import process_reconciliation
from consolidation import consolidate
from export_service import create_export_file

app = FastAPI()


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

@app.post("/reconciliate")
async def reconciliate(
    file_branch: UploadFile = File(...),
    file_finance: UploadFile = File(...)
):
    branch_content = await file_branch.read()
    finance_content = await file_finance.read()
    
    result = await process_reconciliation(branch_content, finance_content)
    
    return result