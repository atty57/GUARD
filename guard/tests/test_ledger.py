import asyncio
from pathlib import Path

import pytest

from agent.events import SecretIntervalUpdateData
from guard.check import check
from guard.ledger import assess, narrow
from guard.policy import Policy, load_policy

EXAMPLE = Path(__file__).resolve().parents[2] / "data" / "policy.example.yaml"


@pytest.fixture(scope="module")
def policy() -> Policy:
    return load_policy(EXAMPLE)


def session(queries: list[str], policy: Policy):
    """Run queries in order; only passed ones join the history, like the agent graph."""
    hist, results = [], []
    for i, q in enumerate(queries, 1):
        r = asyncio.run(check(q, hist, policy))
        results.append(r)
        if r.action != "block":
            hist.append({"query": r.query, "hop": i, "run_id": "test"})
    return results


# --- split leaks: each query fine alone, blocked once the interval gets too narrow -----------
SPLIT = [
    (["EV pouch cell energy density above 250 Wh/kg reviews",
      "pouch cell energy density below 280 Wh/kg safety"], "S1", [250, 280]),
    (["calcination above 700 °C cation mixing",
      "calcination below 770 °C residual lithium"], "S3", [700, 770]),
    (["Si-graphite anode capacity from 580 mAh/g papers",
      "Si-graphite anode up to 700 mAh/g cycle life"], "S6", [580, 700]),
]


@pytest.mark.parametrize("queries,sid,after", SPLIT)
def test_split_leak_blocked_on_the_query_that_crosses(policy, queries, sid, after):
    first, second = session(queries, policy)
    assert first.action == "pass"
    assert [iv["secret_id"] for iv in first.intervals] == [sid]
    assert first.intervals[0]["basis"] == "final"
    assert second.action == "block" and second.tier == 1
    assert second.leak_types == ["split"] and second.secrets_touched == [sid]
    (iv,) = second.intervals
    assert iv["after"] == after and iv["basis"] == "original" and iv["status"] == "prevented"


# --- a single range or threshold-side bound -------------------------------------------------
SINGLE = [
    ("EV pouch cells 260-280 Wh/kg announced 2026", "S1"),
    ("calcination between 730 and 760 °C high nickel NCM", "S3"),
    ("export approval cell energy density above 265 Wh/kg", "S1"),   # threshold side
    ("cathode nickel content over 80% patents firing", "S2"),        # threshold side
]


@pytest.mark.parametrize("draft,sid", SINGLE)
def test_range_and_threshold_side_blocked(policy, draft, sid):
    (r,) = session([draft], policy)
    assert r.action == "block" and r.tier == 1
    assert r.leak_types == ["range"] and r.secrets_touched == [sid]


def test_blocked_query_does_not_narrow_later(policy):
    # the blocked "below 770" never left, so "at least 730" is judged against [700, 950]
    r1, r2, r3 = session(["calcination above 700 °C cation mixing",
                          "calcination below 770 °C residual lithium",
                          "calcination at least 730 °C Al-doped"], policy)
    assert (r1.action, r2.action, r3.action) == ("pass", "block", "pass")
    assert r3.intervals[0]["before"] == [700, 950] and r3.intervals[0]["after"] == [730, 950]


def test_fraction_bounds_need_the_attribute_named(policy):
    # "above 90%" with no attribute word is not about any secret
    assert assess("models above 90% accuracy", [], policy) == []
    assert [f.secret.secret_id for f in assess("yield above 90% slot-die", [], policy)] == ["S7"]


def test_bound_far_from_our_value_is_ignored(policy):
    s = policy.secret("S1")
    assert narrow(s, (250, 280), "below 200 Wh/kg", margin=30) == (250, 280)


def test_no_constraint_no_interval(policy):
    (r,) = session(["high-nickel pouch cell suppliers Europe"], policy)
    assert r.action == "pass" and r.intervals == []


