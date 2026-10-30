"""MOAT Guard — the hook the agent calls before every search (owner: Hyeseong).

Contract: docs/INTERFACES.md §1.

    async def check(draft, history, policy) -> GuardResult

Tier 0, the value firewall, blocks a draft that states a secret outright:

- ``exact_value``: the secret's value in its own unit, within tolerance ("270 Wh/kg" vs 272),
  or a listed equivalent form ("NCM 88/06/06").
- ``unit_change``: the same value in another unit ("0.272 kWh/kg", "1018 K", "720 min").
- ``identifier``: a name secret or one of its aliases ("Ostrava", "3Q27").

Tier 1, the disclosure ledger (guard/ledger.py), blocks bounds and ranges ("above 250",
"260-280 Wh/kg") that, alone or with the queries already sent, narrow a secret past its policy
limits (``range`` / ``split``). Tier 2 (LLM rewrite) comes later; until then a hit is a
redraftable block.

Reasons never contain the secret value: they are shown in the UI and written to run logs.
"""

from __future__ import annotations

import re
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from guard import ledger, profile
from guard.policy import Policy, SecretSpec
from guard.quantities import Quantity, extract_quantities, family, to_canonical, unit_id

Action = Literal["pass", "rewrite", "block"]
BlockKind = Literal["redraftable", "unsatisfiable"]
# Same vocabulary as agent/events.py `LeakType`; the guard does not import the event schema.
LeakType = Literal[
    "exact_value", "unit_change", "range", "split", "derived", "relative", "encoding",
    "process_fingerprint", "identifier", "intent_profile",
]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class SentQuery(_M):
    """One query that actually left (INTERFACES §1)."""

    query: str
    hop: int
    run_id: str


class GuardResult(_M):
    """Shape agreed in INTERFACES §1. ``intervals`` and ``profile`` are plain dicts so the
    agent node can spread them straight into events."""

    action: Action
    query: str | None
    tier: int = Field(ge=0, le=2)
    block_kind: BlockKind | None = None
    leak_types: list[LeakType] = Field(default_factory=list)
    secrets_touched: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    latency_ms: dict[str, float] = Field(default_factory=dict)
    intervals: list[dict] = Field(default_factory=list)
    profile: dict | None = None
    rechecked: bool = False

    @model_validator(mode="after")
    def _shape(self) -> "GuardResult":
        if self.action == "block":
            if self.query is not None or self.block_kind is None:
                raise ValueError("block needs query=None and a block_kind")
        elif not self.query or self.block_kind is not None:
            raise ValueError("pass/rewrite needs a query and no block_kind")
        if self.tier == 2 and not self.rechecked:
            raise ValueError("a tier-2 rewrite must be re-checked by tiers 0-1")
        return self


# --- Tier 0 ----------------------------------------------------------------------------------

# A bare percentage says nothing about *which* percentage. For fraction-unit secrets, Tier 0
# only fires when the query also names the attribute; otherwise "95% confidence" would hit the
# yield secret. Shared with the ledger (guard/ledger.py: CONTEXT).


def _step(s: SecretSpec, uid: str) -> float:
    """Half the secret's resolution, in canonical units."""
    return (s.resolution or 0.0) / 2 * (to_canonical(1, uid) - to_canonical(0, uid))


def _tolerance(policy: Policy, s: SecretSpec, uid: str, canon_value: float) -> float:
    """How close a stated number may be to the secret before it counts as the secret:
    ``bound_tol`` of the value, but never finer than half the secret's resolution."""
    return max(policy.bound_tol * abs(canon_value), _step(s, uid))


def _point_hits(policy: Policy, s: SecretSpec, quantities: list[Quantity], text: str):
    sid = unit_id(s.unit or "")
    if sid is None or not isinstance(s.value, (int, float)):
        return
    fam = family(sid)
    secret = to_canonical(float(s.value), sid)
    if fam == "fraction" and not ledger.applies(s, text):
        return
    tol = _tolerance(policy, s, sid, secret)
    # The legal threshold is public (DATA_SPEC §2): quoting "265 Wh/kg" is not stating our
    # value. Whether it hints which side we are on is a Tier 1 question.
    public = to_canonical(s.threshold, sid) if s.threshold is not None else None
    half_step = _step(s, sid)
    for q in quantities:
        if q.kind != "point" or q.family != fam:
            continue
        if public is not None and abs(q.lo - public) <= half_step:
            continue
        if abs(q.lo - secret) <= tol:
            yield ("exact_value" if q.unit == sid else "unit_change"), q.text


