"""Reading transactions from a CSV file: date,payer,payee,amount,currency,description (with a header)."""

import csv
from decimal import Decimal, InvalidOperation

from .models import Transaction


def read_transactions(path):
    rows = []
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)  # the header
        for row in reader:
            line = reader.line_num
            if not any(cell.strip() for cell in row):
                continue
            if len(row) != 6 or any(not cell.strip() for cell in row[:5]):
                raise ValueError(f"line {line}: expected date,payer,payee,amount,currency,description")
            date, payer, payee, amount, currency, description = row
            try:
                value = Decimal(amount.strip())
            except InvalidOperation:
                raise ValueError(f"line {line}: bad amount {amount!r}") from None
            if not value.is_finite():
                raise ValueError(f"line {line}: bad amount {amount!r}")
            t = Transaction(date, payer.strip(), payee.strip(), value, currency.strip().upper(), description, line)
            if not t.payees:
                raise ValueError(f"line {line}: a split with no names")
            rows.append(t)
    return rows