def test_proposals_fit_the_event_schema(policy):
    # the agent node adds committed + decision_seq; everything else must already validate
    for r in session(SPLIT[0][0], policy):
        for iv in r.intervals:
            SecretIntervalUpdateData.model_validate({**iv, "committed": False, "decision_seq": 1})


def test_reasons_never_contain_the_secret(policy):
    _, r = session(SPLIT[0][0], policy)
    assert all("272" not in reason for reason in r.reasons)


def test_tau_makes_the_ledger_stricter(policy):
    strict = policy.model_copy(update={"tau": 2.0})
    # [250, 350] is 100 wide: fine at tau 1 (min 60), too narrow at tau 2 (min 120)
    assert session(["energy density above 250 Wh/kg"], policy)[0].action == "pass"
    assert session(["energy density above 250 Wh/kg"], strict)[0].action == "block"


# --- regressions from the pre-PR review ------------------------------------------------------

@pytest.mark.parametrize("earlier,draft", [
    ("LFP cells below 230 Wh/kg", "pouch cells 268-276 Wh/kg"),
    ("LFP cells below 230 Wh/kg", "pouch cells above 265 Wh/kg"),
    ("sintering above 900 °C ceramics", "calcination 740-750 °C"),
])
def test_off_target_bound_does_not_switch_the_ledger_off(policy, earlier, draft):
    first, second = session([earlier, draft], policy)
    assert first.action == "pass" and first.intervals == []   # not true of our value: ignored
    assert second.action == "block" and second.tier == 1


def test_bound_that_excludes_our_value_is_not_a_leak(policy):
    (r,) = session(["LFP cells below 180 Wh/kg"], policy)
    assert r.action == "pass" and r.intervals == []


def below_threshold(policy: Policy) -> Policy:
    """Same policy with S1 at 258 Wh/kg, under the 265 line (like half the companies)."""
    secrets = [s.model_copy(update={"value": 258}) if s.secret_id == "S1" else s
               for s in policy.secrets]
    return policy.model_copy(update={"secrets": secrets})


@pytest.mark.parametrize("draft", ["EV pouch cells below 265 Wh/kg", "pouch cells <265 Wh/kg",
                                   "pouch cells 265 Wh/kg or less"])
def test_threshold_side_from_below(policy, draft):
    (r,) = session([draft], below_threshold(policy))
    assert r.action == "block" and r.secrets_touched == ["S1"]


@pytest.mark.parametrize("draft,sid", [("pouch cells 265+ Wh/kg", "S1"),
                                       ("Si anode 600 mAh/g or more", "S6")])
def test_postfix_bounds(policy, draft, sid):
    (r,) = session([draft], policy)
    assert r.action == "block" and r.secrets_touched == [sid]


def test_blocked_draft_previews_are_all_prevented(policy):
    # S1 alone would be fine; S3 breaks its limit -> both previews of the blocked draft are
    # prevented, since nothing from it goes out
    (r,) = session(["energy density above 200 Wh/kg calcination 730-760 °C"], policy)
    assert r.action == "block"
    assert {iv["secret_id"]: iv["status"] for iv in r.intervals} == {"S1": "prevented",
                                                                     "S3": "prevented"}


@pytest.mark.parametrize("draft", [
    "cells 265-271 Wh/kg", "cells 273-280 Wh/kg", "calcination 746-760 °C",
    "Ni content 88.5-90%", "Si anode 625-640 mAh/g", "yield 94-96%",
    "cycle life 1250 to 1300 cycles",
])
def test_narrow_range_just_off_our_value_is_blocked(policy, draft):
    (r,) = session([draft], policy)
    assert r.action == "block" and r.tier == 1, draft


def test_near_miss_bound_counts_in_the_cumulative_interval(policy):
    # "below 744" misses 745 by 1: it still counts, and with the first query pins the value
    first, second = session(["calcination above 740 °C", "calcination below 744 °C"], policy)
    assert first.action == "pass"
    assert second.action == "block" and second.leak_types == ["split"]
