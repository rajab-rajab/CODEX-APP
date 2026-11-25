from pathlib import Path

import app
import report
import users


def test_new_name_and_alias():
    assert users.get_user(1) == "ada"
    assert users.get_usr(2) == "grace"
    assert users.get_user(3) is None


def test_callers_use_the_new_name():
    for name in ("app.py", "report.py"):
        text = Path(name).read_text(encoding="utf-8")
        assert "get_usr" not in text and "get_user" in text, name


def test_behaviour_unchanged():
    assert app.greeting(1) == "Hello, ada!"
    assert app.greeting(9) == "Hello, stranger!"
    assert report.report([1, 9, 2]) == ["ada", "?", "grace"]
