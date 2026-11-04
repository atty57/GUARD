"""A7 runner ↔ Tier 2 integration (keyless).

Covers the runner's own responsibilities for Tier 2 (guard/tier2.py, PR #61), independent of the
guard's rewrite logic:
  1. concurrent cells get isolated, task-local clients (no shared global leaks across runs);
  2. a rewrite call — and its 4k→8k-style retry — is billed to that cell's client and written to
     that cell's recording;
  3. replay reproduces a Tier-2 run with no key and no spend;
  4. a rewrite that still leaks (or a client error) keeps the block ⇒ the draft is never dispatched;
  5. enabling Tier 2 changes only the moat/moat_prompt fingerprints; none/prompt and --tier2-off
     stay byte-identical, and disabled lanes are explicitly configured None.

Everything here fakes the *proposed task-local* tier2 API (configure(client)->token / reset(token)
/ enabled()) and a #61-shaped guard. The real guard code is not imported or modified.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
from pathlib import Path

import pytest

import agent.graph as graphmod
from agent.graph import GuardResult
from agent.llm import LLMClient, _to_completion
from eval import run as R

POLICY = Path(__file__).resolve().parents[2] / "data" / "policy.example.yaml"
SECRET = "272"                       # stands in for a protected value the draft states


# --- fake task-local tier2 module (the proposed PR #61 final API) ------------------------------
class FakeTier2:
    MAX_TOKENS = 1024
    REASONING_EFFORT = "low"
    RESPONSE_FORMAT = {"type": "json_schema"}
    SYSTEM = "rewrite system prompt"
    TIER2_BEHAVIOR_VERSION = "t2.vtest"

    def __init__(self) -> None:
        self._var: contextvars.ContextVar = contextvars.ContextVar("t2_test", default=None)
        self.configured: list = []            # spy: every value configure() was called with, in order

    def configure(self, client):
        self.configured.append(client)
        return self._var.set(client)          # token for task-local restore

    def reset(self, token) -> None:
        self._var.reset(token)

    def enabled(self) -> bool:
        return self._var.get() is not None

    def get_client(self):
        return self._var.get()


# --- a #61-shaped guard that rewrites via the task-local client --------------------------------
class FakeTier2Guard:
    """Pass unless the draft states SECRET. If it does and a Tier-2 client is configured for this
    task, ask it to rewrite (with the 1k→2k retry), then re-check the rewrite against the SAME
    history — a rewrite that still states SECRET keeps the block. No client, or a failed call,
    also keeps the block (fail closed)."""

    def __init__(self, tier2: FakeTier2) -> None:
        self.tier2 = tier2

    async def check(self, draft: str, history: list[dict], policy) -> GuardResult:
        if SECRET not in draft:
            return GuardResult(action="pass", query=draft)
        blocked = GuardResult(action="block", query=None, tier=0, block_kind="redraftable",
                              leak_types=["exact_value"], secrets_touched=["S1"],
                              reasons=["exact_value: S1 energy_density"])
        client = self.tier2.get_client()
        if client is None:
            return blocked
        msgs = [{"role": "system", "content": self.tier2.SYSTEM + " REWRITE"},
                {"role": "user", "content": f"Query: {draft}"}]
        text = ""
        for cap in (self.tier2.MAX_TOKENS, self.tier2.MAX_TOKENS * 2):
            try:
                resp = await client.complete(messages=msgs, temperature=0, seed=0, max_tokens=cap)
            except Exception:
                return blocked                                        # fail closed on a model error
            choice = resp.choices[0] if resp.choices else None
            text = (choice.message.content or "") if choice else ""
            if text.strip() and choice.finish_reason != "length":
                break
        new = " ".join(text.split())
        if not new or SECRET in new:                                  # recheck vs same history
            return GuardResult(action="block", query=None, tier=0, block_kind="redraftable",
                               leak_types=["exact_value"], secrets_touched=["S1"],
                               reasons=["tier 2: rewrite still leaked; block kept"])
        return GuardResult(action="rewrite", query=new, tier=2, rechecked=True,
                           leak_types=["exact_value"], secrets_touched=["S1"],
                           reasons=["tier 2: rewritten"])


# --- a request-aware fake SDK (openai AsyncOpenAI layout) --------------------------------------
def _completion(content, finish="stop", pin=10, pout=20):
    return _to_completion({
        "id": "x", "object": "chat.completion", "created": 0, "model": "muse-spark-1.3",
        "choices": [{"index": 0, "finish_reason": finish,
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": pin, "completion_tokens": pout, "total_tokens": pin + pout}})


class FakeSDK:
    """Serves the agent's draft/reflect/report calls and the guard's rewrite call. The agent's
    draft always states SECRET (so the guard fires); the rewrite reply is configurable."""

    def __init__(self, *, rewrite="high energy density pouch cell patents",
                 truncate_first_rewrite=False, barrier=None):
        self.rewrite, self.truncate_first_rewrite = rewrite, truncate_first_rewrite
        self.barrier, self._rw = barrier, 0

    def with_options(self, **_):
        return self

    async def _create(self, **kwargs):
        if self.barrier is not None and self._rw == 0:
            await self.barrier.wait()                 # force both concurrent cells to interleave
        msgs = kwargs["messages"]
        is_rewrite = any(m.get("role") == "system" and "REWRITE" in (m.get("content") or "")
                         for m in msgs)
        if is_rewrite:
            self._rw += 1
            if self.truncate_first_rewrite and kwargs.get("max_tokens") == FakeTier2.MAX_TOKENS \
                    and self._rw == 1:
                return _completion("", finish="length")              # truncated → guard retries
            return _completion(self.rewrite)
        content = (msgs[-1]["content"] or "").lower()
        if "search query" in content:
            return _completion(f"pouch cell {SECRET} Wh/kg high-nickel calcination patents")
        if "verdict" in content or "reflect on task" in content:
            return _completion("VERDICT: DONE")
        return _completion("Final report citing US1234567 and US7654321.")


def _wire_sdk(sdk: FakeSDK) -> FakeSDK:
    completions = type("completions", (), {"create": staticmethod(sdk._create)})
    sdk.chat = type("chat", (), {"completions": completions})
    return sdk


# --- plan / run helpers (mirrors eval/tests/test_run.py) ---------------------------------------
def _plans(tmp: Path, *, conditions, task_ids=("t1",), seeds=(0,), llm_mode="live", tier2=True):
    tasks = [{"task_id": tid, "company": "Halden Cells", "slug": "halden", "split": "dev",
              "question": f"question for {tid}", "needs_docs": []} for tid in task_ids]
    cells = R.build_matrix(conditions, tasks, list(seeds), None, None)
    return R.plan_cells(cells, {t["task_id"]: t for t in tasks}, tmp / "docs", POLICY,
                        model="muse-spark-1.3", max_tokens=256, corpus_sha="stub",
                        llm_mode=llm_mode, single_company_forced=True, tier2=tier2)


def _run(plans, root, guard, *, concurrency=1, factory=None):
    return asyncio.run(R.run_matrix(
        plans, root=root, concurrency=concurrency, max_usd_total=None, per_run_max_usd=1.0,
        client_factory=factory, guard=guard, search=graphmod._stub_search))


def _sdk_factory(**sdk_kw):
    def factory(plan, rec):
        c = LLMClient(mode=plan.llm_mode, model=plan.model, record_path=rec, max_usd=1.0,
                      api_key="x")
        if plan.llm_mode != "replay":
            c._client = _wire_sdk(FakeSDK(**sdk_kw))
        return c
    return factory


@pytest.fixture
def tier2(monkeypatch):
    fake = FakeTier2()
    monkeypatch.setattr(R, "_tier2", fake)
    return fake


# --- 1. concurrent cells get isolated, task-local clients --------------------------------------
def test_concurrent_cells_do_not_share_the_tier2_client(tmp_path, tier2):
    plans = _plans(tmp_path, conditions=["moat"], task_ids=("t1", "t2"))
    barrier = asyncio.Barrier(2)                       # both cells mid-flight before either rewrites

    def factory(plan, rec):
        c = LLMClient(mode="live", model=plan.model, record_path=rec, max_usd=1.0, api_key="x")
        tag = plan.cell.run_id                         # the rewrite echoes this cell's own tag
        c._client = _wire_sdk(FakeSDK(rewrite=f"high energy pouch cell patents {tag}",
                                      barrier=barrier))
        return c

    s = _run(plans, tmp_path, FakeTier2Guard(tier2), concurrency=2, factory=factory)
    assert len(s["done"]) == 2 and not s["failed"]
    # each cell dispatched a query carrying ITS OWN tag — a shared global client would cross them.
    for run_id, meta in s["done"]:
        assert meta["sent_queries"], f"{run_id} dispatched nothing"
        assert all(run_id in q for q in meta["sent_queries"]), \
            f"{run_id} dispatched another cell's rewrite: {meta['sent_queries']}"


# --- 2. rewrite + retry cost and recording land on this run's client ---------------------------
def test_rewrite_and_retry_billed_and_recorded_on_the_cell(tmp_path, tier2):
    plans = _plans(tmp_path, conditions=["moat"], llm_mode="record")   # recordings need record mode
    s = _run(plans, tmp_path, FakeTier2Guard(tier2),
             factory=_sdk_factory(truncate_first_rewrite=True))
    (_run_id, meta), = s["done"]
    rec = tmp_path / "recordings" / f"{plans[0].cell.run_id}.jsonl"
    entries = [json.loads(x) for x in rec.read_text(encoding="utf-8").splitlines() if x.strip()]
    rewrites = [e for e in entries
                if any(m.get("role") == "system" and "REWRITE" in m["content"]
                       for m in e["request"]["messages"])]
    assert [e["request"]["max_tokens"] for e in rewrites] == [FakeTier2.MAX_TOKENS,
                                                               FakeTier2.MAX_TOKENS * 2]
    assert meta["cost_usd"] > 0 and meta["tokens_out"] > 0
    assert meta["recording_file"] and meta["sent_queries"] and SECRET not in meta["sent_queries"][0]


# --- 3. replay reproduces a Tier-2 run with no key / no spend ----------------------------------
def test_tier2_run_replays_keyless(tmp_path, tier2):
    rec_plans = _plans(tmp_path, conditions=["moat"], llm_mode="record")   # write recordings first
    s1 = _run(rec_plans, tmp_path, FakeTier2Guard(tier2), factory=_sdk_factory())
    (_i, rec_meta), = s1["done"]

    seen = {}

    def replay_factory(plan, rec):
        c = LLMClient(mode="replay", model=plan.model, record_path=rec, api_key="")
        seen["client"] = c
        return c                                       # no SDK injected: replay must not need one

    replay_plans = _plans(tmp_path, conditions=["moat"], llm_mode="replay")
    s2 = asyncio.run(R.run_matrix(replay_plans, root=tmp_path, concurrency=1, max_usd_total=None,
                                  per_run_max_usd=0.0, client_factory=replay_factory,
                                  guard=FakeTier2Guard(tier2), search=graphmod._stub_search))
    (_j, replay_meta), = s2["done"]
    assert replay_meta["cost_usd"] == 0.0 and seen["client"]._client is None   # keyless, no SDK
    assert replay_meta["sent_queries"] == rec_meta["sent_queries"]


# --- 4. a rewrite that still leaks keeps the block ⇒ nothing dispatched -------------------------
def test_fail_closed_when_rewrite_still_leaks(tmp_path, tier2):
    plans = _plans(tmp_path, conditions=["moat"])
    s = _run(plans, tmp_path, FakeTier2Guard(tier2),
             factory=_sdk_factory(rewrite=f"pouch cell {SECRET} Wh/kg patents"))  # still leaks
    (_i, meta), = s["done"]
    assert meta["status"] in R.ACCEPTED
    assert meta["sent"] == 0 and meta["sent_queries"] == []            # the leaky draft never left


def test_fail_closed_when_rewrite_client_errors(tmp_path, tier2):
    class Boom(FakeSDK):
        async def _create(self, **kwargs):
            if any("REWRITE" in (m.get("content") or "") for m in kwargs["messages"]):
                raise RuntimeError("rewrite api down")
            return await super()._create(**kwargs)

    def factory(plan, rec):
        c = LLMClient(mode="live", model=plan.model, record_path=rec, max_usd=1.0, api_key="x")
        c._client = _wire_sdk(Boom())
        return c

    plans = _plans(tmp_path, conditions=["moat"])
    s = _run(plans, tmp_path, FakeTier2Guard(tier2), factory=factory)
    (_i, meta), = s["done"]
    assert meta["sent"] == 0 and meta["sent_queries"] == []


# --- 5. fingerprint: changes for moat only; disabled lanes configured None ---------------------
def test_fingerprint_changes_only_for_guarded_lanes(tmp_path, tier2):
    off = {p.cell.condition: p.fingerprint
           for p in _plans(tmp_path, conditions=list(R.CONDITIONS), tier2=False)}
    on = {p.cell.condition: p.fingerprint
          for p in _plans(tmp_path, conditions=list(R.CONDITIONS), tier2=True)}
    assert on["none"] == off["none"] and on["prompt"] == off["prompt"]        # reusable, unchanged
    assert on["moat"] != off["moat"] and on["moat_prompt"] != off["moat_prompt"]


def test_fingerprint_folds_in_tier2_config(tmp_path, tier2, monkeypatch):
    before = _plans(tmp_path, conditions=["moat"], tier2=True)[0].fingerprint
    monkeypatch.setattr(tier2, "TIER2_BEHAVIOR_VERSION", "t2.vchanged")
    assert _plans(tmp_path, conditions=["moat"], tier2=True)[0].fingerprint != before


def test_disabled_lanes_are_explicitly_configured_none(tmp_path, tier2):
    plans = _plans(tmp_path, conditions=["none", "prompt", "moat"], task_ids=("t1",))
    _run(plans, tmp_path, FakeTier2Guard(tier2), factory=_sdk_factory())
    # every cell configured the context (client for moat, explicit None for none/prompt) and reset.
    assert None in tier2.configured                            # none/prompt were disabled explicitly
    assert any(c is not None for c in tier2.configured)        # moat got its client
    assert tier2.get_client() is None                          # restored after the last reset
