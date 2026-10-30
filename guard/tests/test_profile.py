import asyncio
from pathlib import Path

import pytest

from agent.events import ProfileUpdateData
from guard.check import check
from guard.policy import Policy, load_policy
from guard.profile import assess, load_catalog, revealed_by

EXAMPLE = Path(__file__).resolve().parents[2] / "data" / "policy.example.yaml"


@pytest.fixture(scope="module")
def policy() -> Policy:
    return load_policy(EXAMPLE)


def session(queries, policy):
    hist, out = [], []
    for i, q in enumerate(queries, 1):
        r = asyncio.run(check(q, hist, policy))
        out.append(r)
        if r.action != "block":
            hist.append({"query": r.query, "hop": i, "run_id": "test"})
    return out


def test_mosaic_profile_blocked_when_k_drops_below_k_min(policy):
    r1, r2, r3 = session(["high-nickel pouch cell suppliers Europe",
                          "European EV OEM 2028 platform battery supplier",
                          "silicon anode cells SOP 2027"], policy)
    assert r1.action == r2.action == "pass" and r1.profile["k"] == 6
    assert r3.action == "block" and r3.tier == 1
    assert r3.leak_types == ["intent_profile"] and r3.block_kind == "redraftable"
    assert r3.profile["k"] == 2 and r3.profile["status"] == "prevented"
    assert r3.secrets_touched == []


def test_unsatisfiable_when_history_is_already_under_k_min(policy):
    # e.g. k_min raised mid-session: history alone leaves 6 < 7, so no redraft that adds
    # detail can get back above k_min
    cat = load_catalog(policy.profile_catalog)
    f = assess(cat, "silicon anode cells", ["high-nickel pouch cell suppliers Europe"], k_min=7)
    assert f.k_before == 6 and f.violates and not f.satisfiable


def test_ruling_our_product_out_is_not_a_leak(policy):
    (r,) = session(["LFP cells around 160 Wh/kg cost"], policy)
    assert r.action == "pass" and r.profile["k"] == 1


def test_single_query_profile(policy):
    (r,) = session(["high-nickel pouch cell Si-graphite anode European OEM SOP 2027"], policy)
    assert r.action == "block" and r.profile["k"] == 2


def test_region_needs_market_context(policy):
    cat = load_catalog(policy.profile_catalog)
    assert "region" not in revealed_by(cat, "Korean national core technology list 265 Wh/kg")
    assert revealed_by(cat, "Korean OEM battery supplier")["region"][0] == "Korean"


def test_narrowest_term_wins(policy):
    cat = load_catalog(policy.profile_catalog)
    term, covered = revealed_by(cat, "high-nickel NCM811 pouch")["cathode"]
    assert term == "NCM811" and covered == {"NCM811"}


def test_repeating_known_attributes_is_not_new_narrowing(policy):
    r = session(["high-nickel pouch cell suppliers Europe",
                 "high-nickel pouch cell calcination patents"], policy)
    assert [x.action for x in r] == ["pass", "pass"]
    assert r[1].profile["k"] == 6


def test_no_attribute_no_profile(policy):
    (r,) = session(["UN 38.3 test requirements"], policy)
    assert r.profile is None


def test_catalog_off_when_policy_names_none(policy):
    off = policy.model_copy(update={"profile_catalog": None})
    (r,) = session(["high-nickel pouch cell Si-graphite anode European OEM SOP 2027"], off)
    assert r.action == "pass" and r.profile is None


def test_profile_fits_the_event_schema(policy):
    for r in session(["high-nickel pouch cell suppliers Europe", "silicon anode cells SOP 2027"],
                     policy):
        ProfileUpdateData.model_validate({**r.profile, "committed": False})
