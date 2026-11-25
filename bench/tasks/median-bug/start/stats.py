"""Small statistics helpers."""


def mean(values):
    return sum(values) / len(values)


def median(values):
    values.sort()
    mid = len(values) // 2
    return values[mid]
