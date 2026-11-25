"""Splitting a list into pages."""


def paginate(items, page, size=10):
    start = page * size
    return items[start:start + size]


def page_count(items, size=10):
    return len(items) // size
