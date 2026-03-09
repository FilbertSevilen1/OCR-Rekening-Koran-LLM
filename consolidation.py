def consolidate(statements):

    accounts = []
    transactions = []

    for stmt in statements:

        account = stmt["account"]

        if account not in accounts:
            accounts.append(account)

        transactions.extend(stmt["transactions"])

    transactions.sort(key=lambda x: x["date"])

    return {
        "accounts": accounts,
        "transactions": transactions
    }