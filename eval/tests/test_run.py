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
    plans = _plans(tmp_path, conditions=["none", "prompt", "moat", "moat_prompt"])  # 4 cells

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
