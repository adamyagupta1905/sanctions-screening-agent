"""Replace customer PII in free text with typed placeholders before any LLM call.

Scope: customer-side text only (documents, notes, correspondence). OFAC list data is
public and is not redacted. Customer name and nationality are not redacted either:
the case note cannot discuss a name match without them (data minimisation, not removal;
see ADR-006).

The placeholder -> original mapping is returned to the caller and must stay local.
It is never part of a prompt. Same value -> same placeholder, so the model can still
say "the email in E3 matches the one in E5".

Regexes are a floor, not a guarantee: free-form identifiers (a passport number written
as words, an address) are not caught. Measured in tests, listed in docs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Order matters: more specific patterns first, so an IBAN is not half-eaten as a phone.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){12,18}\d\b")),
    ("DATE", re.compile(
        r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/.]\d{1,2}[/.]\d{2,4}|"
        r"\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{4})\b"
    )),
    ("PHONE", re.compile(r"(?<![\w])\+?\d[\d ().-]{7,}\d\b")),
]


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass
class Redactor:
    """One per case, so placeholders are consistent across that case's documents."""

    mapping: dict[str, str] = field(default_factory=dict)  # placeholder -> original (LOCAL ONLY)
    _reverse: dict[tuple[str, str], str] = field(default_factory=dict)
    _counts: dict[str, int] = field(default_factory=dict)

    def _placeholder(self, kind: str, value: str) -> str:
        key = (kind, value)
        if key not in self._reverse:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            ph = f"[{kind}_{self._counts[kind]}]"
            self._reverse[key] = ph
            self.mapping[ph] = value
        return self._reverse[key]

    def redact(self, text: str) -> str:
        for kind, pattern in _PATTERNS:
            def repl(m: re.Match[str], kind: str = kind) -> str:
                value = m.group(0)
                if kind == "CARD":
                    digits = re.sub(r"\D", "", value)
                    if not (13 <= len(digits) <= 19 and _luhn_ok(digits)):
                        return value  # long number that is not a card: leave for PHONE check
                return self._placeholder(kind, value)

            text = pattern.sub(repl, text)
        return text

    def counts(self) -> dict[str, int]:
        return dict(self._counts)
