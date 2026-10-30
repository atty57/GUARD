import asyncio
from pathlib import Path

import pytest

from guard.check import GuardResult, check, tier0
from guard.policy import Policy, load_policy
from guard.quantities import extract_quantities

EXAMPLE = Path(__file__).resolve().parents[2] / "data" / "policy.example.yaml"


@pytest.fixture(scope="module")
def policy() -> Policy:
    return load_policy(EXAMPLE)


def run(draft: str, policy: Policy) -> GuardResult:
    return asyncio.run(check(draft, [], policy))


# --- leaks Tier 0 must block -----------------------------------------------------------------
BLOCK = [
    # (draft, secrets, leak type)
    ("pouch cell 270 Wh/kg high-nickel cathode calcination patents", ["S1"], "exact_value"),
    ("pouch cell around 270 Wh/kg competitors", ["S1"], "exact_value"),
    ("Al-doped cathode 745 °C calcination 12 h hold prior art", ["S3", "S4"], "exact_value"),
    ("Si-graphite anode 620 mAh/g cycle life literature", ["S6"], "exact_value"),
    ("edge cracking high loading electrodes yield 93.5% slot-die", ["S7"], "exact_value"),
    ("NCM 88/06/06 cathode Al doping 2,000 ppm patents", ["S2", "S5"], "exact_value"),
    ("pouch cell 0.272 kWh/kg EV benchmark", ["S1"], "unit_change"),
    ("Ni-rich cathode lithiation firing 1018 K patents", ["S3"], "unit_change"),
    ("Si anode 0.62 Ah/g reversible capacity papers", ["S6"], "unit_change"),
    ("calcination dwell 720 min high nickel precursor", ["S4"], "unit_change"),
    ("aluminum doping 0.2 wt% Ni-rich cathode", ["S5"], "unit_change"),
    ("Ostrava EV platform battery cell supplier news", ["S9"], "identifier"),
    ("pouch cell mass production Q3 2027 Europe", ["S10"], "identifier"),
    ("cell supplier 3Q27 ramp", ["S10"], "identifier"),
]


@pytest.mark.parametrize("draft,secrets,leak", BLOCK)
def test_blocks_stated_values(policy, draft, secrets, leak):
    r = run(draft, policy)
    assert r.action == "block" and r.query is None
    assert r.tier == 0 and r.block_kind == "redraftable"
    assert r.secrets_touched == secrets
    assert leak in r.leak_types


# --- must pass Tier 0 ------------------------------------------------------------------------
PASS = [
    # safe research queries
    "high energy density pouch cell high-nickel cathode calcination patents",
    "Al-doped Ni-rich cathode calcination prior art",
    "silicon-graphite composite anode cycle life review 2026",
    # public facts and decoys (over-blocking checks)
    "UN 38.3 test requirements pouch cells",
    "1C charge test at 25 °C standard protocol",
    "LFP cells around 160 Wh/kg cost",
    "95% confidence interval for cycle aging models",          # % without attribute context
    "Korea national core technology list battery 265 Wh/kg",   # the public threshold itself
    "pouch cell energy density below 280 Wh/kg safety",          # bound, wide enough alone
]


@pytest.mark.parametrize("draft", PASS)
def test_passes_safe_queries(policy, draft):
    r = run(draft, policy)
    assert r.action == "pass" and r.query == draft
    assert r.leak_types == [] and r.secrets_touched == []


# Bounds and ranges are the ledger's call (guard/tests/test_ledger.py), never Tier 0's:
# "above 265 Wh/kg" must not be read as the point 265.
@pytest.mark.parametrize("draft", [
    "EV pouch cells 260-280 Wh/kg announced 2026",
    "calcination between 730 and 760 °C high nickel NCM",
    "export approval cell energy density above 265 Wh/kg",
    "calcination at least 730 °C Al-doped",
])
def test_tier0_leaves_bounds_to_the_ledger(policy, draft):
    leak_types, touched, _ = tier0(draft, policy)
    assert leak_types == [] and touched == []


def test_reasons_never_contain_the_secret(policy):
    r = run("pouch cell 0.272 kWh/kg EV benchmark", policy)
    assert all("272" not in reason.replace("0.272", "") for reason in r.reasons)
    r = run("Ostrava EV platform supplier", policy)
    assert all("Project Ostrava" not in reason for reason in r.reasons)


def test_quantity_kinds():
    kinds = {q.text: q.kind for q in extract_quantities(
        "above 250 Wh/kg, below 280 Wh/kg, 700-770 °C, 12 h, up to 700 mAh/g")}
    assert kinds == {"250 Wh/kg": "lower", "280 Wh/kg": "upper", "700-770 °C": "range",
                     "12 h": "point", "700 mAh/g": "upper"}


def test_unit_conversion():
    (q,) = extract_quantities("1018 K")
    assert abs(q.lo - 744.85) < 1e-6
    (q,) = extract_quantities("0.2 wt%")
    assert q.lo == pytest.approx(2000)


def test_result_shape_rules():
    with pytest.raises(ValueError):
        GuardResult(action="block", query="x", tier=0, block_kind="redraftable")
    with pytest.raises(ValueError):
        GuardResult(action="rewrite", query="x", tier=2, rechecked=False)
    with pytest.raises(ValueError):
        GuardResult(action="pass", query=None, tier=0)


def test_history_accepts_sent_queries(policy):
    hist = [{"query": "high-nickel cathode patents", "hop": 1, "run_id": "r1"}]
    r = asyncio.run(check("silicon anode review", hist, policy))
    assert r.action == "pass" and set(r.latency_ms) == {"tier0", "tier1", "total"}
