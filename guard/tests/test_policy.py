import math
from pathlib import Path

import pytest

from guard.policy import (
    Policy, SecretSpec, interval_metrics, load_policy, policy_hash,
    reveals_threshold_side, violates,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "data" / "policy.example.yaml"


@pytest.fixture(scope="module")
def policy() -> Policy:
    return load_policy(EXAMPLE)


def test_example_loads(policy):
    assert len(policy.secrets) == 10
    assert policy_hash(policy) == policy_hash(load_policy(EXAMPLE))  # stable


def test_exact_leak_gives_finite_max_bits(policy):
    s = policy.secret("S1")
    ratio, bits = interval_metrics(s, (272, 272))
    assert math.isfinite(bits)
    assert bits == round(math.log2(200 / 1), 3)
    assert ratio == 0.005


def test_prior_gives_zero_bits(policy):
    s = policy.secret("S3")
    assert interval_metrics(s, (650, 950)) == (1.0, 0.0)


def test_split_query_narrowing_violates(policy):
    s = policy.secret("S3")  # min width 80 °C
    assert not violates(policy, s, (700, 950))   # "above 700 °C" alone
    assert violates(policy, s, (700, 770))       # + "below 770 °C" -> 70 °C wide


def test_threshold_side(policy):
    s = policy.secret("S1")  # threshold 265, prior 150-350
    assert reveals_threshold_side(s, (265, 350))  # "above 265 Wh/kg"
    assert not reveals_threshold_side(s, (250, 350))
    assert violates(policy, s, (265, 350))


def test_tau_makes_policy_stricter(policy):
    s = policy.secret("S3")
    strict = policy.model_copy(update={"tau": 1.5})
    assert strict.effective_min_width(s) > policy.effective_min_width(s)
    assert violates(strict, s, (700, 800)) and not violates(policy, s, (700, 800))


def test_rejects_bad_specs():
    with pytest.raises(ValueError):
        SecretSpec(secret_id="X", kind="numeric", dim="d", unit="u", value=10,
                   prior=(20, 30), resolution=1)          # value outside prior
    with pytest.raises(ValueError):
        SecretSpec(secret_id="Y", kind="name", dim="d", value="P", resolution=1)
    with pytest.raises(ValueError):
        Policy(secrets=[
            SecretSpec(secret_id="Z", kind="name", dim="d", value="a"),
            SecretSpec(secret_id="Z", kind="name", dim="d", value="b"),
        ])
