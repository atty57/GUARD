import asyncio
import json
from pathlib import Path

import pytest

import guard.tier2 as tier2
from agent.events import GuardDecisionData
from guard.check import check
from guard.policy import load_policy

EXAMPLE = Path(__file__).resolve().parents[2] / "data" / "policy.example.yaml"
LEAKY = "pouch cell 272 Wh/kg high-nickel cathode calcination patents"


@pytest.fixture(scope="module")
def policy():
    return load_policy(EXAMPLE)


class Scripted:
    """Fake model client: returns the given replies in order and records the prompts."""

    def __init__(self, *replies, raises=None):
        self.replies, self.raises, self.calls = list(replies), raises, []

    async def complete(self, **kw):
        from agent.graph import _chat_completion
        self.calls.append(kw)
        await asyncio.sleep(0)                           # let concurrent checks interleave
        if self.raises:
            raise self.raises
        text, finish = self.replies.pop(0) if self.replies else ("", "stop")
        return _chat_completion(text, finish_reason=finish)


@pytest.fixture
def client():
    def make(*replies, **kw):
        c = Scripted(*replies, **kw)
        tokens.append(tier2.configure(c))
        return c
    tokens = []
    yield make
    for t in reversed(tokens):
        tier2.reset(t)
    assert not tier2.enabled()


def run(draft, policy, history=()):
    return asyncio.run(check(draft, list(history), policy))


def test_off_by_default_keeps_the_block(policy):
    r = run(LEAKY, policy)
    assert r.action == "block" and r.tier == 0 and "tier2" not in r.latency_ms


def test_safe_rewrite_goes_out_rechecked(policy, client):
    c = client(('{"query": "high energy density pouch cell high-nickel cathode calcination patents"}',
                "stop"))
    r = run(LEAKY, policy)
    assert r.action == "rewrite" and r.tier == 2 and r.rechecked
    assert r.query == "high energy density pouch cell high-nickel cathode calcination patents"
    assert r.leak_types == ["exact_value"] and r.secrets_touched == ["S1"]
    assert set(r.latency_ms) == {"tier0", "tier2", "total"}       # Tier 1 never ran on the draft
    assert len(c.calls) == 1
    assert c.calls[0]["reasoning_effort"] == "low"                # PLAN §8
    assert c.calls[0]["response_format"]["type"] == "json_schema"


def test_rewrite_that_still_leaks_is_blocked(policy, client):
    client(('{"query": "pouch cell 0.272 kWh/kg patents"}', "stop"))   # same value, other unit
    r = run(LEAKY, policy)
    assert r.action == "block" and r.tier == 0 and "tier2" in r.latency_ms
    assert r.reasons[-1] == "tier 2: no rewrite passed tiers 0-1; block kept"


def test_rewrite_checked_against_history(policy, client):
    # the rewrite narrows S1 together with an earlier query -> Tier 1 blocks it
    client(('{"query": "pouch cell energy density below 280 Wh/kg patents"}', "stop"))
    hist = [{"query": "pouch cell energy density above 250 Wh/kg", "hop": 1, "run_id": "r"}]
    r = run(LEAKY, policy, hist)
    assert r.action == "block"


@pytest.mark.parametrize("reply", ['{"query": ""}', "not json", '{"query": "' + LEAKY + '"}'])
def test_empty_bad_or_unchanged_reply_keeps_the_block(policy, client, reply):
    client((reply, "stop"))
    assert run(LEAKY, policy).action == "block"


def test_model_error_keeps_the_block(policy, client):
    client(raises=RuntimeError("api down"))
    r = run(LEAKY, policy)
    assert r.action == "block" and "tier2" in r.latency_ms
    assert r.reasons[-1] == "tier 2: model call failed (RuntimeError); block kept"


def test_truncated_reply_retried_once_at_double(policy, client):
    c = client(("", "length"), ('{"query": "high energy density pouch cell patents"}', "stop"))
    r = run(LEAKY, policy)
    assert r.action == "rewrite"
    assert [k["max_tokens"] for k in c.calls] == [tier2.MAX_TOKENS, tier2.MAX_TOKENS * 2]


def test_truncated_twice_keeps_the_block_even_with_valid_json(policy, client):
    reply = '{"query": "high energy density pouch cell patents"}'
    c = client((reply, "length"), (reply, "length"))
    r = run(LEAKY, policy)
    assert r.action == "block" and r.tier == 0 and len(c.calls) == 2
    assert r.reasons[-1] == "tier 2: no rewrite passed tiers 0-1; block kept"


def test_unsatisfiable_block_is_not_rewritten(policy, client):
    c = client(('{"query": "battery patents"}', "stop"))
    hist = [{"query": q, "hop": i, "run_id": "r"} for i, q in enumerate([
        "high-nickel pouch cell European OEM suppliers",
        "silicon anode pouch cell European OEM platforms",
    ])]
    r = run("SOP 2027 European OEM pouch cell silicon anode high-nickel", policy, hist)
    assert r.action == "block" and r.block_kind == "unsatisfiable"
    assert c.calls == []                                   # no rewrite can add detail safely


def test_prompt_carries_no_secret_values(policy, client):
    c = client(('{"query": "high energy density pouch cell patents"}', "stop"))
    run(LEAKY, policy)
    prompt = json.dumps(c.calls[0]["messages"])
    sys_and_reasons = prompt.replace(LEAKY, "")          # the draft itself is inside the boundary
    for s in policy.secrets:
        for form in [str(s.value), *s.equivalents]:
            assert form not in sys_and_reasons


def test_rewrite_fits_the_event_contract(policy, client):
    client(('{"query": "high energy density pouch cell patents"}', "stop"))
    r = run(LEAKY, policy)
    GuardDecisionData(decision=r.action, tier=r.tier, original=LEAKY, final=r.query,
                      diff=[{"op": "delete", "text": "272 Wh/kg"}], leak_types=r.leak_types,
                      secrets_touched=r.secrets_touched, reasons=r.reasons,
                      latency_ms=r.latency_ms, rechecked=r.rechecked)


def test_client_is_task_local(policy):
    # concurrent runs each see only their own client (eval/run.py runs cells concurrently)
    async def one(query):
        c = Scripted(('{"query": "%s"}' % query, "stop"))
        token = tier2.configure(c)
        try:
            return (await check(LEAKY, [], policy)).query, len(c.calls)
        finally:
            tier2.reset(token)

    async def both():
        return await asyncio.gather(one("high energy density pouch cell patents"),
                                    one("pouch cell cathode calcination patents"))
    assert asyncio.run(both()) == [("high energy density pouch cell patents", 1),
                                   ("pouch cell cathode calcination patents", 1)]
    assert not tier2.enabled()


def test_name_block_is_rewritten_and_rechecked(policy, client):
    # #60: a query that only names the company/product is blocked by Tier 0 and can be redrafted
    client(('{"query": "pouch cell high-nickel cathode patents"}', "stop"))
    r = run("Halden HX-9 pouch cell high-nickel cathode patents", policy)
    assert r.action == "rewrite" and r.tier == 2 and r.rechecked
    assert r.leak_types == ["identifier"] and r.query == "pouch cell high-nickel cathode patents"


def test_rewrite_that_keeps_a_name_is_blocked(policy, client):
    client(('{"query": "hx9 pouch cell patents"}', "stop"))        # name survives, other spelling
    r = run("Halden HX-9 pouch cell patents", policy)
    assert r.action == "block" and r.leak_types == ["identifier"]
    assert r.reasons[-1] == "tier 2: no rewrite passed tiers 0-1; block kept"
