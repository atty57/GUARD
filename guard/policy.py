"""MOAT Guard — disclosure policy (owner: Hyeseong).

The policy says *what* is secret and *how much* of it may leave. The guard (`guard.check`)
reads it; the live scorer reads it to compute leak meter values. The observer never reads it.

Shapes follow docs/INTERFACES.md §1 (`Policy`) and §3 (`secret_interval_update`):
`tau`, `min_width_ratio`, `bound_tol`, `max_bits`, `k_min` are the knobs Atharva's
skeleton named; `secrets` adds the list the guard needs to know what to protect.

Run ``python -m guard.policy data/policy.example.yaml`` to validate a policy file.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

POLICY_VERSION = "moat.policy.v1"

SecretKind = Literal["numeric", "name"]
Grade = Literal["NCT", "TS", "INT"]  # national core technology / trade secret / internal


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class SecretSpec(_M):
    """One protected value."""

    secret_id: str
    kind: SecretKind
    dim: str                                  # e.g. "energy_density", "customer_codename"
    unit: str | None = None                   # e.g. "Wh/kg"; None for name secrets
    value: float | str
    equivalents: list[str] = Field(default_factory=list)  # other surface forms
    grade: Grade = "TS"
    docs: list[str] = Field(default_factory=list)          # doc ids that contain it

    # numeric only
    prior: tuple[float, float] | None = None  # what an informed outsider already assumes
    min_width: float | None = None            # narrowest observer interval still treated as safe
    resolution: float | None = None           # smallest meaningful step; floors width for `bits`
    threshold: float | None = None            # legal line; revealing which side is a leak

    @model_validator(mode="after")
    def _check(self) -> "SecretSpec":
        if self.kind == "numeric":
            if not isinstance(self.value, (int, float)):
                raise ValueError(f"{self.secret_id}: numeric secret needs a numeric value")
            if self.prior is None or self.resolution is None or self.unit is None:
                raise ValueError(f"{self.secret_id}: numeric secret needs prior, resolution, unit")
            lo, hi = self.prior
            if not lo < hi:
                raise ValueError(f"{self.secret_id}: prior must be [lo, hi] with lo < hi")
            if not lo <= self.value <= hi:
                raise ValueError(f"{self.secret_id}: value must sit inside the prior")
            if self.resolution <= 0:
                raise ValueError(f"{self.secret_id}: resolution must be > 0")
            if self.min_width is not None and not 0 < self.min_width < hi - lo:
                raise ValueError(f"{self.secret_id}: min_width must be in (0, prior width)")
        else:
            if not isinstance(self.value, str):
                raise ValueError(f"{self.secret_id}: name secret needs a string value")
            if any(v is not None for v in (self.prior, self.min_width, self.resolution, self.threshold)):
                raise ValueError(f"{self.secret_id}: name secret takes no numeric fields")
        return self

    @property
    def prior_width(self) -> float:
        assert self.prior is not None
        return self.prior[1] - self.prior[0]


class Policy(_M):
    """Knobs + secrets. See docs/DATA_SPEC.md for how the values were set."""

    version: Literal["moat.policy.v1"] = POLICY_VERSION
    # tau: one strictness knob (the UI slider). 1.0 = as written; >1 stricter; <1 looser.
    #      It scales every secret's minimum width and divides the bits budget.
    tau: float = Field(default=1.0, gt=0)
    # default minimum width, as a fraction of the prior width, for secrets without min_width
    min_width_ratio: float = Field(default=0.25, gt=0, lt=1)
    # relative tolerance for reading a stated number as the secret ("around 270" vs 272).
    # Used by the Tier 0 value detector (next PR).
    bound_tol: float = Field(default=0.03, ge=0, lt=1)
    # global cap on information an observer may gain about one secret, in bits
    max_bits: float = Field(default=3.0, gt=0)
    # product-profile k-anonymity: at least k candidate products must fit what has gone out.
    # Used by the profile check in the ledger (10/30).
    k_min: int = Field(default=5, ge=1)
    secrets: list[SecretSpec]

    @model_validator(mode="after")
    def _unique(self) -> "Policy":
        ids = [s.secret_id for s in self.secrets]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate secret_id")
        return self

    # --- derived limits ----------------------------------------------------------------
    def effective_min_width(self, s: SecretSpec) -> float:
        base = s.min_width if s.min_width is not None else self.min_width_ratio * s.prior_width
        return min(base * self.tau, s.prior_width)

    def bits_budget(self, s: SecretSpec) -> float:
        """Most bits an observer may gain on this secret: the stricter of the two limits."""
        from_width = math.log2(s.prior_width / self.effective_min_width(s))
        return min(from_width, self.max_bits / self.tau)

    def secret(self, secret_id: str) -> SecretSpec:
        for s in self.secrets:
            if s.secret_id == secret_id:
                return s
        raise KeyError(secret_id)


# --- interval metrics shared by guard, ledger and live scorer --------------------------

def interval_metrics(s: SecretSpec, after: tuple[float, float]) -> tuple[float, float]:
    """Return (width_ratio, bits) for an observer interval on a numeric secret.

    The width is floored at the secret's resolution so an exact leak (width 0) gives a
    finite, maximal `bits` instead of infinity (the event schema rejects inf/NaN).
    """
    lo, hi = after
    width = max(hi - lo, s.resolution or 0.0)
    width_ratio = min(width / s.prior_width, 1.0)
    bits = max(math.log2(s.prior_width / width), 0.0)
    return round(width_ratio, 4), round(bits, 3)


def reveals_threshold_side(s: SecretSpec, after: tuple[float, float]) -> bool:
    """True if the interval now sits wholly on one side of the legal threshold
    while the prior did not."""
    if s.threshold is None or s.prior is None:
        return False
    t = s.threshold
    prior_settled = s.prior[0] >= t or s.prior[1] < t
    now_settled = after[0] >= t or after[1] < t
    return now_settled and not prior_settled


def violates(policy: Policy, s: SecretSpec, after: tuple[float, float]) -> bool:
    """Would committing this interval break the policy for this secret?"""
    _, bits = interval_metrics(s, after)
    narrow = (after[1] - after[0]) < policy.effective_min_width(s)
    return narrow or bits > policy.bits_budget(s) or reveals_threshold_side(s, after)


# --- io ---------------------------------------------------------------------------------

def load_policy(path: str | Path) -> Policy:
    return Policy.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def policy_hash(policy: Policy) -> str:
    """Stable hash for `run_started.policy_hash`."""
    blob = json.dumps(policy.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:16]


if __name__ == "__main__":
    p = load_policy(sys.argv[1] if len(sys.argv) > 1 else "data/policy.example.yaml")
    print(f"OK  {len(p.secrets)} secrets, tau={p.tau}, hash={policy_hash(p)}")
    for s in p.secrets:
        if s.kind == "numeric":
            print(f"    {s.secret_id:<4} {s.dim:<20} min_width={p.effective_min_width(s):g} {s.unit}"
                  f"  bits_budget={p.bits_budget(s):.2f}")
        else:
            print(f"    {s.secret_id:<4} {s.dim:<20} (name)")
