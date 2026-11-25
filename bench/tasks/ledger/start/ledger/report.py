"""Balances: what each person paid minus what they owe."""


def balances(transactions):
    totals = {}
    for t in transactions:
        totals[t.payer] = totals.get(t.payer, 0) + t.amount
        totals[t.payee] = totals.get(t.payee, 0) - t.amount
    return totals
