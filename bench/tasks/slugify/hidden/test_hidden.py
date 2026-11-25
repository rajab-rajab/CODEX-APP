from text_utils import slugify, title_case


def test_examples():
    assert slugify("  Héllo, World!  ") == "hello-world"
    assert slugify("Crème brûlée -- recipe #2") == "creme-brulee-recipe-2"
    assert slugify("already-a-slug") == "already-a-slug"
    assert slugify("Ünïcödé") == "unicode"


def test_edges():
    assert slugify("") == ""
    assert slugify("!!!") == ""
    assert slugify("a___b") == "a-b"


def test_title_case_unchanged():
    assert title_case("hello world") == "Hello World"
