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

from agent.events import make_event
from agent.graph import (
    MAX_HOPS,
    FakeClient,
    ResearchState,
    StubGuard,
    collect,
)


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
