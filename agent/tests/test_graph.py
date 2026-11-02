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
    _call_seed,
    _chat_completion,
    _token_ladder,
    collect,
    stream_group,
)
from agent.llm import ReplayMiss
from guard.policy import load_policy, policy_hash

_POLICY = Path(__file__).resolve().parent.parent.parent / "data" / "policy.example.yaml"


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


# F8: a schema error caught only by make_event (NaN score) must keep the dispatch accounting.
async def test_schema_invalid_hit_retains_dispatch_accounting():
    def _nan_search(query, k=5):
        return [{"id": "US1", "title": "t", "score": float("nan")}]     # NaN fails validation

    events = await collect("demo", "none", search=_nan_search, client=FakeClient())

    err = _by_type(events, "error")[0]
    assert err.data.where == "search" and err.data.dispatched is True
    assert _by_type(events, "search_results") == []
    assert events[-1].data.sent == 1 and events[-1].data.hops == 1
    check_stream(events, "nan-score")


# F9: a profile proposal on an unsatisfiable block is prevented, not safe.
async def test_skipped_profile_proposal_is_prevented():
    prof = {"revealed": {"cathode": "NCM811"}, "k": 2, "k_min": 5}
    guard = StubGuard(mode="block", block_kind="unsatisfiable", profile=prof)
    events = await collect("demo", "moat", guard=guard, client=FakeClient())

    profiles = _by_type(events, "profile_update")
    assert profiles and all(p.data.status == "prevented" for p in profiles)
    assert _by_type(events, "hop_skipped")[0].data.reason == "k_anonymity_unreachable"
    check_stream(events, "prevented-profile")


# --- Hyeseong PR-review asks ------------------------------------------------------------------
# Ask 1(a): with the real example policy loaded, a blocking guard dispatches nothing.
async def test_policy_param_blocks_leak_draft_and_dispatches_nothing():
    policy = load_policy(_POLICY)
    events = await collect("demo", "moat", guard=StubGuard(mode="block"),
                           client=FakeClient(draft="0.272 kWh/kg"), policy=policy)

    assert any("0.272 kWh/kg" in e.data.draft for e in _by_type(events, "query_drafted"))
    assert _by_type(events, "search_results") == []        # nothing crossed the boundary
    assert events[-1].data.sent == 0


# Ask 1(b): run_started.policy_hash is the hash of the loaded policy.
async def test_run_started_policy_hash_matches_loaded_policy():
    policy = load_policy(_POLICY)
    events = await collect("demo", "moat", guard=StubGuard(), client=FakeClient(), policy=policy)

    assert _by_type(events, "run_started")[0].data.policy_hash == policy_hash(policy)


# Ask 2: a blocked interval preview is stamped status "prevented".
async def test_blocked_interval_preview_is_prevented():
    iv = {"secret_id": "S1", "dim": "energy_density", "unit": "Wh/kg", "prior": [150, 350],
          "before": [150, 350], "after": [270, 274], "width_ratio": 0.013, "bits": 6.3,
          "status": "warning", "basis": "original"}        # 'warning' must be overridden on block
    guard = StubGuard(mode="block", block_kind="redraftable", intervals=[iv])
    events = await collect("demo", "moat", guard=guard, client=FakeClient(), policy=load_policy(_POLICY))

    intervals = _by_type(events, "secret_interval_update")
    assert intervals and all(e.data.status == "prevented" for e in intervals)
    assert all(e.data.committed is False for e in intervals)
    check_stream(events, "prevented-interval")


