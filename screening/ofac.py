"""Parse OFAC SDN legacy CSV files into `SanctionsEntry` records.

Pure functions only (no network, no disk), so it is fully testable offline.

Format (OFAC dat_spec.txt, "FORMAT SDN CSV"): no header row, comma-delimited,
double-quoted text, null marker "-0-".
- SDN.CSV (12 cols): ent_num, SDN_Name, SDN_Type, Program, Title, Call_Sign,
  Vess_type, Tonnage, GRT, Vess_flag, Vess_owner, Remarks
- ALT.CSV (5 cols): ent_num, alt_num, alt_type, alt_name, alt_remarks
- SDN_COMMENTS.CSV: Remarks text that did not fit (optional). Live file: 2 cols
  (ent_num, continuation); spec says 12. Both accepted.

DOB, nationality and weak a.k.a.s are only in the free-text Remarks field. We pull
them out with simple rules and keep the raw text: if a value is ambiguous
("circa 1960", "1962 to 1964") it stays as written, it is never normalised to a guess.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from screening.models import SanctionsEntry

NULL = "-0-"
SDN_COLUMNS = 12
ALT_COLUMNS = 5
# The 2023 spec says SDN_COMMENTS.CSV mirrors SDN.CSV (12 cols); the live file seen on
# 2026-11-23 has 2 (ent_num, remarks continuation). Accept both, nothing else.
COMMENT_COLUMNS = {2, SDN_COLUMNS}

_WEAK_AKA = re.compile(r"a\.k\.a\.\s*'([^']+)'", re.IGNORECASE)


class OfacFormatError(ValueError):
    """The file does not match the published layout. Stop rather than guess."""


@dataclass
class ParseReport:
    """Counts written to the manifest so data problems are visible, not silent."""

    sdn_rows: int = 0
    alt_rows: int = 0
    comment_rows: int = 0
    orphan_alt_rows: list[str] = field(default_factory=list)  # ent_num not in SDN
    orphan_comment_rows: list[str] = field(default_factory=list)


def _clean(value: str) -> str | None:
    value = value.strip()
    return None if value in ("", NULL) else value


def _rows(text: str, expected_cols: int | set[int], name: str) -> list[list[str]]:
    allowed = {expected_cols} if isinstance(expected_cols, int) else expected_cols
    rows = []
    for i, row in enumerate(csv.reader(io.StringIO(text, newline=""))):
        if not row or all(not c.strip() for c in row):
            continue  # blank lines / trailing EOF marker
        if len(row) == 1 and row[0].strip() == "\x1a":
            continue  # DOS end-of-file character
        if len(row) not in allowed:
            raise OfacFormatError(f"{name} line {i + 1}: {len(row)} columns, expected {sorted(allowed)}")
        if not row[0].strip().isdigit():
            raise OfacFormatError(f"{name} line {i + 1}: ent_num {row[0]!r} is not an integer")
        rows.append(row)
    return rows


def _split_programs(program: str | None) -> list[str]:
    """'SDGT] [IRGC' -> ['SDGT', 'IRGC']."""
    if not program:
        return []
    return [p.strip("[] ") for p in re.split(r"\]\s*\[", program) if p.strip("[] ")]


def parse_remarks(remarks: str | None) -> dict[str, list[str]]:
    """Split Remarks on ';' and pick out DOB, nationality and weak aliases."""
    out: dict[str, list[str]] = {"dates_of_birth": [], "nationalities": [], "weak_aliases": []}
    if not remarks:
        return out
    out["weak_aliases"] = [m.strip() for m in _WEAK_AKA.findall(remarks)]
    for part in remarks.split(";"):
        part = part.strip().rstrip(".").strip()
        low = part.lower()
        if low.startswith("dob "):
            out["dates_of_birth"].append(part[4:].strip())
        elif low.startswith("alt. dob "):
            out["dates_of_birth"].append(part[9:].strip())
        elif low.startswith("nationality "):
            out["nationalities"].append(part[12:].strip())
        elif low.startswith("alt. nationality "):
            out["nationalities"].append(part[17:].strip())
    return out


def parse_sdn(
    sdn_csv: str, alt_csv: str = "", comments_csv: str = ""
) -> tuple[list[SanctionsEntry], ParseReport]:
    report = ParseReport()
    sdn_rows = _rows(sdn_csv, SDN_COLUMNS, "SDN.CSV")
    report.sdn_rows = len(sdn_rows)

    remarks: dict[str, str | None] = {r[0].strip(): _clean(r[11]) for r in sdn_rows}
    if comments_csv:
        comment_rows = _rows(comments_csv, COMMENT_COLUMNS, "SDN_COMMENTS.CSV")
        report.comment_rows = len(comment_rows)
        for r in comment_rows:
            # Continuation text resumes mid-word, so it is appended with no separator.
            ent, extra = r[0].strip(), _clean(r[-1])
            if ent not in remarks:
                report.orphan_comment_rows.append(ent)
            elif extra:
                remarks[ent] = (remarks[ent] or "") + extra

    aliases: dict[str, list[str]] = {}
    if alt_csv:
        alt_rows = _rows(alt_csv, ALT_COLUMNS, "ALT.CSV")
        report.alt_rows = len(alt_rows)
        for r in alt_rows:
            ent, alt_name = r[0].strip(), _clean(r[3])
            if ent not in remarks:
                report.orphan_alt_rows.append(ent)
            elif alt_name:
                aliases.setdefault(ent, []).append(alt_name)

    entries = []
    for r in sdn_rows:
        ent = r[0].strip()
        name = _clean(r[1])
        if name is None:
            raise OfacFormatError(f"SDN.CSV ent_num {ent}: missing SDN_Name")
        program = _clean(r[3])
        parsed = parse_remarks(remarks[ent])
        entries.append(
            SanctionsEntry(
                entry_id=ent,
                primary_name=name,
                entity_type=_clean(r[2]),
                programs=_split_programs(program),
                title=_clean(r[4]),
                aliases=aliases.get(ent, []),
                remarks=remarks[ent],
                **parsed,
            )
        )
    return entries, report
