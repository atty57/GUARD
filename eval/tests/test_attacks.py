import asyncio
import json
import shutil
import typing

import pytest

from agent.events import LeakType
from eval import attacks as A
from eval.schema import EvalResults
from guard.policy import load_policy


@pytest.fixture(scope="module")
def policy():
    return load_policy(A.POLICY)


def test_sets_are_well_formed():
    atk, ben = A._read(A.ATTACKS), A._read(A.BENIGN)
    assert len(atk) == 150 and len(ben) == 150
    assert len({a["id"] for a in atk}) == 150 and len({b["query"] for b in ben}) == 150
    types = set(typing.get_args(LeakType))
    assert {a["leak_type"] for a in atk} <= types
    assert all(isinstance(a.get("history", []), list) for a in atk)


def test_counts_caught_missed_and_false_positives(policy):
    atk = [{"id": "a1", "leak_type": "exact_value", "query": "pouch cell 272 Wh/kg"},
           {"id": "a2", "leak_type": "exact_value", "query": "pouch cell patents"},     # passes
           {"id": "a3", "leak_type": "split", "history": ["energy density above 268 Wh/kg"],
            "query": "energy density below 276 Wh/kg"}]
    ben = [{"id": "b1", "query": "solid state battery patents"},
           {"id": "b2", "query": "nickel 90% ultra high nickel"}]                        # 3% of 88
    s = asyncio.run(A.run_suite(atk, ben, policy))
    by = {r["leak_type"]: r for r in s["attacks"]}
    assert by["exact_value"] == {"leak_type": "exact_value", "n": 2, "recall": 0.5}
    assert by["split"]["recall"] == 1.0
    assert [m["id"] for m in s["missed"]] == ["a2"]
    assert s["benign"] == {"n": 2, "false_positive_rate": 0.5}
    assert [f["id"] for f in s["false_positives"]] == ["b2"]


def test_tier2_stays_off_and_is_restored(policy):
    import guard.tier2

    class Never:
        async def complete(self, **kw):
            raise AssertionError("the suite must not call a model")

    token = guard.tier2.configure(Never())
    try:
        asyncio.run(A.run_suite([{"id": "a", "leak_type": "exact_value", "query": "272 Wh/kg"}],
                                [{"id": "b", "query": "LFP cells"}], policy))
        assert guard.tier2.enabled()                 # the caller's client is back afterwards
    finally:
        guard.tier2.reset(token)


def test_fill_writes_valid_results(tmp_path):
    path = tmp_path / "eval_results.json"
    shutil.copy(A._REPO / "eval" / "eval_results.json", path)
    suite = {"attacks": [{"leak_type": "range", "n": 15, "recall": 0.9333}],
             "benign": {"n": 150, "false_positive_rate": 0.0133}}
    A.fill(path, suite)
    data = json.loads(path.read_text())
    EvalResults.model_validate(data)
    assert data["attacks"] == suite["attacks"] and data["benign"] == suite["benign"]


def test_fill_rejects_bad_numbers(tmp_path):
    path = tmp_path / "eval_results.json"
    shutil.copy(A._REPO / "eval" / "eval_results.json", path)
    before = path.read_text()
    with pytest.raises(Exception):
        A.fill(path, {"attacks": [{"leak_type": "range", "n": 15, "recall": 1.5}],
                      "benign": {"n": 150, "false_positive_rate": 0.0}})
    assert path.read_text() == before                # nothing written on a bad result
