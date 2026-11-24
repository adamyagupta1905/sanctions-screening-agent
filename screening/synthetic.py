"""Labelled synthetic customers for evaluating screening.

Three kinds of customer, each with ground-truth labels set by construction:

| kind            | built from                            | should_alert | is_true_match |
|-----------------|---------------------------------------|--------------|---------------|
| true_match      | a real SDN individual, perturbed      | True         | True          |
| hard_negative   | SDN name, DOB >= 10 years away,        | True         | False         |
|                 | different/no nationality              |              |               |
| clean           | random names from a fixed list        | False        | False         |
| lookalike_clean | given name of listed person A +       | False        | False         |
|                 | surname of listed person B (same      |              |               |
|                 | nationality); not equal to, or a      |              |               |
|                 | sub/superset of, any list name        |              |               |

Look-alikes measure false-alert *workload*: they sound like list names (same regions,
same naming conventions) without being anyone on the list. An alert on one is not a
compliance failure, it is analyst time, which is the cost banks try to reduce.

`should_alert` = the name is close enough that a screening system should raise it
for review. `is_true_match` = it is actually the listed person. The gap between the
two is what the human reviewer (and the LLM case note) exists to resolve.

Business note: the customer records are synthetic; only the list side is real public
data. Where the list gives a year-only DOB, the *customer* gets a full date inside that
year (customers always have full DOBs in a bank's records). The list value is never changed.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from datetime import date, timedelta
from enum import Enum

from pydantic import BaseModel, Field

from screening.models import Customer, SanctionsEntry
from screening.normalize import natural_order, token_key

# --- perturbations: each is a pure function of (name, rng) ---------------------------

# Known spelling variants of the same name across transliteration schemes. Small and
# hand-written on purpose: every pair is a documented real-world variant.
TRANSLITERATIONS: dict[str, list[str]] = {
    "mohammed": ["muhammad", "mohamed", "mohammad"],
    "muhammad": ["mohammed", "mohamed", "mohammad"],
    "mohammad": ["mohammed", "muhammad"],
    "mohamed": ["mohammed", "muhammad"],
    "abdul": ["abdel", "abd al"],
    "abdel": ["abdul", "abd al"],
    "yusuf": ["youssef", "yousef"],
    "youssef": ["yusuf", "yousef"],
    "hussein": ["husayn", "hussain"],
    "husayn": ["hussein", "hussain"],
    "ali": ["aly"],
    "ahmad": ["ahmed"],
    "ahmed": ["ahmad"],
    "aleksandr": ["alexander", "aleksander"],
    "alexander": ["aleksandr"],
    "sergei": ["sergey"],
    "sergey": ["sergei"],
    "yuri": ["yury", "iurii"],
    "yury": ["yuri"],
    "dmitri": ["dmitry", "dmitriy"],
    "dmitry": ["dmitri", "dmitriy"],
    "andrei": ["andrey"],
    "andrey": ["andrei"],
    "nikolai": ["nikolay"],
    "nikolay": ["nikolai"],
    "evgeny": ["yevgeny", "evgeniy"],
    "yevgeny": ["evgeny"],
    "kim": ["gim"],
}


class Perturbation(str, Enum):
    REORDER = "reorder"  # natural order instead of "SURNAME, Given"
    PUNCT = "punct"  # punctuation removed ("Sa'ad Al-Ahmad" -> "Saad Al Ahmad")
    TYPO = "typo"  # one-character edit in one token
    DROP_TOKEN = "drop_token"  # a middle token removed
    TRANSLITERATION = "transliteration"
    STRONG_ALIAS = "strong_alias"  # name taken from ALT.CSV alias
    CUSTOMER_DOB_MISSING = "customer_dob_missing"
    LIST_DOB_YEAR_ONLY = "list_dob_year_only"
    LIST_DOB_MISSING = "list_dob_missing"


def reorder(name: str) -> str:
    return natural_order(name)


def strip_punct(name: str) -> str:
    text = natural_order(name).replace("'", "").replace("’", "")
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")


def customer_style(name: str) -> str:
    """Title case, as a bank's own records would hold it. Applied to EVERY customer so
    letter case cannot reveal whether a record was derived from the list."""
    return " ".join(_WORD.sub(lambda m: m.group(0).capitalize(), name).split())


def typo(name: str, rng: random.Random) -> str | None:
    tokens = natural_order(name).split()
    candidates = [i for i, t in enumerate(tokens) if len(t) >= 5 and t.isalpha()]
    if not candidates:
        return None
    i = rng.choice(candidates)
    t = tokens[i]
    pos = rng.randrange(1, len(t) - 1)  # keep first letter: realistic keying error
    op = rng.choice(["swap", "drop", "double"])
    if op == "swap":
        t = t[:pos] + t[pos + 1] + t[pos] + t[pos + 2 :]
    elif op == "drop":
        t = t[:pos] + t[pos + 1 :]
    else:
        t = t[:pos] + t[pos] + t[pos:]
    tokens[i] = t
    return " ".join(tokens)


def drop_token(name: str, rng: random.Random) -> str | None:
    tokens = natural_order(name).split()
    if len(tokens) < 3:
        return None
    del tokens[rng.randrange(1, len(tokens) - 1)]  # never first or last
    return " ".join(tokens)


def transliterate(name: str, rng: random.Random) -> str | None:
    tokens = natural_order(name).split()
    hits = [i for i, t in enumerate(tokens) if t.lower() in TRANSLITERATIONS]
    if not hits:
        return None
    i = rng.choice(hits)
    tokens[i] = rng.choice(TRANSLITERATIONS[tokens[i].lower()]).title()
    return " ".join(tokens)


# --- DOB handling ----------------------------------------------------------------------

_FULL_DOB = re.compile(r"^(\d{2}) ([A-Z][a-z]{2}) (\d{4})$")
_YEAR_DOB = re.compile(r"^(\d{4})$")
_MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}


def parse_list_dob(raw: str) -> tuple[str, date | int] | None:
    """('full', date) or ('year', 1965). Anything vaguer (circa, ranges) -> None."""
    raw = raw.strip()
    if m := _FULL_DOB.match(raw):
        try:
            return "full", date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
        except (KeyError, ValueError):
            return None
    if m := _YEAR_DOB.match(raw):
        return "year", int(m.group(1))
    return None


def _random_date_in_year(year: int, rng: random.Random) -> date:
    start = date(year, 1, 1)
    return start + timedelta(days=rng.randrange((date(year + 1, 1, 1) - start).days))


# --- output model ---------------------------------------------------------------------


class CustomerKind(str, Enum):
    TRUE_MATCH = "true_match"
    HARD_NEGATIVE = "hard_negative"
    CLEAN = "clean"
    LOOKALIKE_CLEAN = "lookalike_clean"  # list-style name, not on the list


class LabelledCustomer(BaseModel):
    customer: Customer
    kind: CustomerKind
    should_alert: bool
    is_true_match: bool
    source_entry_id: str | None = None  # SDN entry the record was derived from
    perturbations: list[Perturbation] = Field(default_factory=list)


# --- clean-name pool ------------------------------------------------------------------

GIVEN_NAMES = (
    "Olivia Liam Amara Kenji Priya Mateo Sofia Lars Ingrid Tomasz Aisha Chen Wei Lucia "
    "Raj Ananya Kofi Ama Hugo Elodie Niamh Callum Freya Arjun Mei Diego Valentina Sipho "
    "Thandiwe Emre Selin Nora Henrik Isla Rohan Kavya Tariq Leila Pavel Zofia Bruno Ines"
).split()
SURNAMES = (
    "Hartley Okafor Lindqvist Moreau Takahashi Fernandes Novak Brennan Haddad Iyer "
    "Mensah Kowalczyk Castellano Ruiz Andersen Whitfield Nakamura Duarte Osei Petrov "
    "Varga Sorensen Dlamini Kaur Albrecht Quintero Fairbairn Mbeki Yilmaz Ashworth"
).split()
COUNTRIES = "United Kingdom;India;Germany;Brazil;Japan;Kenya;Canada;France;Poland;Ghana".split(";")


# --- generator ------------------------------------------------------------------------


class Generator:
    """Deterministic for a given (entries, seed)."""

    def __init__(self, entries: list[SanctionsEntry], seed: int = 7) -> None:
        self.rng = random.Random(seed)
        self.individuals = [e for e in entries if e.entity_type == "individual"]
        self._list_keys: set[frozenset[str]] = set()
        for e in self.individuals:
            self._list_keys.add(token_key(e.primary_name))
            self._list_keys.update(token_key(a) for a in e.aliases + e.weak_aliases)
        self.rejected_clean_collisions = 0
        self.rejected_lookalikes = 0
        self._by_nationality: dict[str, list[SanctionsEntry]] = {}
        for e in self.individuals:
            if e.nationalities:
                self._by_nationality.setdefault(e.nationalities[0], []).append(e)

    # true matches -------------------------------------------------------------------
    def true_match(self, entry: SanctionsEntry) -> LabelledCustomer:
        rng = self.rng
        tags: list[Perturbation] = []
        base = entry.primary_name
        if entry.aliases and rng.random() < 0.2:
            base = rng.choice(entry.aliases)
            tags.append(Perturbation.STRONG_ALIAS)

        name = reorder(base)
        tags.append(Perturbation.REORDER)
        for kind, fn in (
            (Perturbation.TRANSLITERATION, transliterate),
            (Perturbation.TYPO, typo),
            (Perturbation.DROP_TOKEN, drop_token),
        ):
            if rng.random() < 0.35 and (new := fn(name, rng)) is not None:
                name = new
                tags.append(kind)
        if rng.random() < 0.5:
            name = strip_punct(name)
            tags.append(Perturbation.PUNCT)

        dob = self._customer_dob_for(entry, tags)
        if dob is not None and rng.random() < 0.1:
            dob = None
            tags.append(Perturbation.CUSTOMER_DOB_MISSING)

        return LabelledCustomer(
            customer=Customer(
                customer_id="pending",  # numbered after shuffle
                full_name=customer_style(name),
                date_of_birth=dob,
                nationality=entry.nationalities[0] if entry.nationalities else None,
            ),
            kind=CustomerKind.TRUE_MATCH,
            should_alert=True,
            is_true_match=True,
            source_entry_id=entry.entry_id,
            perturbations=tags,
        )

    def _customer_dob_for(self, entry: SanctionsEntry, tags: list[Perturbation]) -> date | None:
        parsed = [p for p in (parse_list_dob(d) for d in entry.dates_of_birth) if p]
        if not parsed:
            tags.append(Perturbation.LIST_DOB_MISSING)
            return None
        kind, value = parsed[0]
        if kind == "full":
            return value  # type: ignore[return-value]
        tags.append(Perturbation.LIST_DOB_YEAR_ONLY)
        return _random_date_in_year(value, self.rng)  # type: ignore[arg-type]

    # hard negatives -----------------------------------------------------------------
    def hard_negative(self, entry: SanctionsEntry) -> LabelledCustomer | None:
        """Same name as a listed person, provably different person (DOB far away)."""
        parsed = [p for p in (parse_list_dob(d) for d in entry.dates_of_birth) if p]
        if not parsed or len(parsed) != len(entry.dates_of_birth):
            return None  # vague or multiple-format DOBs: cannot prove "different person"
        years = [v.year if k == "full" else v for k, v in parsed]  # type: ignore[union-attr]
        shift = self.rng.choice([-1, 1]) * self.rng.randint(10, 25)
        year = min(years) + shift if shift < 0 else max(years) + shift
        if not 1920 <= year <= 2005:
            return None
        other = [c for c in COUNTRIES if c not in entry.nationalities]
        return LabelledCustomer(
            customer=Customer(
                customer_id="pending",  # numbered after shuffle
                full_name=customer_style(reorder(entry.primary_name)),
                date_of_birth=_random_date_in_year(year, self.rng),
                nationality=self.rng.choice(other),
            ),
            kind=CustomerKind.HARD_NEGATIVE,
            should_alert=True,
            is_true_match=False,
            source_entry_id=entry.entry_id,
            perturbations=[Perturbation.REORDER],
        )

    # clean customers ----------------------------------------------------------------
    def clean(self) -> LabelledCustomer:
        while True:
            name = f"{self.rng.choice(GIVEN_NAMES)} {self.rng.choice(SURNAMES)}"
            if token_key(name) not in self._list_keys:
                break
            self.rejected_clean_collisions += 1
        return LabelledCustomer(
            customer=Customer(
                customer_id="pending",  # numbered after shuffle
                full_name=customer_style(name),
                date_of_birth=_random_date_in_year(self.rng.randint(1945, 2003), self.rng),
                nationality=self.rng.choice(COUNTRIES),
            ),
            kind=CustomerKind.CLEAN,
            should_alert=False,
            is_true_match=False,
        )

    # look-alike clean customers -------------------------------------------------------
    def _too_close_to_list(self, key: frozenset[str]) -> bool:
        """Equal to, or a subset/superset of, any list name: that would be a real hit
        (e.g. a listed person with a dropped name), so it cannot be labelled clean."""
        return any(key == k or (len(k) >= 2 and (key <= k or k <= key)) for k in self._list_keys)

    @staticmethod
    def _split_name(primary: str) -> tuple[list[str], list[str]] | None:
        if "," not in primary:
            return None
        surname, _, given = primary.partition(",")
        s, g = surname.split(), given.split()
        return (g, s) if g and s else None

    def lookalike_clean(self, max_tries: int = 200) -> LabelledCustomer | None:
        rng = self.rng
        for _ in range(max_tries):
            a = rng.choice(self.individuals)
            parts_a = self._split_name(a.primary_name)
            if parts_a is None:
                continue
            same_region = [
                e for e in self._by_nationality.get(a.nationalities[0], []) if e is not a
            ] if a.nationalities else []
            if not same_region:
                continue
            b = rng.choice(same_region)
            parts_b = self._split_name(b.primary_name)
            if parts_b is None:
                continue
            name = " ".join(parts_a[0] + parts_b[1])
            if self._too_close_to_list(token_key(name)):
                self.rejected_lookalikes += 1
                continue
            return LabelledCustomer(
                customer=Customer(
                    customer_id="pending",  # numbered after shuffle
                    full_name=customer_style(name),
                    date_of_birth=_random_date_in_year(rng.randint(1945, 2003), rng),
                    nationality=a.nationalities[0],
                ),
                kind=CustomerKind.LOOKALIKE_CLEAN,
                should_alert=False,
                is_true_match=False,
            )
        return None

    # batch ----------------------------------------------------------------------------
    def generate(
        self, n: int, true_share: float = 0.4, hard_share: float = 0.2, lookalike_share: float = 0.0
    ) -> list[LabelledCustomer]:
        """lookalike_share defaults to 0 so the dev set (seed 7) stays byte-identical."""
        if not self.individuals:
            raise ValueError("No individuals in the list; cannot build true matches.")
        n_true, n_hard = round(n * true_share), round(n * hard_share)
        out: list[LabelledCustomer] = []
        for entry in self.rng.sample(self.individuals, min(n_true, len(self.individuals))):
            out.append(self.true_match(entry))
        pool = self.rng.sample(self.individuals, len(self.individuals))
        for entry in pool:
            if sum(c.kind is CustomerKind.HARD_NEGATIVE for c in out) >= n_hard:
                break
            if (hn := self.hard_negative(entry)) is not None:
                out.append(hn)
        n_look = round(n * lookalike_share)
        while sum(c.kind is CustomerKind.LOOKALIKE_CLEAN for c in out) < n_look:
            lc = self.lookalike_clean()
            if lc is None:
                raise RuntimeError("Could not build enough look-alike customers from this list.")
            out.append(lc)
        while len(out) < n:
            out.append(self.clean())
        # Shuffle, then number: neither position nor id may reveal the kind.
        self.rng.shuffle(out)
        return [
            c.model_copy(update={"customer": c.customer.model_copy(update={"customer_id": f"C{i:05d}"})})
            for i, c in enumerate(out, 1)
        ]


def summarize(customers: list[LabelledCustomer]) -> dict:
    return {
        "total": len(customers),
        "by_kind": dict(Counter(c.kind.value for c in customers)),
        "by_perturbation": dict(Counter(p.value for c in customers for p in c.perturbations)),
        "customer_dob_missing": sum(c.customer.date_of_birth is None for c in customers),
    }
