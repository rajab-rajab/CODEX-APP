import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ledger.parse import read_transactions  # noqa: E402
from ledger.report import balances  # noqa: E402


def test_one_payment(tmp_path):
    f = tmp_path / "t.csv"
    f.write_text("date,payer,payee,amount,currency,description\n2026-01-02,alice,bob,10.00,EUR,lunch\n")
    result = balances(read_transactions(f))
    assert result["alice"] == 10 and result["bob"] == -10
