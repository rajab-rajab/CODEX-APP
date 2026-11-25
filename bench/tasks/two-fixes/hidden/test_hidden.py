import json

import cli
from inventory import total


def test_total_uses_quantity():
    assert total() == 12.5
    assert total([{"name": "x", "price": 2, "quantity": 5}]) == 10
    assert total([]) == 0


def test_text_report(capsys):
    assert cli.main([]) == 0
    assert capsys.readouterr().out.strip() == "Items: 2, total: 12.50"


def test_json_report(capsys):
    assert cli.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"items": 2, "total": 12.5}
