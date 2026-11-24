"""Handling of untrusted text (customer names, documents, OFAC remarks) before it reaches a model.

Three layers, in order of how much we rely on them:
1. Architecture (the real defence, enforced elsewhere): the model cannot set a score, an
   alert or a disposition. Those come from Python and from a human. Injected text can at
   worst distort the free-text case note, which a human reads next to the evidence.
2. Wrapping: every untrusted string is placed in a <data> block with an id, and '<', '>'
   and '&' are escaped so the text cannot close the block or open a fake instruction tag.
3. Detection: pattern flags for common injection phrasing. Shown to the reviewer and
   logged. Easy to evade by paraphrase, so it is a signal, never a gate.
"""

from __future__ import annotations

import html
import re

# name -> pattern. Case-insensitive. Kept short and readable on purpose.
INJECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    name: re.compile(p, re.IGNORECASE)
    for name, p in {
        "ignore_instructions": r"\b(ignore|disregard|forget|override)\b.{0,40}\b(instructions?|prompts?|rules|guidelines|above|previous)\b",
        "role_change": r"\b(you are now|act as|pretend (to be|you are)|from now on you)\b",
        "fake_role_tag": r"</?\s*(system|assistant|user|instructions?)\s*>|^\s*(system|assistant)\s*:",
        "set_outcome": r"\b(mark|set|classify|treat|record|label)\b.{0,40}\b(cleared|false positive|not a match|no match|safe|approved|low risk)\b",
        "claimed_clearance": r"\b(pre-?cleared|already (been )?(cleared|approved|verified|whitelisted)|whitelisted by)\b",
        "suppress": r"\b(do not|don't|never)\b.{0,20}\b(flag|escalate|report|mention|alert)\b",
        "addressed_to_model": r"\b(note|message|instruction)s? (to|for) (the )?(ai|assistant|model|llm|bot|screening system)\b",
    }.items()
}


def scan_for_injection(text: str) -> list[str]:
    """Names of the patterns that fire. Empty list = nothing detected (NOT 'safe')."""
    return [name for name, pattern in INJECTION_PATTERNS.items() if pattern.search(text)]


_ID = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


def wrap(evidence_id: str, source: str, text: str) -> str:
    """Render one untrusted string as an escaped, labelled data block."""
    if not _ID.match(evidence_id) or not _ID.match(source):
        raise ValueError("evidence_id and source must be simple identifiers")
    body = html.escape(text, quote=False)
    return f'<data id="{evidence_id}" source="{source}">\n{body}\n</data>'
