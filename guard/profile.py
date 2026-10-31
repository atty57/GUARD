"""Product-profile check, Tier 1 (owner: Hyeseong).

No single query has to contain a number to give the product away. "high-nickel pouch cell
suppliers Europe" and later "silicon anode cells SOP 2027" together can leave only one or two
products in the market that fit. The check is k-anonymity-style (after Sweeney, 2002):

    k = number of candidate products that fit every attribute the sent queries revealed

Only terms that are true of our own product count. "LFP cells" rules our product out, so it
reveals nothing about it and is ignored; it can't hide later terms that do apply.

A draft that would push k below ``policy.k_min`` is blocked as ``redraftable`` (drop or
broaden the new term). ``unsatisfiable`` is a defensive path: it is only reached if the
history is already under ``k_min`` (e.g. ``k_min`` raised mid-session), since the guard
never lets a guarded session get there.

k is a crowd count over a hand-written catalog of fictional products, not a market survey, and
there is no automatic generalization (``generalized_from`` is always None). Candidates and the
term vocabulary live in ``data/profile_catalog.*.yaml``, referenced by
``Policy.profile_catalog``. The check is off when the policy names no catalog.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Catalog:
    terms: dict[str, dict[str, frozenset[str]]]   # attribute -> term -> covered values
    region_context: re.Pattern
    products: tuple[dict, ...]
    target: str                                   # id of our own product

    def k(self, revealed: dict[str, set[str]]) -> int:
        return sum(all(p[a] in vals for a, vals in revealed.items()) for p in self.products)

    @property
    def target_product(self) -> dict:
        return next(p for p in self.products if p["id"] == self.target)


def load_catalog(path: str) -> Catalog:
    """Load the catalog; ``path`` is relative to the repo root. Cached on (path, mtime), so an
    edited catalog is picked up by a long-running server."""
    p = Path(path)
    p = p if p.is_absolute() else _REPO / p
    return _load(str(p), p.stat().st_mtime_ns)


@lru_cache(maxsize=8)
def _load(path: str, _mtime: int) -> Catalog:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    terms = {a: {t: frozenset(str(v) for v in vs) for t, vs in ts.items()}
             for a, ts in raw["attributes"].items()}
    products = tuple({k: str(v) for k, v in pr.items()} for pr in raw["products"])
    target = str(raw["target"])
    if not any(pr["id"] == target for pr in products):
        raise ValueError(f"catalog target {target!r} is not in products")
    return Catalog(terms, re.compile(raw.get("region_context", "."), re.I), products, target)


def _found(term: str, text: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text, re.I) is not None


def revealed_by(cat: Catalog, text: str) -> dict[str, tuple[str, frozenset[str]]]:
    """Attribute -> (most specific term in ``text`` that is true of our product, values it
    covers). Terms that rule our product out are left out."""
    ours = cat.target_product
    out: dict[str, tuple[str, frozenset[str]]] = {}
    for attr, terms in cat.terms.items():
        if attr == "region" and not cat.region_context.search(text):
            continue
        hits = [(t, vals) for t, vals in terms.items() if ours[attr] in vals and _found(t, text)]
        if hits:
            out[attr] = min(hits, key=lambda h: len(h[1]))  # narrowest term wins
    return out


def _combine(views: list[dict[str, tuple[str, frozenset[str]]]]):
    """Intersect what several queries revealed. Returns (attr -> values, attr -> term shown)."""
    vals: dict[str, set[str]] = {}
    shown: dict[str, str] = {}
    for v in views:
        for attr, (term, covered) in v.items():
            if attr not in vals or len(covered & vals[attr]) < len(vals[attr]):
                shown[attr] = term
            vals[attr] = set(covered) if attr not in vals else vals[attr] & covered
    return vals, shown


@dataclass(frozen=True)
class ProfileFinding:
    k_before: int
    k_after: int
    k_min: int
    revealed: dict[str, str]        # attr -> term, cumulative incl. the draft
    new_attrs: list[str]            # attributes the draft narrowed
    violates: bool
    satisfiable: bool               # could a broader draft still keep k >= k_min?


def assess(cat: Catalog, draft: str, history: list[str], k_min: int) -> ProfileFinding | None:
    """None when the draft reveals nothing true of our product."""
    d = revealed_by(cat, draft)
    if not d:
        return None
    hist_views = [revealed_by(cat, q) for q in history]
    before, _ = _combine(hist_views)
    after, shown = _combine(hist_views + [d])
    k_before, k_after = cat.k(before), cat.k(after)
    new_attrs = [attr for attr in d if attr not in before or len(after[attr]) < len(before[attr])]
    narrowed = k_after < k_before
    return ProfileFinding(
        k_before=k_before, k_after=k_after, k_min=k_min, revealed=shown,
        new_attrs=new_attrs if narrowed else [],
        violates=narrowed and k_after < k_min,
        satisfiable=k_before >= k_min,
    )


def proposal(f: ProfileFinding, blocked: bool = False) -> dict:
    """``ProfileProposal`` (INTERFACES §1) as a plain dict, plus ``status``.
    On a blocked draft the proposal is ``prevented``."""
    if blocked or f.violates:
        status = "prevented"
    elif f.k_after < 2 * f.k_min:
        status = "warning"
    else:
        status = "safe"
    return {"revealed": dict(f.revealed), "k": f.k_after, "k_min": f.k_min,
            "generalized_from": None, "status": status}
