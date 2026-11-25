"""Reading transactions from a CSV file: date,payer,payee,amount,currency,description (with a header)."""

from .models import Transaction


def read_transactions(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        next(f)  # the header
        for line in f:
            if not line.strip():
                continue
            date, payer, payee, amount, currency, description = line.rstrip("\n").split(",")
            rows.append(Transaction(date, payer, payee, float(amount), currency, description))
    return rows
