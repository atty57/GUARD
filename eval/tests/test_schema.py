import copy
import json

import pytest
from pydantic import ValidationError

from eval.schema import DEFAULT_PATH, EvalResults, load_results


@pytest.fixture
def raw() -> dict:
    return json.loads(DEFAULT_PATH.read_text(encoding="utf-8"))


def test_committed_file_validates():
    r = load_results()
    assert r.placeholder  # flip to False when real results replace it
    assert {c.condition for c in r.conditions} == {"none", "prompt", "moat", "moat_prompt"}


def _bad(raw, edit):
    d = copy.deepcopy(raw)
    edit(d)
    with pytest.raises(ValidationError):
        EvalResults.model_validate(d)


def test_rejects_missing_condition(raw):
    _bad(raw, lambda d: d["conditions"].pop())


def test_rejects_ci_that_misses_the_mean(raw):
    def edit(d):
        d["conditions"][0]["leakage"]["bits"]["ci95"] = [0.0, 1.0]
    _bad(raw, edit)


def test_rejects_sweep_on_another_condition(raw):
    def edit(d):
        d["tau_sweep"][0]["condition"] = "none"
    _bad(raw, edit)


def test_rejects_unsorted_sweep(raw):
    _bad(raw, lambda d: d["tau_sweep"].reverse())


def test_rejects_unknown_metric(raw):
    def edit(d):
        d["comparisons"][0]["metric"] = "leakage.accuracy"
    _bad(raw, edit)


def test_rejects_rate_above_one(raw):
    def edit(d):
        d["benign"]["false_positive_rate"] = 1.2
    _bad(raw, edit)


def test_rejects_nan(raw):
    def edit(d):
        d["cost_usd"] = float("nan")
    _bad(raw, edit)
