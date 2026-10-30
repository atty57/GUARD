"""Behaviour tests for the Muse Spark model client (agent/llm.py).

Seam under test: ``LLMClient.complete()`` and ``client.cost_usd``. The OpenAI SDK transport is
stubbed only at that external boundary — no internal method is mocked. Expected values come from
an independent source of truth: an inline SHA-256 computation and the HANDOFF §4 price literals.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent.llm import BudgetExceeded, LLMClient, ReplayMiss

MODEL = "muse-spark-1.3"


def _completion(content: str, *, prompt: int, completion: int, cached: int = 0) -> dict:
    """A minimal OpenAI chat.completion dict with the usage we want to price."""
    usage: dict = {"prompt_tokens": prompt, "completion_tokens": completion,
                   "total_tokens": prompt + completion}
    if cached:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    return {
        "id": "cmpl-test", "object": "chat.completion", "created": 0, "model": MODEL,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}],
        "usage": usage,
    }


def _expected_key(request: dict) -> str:
    """Recompute the replay key independently of the module (sorted-JSON SHA-256)."""
    blob = json.dumps(request, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _write_record(path: Path, request_without_model: dict, response: dict) -> dict:
    """Write one {key, request, response} line keyed as the client keys it (model injected)."""
    request = {"model": MODEL, **request_without_model}
    rec = {"key": _expected_key(request), "request": request, "response": response}
    path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    return rec


class _StubSDK:
    """Stands in for AsyncOpenAI: records whether it was ever asked to make a call."""

    def __init__(self, response: dict):
        self._response = response
        self.calls: list[dict] = []
        outer = self

        class _Completions:
            async def create(self, **kwargs):
                outer.calls.append(kwargs)
                from openai.types.chat import ChatCompletion

                return ChatCompletion.model_validate(outer._response)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


# 1. replay hit, no network --------------------------------------------------------------
async def test_replay_hit_returns_recording_without_network(tmp_path):
    path = tmp_path / "llm.jsonl"
    _write_record(path, {"messages": [{"role": "user", "content": "ping"}]},
                  _completion("pong", prompt=10, completion=5))

    client = LLMClient(mode="replay", api_key="", record_path=path)
    out = await client.complete(messages=[{"role": "user", "content": "ping"}])

    assert out.choices[0].message.content == "pong"
    assert client._client is None, "replay must not construct the SDK"
    assert client.cost_usd == 0.0


# 2. replay key is order-independent -----------------------------------------------------
async def test_replay_key_ignores_kwarg_order(tmp_path):
    path = tmp_path / "llm.jsonl"
    # Recorded with temperature first, messages second.
    _write_record(path, {"temperature": 0.2, "messages": [{"role": "user", "content": "hi"}]},
                  _completion("ordered", prompt=1, completion=1))

    client = LLMClient(mode="replay", api_key="", record_path=path)
    # Called with the opposite kwarg order — must still hit the same record.
    out = await client.complete(messages=[{"role": "user", "content": "hi"}], temperature=0.2)

    assert out.choices[0].message.content == "ordered"


# 3. replay miss fails closed ------------------------------------------------------------
async def test_replay_miss_raises(tmp_path):
    path = tmp_path / "llm.jsonl"
    _write_record(path, {"messages": [{"role": "user", "content": "known"}]},
                  _completion("x", prompt=1, completion=1))

    client = LLMClient(mode="replay", api_key="", record_path=path)
    with pytest.raises(ReplayMiss):
        await client.complete(messages=[{"role": "user", "content": "unknown"}])


# 4. cost accounting matches the §4 price table ------------------------------------------
async def test_cost_matches_price_table(tmp_path):
    path = tmp_path / "llm.jsonl"
    client = LLMClient(mode="record", api_key="k", record_path=path, max_usd=1000.0)

    # 1M prompt (uncached) + 1M completion = 1.25 + 4.25 = $5.50.
    client._client = _StubSDK(_completion("a", prompt=1_000_000, completion=1_000_000))
    await client.complete(messages=[{"role": "user", "content": "a"}])
    assert client.cost_usd == pytest.approx(5.50)

    # 1M cached prompt + 0 completion = $0.15, accrued -> $5.65.
    client._client = _StubSDK(_completion("b", prompt=1_000_000, completion=0, cached=1_000_000))
    await client.complete(messages=[{"role": "user", "content": "b"}])
    assert client.cost_usd == pytest.approx(5.65)

    # token counters accumulate across both calls (2M prompt, 1M completion).
    assert (client.tokens_in, client.tokens_out) == (2_000_000, 1_000_000)


# 5. budget hard stop --------------------------------------------------------------------
async def test_budget_hard_stop_before_next_call(tmp_path):
    path = tmp_path / "llm.jsonl"
    stub = _StubSDK(_completion("a", prompt=1_000_000, completion=1_000_000))  # $5.50/call
    client = LLMClient(mode="live", api_key="k", record_path=path, max_usd=1.0)
    client._client = stub

    await client.complete(messages=[{"role": "user", "content": "a"}])  # accrues $5.50 > cap
    assert len(stub.calls) == 1

    with pytest.raises(BudgetExceeded):
        await client.complete(messages=[{"role": "user", "content": "b"}])
    assert len(stub.calls) == 1, "the over-budget call must not reach the SDK"


# 6. record -> replay round-trip (the acceptance path) -----------------------------------
async def test_record_then_replay_with_no_key(tmp_path):
    path = tmp_path / "llm.jsonl"
    req = {"messages": [{"role": "user", "content": "round"}], "temperature": 0.0}

    recorder = LLMClient(mode="record", api_key="secret", record_path=path, max_usd=1000.0)
    recorder._client = _StubSDK(_completion("trip", prompt=3, completion=4))
    rec_out = await recorder.complete(**req)
    assert rec_out.choices[0].message.content == "trip"
    assert path.exists(), "record mode must persist the call"

    # Fresh client, replay mode, NO api key — the §A2 "done when".
    replayer = LLMClient(mode="replay", api_key="", record_path=path)
    rep_out = await replayer.complete(**req)
    assert rep_out.choices[0].message.content == "trip"
    assert replayer._client is None
