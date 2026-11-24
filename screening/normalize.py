"""Name normalisation shared by the generator and (later) the matcher.

Deterministic, no model involved. Lowercase, strip diacritics, drop punctuation,
collapse whitespace, and turn OFAC's "SURNAME, Given Names" into natural order.
"""

from __future__ import annotations

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")


def strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def natural_order(name: str) -> str:
    """'QUILLFEATHER, Marten Ivo' -> 'Marten Ivo QUILLFEATHER'. No comma: unchanged."""
    if "," not in name:
        return name.strip()
    surname, _, given = name.partition(",")
    return f"{given.strip()} {surname.strip()}".strip()


def normalize(name: str) -> str:
    text = strip_diacritics(natural_order(name)).lower()
    text = _NON_ALNUM.sub(" ", text.replace("-", " ").replace("'", ""))
    return " ".join(text.split())


def token_key(name: str) -> frozenset[str]:
    """Order-insensitive key, used to detect accidental collisions."""
    return frozenset(normalize(name).split())
