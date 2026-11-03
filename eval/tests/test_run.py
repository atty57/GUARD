"""A7 experiment-runner tests (keyless; stub guard/search, fake model client).

The three behaviours the task called out, at the public seams of ``eval.run``:
  1. kill mid-run then restart ⇒ no duplicate runs, no missing runs (resume + atomic publish);
  2. the same seed ⇒ the same draft request across conditions that share a prompt (G2 pairing);
  3. the cost cap aborts cleanly, keeping the runs that finished before it tripped (R4).
Plus two correctness guards: sent_queries survives a post-dispatch search failure (R6), and the
input fingerprint changes when a document changes (R3).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import agent.graph as graphmod
from eval import run as R

POLICY = Path(__file__).resolve().parents[2] / "data" / "policy.example.yaml"


# --- fakes -------------------------------------------------------------------------------------
class FakeClient:
    """Deterministic keyless client: canned answers, a fixed cost, and a request log."""

    def __init__(self, *, cost: float = 0.0, calls: list | None = None, run_id: str = "") -> None:
        self.model, self.mode = "muse-spark-1.3", "replay"
        self.cost_usd = 0.0
        self.tokens_in = self.tokens_out = 0
        self._cost, self.calls, self.run_id = cost, calls if calls is not None else [], run_id

    async def complete(self, **kw):
        self.calls.append({"run_id": self.run_id, "messages": kw["messages"],
                           "seed": kw.get("seed")})
        self.cost_usd = self._cost
        return graphmod._chat_completion(graphmod._canned_text(kw["messages"]))


def _plans(tmp: Path, *, conditions, task_ids=("t1",), seeds=(0,), docs=None,
           model="muse-spark-1.3", llm_mode="replay"):
    tasks = [{"task_id": tid, "company": "Halden Cells", "slug": "halden", "split": "dev",
              "question": f"question for {tid}", "needs_docs": list((docs or {}).get(tid, []))}
             for tid in task_ids]
    # write any requested docs under <tmp>/docs/halden/
    for tid, ids in (docs or {}).items():
        for did in ids:
            p = tmp / "docs" / "halden" / f"{did}.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():                           # don't clobber a test's edit
                p.write_text(f"body of {did}", encoding="utf-8")
    cells = R.build_matrix(conditions, tasks, list(seeds), None, None)
    return R.plan_cells(cells, {t["task_id"]: t for t in tasks}, tmp / "docs", POLICY,
                        model=model, max_tokens=256, corpus_sha="stub", llm_mode=llm_mode,
                        single_company_forced=True)


def _run(plans, root, *, concurrency=1, max_usd_total=None, per_run=0.5, client_factory=None,
         force=False):
    return asyncio.run(R.run_matrix(
        plans, root=root, concurrency=concurrency, max_usd_total=max_usd_total,
        per_run_max_usd=per_run, client_factory=client_factory, force=force,
        guard=graphmod.StubGuard(mode="pass"), search=graphmod._stub_search))


# --- 1. kill mid-run + restart: no duplicates, no missing --------------------------------------
def test_resume_after_crash_no_dupes_no_missing(tmp_path):
    plans = _plans(tmp_path, conditions=["none", "prompt", "moat", "moat_prompt"])
    exec_count: dict = {}

    def factory(plan, rec):
        exec_count[plan.cell.run_id] = exec_count.get(plan.cell.run_id, 0) + 1
        return FakeClient(run_id=plan.cell.run_id)

    # Phase 1: full run — every cell completes and writes a sidecar.
    s1 = _run(plans, tmp_path, client_factory=factory)
    assert len(s1["done"]) == len(plans) and not s1["failed"]
    assert all(exec_count[p.cell.run_id] == 1 for p in plans)

    # Simulate a crash on ONE cell between the JSONL and the sidecar rename: drop its sidecar,
    # keep its JSONL (looks "complete" by content but has no done-marker).
    victim = plans[2].cell
    victim.meta_path(tmp_path).unlink()
    assert victim.out_path(tmp_path).exists()            # JSONL still there

    # Phase 2: restart. Only the crashed cell re-runs; the rest are skipped.
    exec_count.clear()
    s2 = _run(plans, tmp_path, client_factory=factory)
    assert exec_count == {victim.run_id: 1}              # exactly one re-run, nothing else
    assert set(s2["skipped"]) == {p.cell.run_id for p in plans} - {victim.run_id}

    # No missing: every cell has an accepted sidecar. No duplicates: one JSONL per cell.
    for p in plans:
        meta = json.loads(p.cell.meta_path(tmp_path).read_text(encoding="utf-8"))
        assert meta["status"] in R.ACCEPTED
    jsonls = {j.relative_to(tmp_path).as_posix() for j in tmp_path.glob("*/*.jsonl")}
    assert jsonls == {f"{p.cell.label}/{p.cell.task_id}_{p.cell.seed}.jsonl" for p in plans}


# --- 2. same seed ⇒ same draft across conditions that share a prompt ---------------------------
def test_same_seed_same_draft_across_conditions(tmp_path):
    calls_none: list = []
    calls_moat: list = []

    async def drive(cond, calls):
        client = FakeClient(calls=calls, run_id=cond)
        async for _ in graphmod.stream("t1", cond, client=client, policy=None,
                                       question="compare HX-9 energy density", documents=[],
                                       seed=0, max_tokens=256,
                                       guard=graphmod.StubGuard(mode="pass"),
                                       search=graphmod._stub_search):
            pass

    asyncio.run(drive("none", calls_none))
    asyncio.run(drive("moat", calls_moat))

    # The first model call of each run is the hop-1 draft. none & moat share the base system
    # prompt (no privacy note), so with a condition-independent seed the requests are identical.
    draft_none = next(c for c in calls_none if "search query" in c["messages"][-1]["content"].lower())
    draft_moat = next(c for c in calls_moat if "search query" in c["messages"][-1]["content"].lower())
    assert draft_none["seed"] == draft_moat["seed"] is not None
    assert draft_none["messages"] == draft_moat["messages"]


# --- 3. cost cap aborts cleanly, keeping partial results ---------------------------------------
def test_budget_abort_keeps_partial_results(tmp_path):
    plans = _plans(tmp_path, conditions=["none", "prompt", "moat", "moat_prompt"],
                   llm_mode="live")  # paid fake cells

    def factory(plan, rec):
        return FakeClient(cost=0.10, run_id=plan.cell.run_id)

    # per-run reserve 0.10, total cap 0.25 → admit 2 (committed 0.20), refuse the 3rd (0.30 > 0.25).
    s = _run(plans, tmp_path, concurrency=1, max_usd_total=0.25, per_run=0.10,
             client_factory=factory)

    assert s["aborted"] is True
    assert len(s["done"]) == 2
    assert len(s["budget_skipped"]) == 2
    # Partial results are intact and valid on disk …
    for run_id, meta in s["done"]:
        p = tmp_path / meta["label"] / f"{meta['task_id']}_{meta['seed']}.meta.json"
        assert json.loads(p.read_text(encoding="utf-8"))["status"] in R.ACCEPTED
    # … and the refused cells wrote nothing.
    assert len(list(tmp_path.glob("*/*.meta.json"))) == 2


# --- R6: sent_queries recovers a query dispatched before search failed -------------------------
def test_sent_queries_recovers_post_dispatch_search_failure(tmp_path):
    def boom(query, k=5):
        raise RuntimeError("search backend down")

    async def go():
        return [e async for e in graphmod.stream(
            "t1", "none", client=FakeClient(), policy=None, question="q", documents=[],
            seed=0, max_tokens=64, guard=graphmod.StubGuard(mode="pass"), search=boom)]

    events = [json.loads(e.model_dump_json(by_alias=True)) for e in asyncio.run(go())]
    sent, _report, rc = R.extract_results(events)
    # The hop dispatched (query left for the adversary) but search raised → no search_results event,
    # yet run_complete still counts it as sent, and extraction recovers the approved query.
    assert rc["sent"] == 1 and len(sent) == 1 and sent[0]


# --- R3: the input fingerprint reacts to a document change -------------------------------------
def test_fingerprint_changes_when_document_changes(tmp_path):
    plans = _plans(tmp_path, conditions=["none"], docs={"t1": ["D1"]})
    fp_before = plans[0].fingerprint
    (tmp_path / "docs" / "halden" / "D1.md").write_text("EDITED body of D1", encoding="utf-8")
    plans2 = _plans(tmp_path, conditions=["none"], docs={"t1": ["D1"]})
    assert plans2[0].fingerprint != fp_before


def test_fingerprint_folds_in_gen_retry_policy(tmp_path, monkeypatch):
    fp_before = _plans(tmp_path, conditions=["none"])[0].fingerprint
    monkeypatch.setattr(graphmod, "GEN_RETRY_POLICY", "4k->8k.v2-different")
    assert _plans(tmp_path, conditions=["none"])[0].fingerprint != fp_before


def test_budget_waits_for_unused_reservations(tmp_path):
    plans = _plans(tmp_path, conditions=["none", "prompt", "moat", "moat_prompt"], llm_mode="live")
    for concurrency in (1, 4):
        root = tmp_path / str(concurrency)
        result = _run(plans, root, concurrency=concurrency, max_usd_total=.15, per_run=.1,
                      client_factory=lambda p, r: FakeClient(cost=.001))
        assert len(result["done"]) == 4 and not result["aborted"]
        assert result["committed_usd"] == pytest.approx(.004)


def test_forced_attempt_costs_accumulate_across_restarts(tmp_path):
    plans = _plans(tmp_path, conditions=["none"], llm_mode="live")
    factory = lambda p, r: FakeClient(cost=.1)
    for expected in (.1, .2, .3):
        result = _run(plans, tmp_path, force=True, max_usd_total=.3, per_run=.1,
                      client_factory=factory)
        assert len(result["done"]) == 1
        assert R.committed_spend(tmp_path) == pytest.approx(expected)
    result = _run(plans, tmp_path, force=True, max_usd_total=.3, per_run=.1, client_factory=factory)
    assert result["aborted"] and not result["done"]


def test_failed_attempts_remain_charged(tmp_path):
    class FailingClient(FakeClient):
        async def complete(self, **kw):
            self.cost_usd = .1
            raise RuntimeError("model failed after billing")

    plans = _plans(tmp_path, conditions=["none"], llm_mode="live")
    result = _run(plans, tmp_path, per_run=.1, client_factory=lambda p, r: FailingClient())
    assert result["failed"] and R.committed_spend(tmp_path) == pytest.approx(.1)
    result = _run(plans, tmp_path, per_run=.1, max_usd_total=.15,
                  client_factory=lambda p, r: FakeClient(cost=.1))
    assert result["aborted"] and not result["done"]


def test_replay_does_not_erase_paid_attempt_cost(tmp_path):
    paid = _plans(tmp_path, conditions=["none"], llm_mode="live")
    _run(paid, tmp_path, client_factory=lambda p, r: FakeClient(cost=.1))
    replay = _plans(tmp_path, conditions=["none"], llm_mode="replay")
    result = _run(replay, tmp_path, max_usd_total=0, client_factory=lambda p, r: FakeClient())
    assert len(result["done"]) == 1 and not result["aborted"]
    assert R.committed_spend(tmp_path) == pytest.approx(.1)


def test_cancelled_attempt_retains_durable_reservation(tmp_path):
    plans = _plans(tmp_path, conditions=["none"], llm_mode="live")

    async def interrupt():
        started = asyncio.Event()

        class WaitingClient(FakeClient):
            async def complete(self, **kw):
                started.set()
                await asyncio.Future()

        task = asyncio.create_task(R.run_matrix(
            plans, root=tmp_path, concurrency=1, max_usd_total=.15, per_run_max_usd=.1,
            client_factory=lambda p, r: WaitingClient(), guard=graphmod.StubGuard(),
            search=graphmod._stub_search))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(interrupt())
    assert R.committed_spend(tmp_path) == pytest.approx(.1)
    result = _run(plans, tmp_path, per_run=.1, max_usd_total=.15,
                  client_factory=lambda p, r: FakeClient())
    assert result["aborted"] and not result["done"]


def test_publish_crash_cannot_accept_old_sidecar(tmp_path, monkeypatch):
    plans = _plans(tmp_path, conditions=["none"], llm_mode="live")
    plan = plans[0]
    _run(plans, tmp_path, per_run=.1, client_factory=lambda p, r: FakeClient(cost=.01))
    old_marker = plan.cell.meta_path(tmp_path).read_bytes()
    original = R._atomic_write

    class NewQuery(FakeClient):
        async def complete(self, **kw):
            if "search query" in kw["messages"][-1]["content"]:
                return graphmod._chat_completion("new query after force")
            return await super().complete(**kw)

    def fail_marker(path, text):
        if path == plan.cell.meta_path(tmp_path):
            raise OSError("interrupted before completion marker")
        original(path, text)

    with monkeypatch.context() as patch:
        patch.setattr(R, "_atomic_write", fail_marker)
        with pytest.raises(OSError, match="completion marker"):
            _run(plans, tmp_path, force=True, per_run=.1,
                 client_factory=lambda p, r: NewQuery(cost=.01))
    assert not plan.cell.meta_path(tmp_path).exists()
    assert not R.is_done(plan, tmp_path)
    # Even a stale restored marker cannot validate events from a different attempt.
    plan.cell.meta_path(tmp_path).write_bytes(old_marker)
    assert not R.is_done(plan, tmp_path)
    assert R.committed_spend(tmp_path) == pytest.approx(.02)
    result = _run(plans, tmp_path, client_factory=lambda p, r: FakeClient())
    assert len(result["done"]) == 1 and R.is_done(plan, tmp_path)


def test_resume_rejects_missing_or_truncated_events(tmp_path):
    plans = _plans(tmp_path, conditions=["none"])
    _run(plans, tmp_path, client_factory=lambda p, r: FakeClient())
    plan = plans[0]
    assert R.is_done(plan, tmp_path)
    path = plan.cell.out_path(tmp_path)
    payload = path.read_bytes()
    path.unlink()
    assert not R.is_done(plan, tmp_path)
    path.write_bytes(payload[:-10])
    assert not R.is_done(plan, tmp_path)


def test_legacy_failed_cost_imported_once(tmp_path):
    path = tmp_path / "none" / "old.meta.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"run_id": "old", "status": "failed", "cost_usd": .1}))
    assert R.committed_spend(tmp_path) == pytest.approx(.1)
    assert R.committed_spend(tmp_path) == pytest.approx(.1)
    path.unlink()
    assert R.committed_spend(tmp_path) == pytest.approx(.1)


def test_cli_returns_failure_for_failed_cell(tmp_path, monkeypatch):
    path = tmp_path / "tasks.jsonl"
    path.write_text(json.dumps({"task_id": "missing-recording", "company": "Halden Cells",
                                "question": "q", "needs_docs": []}) + "\n")
    monkeypatch.setattr(R, "corpus_preflight", lambda allow_stub: "stub")
    assert R.main(["--tasks-file", str(path), "--out-root", str(tmp_path / "out"),
                   "--conditions", "none", "--llm-mode", "replay"]) == 1


def test_document_framing_preserves_privacy_prompt_ablation():
    document = [{"doc_id": "D1", "text": "Internal development marker"}]
    prompts = {condition: graphmod.draft_prompt(graphmod._initial_state(
        "t1", condition, "research question", document, 0)) for condition in R.CONDITIONS}
    for condition, messages in prompts.items():
        assert "Internal development marker" in messages[-1]["content"]
        assert "do not leak into queries" not in messages[-1]["content"]
        assert (graphmod.PRIVACY_NOTE in messages[0]["content"]) == (condition in ("prompt", "moat_prompt"))
