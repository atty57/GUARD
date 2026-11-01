"""MOAT Guard model client — async Muse Spark 1.3 over the OpenAI-compatible Meta API.

Three modes, selected by ``LLM_MODE``:
  * ``live``   — call the API and return the completion.
  * ``record`` — call the API, append ``{key, request, response}`` to ``runs/llm.jsonl``.
  * ``replay`` — serve a response from ``runs/llm.jsonl`` by key; a miss raises (fail closed).

The replay key is the SHA-256 of the request serialized with sorted keys, so the demo and the
test suite run with **no API key** (``LLM_MODE=replay``). This client only ever calls plain chat
completions — it never wires Meta's server-side ``web_search`` tool, which would bypass the guard.

Run ``python -m agent.llm`` for a keyless record->replay smoke check.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

# Pricing per 1M tokens (HANDOFF §4), USD.
_PRICE_IN, _PRICE_CACHED, _PRICE_OUT = 1.25, 0.15, 4.25
_MODES = ("live", "record", "replay")
_DEFAULT_RECORD = Path(__file__).resolve().parent.parent / "runs" / "llm.jsonl"


class ReplayMiss(RuntimeError):
    """Replay mode found no recording for the request (fail closed)."""


class BudgetExceeded(RuntimeError):
    """Accrued spend reached MAX_USD_PER_RUN; no further live calls are made."""


def _request_key(request: dict) -> str:
    """SHA-256 of the request kwargs with sorted keys — stable across dict ordering."""
    blob = json.dumps(request, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _field(obj: Any, name: str) -> int:
    """Read an int field from a pydantic object or a plain dict; missing/None -> 0."""
    if obj is None:
        return 0
    val = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, 0)
    return val or 0


def _price(usage: Any) -> float:
    """USD for one call from its token usage (prompt / cached prompt / completion)."""
    prompt = _field(usage, "prompt_tokens")
    completion = _field(usage, "completion_tokens")
    details = usage.get("prompt_tokens_details") if isinstance(usage, dict) \
        else getattr(usage, "prompt_tokens_details", None)
    cached = _field(details, "cached_tokens")
    uncached = max(prompt - cached, 0)
    return (uncached * _PRICE_IN + cached * _PRICE_CACHED + completion * _PRICE_OUT) / 1e6


def _to_completion(data: dict):
    """Rebuild an openai ChatCompletion from a recorded dict, so replay and live match type."""
    from openai.types.chat import ChatCompletion

    return ChatCompletion.model_validate(data)


class LLMClient:
    """Async Muse Spark client with live/record/replay modes and a per-run spend cap."""

    def __init__(self, *, mode: str | None = None, model: str | None = None,
                 api_key: str | None = None, base_url: str | None = None,
                 max_usd: float | None = None, record_path: str | Path | None = None,
                 max_retries: int = 5) -> None:
        self.mode = (mode or os.getenv("LLM_MODE", "replay")).lower()
        if self.mode not in _MODES:
            raise ValueError(f"LLM_MODE must be one of {_MODES}, got {self.mode!r}")
        self.model = model or os.getenv("MODEL", "muse-spark-1.3")
        self.base_url = base_url or os.getenv("META_BASE_URL", "https://api.meta.ai/v1")
        self.api_key = api_key if api_key is not None else os.getenv("MODEL_API_KEY", "")
        self.max_usd = float(max_usd if max_usd is not None
                             else os.getenv("MAX_USD_PER_RUN", "0.50"))
        self.record_path = Path(record_path) if record_path else _DEFAULT_RECORD
        self.max_retries = max_retries
        self.cost_usd = 0.0
        self.tokens_in = 0
        self.tokens_out = 0
        self._client = None                 # lazily built AsyncOpenAI (never in replay)
        self._cache: dict[str, dict] | None = None

    def _sdk(self):
        if self._client is None:
            from openai import AsyncOpenAI

            # max_retries gives exponential backoff on 429/5xx for free (HANDOFF A2).
            self._client = AsyncOpenAI(api_key=self.api_key or None, base_url=self.base_url,
                                       max_retries=self.max_retries)
        return self._client

    def _load_cache(self) -> dict[str, dict]:
        if self._cache is None:
            cache: dict[str, dict] = {}
            if self.record_path.exists():
                for line in self.record_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        rec = json.loads(line)
                        cache[rec["key"]] = rec["response"]
            self._cache = cache
        return self._cache

    def _append(self, key: str, request: dict, resp) -> None:
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"key": key, "request": request, "response": resp.model_dump(mode="json")}
        with self.record_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        if self._cache is not None:
            self._cache[key] = rec["response"]

    async def complete(self, **kwargs):
        """One chat completion. kwargs are OpenAI chat params (messages, temperature, ...)."""
        if self.cost_usd >= self.max_usd:
            raise BudgetExceeded(
                f"accrued ${self.cost_usd:.4f} >= cap ${self.max_usd:.4f}; refusing further calls")
        request = {"model": self.model, **kwargs}
        key = _request_key(request)

        if self.mode == "replay":
            cache = self._load_cache()
            if key not in cache:
                raise ReplayMiss(
                    f"no recording for request key {key[:12]}... in {self.record_path}")
            return _to_completion(cache[key])

        # G4: bound a single call's worst case so one response can't blow the per-run cap.
        # Output tokens dominate price and are the only unbounded term (input is our prompt).
        max_tokens = kwargs.get("max_tokens")
        if max_tokens is not None:
            projected = self.cost_usd + max_tokens * _PRICE_OUT / 1e6
            if projected > self.max_usd:
                raise BudgetExceeded(
                    f"projected ${projected:.4f} (accrued ${self.cost_usd:.4f} + "
                    f"{max_tokens} out tok) > cap ${self.max_usd:.4f}; refusing call")

        resp = await self._sdk().chat.completions.create(**request)
        self.tokens_in += _field(resp.usage, "prompt_tokens")
        self.tokens_out += _field(resp.usage, "completion_tokens")
        self.cost_usd += _price(resp.usage)
        if self.mode == "record":
            self._append(key, request, resp)
        return resp


# --- self-check: record then replay with the key unset (HANDOFF A2 "done when") ----------
def _demo() -> int:
    import tempfile

    sample = {
        "id": "cmpl-demo", "object": "chat.completion", "created": 0, "model": "muse-spark-1.3",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "pong"}}],
        "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000,
                  "total_tokens": 2_000_000},
    }
    completion = _to_completion(sample)

    class _FakeSDK:
        class chat:  # noqa: N801 - mirrors the openai client attribute layout
            class completions:
                @staticmethod
                async def create(**kwargs):
                    return completion

    req = {"messages": [{"role": "user", "content": "ping"}], "temperature": 0.0}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "llm.jsonl"

        rec = LLMClient(mode="record", api_key="x", record_path=path, max_usd=100.0)
        rec._client = _FakeSDK()
        out = asyncio.run(rec.complete(**req))
        assert out.choices[0].message.content == "pong"
        assert abs(rec.cost_usd - 5.50) < 1e-9, f"cost {rec.cost_usd} != 5.50"

        rep = LLMClient(mode="replay", api_key="", record_path=path)  # no key
        out2 = asyncio.run(rep.complete(**req))
        assert out2.choices[0].message.content == "pong"
        assert rep.cost_usd == 0.0 and rep._client is None, "replay must not spend or build the SDK"

        try:
            asyncio.run(rep.complete(messages=[{"role": "user", "content": "other"}]))
        except ReplayMiss:
            pass
        else:
            raise AssertionError("replay miss did not fail closed")

    print("OK  agent.llm: record->replay round-trip, cost=$5.50, keyless replay, miss fails closed")
    return 0


if __name__ == "__main__":
    import sys

    try:
        sys.exit(_demo())
    except AssertionError as exc:
        print(f"SELF-CHECK FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
