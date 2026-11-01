"""Shape of ``eval/eval_results.json`` — schema ``moat.eval.v1`` (owner: Hyeseong).

The eval dashboard (S5) reads this file and nothing else. ``eval/score.py`` (H7) writes it from
the recorded runs. Until results land, the committed file holds placeholder numbers with
``"placeholder": true``; the dashboard should show that as a banner.

What is in it follows PLAN §10:

- ``conditions``: the 2×2 ablation at the policy's own ``tau``, one entry per condition.
- ``tau_sweep``: the ``moat`` condition at five ``tau`` values, for the slider.
- ``attacks``: the scripted attack suite, recall per leak type; ``benign``: false-positive rate
  on the controls.
- ``comparisons``: paired tests between conditions, Holm-corrected.
- ``latency_ms``: per guard tier.

Units:

- Every rate and score is in [0, 1].
- ``numeric_gain`` is the observer's error with an empty log minus its error with the real log,
  each as a fraction of the secret's prior width, so secrets with different units can be
  averaged. Positive = the queries helped the observer.
- ``bits`` is the ledger estimate (guard/ledger.py), summed over secrets per run.

Run ``python -m eval.schema [path]`` to validate a results file.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.events import Condition, LeakType

SCHEMA = "moat.eval.v1"
CONDITIONS: tuple[str, ...] = ("none", "prompt", "moat", "moat_prompt")
DEFAULT_PATH = Path(__file__).resolve().parent / "eval_results.json"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class Estimate(_M):
    """A mean over paired tasks with its bootstrap 95% CI."""

    mean: float
    ci95: list[float] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def _ordered(self) -> "Estimate":
        lo, hi = self.ci95
        if not lo <= self.mean <= hi:
            raise ValueError(f"ci95 {self.ci95} must contain mean {self.mean}")
        return self


class Leakage(_M):
    numeric_gain: Estimate                 # see module docstring
    width_ratio: Estimate                  # final interval width / prior width (ledger)
    bits: Estimate                         # ledger bits, summed over secrets
    hit_within_2pct: Estimate              # share of numeric secrets the observer guessed within ±2%
    categorical_gain: Estimate             # exact-match rate on name secrets, minus the empty-log rate
    intent_similarity: Estimate            # observer's guess of the task vs the real task


class Utility(_M):
    citation_recall: Estimate              # planted gold patents cited in the report
    rubric: Estimate                       # blind LLM judge score, scaled to [0, 1]


class Flow(_M):
    """What the guard did to the run, per query and per run."""

    queries_per_run: float = Field(ge=0)
    block_rate: float = Field(ge=0, le=1)       # share of drafts blocked
    rewrite_rate: float = Field(ge=0, le=1)     # share of drafts rewritten (Tier 2)
    skip_rate: float = Field(ge=0, le=1)        # share of hops skipped


class ConditionResult(_M):
    condition: Condition
    tau: float = Field(gt=0)
    n_runs: int = Field(ge=1)
    leakage: Leakage
    utility: Utility
    flow: Flow


class AttackResult(_M):
    leak_type: LeakType
    n: int = Field(ge=1)
    recall: float = Field(ge=0, le=1)       # share of attacks blocked or rewritten before dispatch


class BenignResult(_M):
    n: int = Field(ge=1)
    false_positive_rate: float = Field(ge=0, le=1)


class Comparison(_M):
    metric: str                            # dotted path into ConditionResult, e.g. leakage.numeric_gain
    a: Condition
    b: Condition
    test: Literal["wilcoxon", "mcnemar"]
    effect: float                          # a − b, paired by task
    p_value: float = Field(ge=0, le=1)
    p_holm: float = Field(ge=0, le=1)


class Latency(_M):
    p50: float = Field(ge=0)
    p95: float = Field(ge=0)


class Models(_M):
    agent: str
    observer: str
    judge: str


class EvalResults(_M):
    schema_: Literal["moat.eval.v1"] = Field(default=SCHEMA, alias="schema")
    placeholder: bool
    generated_at: datetime
    split: Literal["dev", "test"]
    n_tasks: int = Field(ge=1)
    n_seeds: int = Field(ge=1)
    corpus_sha: str
    policy_hash: str
    models: Models
    conditions: list[ConditionResult]
    tau_sweep: list[ConditionResult]
    attacks: list[AttackResult]
    benign: BenignResult
    comparisons: list[Comparison]
    latency_ms: dict[Literal["tier0", "tier1", "tier2", "total"], Latency]
    tier2_escalation_rate: float = Field(ge=0, le=1)
    cost_usd: float = Field(ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> "EvalResults":
        conds = [c.condition for c in self.conditions]
        if sorted(conds) != sorted(CONDITIONS):
            raise ValueError(f"conditions must be exactly {CONDITIONS} once each, got {conds}")
        if len({c.tau for c in self.conditions}) != 1:
            raise ValueError("the 2×2 conditions must share one tau")
        taus = [p.tau for p in self.tau_sweep]
        if len(taus) != 5 or taus != sorted(set(taus)):
            raise ValueError("tau_sweep needs 5 distinct tau values in ascending order")
        if any(p.condition != "moat" for p in self.tau_sweep):
            raise ValueError("tau_sweep runs the moat condition only")
        if self.conditions[0].tau not in taus:
            raise ValueError("tau_sweep must include the conditions' tau")
        kinds = [a.leak_type for a in self.attacks]
        if len(kinds) != len(set(kinds)):
            raise ValueError("one attacks entry per leak type")
        for c in self.comparisons:
            if c.a == c.b:
                raise ValueError(f"comparison {c.metric} compares {c.a} with itself")
            head, _, leaf = c.metric.partition(".")
            if head not in ("leakage", "utility") or leaf not in {
                    "leakage": Leakage, "utility": Utility}[head].model_fields:
                raise ValueError(f"unknown metric {c.metric!r}")
            if c.p_holm < c.p_value:
                raise ValueError(f"{c.metric}: Holm-adjusted p cannot be below the raw p")
        if "total" not in self.latency_ms:
            raise ValueError("latency_ms needs a total")
        return self


def load_results(path: str | Path = DEFAULT_PATH) -> EvalResults:
    return EvalResults.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def _main(argv: list[str]) -> int:
    path = Path(argv[0]) if argv else DEFAULT_PATH
    r = load_results(path)
    tag = " (placeholder numbers)" if r.placeholder else ""
    print(f"OK  {path.name}: {SCHEMA}, {len(r.conditions)} conditions, "
          f"{len(r.tau_sweep)} tau points, {len(r.attacks)} attack types{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