# --- #9: search results reach the reflect / report prompts ------------------------------------
class _CapturingClient(FakeClient):
    """FakeClient that records every message list it is asked to complete."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls: list[list[dict]] = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs["messages"])
        return await super().complete(**kwargs)


def _marker_search(query, k=5):
    return [{"id": "US-MARKER", "title": "Patent MARKER-TITLE-ZZZ marker-in-title",
             "snippet": "snippet carrying MARKER-ZZZ about the task", "score": 9.0}]


# R1: a stub search's marker string is reachable in the generate_report prompt.
async def test_search_marker_reaches_report_prompt():
    client = _CapturingClient()                            # default reflect_answer="DONE" -> 1 hop
    await collect("demo", "none", search=_marker_search, client=client)

    report_msgs = [c for c in client.calls
                   if any("research report" in m["content"] for m in c)]
    assert report_msgs, "generate_report must have been prompted"
    text = " ".join(m["content"] for m in report_msgs[0])
    assert "MARKER-ZZZ" in text and "MARKER-TITLE-ZZZ" in text
    assert "[hop 1]" in text                               # hop number is carried through


class _MarkerReflectClient(FakeClient):
    """Says DONE only when the reflect prompt shows a result containing the answer marker."""

    async def complete(self, **kwargs):
        text = " ".join(m["content"] for m in kwargs["messages"]).lower()
        if "reflect on task" in text:
            return _chat_completion("DONE" if "answer-found" in text else "CONTINUE")
        return await super().complete(**kwargs)


# R2: reflect continues on thin results and finishes once the results answer the task.
async def test_reflect_decides_from_results():
    def thin_search(query, k=5):
        return [{"id": "US1", "title": "unrelated", "snippet": "nothing useful", "score": 1.0}]

    def answering_search(query, k=5):
        return [{"id": "US2", "title": "the answer", "snippet": "ANSWER-FOUND here", "score": 9.0}]

    thin = await collect("demo", "none", search=thin_search, client=_MarkerReflectClient())
    assert thin[-1].data.hops == MAX_HOPS                  # never DONE -> loops to the cap

    ans = await collect("demo", "none", search=answering_search, client=_MarkerReflectClient())
    assert ans[-1].data.hops == 1                          # reflect saw the answer -> DONE at hop 1


# R3: a run that sends zero queries still produces a report without crashing.
async def test_zero_queries_still_reports():
    # every hop's draft is blocked unsatisfiably -> skip_hop, nothing ever searched.
    events = await collect("demo", "moat", guard=StubGuard(mode="block", block_kind="unsatisfiable"),
                           client=FakeClient(), policy=load_policy(_POLICY))

    assert events[-1].data.sent == 0
    assert _by_type(events, "search_results") == []
    assert _by_type(events, "report_chunk"), "a report must still be generated with no results"
    check_stream(events, "zero-queries-report")


# --- G2 seed-range regression (keyless) --------------------------------------------------------
# The Meta API rejects a seed outside the nonnegative signed 32-bit range 0..2147483647. The old
# derivation took the top 8 hex of a SHA-256 (reaching 0xFFFFFFFF), so ~half of all per-call seeds
# were out of range and the live call failed. _call_seed masks with 0x7FFFFFFF; these lock that in.
_INT32_MAX = 2147483647


def test_call_seed_stays_in_signed_int32_including_previously_rejected():
    # Exhaustively sweep representative call coordinates; every seed must be API-acceptable.
    for task in ("halden-t1", "halden-t5", "demo"):
        for seed_index in (0, 1, 7):
            for role in ("draft", "reflect", "report"):
                for hop in range(6):
                    for attempt in range(4):
                        s = _call_seed(task, seed_index, role, hop, attempt)
                        assert isinstance(s, int) and 0 <= s <= _INT32_MAX, (
                            task, seed_index, role, hop, attempt, s)
    # A coordinate whose pre-fix seed was 2853632783 (> 2**31-1) — exactly the case the API used to
    # reject — now maps into range by masking off the high bit.
    assert _call_seed("halden-t1", 0, "reflect", 0, 0) == 2853632783 & _INT32_MAX == 706149135
    # seed_index None keeps back-compat (omit the seed entirely).
    assert _call_seed("halden-t1", None, "draft", 1, 0) is None


def test_call_seed_is_condition_independent_so_matched_calls_pair():
    # _call_seed takes no condition: none vs moat at the same (task, seed_index, role, hop, attempt)
    # issue the identical seed, which is what makes a condition pair sample identically (G2).
    coord = ("halden-t1", 0, "draft", 1, 0)
    assert _call_seed(*coord) == _call_seed(*coord)        # deterministic
    assert "condition" not in _call_seed.__code__.co_varnames


def test_call_seed_differs_across_hops_roles_and_redraft_attempts():
    base = _call_seed("halden-t1", 0, "draft", 1, 0)
    assert _call_seed("halden-t1", 0, "draft", 2, 0) != base      # different hop
    assert _call_seed("halden-t1", 0, "reflect", 1, 0) != base    # different role
    assert _call_seed("halden-t1", 0, "draft", 1, 1) != base      # different redraft attempt
    # a batch of distinct coordinates should give distinct seeds (no systematic collisions).
    seeds = {_call_seed("halden-t1", 0, r, h, a)
             for r in ("draft", "reflect", "report") for h in range(5) for a in range(3)}
    assert len(seeds) == 3 * 5 * 3


# --- empty model response is rejected loudly (keyless) -----------------------------------------
# muse-spark-1.3 can exhaust max_tokens on hidden reasoning and return the exact shape the pilot
# recorded: message.content=None with finish_reason='length'. The graph must fail loud (naming the
# node + finish_reason), never .strip() a None or let an empty draft reach guard/search.
def _empty_completion():
    return _chat_completion(None, finish_reason="length")   # the recorded truncated response


class _EmptyAtClient:
    """Canned responses, except the named node's call returns the recorded empty completion."""

    model, mode = "muse-spark-1.3", "fake"

    def __init__(self, empty_role: str) -> None:            # 'draft' | 'reflect' | 'report'
        self.empty_role = empty_role

    async def complete(self, **kwargs):
        from agent.graph import _canned_text

        text = " ".join(m["content"] for m in kwargs["messages"]).lower()
        role = "draft" if "search query" in text else "report" if "report" in text else "reflect"
        if role == self.empty_role:
            return _empty_completion()
        return _chat_completion(_canned_text(kwargs["messages"]))


