from dataclasses import dataclass
from decimal import Decimal


@dataclass
class Transaction:
    date: str
    payer: str
    payee: str        # one name, or split:NAME1,NAME2,...
    amount: Decimal
    currency: str
    description: str
    line: int = 0     # the CSV line it came from (the header is line 1)

    @property
    def payees(self):
        if not self.payee.startswith("split:"):
            return [self.payee]
        return [n.strip() for n in self.payee[len("split:"):].split(",") if n.strip()]
