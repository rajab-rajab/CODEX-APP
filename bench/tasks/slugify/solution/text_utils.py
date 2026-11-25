"""Text helpers."""

import re
import unicodedata


def title_case(text):
    return " ".join(word.capitalize() for word in text.split())


def slugify(text):
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")
