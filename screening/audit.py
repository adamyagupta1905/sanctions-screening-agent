"""Append-only, hash-chained audit log in SQLite.

Why: a screening control must be able to show, months later, what was screened, against
which list snapshot, what the model was asked (prompt version + hash), what it produced,
who decided, and why. Two properties make the log trustworthy:

1. Append-only: SQLite triggers reject UPDATE and DELETE on the table.
2. Tamper-evident: each row stores sha256(previous row hash + this row's content).
   Anyone who edits the file directly (bypassing the triggers) breaks the chain, and
   `verify()` reports the first broken row.

What it does not do: stop someone with file access from deleting the whole file or
rewriting the entire chain consistently. In production the chain head would be anchored
somewhere else (a WORM store, a periodic signed checkpoint). Noted in ADR-008.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GENESIS = "0" * 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    case_ref   TEXT NOT NULL,
    step       TEXT NOT NULL,
    at         TEXT NOT NULL,
    payload    TEXT NOT NULL,      -- canonical JSON of the full event
    prev_hash  TEXT NOT NULL,
    hash       TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS audit_case ON audit_events(case_ref, seq);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;
"""


def _canonical(event: dict[str, Any]) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _row_hash(prev_hash: str, payload: str) -> str:
    return hashlib.sha256((prev_hash + "\n" + payload).encode()).hexdigest()


@dataclass(frozen=True)
class AuditRow:
    seq: int
    case_ref: str
    step: str
    at: str
    event: dict[str, Any]
    hash: str


class AuditLog:
    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def append(self, event: dict[str, Any]) -> AuditRow:
        """Event must carry case_ref, step and at. Stored exactly as given."""
        for key in ("case_ref", "step", "at"):
            if not event.get(key):
                raise ValueError(f"audit event missing '{key}'")
        payload = _canonical(event)
        with self._lock:
            # BEGIN IMMEDIATE: no other writer can slip a row in between read and insert.
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute("SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
                prev = row[0] if row else GENESIS
                h = _row_hash(prev, payload)
                cur = self._conn.execute(
                    "INSERT INTO audit_events (case_ref, step, at, payload, prev_hash, hash) VALUES (?,?,?,?,?,?)",
                    (event["case_ref"], event["step"], event["at"], payload, prev, h),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return AuditRow(cur.lastrowid, event["case_ref"], event["step"], event["at"], json.loads(payload), h)

    def case_history(self, case_ref: str) -> list[AuditRow]:
        rows = self._conn.execute(
            "SELECT seq, case_ref, step, at, payload, hash FROM audit_events WHERE case_ref=? ORDER BY seq",
            (case_ref,),
        ).fetchall()
        return [AuditRow(s, c, st, at, json.loads(p), h) for s, c, st, at, p, h in rows]

    def verify(self) -> int | None:
        """Recompute the whole chain. Returns the seq of the first bad row, or None if intact."""
        prev = GENESIS
        for seq, payload, stored_prev, stored_hash in self._conn.execute(
            "SELECT seq, payload, prev_hash, hash FROM audit_events ORDER BY seq"
        ):
            if stored_prev != prev or _row_hash(prev, payload) != stored_hash:
                return seq
            prev = stored_hash
        return None

    def close(self) -> None:
        self._conn.close()
