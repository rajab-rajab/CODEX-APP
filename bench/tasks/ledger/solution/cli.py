import argparse
import json
import os
import sys

from ledger.parse import read_transactions
from ledger.report import balances, load_rates


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--rates", default="rates.json")
    parser.add_argument("--currency", default="EUR")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    rates = load_rates(args.rates) if os.path.exists(args.rates) else None
    currency = args.currency.upper()
    result = balances(read_transactions(args.file), rates, currency)
    if args.json:
        print(json.dumps({"currency": currency, "balances": {n: f"{v:.2f}" for n, v in sorted(result.items())}}))
    else:
        for name, value in sorted(result.items()):
            print(f"{name}: {value:.2f} {currency}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
