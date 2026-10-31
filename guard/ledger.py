"""Disclosure ledger, Tier 1 (owner: Hyeseong).

Tracks what an observer of the sent queries can infer about each numeric secret, across the
whole session. Each bound or range in a query narrows that secret's feasible interval:

    prior [150, 350] Wh/kg
    "above 250 Wh/kg"  -> [250, 350]   width 100: fine on its own
    "below 280 Wh/kg"  -> [250, 280]   width 30 < min width 60: a split leak

The interval is rebuilt from ``history`` (queries that actually left) on every call, so the
ledger holds no state of its own and can't drift from what was really sent.

Only bounds that hold for our value narrow the interval. "LFP cells below 230 Wh/kg" says
nothing true about a 272 Wh/kg product, so it is skipped; it can't hide later bounds that do
apply. Every bound that does hold is assumed to reach the observer as a fact about the secret
(worst case), and that interval is what the policy limits (``min_width``, bits budget,
threshold side) are measured against. Bare points are Tier 0's job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from guard.policy import Policy, SecretSpec, interval_metrics, reveals_threshold_side, violates
from guard.quantities import UNITS, Quantity, extract_quantities, family, unit_id

# Fraction units (%, wt%, ppm) are ambiguous on their own; a bound only applies to a secret
# whose attribute is named in the same query. Same rule as Tier 0.
CONTEXT = {
    "ni_content": r"\bni\b|nickel|\bncm|\bnmc|\bnca\b",
    "production_yield": r"yield",
    "al_dopant": r"\bal\b|alumin|dop",
}


def applies(s: SecretSpec, text: str) -> bool:
    """Whether a fraction-unit number in ``text`` can be about secret ``s``."""
    ctx = CONTEXT.get(s.dim)
    return ctx is None or re.search(ctx, text, re.I) is not None


def _to_secret_unit(x: float, sid: str) -> float:
    _, scale, offset = UNITS[sid]
    return (x - offset) / scale


def constraints(s: SecretSpec, text: str) -> list[Quantity]:
    """Bounds and ranges in ``text`` that constrain numeric secret ``s``."""
    sid = unit_id(s.unit or "")
    if s.kind != "numeric" or sid is None:
        return []
    fam = family(sid)
    if fam == "fraction" and not applies(s, text):
        return []
    return [q for q in extract_quantities(text) if q.kind != "point" and q.family == fam]


def _holds(s: SecretSpec, q: Quantity, a: float, b: float) -> bool:
    """Whether bound/range ``q`` (``a``..``b`` in the secret's unit) is true of our value."""
    v = float(s.value)
    if q.kind == "lower":
        return v >= a
    if q.kind == "upper":
        return v <= b
    return a <= v <= b


def narrow(s: SecretSpec, interval: tuple[float, float], text: str) -> tuple[float, float]:
    """Apply every bound/range in ``text`` that holds for our value to ``interval``
    (both in the secret's unit). Bounds that exclude our value are skipped."""
    sid = unit_id(s.unit or "")
    lo, hi = interval
    for q in constraints(s, text):
        a, b = sorted((_to_secret_unit(q.lo, sid), _to_secret_unit(q.hi, sid)))
        if not _holds(s, q, a, b):
            continue
        if q.kind == "lower":
            lo = max(lo, a)
        elif q.kind == "upper":
            hi = min(hi, b)
        else:  # range
            lo, hi = max(lo, a), min(hi, b)
    return lo, hi


@dataclass(frozen=True)
class Finding:
    secret: SecretSpec
    before: tuple[float, float]
    after: tuple[float, float]
    violates: bool
    leak_type: str            # "range" (this query alone) or "split" (only with history)
    threshold_side: bool


def assess(draft: str, history: list[str], policy: Policy) -> list[Finding]:
    """For each numeric secret the draft narrows, compare the interval before and after it."""
    out: list[Finding] = []
    for s in policy.secrets:
        if s.kind != "numeric" or s.prior is None:
            continue
        if not constraints(s, draft):
            continue
        before = tuple(s.prior)
        for q in history:
            before = narrow(s, before, q)
        after = narrow(s, before, draft)
        if after == before:
            continue
        # A draft that narrows the interval is blocked if the result breaks a limit,
        # even if the history already had.
        bad = violates(policy, s, after)
        alone = violates(policy, s, narrow(s, tuple(s.prior), draft))
        out.append(Finding(
            secret=s, before=before, after=after, violates=bad,
            leak_type="range" if alone else "split",
            threshold_side=(reveals_threshold_side(s, after)
                            and not reveals_threshold_side(s, before)),
        ))
    return out


def proposal(policy: Policy, f: Finding, basis: str, blocked: bool = False) -> dict:
    """``IntervalProposal`` (INTERFACES §1) as a plain dict; the agent node adds
    ``committed`` and ``decision_seq``. On a blocked draft every proposal is ``prevented``."""
    s = f.secret
    ratio, bits = interval_metrics(s, f.after)
    if blocked or f.violates:
        status = "prevented"
    elif bits > policy.bits_budget(s) / 2:
        status = "warning"
    else:
        status = "safe"
    r = lambda iv: [round(iv[0], 6), round(iv[1], 6)]  # noqa: E731
    return {
        "secret_id": s.secret_id, "dim": s.dim, "unit": s.unit,
        "prior": r(s.prior), "before": r(f.before), "after": r(f.after),
        "width_ratio": ratio, "bits": bits, "status": status, "basis": basis,
    }