async def test_empty_draft_fails_before_guard_and_search():
    events = await collect("demo", "moat", client=_EmptyAtClient("draft"),
                           policy=load_policy(_POLICY))
    err = _by_type(events, "error")[-1]
    assert "draft_query" in err.data.message and "length" in err.data.message
    assert events[-1].type == "run_complete" and events[-1].data.status == "failed"
    assert _by_type(events, "guard_decision") == [], "empty draft must not reach the guard"
    assert _by_type(events, "search_results") == [], "empty draft must not reach search"
    assert events[-1].data.sent == 0


async def test_empty_reflection_is_rejected():
    events = await collect("demo", "none", client=_EmptyAtClient("reflect"),
                           policy=load_policy(_POLICY))
    err = _by_type(events, "error")[-1]
    assert "reflect" in err.data.message and "length" in err.data.message
    assert events[-1].data.status == "failed"


async def test_empty_report_is_rejected():
    events = await collect("demo", "none", client=_EmptyAtClient("report"),
                           policy=load_policy(_POLICY))
    err = _by_type(events, "error")[-1]
    assert "generate_report" in err.data.message and "length" in err.data.message
    assert events[-1].data.status == "failed"
    assert _by_type(events, "report_chunk") == [], "no report chunk emitted when report is empty"


# --- GEN_RETRY_POLICY: one bounded 4k->8k retry on truncation (keyless) ------------------------
class _TruncateThenSucceedClient:
    """Returns the recorded truncated shape while max_tokens <= limit, else canned text.
    Records (seed, max_tokens) per call to verify the paired seed survives the retry."""

    model, mode = "muse-spark-1.3", "fake"

    def __init__(self, limit: int = 4096) -> None:
        self.limit = limit
        self.calls: list[tuple] = []

    async def complete(self, **kwargs):
        from agent.graph import _canned_text

        self.calls.append((kwargs.get("seed"), kwargs.get("max_tokens")))
        if (kwargs.get("max_tokens") or 0) <= self.limit:
            return _chat_completion(None, finish_reason="length")   # recorded truncated shape
        return _chat_completion(_canned_text(kwargs["messages"]))


def test_token_ladder_is_initial_then_double():
    assert _token_ladder(4096) == [4096, 8192]
    assert _token_ladder(2048) == [2048, 4096]
    assert _token_ladder(None) == [None]        # back-compat: no cap, single call, no retry


async def test_retry_recovers_truncated_draft_with_same_seed():
    client = _TruncateThenSucceedClient(limit=4096)        # 4k truncates, 8k succeeds
    events = await collect("demo", "none", client=client, seed=0, max_tokens=4096,
                           policy=load_policy(_POLICY))
    assert events[-1].type == "run_complete"
    assert events[-1].data.status in ("completed", "completed_with_skips")
    # the first draft call hits the 4k cap, then retries ONCE at 8k with the identical seed
    assert client.calls[0][1] == 4096
    assert client.calls[1] == (client.calls[0][0], 8192)
    assert all(mt in (4096, 8192) for _, mt in client.calls)


