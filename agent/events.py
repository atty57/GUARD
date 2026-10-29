"""MOAT Guard event contract — schema ``moat.v2``.

Machine-checkable twin of ``docs/INTERFACES.md`` §3. Import these models to build and
validate the SSE event stream shared by the agent graph (provider) and the UI backend
(consumer). Run ``python -m agent.events`` to validate the hand-written fixtures in
``runs/fixtures/`` against the contract, including the cross-field invariants that plain
type-checking cannot catch.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

SCHEMA = "moat.v2"

# --- frozen vocabularies (INTERFACES.md §3) ---------------------------------------------
Lane = Literal["baseline", "moat"]
Condition = Literal["none", "prompt", "moat", "moat_prompt"]
Decision = Literal["pass", "rewrite", "block"]
BlockKind = Literal["redraftable", "unsatisfiable"]
Node = Literal[
    "ingest", "draft_query", "moat_guard", "passthrough",
    "web_search", "skip_hop", "reflect", "generate_report",
]
LeakType = Literal[
    "exact_value", "unit_change", "range", "split", "derived",
    "relative", "encoding", "process_fingerprint", "identifier", "intent_profile",
]
DiffOpKind = Literal["equal", "insert", "delete"]
RunStatus = Literal["completed", "completed_with_skips", "failed"]
SkipReason = Literal["redrafts_exhausted", "k_anonymity_unreachable", "guard_exception"]
ErrorWhere = Literal["guard", "search", "model", "graph"]
IntervalBasis = Literal["original", "final"]
IntervalStatus = Literal["safe", "warning", "prevented"]
InjectionAction = Literal["ignored", "blocked"]

_Interval = Annotated[list[float], Field(min_length=2, max_length=2)]  # [lo, hi]


# --- nested payload pieces --------------------------------------------------------------
class _D(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Hit(_D):
    id: str
    title: str
    score: float


class DiffOp(_D):
    op: DiffOpKind
    text: str


# --- per-event data payloads (INTERFACES.md §3 table) -----------------------------------
class HelloData(_D):
    schema_: Literal["moat.v2"] = Field(default=SCHEMA, alias="schema")
    group_id: str


class RunStartedData(_D):
    task_id: str
    condition: Condition
    tau: float
    model: str
    policy_hash: str
    replay: bool
    lanes: list[Lane]


class NodeEnteredData(_D):
    node: Node


class HopStartedData(_D):
    hop: int
    subgoal: str


class QueryDraftedData(_D):
    draft: str
    latency_ms: dict[str, float]


class GuardDecisionData(_D):
    decision: Decision
    tier: int
    original: str
    final: str | None
    diff: list[DiffOp]
    leak_types: list[LeakType]
    secrets_touched: list[str]
    reasons: list[str]
    latency_ms: dict[str, float]
    block_kind: BlockKind | None = None
    rechecked: bool | None = None


class SecretIntervalUpdateData(_D):
    secret_id: str
    dim: str
    unit: str
    prior: _Interval
    before: _Interval
    after: _Interval
    width_ratio: float
    bits: float
    status: IntervalStatus
    committed: bool
    basis: IntervalBasis
    decision_seq: int


class ProfileUpdateData(_D):
    revealed: dict[str, str]
    k: int
    k_min: int
    generalized_from: str | None = None
    committed: bool
    status: IntervalStatus


class SearchResultsData(_D):
    query: str
    corpus: str
    corpus_sha: str
    hits: list[Hit]


class HopSkippedData(_D):
    reason: SkipReason
    detail: str


class InjectionDetectedData(_D):
    doc_id: str
    pattern: str
    action: InjectionAction


class ObserverEstimateData(_D):
    condition: Condition
    attr: str
    point: float
    p10: float
    p90: float


class ReportChunkData(_D):
    text: str


class RunCompleteData(_D):
    status: RunStatus
    hops: int
    sent: int
    rewritten: int
    blocked: int
    trace_url: str | None = None


class ErrorData(_D):
    where: ErrorWhere
    message: str
    fail_closed: Literal[True] = True
    dispatched: bool


class ReplayMetaData(_D):
    recorded_at: str
    source_run_id: str
    speed: float


# --- envelope + typed events ------------------------------------------------------------
class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, allow_inf_nan=False)
    schema_: Literal["moat.v2"] = Field(default=SCHEMA, alias="schema")
    run_id: str | None = None
    group_id: str
    lane: Lane | None = None
    seq: int | None = None
    ts: str
    hop: int | None = None


class HelloEvent(_Base):
    type: Literal["hello"] = "hello"
    data: HelloData


class RunStartedEvent(_Base):
    type: Literal["run_started"] = "run_started"
    data: RunStartedData


class NodeEnteredEvent(_Base):
    type: Literal["node_entered"] = "node_entered"
    data: NodeEnteredData


class HopStartedEvent(_Base):
    type: Literal["hop_started"] = "hop_started"
    data: HopStartedData


class QueryDraftedEvent(_Base):
    type: Literal["query_drafted"] = "query_drafted"
    data: QueryDraftedData


class GuardDecisionEvent(_Base):
    type: Literal["guard_decision"] = "guard_decision"
    data: GuardDecisionData


class SecretIntervalUpdateEvent(_Base):
    type: Literal["secret_interval_update"] = "secret_interval_update"
    data: SecretIntervalUpdateData


class ProfileUpdateEvent(_Base):
    type: Literal["profile_update"] = "profile_update"
    data: ProfileUpdateData


class SearchResultsEvent(_Base):
    type: Literal["search_results"] = "search_results"
    data: SearchResultsData


class HopSkippedEvent(_Base):
    type: Literal["hop_skipped"] = "hop_skipped"
    data: HopSkippedData


class InjectionDetectedEvent(_Base):
    type: Literal["injection_detected"] = "injection_detected"
    data: InjectionDetectedData


class ObserverEstimateEvent(_Base):
    type: Literal["observer_estimate"] = "observer_estimate"
    data: ObserverEstimateData


class ReportChunkEvent(_Base):
    type: Literal["report_chunk"] = "report_chunk"
    data: ReportChunkData


class RunCompleteEvent(_Base):
    type: Literal["run_complete"] = "run_complete"
    data: RunCompleteData


class ErrorEvent(_Base):
    type: Literal["error"] = "error"
    data: ErrorData


class ReplayMetaEvent(_Base):
    type: Literal["replay_meta"] = "replay_meta"
    data: ReplayMetaData


Event = Annotated[
    Union[
        HelloEvent, RunStartedEvent, NodeEnteredEvent, HopStartedEvent, QueryDraftedEvent,
        GuardDecisionEvent, SecretIntervalUpdateEvent, ProfileUpdateEvent, SearchResultsEvent,
        HopSkippedEvent, InjectionDetectedEvent, ObserverEstimateEvent, ReportChunkEvent,
        RunCompleteEvent, ErrorEvent, ReplayMetaEvent,
    ],
    Field(discriminator="type"),
]
EventAdapter: TypeAdapter = TypeAdapter(Event)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_event(type: str, group_id: str, data: dict, *, run_id: str | None = None,
               lane: str | None = None, seq: int | None = None, hop: int | None = None,
               ts: str | None = None):
    """Build and validate one event. Raises if it violates the contract."""
    evt = EventAdapter.validate_python({
        "schema": SCHEMA, "type": type, "group_id": group_id, "run_id": run_id,
        "lane": lane, "seq": seq, "hop": hop, "ts": ts or _now_iso(), "data": data,
    })
    _check_event(evt)
    return evt


def to_sse(evt) -> str:
    """Render one event as an SSE frame. Omits ``id:`` when ``seq`` is None (e.g. hello)."""
    lines = [f"event: {evt.type}"]
    if evt.seq is not None:
        lines.append(f"id: {evt.seq}")
    lines.append(f"data: {evt.model_dump_json(by_alias=True)}")
    return "\n".join(lines) + "\n\n"


# --- self-check: validate fixtures + cross-field/stream invariants (INTERFACES.md P2-8) --
_FIXTURES = Path(__file__).resolve().parent.parent / "runs" / "fixtures"


def _load(path: Path) -> list:
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(EventAdapter.validate_json(line))
        except Exception as exc:  # noqa: BLE001 - want the line number in the message
            raise AssertionError(f"{path.name}:{i} failed validation: {exc}") from exc
    return out


def _check_event(e) -> None:
    """Per-event semantic checks, shared by make_event() and check_stream()."""
    if e.type == "hello":
        assert e.seq is None and e.run_id is None and e.lane is None and e.hop is None, \
            "hello must be group-scoped (null run_id/lane/seq/hop)"
        return
    assert (e.run_id is not None and e.lane is not None
            and e.seq is not None and e.hop is not None), \
        f"{e.type} must carry run_id/lane/seq/hop (seq {e.seq})"
    if e.type == "guard_decision":
        d = e.data
        if d.decision == "pass":
            assert d.final == d.original and d.diff == [] and d.leak_types == [], \
                f"pass must not change the query (seq {e.seq})"
        elif d.decision == "rewrite":
            assert d.final is not None and d.final != d.original and d.diff, \
                f"rewrite must change the query and carry a diff (seq {e.seq})"
        else:  # block
            assert d.final is None, f"block must have final=null (seq {e.seq})"
        if d.tier == 2:
            assert d.rechecked is True, f"tier-2 rewrite must be rechecked (seq {e.seq})"
    elif e.type == "hop_started":
        assert e.data.hop == e.hop, f"hop_started envelope/data hop mismatch (seq {e.seq})"
    elif e.type == "run_started":
        cond, lane = e.data.condition, e.lane
        ok = (lane == "baseline" and cond in ("none", "prompt")) or \
             (lane == "moat" and cond in ("moat", "moat_prompt"))
        assert ok, f"condition {cond!r} not allowed on lane {lane!r} (seq {e.seq})"


def check_stream(events: list, name: str) -> None:
    """Assert the cross-field and whole-stream invariants from the contract."""
    assert events, f"{name}: empty stream"

    # per-event checks + strictly increasing seq (hello is unnumbered, group-scoped).
    last = -1
    for e in events:
        _check_event(e)
        if e.type == "hello":
            continue
        assert e.seq > last, f"{name}: seq not strictly increasing at {e.seq}"
        last = e.seq

    # run_started binds the whole (group_id, lane, run_id) tuple; the lanes of a group have
    # distinct run_ids.
    lane_run: dict[str, str] = {}
    announced: set[str] = set()
    started: set[tuple[str, str, str]] = set()
    for e in events:
        if e.type == "run_started":
            assert e.lane not in lane_run, f"{name}: lane {e.lane} started twice"
            lane_run[e.lane] = e.run_id
            announced.update(e.data.lanes)
            started.add((e.group_id, e.lane, e.run_id))
    assert announced == set(lane_run), \
        f"{name}: announced lanes {announced} != started lanes {set(lane_run)}"
    assert len(set(lane_run.values())) == len(lane_run), \
        f"{name}: lanes in a group must have distinct run_ids ({lane_run})"

    # indexes for the dispatch / commit cross-references.
    decisions: dict[int, GuardDecisionEvent] = {}
    dispatched: set[tuple[str, int]] = set()
    previews: dict[tuple[int, str, str], SecretIntervalUpdateData] = {}
    for e in events:
        if e.type == "guard_decision":
            decisions[e.seq] = e
        elif e.type == "search_results":
            dispatched.add((e.run_id, e.hop))
        elif e.type == "error" and e.data.dispatched:
            dispatched.add((e.run_id, e.hop))
        elif e.type == "secret_interval_update" and not e.data.committed:
            previews[(e.data.decision_seq, e.data.secret_id, e.data.basis)] = e.data

    # Every interval points to a decision on its own (run_id, hop). A committed interval needs
    # a pass/rewrite decision, a dispatched query, and numbers equal to its own final preview
    # -- a blocked decision can never back a commit, and a commit cannot restate the preview.
    for e in events:
        if e.type != "secret_interval_update":
            continue
        d = e.data
        dec = decisions.get(d.decision_seq)
        assert dec is not None and dec.run_id == e.run_id and dec.hop == e.hop, \
            f"{name}: interval decision_seq {d.decision_seq} is not a decision on this hop (seq {e.seq})"
        if d.committed:
            assert dec.data.decision in ("pass", "rewrite"), \
                f"{name}: committed interval cannot be backed by a block (seq {e.seq})"
            assert d.basis == "final", f"{name}: committed interval must be basis=final (seq {e.seq})"
            pv = previews.get((d.decision_seq, d.secret_id, "final"))
            assert pv is not None, f"{name}: committed interval has no matching preview (seq {e.seq})"
            assert (d.after, d.width_ratio, d.bits) == (pv.after, pv.width_ratio, pv.bits), \
                f"{name}: committed interval must equal its preview (seq {e.seq})"
            assert (e.run_id, e.hop) in dispatched, \
                f"{name}: committed interval without a dispatched query (seq {e.seq})"

    # committed profile exposure must have actually dispatched.
    for e in events:
        if e.type == "profile_update" and e.data.committed:
            assert (e.run_id, e.hop) in dispatched, \
                f"{name}: committed profile_update without a dispatched query (seq {e.seq})"

    # each run_complete must match a run_started on the exact (group_id, lane, run_id) tuple.
    completed: dict[str, int] = {}
    for e in events:
        if e.type == "run_complete":
            assert (e.group_id, e.lane, e.run_id) in started, \
                f"{name}: run_complete (group,lane,run) has no matching run_started (seq {e.seq})"
            completed[e.lane] = completed.get(e.lane, 0) + 1
            assert e.data.rewritten <= e.data.sent, \
                f"{name}: rewritten must be <= sent (seq {e.seq})"
    for lane in lane_run:
        assert completed.get(lane) == 1, f"{name}: lane {lane} has {completed.get(lane, 0)} completions"


# --- negative tests: Codex counterexamples the contract must now reject -----------------
def _expect_fail(desc: str, fn) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - any rejection is a pass
        print(f"OK  (rejected) {desc}  <- {type(exc).__name__}")
        return
    raise AssertionError(f"NEGATIVE TEST DID NOT FAIL: {desc}")


def _neg_commit_backed_by_block() -> None:
    """A committed interval whose decision_seq points at a blocked decision."""
    evs = [
        make_event("hello", "g", {"schema": SCHEMA, "group_id": "g"}),
        make_event("run_started", "g", {"task_id": "t", "condition": "moat", "tau": 0.5,
                   "model": "m", "policy_hash": "h", "replay": True, "lanes": ["moat"]},
                   run_id="r", lane="moat", hop=0, seq=0),
        make_event("guard_decision", "g", {"decision": "block", "tier": 0, "original": "x",
                   "final": None, "diff": [], "leak_types": ["exact_value"],
                   "secrets_touched": ["S"], "reasons": ["blocked"], "latency_ms": {"total": 1.0},
                   "block_kind": "redraftable"}, run_id="r", lane="moat", hop=3, seq=6),
        make_event("search_results", "g", {"query": "q", "corpus": "c", "corpus_sha": "s",
                   "hits": []}, run_id="r", lane="moat", hop=3, seq=7),
        make_event("secret_interval_update", "g", {"secret_id": "S", "dim": "d", "unit": "u",
                   "prior": [0, 1], "before": [0, 1], "after": [0, 1], "width_ratio": 1.0,
                   "bits": 0.0, "status": "safe", "committed": True, "basis": "final",
                   "decision_seq": 6}, run_id="r", lane="moat", hop=3, seq=8),
        make_event("run_complete", "g", {"status": "completed", "hops": 1, "sent": 1,
                   "rewritten": 0, "blocked": 0, "trace_url": None},
                   run_id="r", lane="moat", hop=3, seq=9),
    ]
    check_stream(evs, "neg_commit_backed_by_block")


def _neg_same_run_id_both_lanes() -> None:
    """Both lanes of one group sharing a single run_id."""
    def rs(cond):
        return {"task_id": "t", "condition": cond, "tau": 0.5, "model": "m",
                "policy_hash": "h", "replay": True, "lanes": ["baseline", "moat"]}
    evs = [
        make_event("hello", "g", {"schema": SCHEMA, "group_id": "g"}),
        make_event("run_started", "g", rs("none"), run_id="DUP", lane="baseline", hop=0, seq=0),
        make_event("run_started", "g", rs("moat"), run_id="DUP", lane="moat", hop=0, seq=1),
    ]
    check_stream(evs, "neg_same_run_id_both_lanes")


def _neg_nan_hit_score() -> None:
    """A Hit.score of NaN must not round-trip through the contract."""
    make_event("search_results", "g", {"query": "q", "corpus": "c", "corpus_sha": "s",
               "hits": [{"id": "US1", "title": "t", "score": float("nan")}]},
               run_id="r", lane="moat", hop=1, seq=0)


def _rs(cond):
    return {"task_id": "t", "condition": cond, "tau": 0.5, "model": "m", "policy_hash": "h",
            "replay": True, "lanes": ["baseline", "moat"]}


def _neg_completion_group_mismatch() -> None:
    """A run_complete whose (group_id, lane, run_id) tuple was never started."""
    evs = [
        make_event("hello", "g", {"schema": SCHEMA, "group_id": "g"}),
        make_event("run_started", "g", _rs("none"), run_id="B", lane="baseline", hop=0, seq=0),
        make_event("run_started", "other-group", _rs("moat"), run_id="M", lane="moat", hop=0, seq=1),
        make_event("run_complete", "g", {"status": "completed", "hops": 1, "sent": 1,
                   "rewritten": 0, "blocked": 0, "trace_url": None},
                   run_id="M", lane="moat", hop=1, seq=2),
    ]
    check_stream(evs, "neg_completion_group_mismatch")


def _neg_commit_not_equal_preview() -> None:
    """A committed interval whose (after, width_ratio, bits) differ from its preview."""
    base = {"secret_id": "S", "dim": "energy_density", "unit": "Wh/kg", "prior": [150, 500],
            "decision_seq": 1}
    evs = [
        make_event("hello", "g", {"schema": SCHEMA, "group_id": "g"}),
        make_event("run_started", "g", {"task_id": "t", "condition": "moat", "tau": 0.5,
                   "model": "m", "policy_hash": "h", "replay": True, "lanes": ["moat"]},
                   run_id="r", lane="moat", hop=0, seq=0),
        make_event("guard_decision", "g", {"decision": "rewrite", "tier": 1, "original": "a",
                   "final": "b", "diff": [{"op": "insert", "text": "b"}], "leak_types": ["range"],
                   "secrets_touched": ["S"], "reasons": ["x"], "latency_ms": {"total": 1.0}},
                   run_id="r", lane="moat", hop=1, seq=1),
        make_event("secret_interval_update", "g", {**base, "before": [150, 500], "after": [150, 500],
                   "width_ratio": 1.0, "bits": 0.0, "status": "safe", "committed": False,
                   "basis": "final"}, run_id="r", lane="moat", hop=1, seq=2),
        make_event("search_results", "g", {"query": "b", "corpus": "c", "corpus_sha": "s",
                   "hits": []}, run_id="r", lane="moat", hop=1, seq=3),
        make_event("secret_interval_update", "g", {**base, "before": [150, 500], "after": [372, 372],
                   "width_ratio": 0.0, "bits": 8.45, "status": "warning", "committed": True,
                   "basis": "final"}, run_id="r", lane="moat", hop=1, seq=4),
        make_event("run_complete", "g", {"status": "completed", "hops": 1, "sent": 1,
                   "rewritten": 1, "blocked": 0, "trace_url": None},
                   run_id="r", lane="moat", hop=1, seq=5),
    ]
    check_stream(evs, "neg_commit_not_equal_preview")


def _neg_condition_lane_mismatch() -> None:
    """condition=none declared on the moat lane."""
    make_event("run_started", "g", {"task_id": "t", "condition": "none", "tau": 0.5, "model": "m",
               "policy_hash": "h", "replay": True, "lanes": ["baseline", "moat"]},
               run_id="r", lane="moat", hop=0, seq=0)


def _main() -> int:
    files = sorted(_FIXTURES.glob("*.jsonl"))
    assert files, f"no fixtures found in {_FIXTURES}"
    for path in files:
        events = _load(path)
        check_stream(events, path.name)
        for e in events:  # every valid event must render to an SSE frame (no NaN/inf)
            to_sse(e)
        print(f"OK  {path.name}: {len(events)} events validated")
    print(f"All {len(files)} fixtures satisfy the moat.v2 contract.")
    print("--- negative tests ---")
    _expect_fail("commit backed by a blocked decision_seq", _neg_commit_backed_by_block)
    _expect_fail("both lanes share one run_id", _neg_same_run_id_both_lanes)
    _expect_fail("Hit.score = NaN", _neg_nan_hit_score)
    _expect_fail("run_complete tuple never started", _neg_completion_group_mismatch)
    _expect_fail("committed interval != its preview", _neg_commit_not_equal_preview)
    _expect_fail("condition none on moat lane", _neg_condition_lane_mismatch)
    print("All negative tests correctly rejected.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except AssertionError as exc:
        print(f"CONTRACT CHECK FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
