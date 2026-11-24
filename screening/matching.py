"""Deterministic name screening against the OFAC list. No model involved.

How a customer is screened (mirrors how bank screening engines work):
1. Normalise the customer's name (screening/normalize.py).
2. Compare it with every list name: each entry's primary name, strong aliases (ALT.CSV)
   and weak aliases (Remarks). Each alias is scored separately, because an alias can be
   a completely different name from the primary one.
3. Name score = the better of
   - token-sort similarity (handles word order and typos), and
   - token-set similarity minus a penalty per extra/missing word (handles a dropped middle
     name without letting "Ali Hassan" fully match "Ali Hassan Mohammed Ahmed").
   Weak aliases are discounted, as OFAC itself describes them as lower quality.
4. Alert if the best name score >= threshold. The alert is name-only on purpose: DOB and
   nationality are attached as evidence for the reviewer, not used to suppress alerts,
   because missing or vague list DOBs would otherwise hide real hits.

Known gap: no transliteration table. The generator has one for building test data;
reusing it here would let the matcher "know the answers", so it is deliberately not shared.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from rapidfuzz import fuzz, process

from screening.dob import compare as compare_dob
from screening.models import Customer, MatchCandidate, SanctionsEntry, ScreeningResult
from screening.normalize import normalize, token_key

DEFAULT_THRESHOLD = 0.88
CANDIDATE_FLOOR = 0.75  # candidates below this are not returned at all
WEAK_ALIAS_FACTOR = 0.90
SUBSET_PENALTY_PER_TOKEN = 0.06


class NameSource(str, Enum):
    PRIMARY = "primary"
    ALIAS = "alias"
    WEAK_ALIAS = "weak_alias"


def name_score(a: str, b: str) -> float:
    """Similarity of two *normalised* names, 0..1."""
    sort_sim = fuzz.token_sort_ratio(a, b) / 100
    ta, tb = a.split(), b.split()
    set_sim = 0.0
    if min(len(ta), len(tb)) >= 2:  # one-word names must match on token_sort alone
        set_sim = fuzz.token_set_ratio(a, b) / 100 - SUBSET_PENALTY_PER_TOKEN * abs(len(ta) - len(tb))
    return round(max(sort_sim, set_sim, 0.0), 4)


def nationality_check(customer: Customer, entry: SanctionsEntry) -> str:
    if not customer.nationality or not entry.nationalities:
        return "unknown"
    wanted = customer.nationality.strip().lower()
    return "match" if any(n.strip().lower() == wanted for n in entry.nationalities) else "mismatch"


@dataclass(frozen=True)
class _ListName:
    entry_index: int
    source: NameSource
    original: str


class Matcher:
    def __init__(
        self,
        entries: list[SanctionsEntry],
        entity_types: set[str | None] | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        floor: float = CANDIDATE_FLOOR,
    ) -> None:
        if not floor <= threshold:
            raise ValueError("floor must be <= threshold")
        types = entity_types if entity_types is not None else {"individual"}
        self.entries = [e for e in entries if e.entity_type in types]
        self.threshold = threshold
        self.floor = floor
        self._names: list[str] = []
        self._meta: list[_ListName] = []
        for i, e in enumerate(self.entries):
            for source, names in (
                (NameSource.PRIMARY, [e.primary_name]),
                (NameSource.ALIAS, e.aliases),
                (NameSource.WEAK_ALIAS, e.weak_aliases),
            ):
                for original in names:
                    norm = normalize(original)
                    if norm:
                        self._names.append(norm)
                        self._meta.append(_ListName(i, source, original))

    def screen(self, customer: Customer) -> ScreeningResult:
        queries = [q for q in {normalize(n) for n in [customer.full_name, *customer.aliases]} if q]
        best: dict[int, tuple[float, _ListName]] = {}
        for q in queries:
            # Prefilter with BOTH scorers name_score uses. A name passes if either reaches
            # the floor, so the cut-off can never drop a name that name_score would keep.
            # (token_set is NOT always >= token_sort; checked empirically.)
            idxs = {
                idx
                for scorer in (fuzz.token_sort_ratio, fuzz.token_set_ratio)
                for _, _, idx in process.extract(
                    q, self._names, scorer=scorer, score_cutoff=self.floor * 100, limit=None
                )
            }
            for idx in idxs:
                meta = self._meta[idx]
                score = name_score(q, self._names[idx])
                if meta.source is NameSource.WEAK_ALIAS:
                    score = round(score * WEAK_ALIAS_FACTOR, 4)
                if score >= self.floor and score > best.get(meta.entry_index, (-1.0, meta))[0]:
                    best[meta.entry_index] = (score, meta)

        candidates = []
        for entry_index, (score, meta) in best.items():
            entry = self.entries[entry_index]
            dob = compare_dob(customer.date_of_birth, entry.dates_of_birth)
            candidates.append(
                MatchCandidate(
                    customer_id=customer.customer_id,
                    entry_id=entry.entry_id,
                    score=score,
                    matched_on=meta.original,
                    name_source=meta.source.value,
                    dob_check=dob.check.value,
                    dob_years_apart=dob.closest_years_apart,
                    nationality_check=nationality_check(customer, entry),
                    unparsed_list_dobs=list(dob.unparsed),
                )
            )
        candidates.sort(key=lambda c: (-c.score, c.entry_id))
        return ScreeningResult(
            customer_id=customer.customer_id,
            alert=bool(candidates) and candidates[0].score >= self.threshold,
            threshold=self.threshold,
            candidates=candidates,
        )


class ExactBaseline:
    """The naive comparison point: alert only if the normalised word set is identical to
    an entry's primary name or strong alias (order-insensitive, case/accents ignored)."""

    def __init__(self, entries: list[SanctionsEntry], entity_types: set[str | None] | None = None) -> None:
        types = entity_types if entity_types is not None else {"individual"}
        self._index: dict[frozenset[str], list[tuple[SanctionsEntry, str]]] = {}
        for e in entries:
            if e.entity_type not in types:
                continue
            for name in [e.primary_name, *e.aliases]:
                self._index.setdefault(token_key(name), []).append((e, name))

    def screen(self, customer: Customer) -> ScreeningResult:
        seen: dict[str, MatchCandidate] = {}
        for name in [customer.full_name, *customer.aliases]:
            for entry, matched in self._index.get(token_key(name), []):
                if entry.entry_id in seen:
                    continue
                dob = compare_dob(customer.date_of_birth, entry.dates_of_birth)
                seen[entry.entry_id] = MatchCandidate(
                    customer_id=customer.customer_id,
                    entry_id=entry.entry_id,
                    score=1.0,
                    matched_on=matched,
                    name_source="primary" if matched == entry.primary_name else "alias",
                    dob_check=dob.check.value,
                    dob_years_apart=dob.closest_years_apart,
                    nationality_check=nationality_check(customer, entry),
                    unparsed_list_dobs=list(dob.unparsed),
                )
        candidates = sorted(seen.values(), key=lambda c: c.entry_id)
        return ScreeningResult(
            customer_id=customer.customer_id, alert=bool(candidates), threshold=1.0, candidates=candidates
        )
