"""Splitting a list into pages."""


def paginate(items, page, size=10):
    if page < 1 or size < 1:
        raise ValueError("page and size must be 1 or more")
    start = (page - 1) * size
    return items[start:start + size]


def page_count(items, size=10):
    if size < 1:
        raise ValueError("size must be 1 or more")
    return -(-len(items) // size)
