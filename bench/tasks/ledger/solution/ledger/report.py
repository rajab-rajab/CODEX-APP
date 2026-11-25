"""Balances: what each person paid minus what they owe."""

import json
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def load_rates(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {code.upper(): Decimal(str(rate)) for code, rate in data["rates"].items()}


def _shares(amount, names):
    cents = int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))
    each, extra = divmod(cents, len(names))
    return [(name, Decimal(each + (1 if i < extra else 0)) / 100) for i, name in enumerate(names)]


def balances(transactions, rates=None, target="EUR"):
    target = target.upper()
    if rates is not None and target not in rates:
        raise ValueError(f"unknown target currency {target}")

    def convert(amount, t):
        if t.currency == target:
            return amount
        if rates is None or t.currency not in rates:
            raise ValueError(f"line {t.line}: unknown currency {t.currency}")
        return amount / rates[t.currency] * rates[target]

    totals = {}
    for t in transactions:
        if rates is not None and t.currency not in rates:
            raise ValueError(f"line {t.line}: unknown currency {t.currency}")
        totals[t.payer] = totals.get(t.payer, Decimal(0)) + convert(t.amount, t)
        for name, share in _shares(t.amount, t.payees):
            totals[name] = totals.get(name, Decimal(0)) - convert(share, t)
    return {name: value.quantize(CENT, rounding=ROUND_HALF_UP) for name, value in totals.items()}
