"""Behaviour tests for the research agent graph (agent/graph.py).

Seam under test: ``agent.graph.stream``/``collect`` — the ``moat.v2`` event stream a run emits for a
given task, condition, stub guard and stub search. The external boundaries (guard, search, model
client) are injected stubs; no internal node or method is mocked. The independent oracle is the
shipped contract checker ``agent.events.check_stream`` / ``make_event`` — expected values come from
the §5 hard rules and the contract, never recomputed from the implementation.

The six tests the task named, in order:
  (1) sent_queries are a subset of the approved queries,
  (2) a block at the redraft cap goes to skip_hop and never searches,
  (3) a guard exception counts as a block,
  (4) the multi-hop loop stops at MAX_HOPS,
  (5) the graph state carries no ground-truth field,
  (6) every emitted event re-validates through make_event().
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.events import check_stream, make_event
from agent.graph import (
    MAX_HOPS,
    FakeClient,
    GuardResult,
    ResearchState,
    StubGuard,
    collect,
    stream_group,
)
from agent.llm import ReplayMiss


def _by_type(events, type_):
    return [e for e in events if e.type == type_]


def _approved_finals(events):
    """Every query the guard approved this run (pass/rewrite guard_decision finals)."""
    return {e.data.final for e in _by_type(events, "guard_decision")
            if e.data.decision in ("pass", "rewrite")}


# 1. sent_queries ⊆ approved queries -----------------------------------------------------------
async def test_sent_queries_subset_of_approved(tmp_path):
    events = await collect("demo", "none", guard=StubGuard(), client=FakeClient())

    sent = {e.data.query for e in _by_type(events, "search_results")}
    assert sent, "a passing baseline run must dispatch at least one query"
    assert sent <= _approved_finals(events), "a sent query was never approved by the guard"

    # and each dispatched query equals an approving decision on its own hop (fail-closed dispatch).
    decisions = {(e.hop, e.data.final) for e in _by_type(events, "guard_decision")
                 if e.data.decision in ("pass", "rewrite")}
    for sr in _by_type(events, "search_results"):
        assert (sr.hop, sr.data.query) in decisions


# 2. block at the redraft cap → skip_hop, never searches ---------------------------------------
async def test_block_at_cap_skips_and_never_searches():
    events = await collect("demo", "moat",
                           guard=StubGuard(mode="block", block_kind="redraftable"),
                           client=FakeClient())

    assert _by_type(events, "search_results") == [], "a blocked query must never reach search"
    skips = _by_type(events, "hop_skipped")
    assert skips and skips[0].data.reason == "redrafts_exhausted"
    assert events[-1].data.blocked > 0 and events[-1].data.sent == 0


# 3. guard exception == block ------------------------------------------------------------------
async def test_guard_exception_counts_as_block():
    events = await collect("demo", "moat", guard=StubGuard(mode="raise"), client=FakeClient())

    gd = _by_type(events, "guard_decision")
    assert gd and all(e.data.decision == "block" for e in gd), "a raising guard must block"
    err = _by_type(events, "error")
    assert err and err[0].data.where == "guard" and err[0].data.dispatched is False
    assert _by_type(events, "search_results") == []           # fail closed: nothing dispatched


# 4. multi-hop loop stops at MAX_HOPS ----------------------------------------------------------
async def test_loop_stops_at_max_hops():
    # guard always passes; reflect never says DONE -> the loop runs until the MAX_HOPS cap.
    events = await collect("demo", "moat", guard=StubGuard(),
                           client=FakeClient(reflect_answer="CONTINUE"))

    assert len(_by_type(events, "search_results")) == MAX_HOPS
    assert _by_type(events, "report_chunk"), "the loop must terminate into generate_report"
    assert events[-1].data.status == "completed" and events[-1].data.hops == MAX_HOPS


# 5. no ground-truth field in state ------------------------------------------------------------
def test_state_has_no_ground_truth_field():
    keys = set(ResearchState.__annotations__)
    assert "private_facts" not in keys                        # §5 rule 5, verbatim
    assert not {k for k in keys if "secret" in k or "truth" in k or "answer" in k}


# 6. every emitted event re-validates through make_event() -------------------------------------
async def test_every_event_revalidates_with_make_event():
    events = await collect("demo", "moat", guard=StubGuard(), client=FakeClient())

    for e in events:
        data = e.data.model_dump(by_alias=True)
        # Rebuilding from the envelope + data must not raise: the stream is contract-valid.
        make_event(e.type, e.group_id, data, run_id=e.run_id, lane=e.lane, seq=e.seq, hop=e.hop)


# --- Codex-finding regression tests (one per bug) ---------------------------------------------
class _BlockThenPass:
    """Guard that blocks its first ``n`` calls, then passes — for the redraft-cap test."""

    def __init__(self, n: int) -> None:
        self.n, self.calls = n, 0

    async def check(self, draft, history, policy):
        self.calls += 1
        if self.calls <= self.n:
            return GuardResult(action="block", query=None, block_kind="redraftable")
        return GuardResult(action="pass", query=draft)


class _HistorySpy:
    """Pass-through guard that records the history it was handed on each call."""

    def __init__(self) -> None:
        self.histories: list[list[dict]] = []

    async def check(self, draft, history, policy):
        self.histories.append([dict(h) for h in history])
        return GuardResult(action="pass", query=draft)


# F1: a model failure after this hop already searched is NOT a block (dispatched preserved).
async def test_model_failure_after_search_keeps_dispatched():
    class _FailOnReflect(FakeClient):
        async def complete(self, **kwargs):
            if "reflect" in " ".join(m["content"] for m in kwargs["messages"]).lower():
                raise ReplayMiss("no recording for reflect")
            return await super().complete(**kwargs)

    events = await collect("demo", "none", guard=StubGuard(), client=_FailOnReflect())

    assert _by_type(events, "search_results"), "hop 1 must have dispatched before the failure"
    err = _by_type(events, "error")[0]
    assert err.data.where == "model" and err.data.dispatched is True   # not rendered as BLOCKED
    assert events[-1].data.status == "failed" and events[-1].data.sent == 1
    check_stream(events, "model-failure")


# F2: a malformed hit (projection failure) must not drop the already-dispatched accounting.
async def test_malformed_hit_retains_dispatch_accounting():
    def _bad_search(query, k=5):
        return [{"title": "t", "score": 1.0}]          # missing "id" -> projection raises

    events = await collect("demo", "none", search=_bad_search, client=FakeClient())

    err = _by_type(events, "error")[0]
    assert err.data.where == "search" and err.data.dispatched is True
    assert events[-1].data.sent == 1 and events[-1].data.hops == 1
    check_stream(events, "malformed-hit")


# F3: a profile proposal (which carries no status) must be given one in preview and commit.
async def test_profile_proposal_gets_status():
    prof = {"revealed": {"cathode": "NCM811"}, "k": 3, "k_min": 5}      # no status field
    guard = StubGuard(mode="rewrite", profile=prof)
    events = await collect("demo", "moat", guard=guard, client=FakeClient())

    profiles = _by_type(events, "profile_update")
    assert profiles, "a profile proposal must surface as profile_update events"
    assert all(p.data.status for p in profiles)
    assert events[-1].data.status == "completed"
    check_stream(events, "profile-status")


# F4: MAX_REDRAFTS=2 must permit a third attempt (initial + 2 redrafts).
async def test_redraft_cap_allows_third_attempt():
    guard = _BlockThenPass(2)                          # block twice, pass the third check
    events = await collect("demo", "moat", guard=guard, client=FakeClient())

    assert guard.calls == 3, "the guard's third check must be reached"
    assert len(_by_type(events, "search_results")) == 1, "the third, passing draft dispatches"
    assert _by_type(events, "hop_skipped") == []


# F5: LLM recordings must not sit in runs/fixtures/, which the event checker loads as moat.v2.
def test_event_fixture_dir_holds_only_moat_events():
    fixtures = Path(__file__).resolve().parent.parent.parent / "runs" / "fixtures"
    for path in fixtures.glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                assert json.loads(line).get("schema") == "moat.v2", \
                    f"{path.name} is not a moat.v2 event fixture"


# F6: two lanes sharing a group need one seq allocator and one announced-lane list.
async def test_group_stream_shares_sequence_and_lanes():
    events = [e async for e in stream_group("demo", ["none", "moat"],
                                            guard=StubGuard(), client=FakeClient())]

    check_stream(events, "group")                      # group-wide seq + lane-announcement
    started = _by_type(events, "run_started")
    assert {e.lane for e in started} == {"baseline", "moat"}
    assert all(set(e.data.lanes) == {"baseline", "moat"} for e in started)
    seqs = [e.seq for e in events if e.seq is not None]
    assert seqs == sorted(set(seqs)), "seq must be group-stable and strictly increasing"


# F7: the guard receives each sent query's real provenance (hop + run_id), not hop:0/run_id:"".
async def test_guard_history_carries_real_provenance():
    spy = _HistorySpy()
    await collect("demo", "moat", guard=spy, client=FakeClient(reflect_answer="CONTINUE"),
                  run_id="run-xyz")

    assert len(spy.histories) >= 2, "need a second hop to inspect the history"
    first_sent = spy.histories[1][0]                   # the query dispatched on hop 1
    assert first_sent["hop"] == 1 and first_sent["run_id"] == "run-xyz"
