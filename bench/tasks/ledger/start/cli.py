import sys

from ledger.parse import read_transactions
from ledger.report import balances


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    for name, amount in balances(read_transactions(argv[0])).items():
        print(f"{name}: {amount}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
