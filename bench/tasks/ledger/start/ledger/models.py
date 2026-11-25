from dataclasses import dataclass


@dataclass
class Transaction:
    date: str
    payer: str
    payee: str        # one name
    amount: float
    currency: str
    description: str