async def test_no_retry_when_first_call_succeeds():
    class _OK(_TruncateThenSucceedClient):
        async def complete(self, **kwargs):
            from agent.graph import _canned_text

            self.calls.append((kwargs.get("seed"), kwargs.get("max_tokens")))
            return _chat_completion(_canned_text(kwargs["messages"]))

    client = _OK()
    events = await collect("demo", "none", client=client, seed=0, max_tokens=4096,
                           policy=load_policy(_POLICY))
    assert events[-1].data.status in ("completed", "completed_with_skips")
    assert all(mt == 4096 for _, mt in client.calls), "must not escalate when the 4k call succeeds"


async def test_retry_exhausted_still_empty_fails_loud():
    client = _TruncateThenSucceedClient(limit=10**9)       # truncates at every cap
    events = await collect("demo", "none", client=client, seed=0, max_tokens=4096,
                           policy=load_policy(_POLICY))
    err = _by_type(events, "error")[-1]
    assert "draft_query" in err.data.message and "length" in err.data.message
    assert events[-1].data.status == "failed"
    # exactly one bounded retry: draft tried 4k then 8k, both truncated, then failed loud
    assert [mt for _, mt in client.calls] == [4096, 8192]


# --- a truncated FINAL attempt is an error even with partial text (never an accepted run) -------
class _TruncatedWithTextClient:
    """Every call returns partial text with finish_reason='length' (never a clean stop)."""

    model, mode = "muse-spark-1.3", "fake"

    async def complete(self, **kwargs):
        return _chat_completion("partial draft cut off mid-", finish_reason="length")


class _TruncatedReportClient(FakeClient):
    """Clean draft + DONE reflection, but the report comes back truncated-with-text."""

    async def complete(self, **kwargs):
        from agent.graph import _canned_text

        text = " ".join(m["content"] for m in kwargs["messages"]).lower()
        if "report" in text:
            return _chat_completion("partial report body", finish_reason="length")
        return _chat_completion(_canned_text(kwargs["messages"]))


async def test_truncated_final_draft_with_text_is_not_accepted():
    events = await collect("demo", "none", client=_TruncatedWithTextClient(), seed=0,
                           max_tokens=4096, policy=load_policy(_POLICY))
    err = _by_type(events, "error")[-1]
    assert "draft_query" in err.data.message and "truncated output" in err.data.message
    assert events[-1].type == "run_complete"
    assert events[-1].data.status == "failed"
    assert events[-1].data.status not in ("completed", "completed_with_skips")
    assert _by_type(events, "search_results") == []        # truncated draft never dispatched


async def test_truncated_final_report_with_text_is_not_accepted():
    events = await collect("demo", "none", client=_TruncatedReportClient(), seed=0,
                           max_tokens=4096, policy=load_policy(_POLICY))
    err = _by_type(events, "error")[-1]
    assert "generate_report" in err.data.message and "truncated output" in err.data.message
    assert events[-1].data.status == "failed"
    assert events[-1].data.status not in ("completed", "completed_with_skips")
    assert _by_type(events, "report_chunk") == [], "a truncated report must not be published"


# --- reflection must be exactly DONE or CONTINUE -----------------------------------------------
async def test_reflection_other_than_done_or_continue_fails_loud():
    events = await collect("demo", "none", client=FakeClient(reflect_answer="maybe keep looking"),
                           policy=load_policy(_POLICY))
    err = _by_type(events, "error")[-1]
    assert "reflect" in err.data.message and "DONE" in err.data.message and "CONTINUE" in err.data.message
    assert events[-1].data.status == "failed"


async def test_reflection_continue_and_done_are_both_valid():
    # CONTINUE at hop 1 then (FakeClient default) DONE keeps the run accepted — strict check passes.
    done = await collect("demo", "none", client=FakeClient(reflect_answer="  continue  "),
                         policy=load_policy(_POLICY))
    # 'continue' (whitespace/case tolerant) is accepted as not-done; the loop runs to MAX_HOPS then
    # reports, so the run still completes rather than erroring on the reflection format.
    assert done[-1].type == "run_complete"
    assert done[-1].data.status in ("completed", "completed_with_skips")
