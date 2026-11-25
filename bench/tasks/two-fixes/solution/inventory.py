"""Stock."""

ITEMS = [
    {"name": "bread", "price": 2.5, "quantity": 3},
    {"name": "milk", "price": 1.25, "quantity": 4},
]


def total(items=ITEMS):
    return sum(item["price"] * item["quantity"] for item in items)
