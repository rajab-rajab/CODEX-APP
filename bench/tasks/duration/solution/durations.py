"""Durations."""

import re

_DURATION = re.compile(r"^\s*(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?\s*$")


def parse_duration(text):
    found = _DURATION.match(text or "")
    if not found or not any(found.groups()):
        raise ValueError(f"not a duration: {text!r}")
    hours, minutes, seconds = (int(g or 0) for g in found.groups())
    return hours * 3600 + minutes * 60 + seconds
