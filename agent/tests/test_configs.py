"""Tests for the experiment conditions (configs/, issue #12).

Seam under test: ``configs.run`` — the config switch that drives each of the 4 conditions
through the one agent graph. The model client is the keyless ``FakeClient``; moat conditions
use the real ``guard.check`` (the production default), none/prompt skip the guard. The oracle
is the shipped contract checker ``agent.events.check_stream`` and the loaded policy's own
``policy_hash`` — never recomputed from the implementation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.events import check_stream
from agent.graph import FakeClient
from configs import CONDITIONS, make_policy, run
from guard.policy import load_policy, policy_hash

_POLICY = Path(__file__).resolve().parent.parent.parent / "data" / "policy.example.yaml"
_LANE = {"none": "baseline", "prompt": "baseline", "moat": "moat", "moat_prompt": "moat"}


def _one(events, type_):
    return [e for e in events if e.type == type_]


# 1. every condition completes one run through the same graph with the fake llm ----------------
@pytest.mark.parametrize("condition", CONDITIONS)
async def test_condition_completes_one_run(condition):
    events = await run("demo", condition, client=FakeClient())

    check_stream(events, condition)                      # contract-valid stream end to end
    assert _one(events, "run_started"), "a run must announce itself"
    assert events[-1].type == "run_complete"
    assert events[-1].data.status in ("completed", "completed_with_skips")


# 2. run_started carries the right condition, lane and policy_hash -----------------------------
@pytest.mark.parametrize("condition", CONDITIONS)
async def test_run_started_condition_lane_and_hash(condition):
    policy = load_policy(_POLICY)
    events = await run("demo", condition, client=FakeClient())

    started = _one(events, "run_started")[0]
    assert started.data.condition == condition
    assert started.lane == _LANE[condition]              # enforced by make_event/check_stream
    assert started.data.policy_hash == policy_hash(policy)
    assert started.data.tau == policy.tau


# 3. with the real policy, a moat run blocks "0.272 kWh/kg" but a none run sends it ------------
async def test_moat_blocks_leak_draft_none_sends_it():
    leak = "0.272 kWh/kg"                                 # S1's equivalent form (energy density)

    moat = await run("demo", "moat", client=FakeClient(draft=leak))
    assert _one(moat, "search_results") == [], "the real guard must block the leak draft"
    assert moat[-1].data.sent == 0 and moat[-1].data.blocked > 0

    none = await run("demo", "none", client=FakeClient(draft=leak))
    sent = _one(none, "search_results")
    assert sent and sent[0].data.query == leak, "the baseline sends the same draft unguarded"
    assert none[-1].data.sent >= 1


# 4. tau is a sweep knob: it changes policy_hash, so sweep points are distinguishable ----------
async def test_tau_override_changes_policy_hash():
    base = await run("demo", "moat", client=FakeClient())
    strict = await run("demo", "moat", tau=1.5, client=FakeClient())

    base_hash = _one(base, "run_started")[0].data.policy_hash
    strict_hash = _one(strict, "run_started")[0].data.policy_hash
    assert base_hash != strict_hash
    assert _one(strict, "run_started")[0].data.tau == 1.5
    assert make_policy(1.5).tau == 1.5


# 5. an unknown condition is rejected, not run -------------------------------------------------
async def test_unknown_condition_rejected():
    with pytest.raises(ValueError):
        await run("demo", "bogus", client=FakeClient())
