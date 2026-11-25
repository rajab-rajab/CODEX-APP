import json
import shutil
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cli  # noqa: E402
from ledger.parse import read_transactions  # noqa: E402
from ledger.report import balances, load_rates  # noqa: E402

HEADER = "date,payer,payee,amount,currency,description\n"
RATES = {"EUR": Decimal("1"), "USD": Decimal("1.25"), "GBP": Decimal("0.80")}


def csv_file(tmp_path, *rows):
    f = tmp_path / "t.csv"
    f.write_text(HEADER + "".join(r + "\n" for r in rows), encoding="utf-8")
    return f


def test_decimal_and_rounding(tmp_path):
    f = csv_file(tmp_path, "d,alice,bob,0.10,EUR,a", "d,alice,bob,0.20,EUR,b")
    result = balances(read_transactions(f))
    assert result == {"alice": Decimal("0.30"), "bob": Decimal("-0.30")}
    assert all(isinstance(v, Decimal) for v in result.values())
    assert isinstance(read_transactions(f)[0].amount, Decimal)


def test_quoted_fields(tmp_path):
    f = csv_file(tmp_path, 'd,alice,bob,5.00,EUR,"lunch, with ""friends"""')
    [t] = read_transactions(f)
    assert t.description == 'lunch, with "friends"' and t.amount == Decimal("5.00")


def test_load_rates():
    assert load_rates(ROOT / "rates.json") == RATES


def test_conversion(tmp_path):
    f = csv_file(tmp_path, "d,alice,bob,12.50,USD,x")  # 12.50 USD is 10.00 EUR
    assert balances(read_transactions(f), RATES, "EUR") == {"alice": Decimal("10.00"), "bob": Decimal("-10.00")}
    assert balances(read_transactions(f), RATES, "GBP") == {"alice": Decimal("8.00"), "bob": Decimal("-8.00")}


def test_split_with_leftover_cents(tmp_path):
    f = csv_file(tmp_path, 'd,alice,"split:alice,bob,carol",10.00,EUR,x')
    assert balances(read_transactions(f)) == {"alice": Decimal("6.66"), "bob": Decimal("-3.33"),
                                              "carol": Decimal("-3.33")}
    f = csv_file(tmp_path, 'd,dave,"split:bob,carol,erin",10.00,EUR,x')
    assert balances(read_transactions(f)) == {"dave": Decimal("10.00"), "bob": Decimal("-3.34"),
                                              "carol": Decimal("-3.33"), "erin": Decimal("-3.33")}


def test_split_then_convert(tmp_path):
    # 30.00 USD among three: 10.00 USD each, 8.00 EUR each; bob paid 24.00 EUR and owes 8.00
    f = csv_file(tmp_path, 'd,bob,"split:alice,bob,carol",30.00,USD,x')
    assert balances(read_transactions(f), RATES) == {"bob": Decimal("16.00"), "alice": Decimal("-8.00"),
                                                     "carol": Decimal("-8.00")}


@pytest.mark.parametrize("row", ["d,alice,bob,ten,EUR,x", "d,alice,bob,1.00,EUR", "d,alice,split:,1.00,EUR,x"])
def test_errors_name_the_line(tmp_path, row):
    f = csv_file(tmp_path, "d,alice,bob,1.00,EUR,ok", row)
    with pytest.raises(ValueError, match="line 3"):
        balances(read_transactions(f), RATES)


def test_unknown_currency_names_the_line(tmp_path):
    f = csv_file(tmp_path, "d,alice,bob,1.00,EUR,ok", "d,alice,bob,1.00,EUR,ok", "d,alice,bob,1.00,XYZ,x")
    with pytest.raises(ValueError, match="line 4"):
        balances(read_transactions(f), RATES)


def test_cli_text_and_json(tmp_path, monkeypatch, capsys):
    shutil.copy(ROOT / "rates.json", tmp_path / "rates.json")
    f = csv_file(tmp_path, "d,carol,alice,8.00,GBP,x", "d,alice,bob,2.50,EUR,y")
    monkeypatch.chdir(tmp_path)
    assert cli.main([str(f)]) == 0
    assert capsys.readouterr().out.splitlines() == ["alice: -7.50 EUR", "bob: -2.50 EUR", "carol: 10.00 EUR"]
    assert cli.main([str(f), "--currency", "USD", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"currency": "USD", "balances": {
        "alice": "-9.38", "bob": "-3.13", "carol": "12.50"}}
