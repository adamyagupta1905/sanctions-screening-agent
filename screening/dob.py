"""Compare a customer's date of birth with OFAC's free-text DOBs, at the precision given.

OFAC writes DOBs as "14 Mar 1968", "1968", "circa 1968", "1966 to 1968", "Mar 1968".
We never pad a year into a full date. Each list value becomes a span (earliest,
latest possible date) and the comparison says how precise the agreement is.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from enum import Enum

_MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
_FULL = re.compile(r"^(\d{1,2}) ([A-Z][a-z]{2}) (\d{4})$")
_MONTH_YEAR = re.compile(r"^([A-Z][a-z]{2}) (\d{4})$")
_YEAR = re.compile(r"^(\d{4})$")
_CIRCA = re.compile(r"^circa (\d{4})$", re.IGNORECASE)
_RANGE = re.compile(r"^(\d{4}) to (\d{4})$")

CIRCA_YEARS = 2  # documented assumption: "circa 1968" read as 1966-1970


class DobCheck(str, Enum):
    EXACT = "exact"  # same full date
    WITHIN_SPAN = "within_span"  # inside a year / month / circa / range value
    MISMATCH = "mismatch"  # outside every list value
    UNKNOWN = "unknown"  # customer DOB missing, or no list DOB we can read


@dataclass(frozen=True)
class DobSpan:
    raw: str
    earliest: date
    latest: date

    @property
    def is_full_date(self) -> bool:
        return self.earliest == self.latest


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_span(raw: str) -> DobSpan | None:
    """None when the text is not one of the known shapes (it is reported, not guessed)."""
    s = raw.strip()
    try:
        if m := _FULL.match(s):
            d = date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
            return DobSpan(raw, d, d)
        if m := _MONTH_YEAR.match(s):
            y, mo = int(m.group(2)), _MONTHS[m.group(1)]
            return DobSpan(raw, date(y, mo, 1), _month_end(y, mo))
        if m := _YEAR.match(s):
            y = int(m.group(1))
            return DobSpan(raw, date(y, 1, 1), date(y, 12, 31))
        if m := _CIRCA.match(s):
            y = int(m.group(1))
            return DobSpan(raw, date(y - CIRCA_YEARS, 1, 1), date(y + CIRCA_YEARS, 12, 31))
        if m := _RANGE.match(s):
            a, b = sorted((int(m.group(1)), int(m.group(2))))
            return DobSpan(raw, date(a, 1, 1), date(b, 12, 31))
    except (KeyError, ValueError):
        return None
    return None


@dataclass(frozen=True)
class DobResult:
    check: DobCheck
    closest_years_apart: int | None  # for MISMATCH: smallest gap in whole years
    unparsed: tuple[str, ...]  # list values we could not read (shown to the reviewer)


def compare(customer_dob: date | None, list_dobs: list[str]) -> DobResult:
    spans = [s for s in (parse_span(r) for r in list_dobs) if s]
    unparsed = tuple(r for r in list_dobs if parse_span(r) is None)
    if customer_dob is None or not spans:
        return DobResult(DobCheck.UNKNOWN, None, unparsed)
    if any(s.is_full_date and s.earliest == customer_dob for s in spans):
        return DobResult(DobCheck.EXACT, None, unparsed)
    if any(s.earliest <= customer_dob <= s.latest for s in spans):
        return DobResult(DobCheck.WITHIN_SPAN, None, unparsed)
    gap = min(
        min(abs(customer_dob.year - s.earliest.year), abs(customer_dob.year - s.latest.year))
        for s in spans
    )
    return DobResult(DobCheck.MISMATCH, gap, unparsed)