def _alias_hits(s: SecretSpec, text: str):
    forms = list(s.equivalents)
    if s.kind == "name":
        forms.insert(0, str(s.value))
    for f in forms:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(f)}(?![A-Za-z0-9])", text, re.I):
            yield ("identifier" if s.kind == "name" else "exact_value"), f


def tier0(draft: str, policy: Policy) -> tuple[list[str], list[str], list[str]]:
    """Return (leak_types, secrets_touched, reasons) for a single draft."""
    quantities = extract_quantities(draft)
    leak_types: list[str] = []
    touched: list[str] = []
    reasons: list[str] = []
    for s in policy.secrets:
        hits = list(_point_hits(policy, s, quantities, draft)) if s.kind == "numeric" else []
        if not hits:
            hits = list(_alias_hits(s, draft))
        if not hits:
            continue
        touched.append(s.secret_id)
        for lt, span in hits:
            if lt not in leak_types:
                leak_types.append(lt)
            reasons.append(f"{s.secret_id} ({s.dim}): '{span}' states the protected value")
    return leak_types, touched, reasons


def _sent(history: list[SentQuery] | list[dict]) -> list[str]:
    return [h.query if isinstance(h, SentQuery) else SentQuery.model_validate(h).query
            for h in history]


def tier1(draft: str, sent: list[str], policy: Policy):
    """Ledger check. Returns (findings, leak_types, touched, reasons) for violations only."""
    findings = ledger.assess(draft, sent, policy)
    leak_types: list[str] = []
    touched: list[str] = []
    reasons: list[str] = []
    for f in findings:
        if not f.violates:
            continue
        s = f.secret
        touched.append(s.secret_id)
        if f.leak_type not in leak_types:
            leak_types.append(f.leak_type)
        if f.threshold_side:
            why = "reveals which side of the legal threshold it is on"
        elif f.leak_type == "split":
            why = "together with earlier queries, narrows it past the policy limit"
        else:
            why = "narrows it past the policy limit"
        reasons.append(f"{s.secret_id} ({s.dim}): this query {why}")
    return findings, leak_types, touched, reasons


async def check(draft: str, history: list[SentQuery] | list[dict], policy: Policy) -> GuardResult:
    """Decide whether ``draft`` may leave. Raises on bad input; the caller treats any
    exception as a block (fail closed)."""
    sent = _sent(history)
    t0 = time.perf_counter()
    leak_types, touched, reasons = tier0(draft, policy)
    t1 = time.perf_counter()
    latency = {"tier0": round((t1 - t0) * 1000, 3)}
    if touched:
        latency["total"] = latency["tier0"]
        return GuardResult(action="block", query=None, tier=0, block_kind="redraftable",
                           leak_types=leak_types, secrets_touched=touched, reasons=reasons,
                           latency_ms=latency)

    findings, leak_types, touched, reasons = tier1(draft, sent, policy)
    pf = None
    if policy.profile_catalog:
        pf = profile.assess(profile.load_catalog(policy.profile_catalog), draft, sent, policy.k_min)
    t2 = time.perf_counter()
    latency["tier1"] = round((t2 - t1) * 1000, 3)
    latency["total"] = round((t2 - t0) * 1000, 3)

    kind = "redraftable"
    if pf is not None and pf.violates:
        leak_types.append("intent_profile")
        terms = ", ".join(repr(t) for t in pf.new_terms)
        reasons.append(f"profile: with {terms}, only {pf.k_after} candidate product(s) fit "
                       f"what has been sent (k_min {pf.k_min})")
        if not pf.satisfiable and not touched:
            kind = "unsatisfiable"   # already under k_min: no redraft can add detail safely
    prof = profile.proposal(pf) if pf is not None else None

    if touched or (pf is not None and pf.violates):
        return GuardResult(action="block", query=None, tier=1, block_kind=kind,
                           leak_types=leak_types, secrets_touched=touched, reasons=reasons,
                           latency_ms=latency, profile=prof,
                           intervals=[ledger.proposal(policy, f, "original") for f in findings])
    return GuardResult(action="pass", query=draft, tier=0, latency_ms=latency, profile=prof,
                       intervals=[ledger.proposal(policy, f, "final") for f in findings])
