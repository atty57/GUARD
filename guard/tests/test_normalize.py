import asyncio
from pathlib import Path

import pytest

from guard import normalize
from guard.check import check
from guard.policy import load_policy

EXAMPLE = Path(__file__).resolve().parents[2] / "data" / "policy.example.yaml"


@pytest.fixture(scope="module")
def policy():
    return load_policy(EXAMPLE)


def run(draft, policy):
    return asyncio.run(check(draft, [], policy))


BLOCK = [
    # (draft, secret, leak type)
    ("two hundred seventy-two Wh/kg pouch cell", "S1", "encoding"),
    ("2.72e2 Wh/kg gravimetric energy", "S1", "encoding"),
    ("745C firing nickel rich cathode", "S3", "encoding"),
    ("energy density 7 Wh/kg above 265 Wh/kg", "S1", "relative"),
    ("cycle life twice 600 cycles", "S8", "relative"),
    ("68 Wh cell weighing 250 g", "S1", "derived"),
    ("scrap rate 6.5% cell line", "S7", "derived"),
    ("half a day calcination hold", "S4", "derived"),
    ("Al dopant 2 mg/g cathode", "S5", "unit_change"),
]


@pytest.mark.parametrize("draft,secret,leak", BLOCK)
def test_blocks_disguised_values(policy, draft, secret, leak):
    r = run(draft, policy)
    assert r.action == "block" and r.tier == 0
    assert secret in r.secrets_touched and leak in r.leak_types


PASS = [
    "1C charge rate cycle life",
    "4C fast charging patents",
    "LFP 160 Wh/kg cells",
    "graphite anode 372 mAh/g",
    "cell 5 Ah pouch",
    "800 °C solid state synthesis",
]


@pytest.mark.parametrize("draft", PASS)
def test_passes_ordinary_queries(policy, draft):
    assert run(draft, policy).action == "pass"


def test_variants_leave_plain_text_alone():
    assert normalize.variants("high nickel cathode coating patents") == []
