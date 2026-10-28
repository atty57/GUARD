# MOAT Guard — Interfaces (schema `moat.v2`)

> **Frozen:** 2026-10-28 (M1). This file is the single authoritative contract for the
> agent ↔ guard ↔ UI boundary. Changes here or to `agent/events.py` need the affected
> owner's approval (HANDOFF §12).

| Surface | Provider | Consumer |
|---|---|---|
| `guard.check()` | Hyeseong (guard) | Atharva (agent graph) |
| `search()` | Atharva (search) | Hyeseong (observer agent) |
| Event contract `moat.v2` | Atharva (agent graph) | Sebika (SSE backend + React UI) |

All identifiers (`run_id`, `group_id`, patent/`Hit.id`, `secret_id`, `task_id`) are opaque
**strings** — never parse one as a number.

---

## 1. Guard hook (Atharva ↔ Hyeseong)

```python
async def check(draft: str, history: list[SentQuery], policy: Policy) -> GuardResult: ...
```

- **Async.** The guard runs in the same async world as the graph; Tier 2 (the Muse Spark
  rewrite, §8) awaits the shared model client. Do not block the event loop.
- `history` is the list of queries **actually dispatched** this session (see §2 on the commit
  point) — not drafts, not blocked queries.
- The guard returns a **pure** `GuardResult`. It does **not** emit stream events and never
  imports LangGraph or the `moat.v2` schema. The agent's `moat_guard` node translates the
  result into `guard_decision`, `secret_interval_update`, and `profile_update` events.
- Any exception raised by `check()` counts as a **block** (fail closed).

### `GuardResult` (proposed — Hyeseong to confirm field names/types before freeze)

| field | type | meaning |
|---|---|---|
| `action` | `"pass" \| "rewrite" \| "block"` | the decision |
| `query` | `str \| None` | final query if `pass`/`rewrite`; **`None` on `block`** |
| `tier` | `int` | the **deciding (highest) tier that fired** — 0 value firewall, 1 ledger, 2 LLM rewrite |
| `block_kind` | `"redraftable" \| "unsatisfiable" \| None` | on `block`: `redraftable` → the node may redraft; `unsatisfiable` (e.g. k-anonymity cannot be reached) → the node must **skip the hop**. Lets routing avoid parsing `reasons`. `None` when not a block. |
| `leak_types` | `list[LeakType]` | which detectors fired (see §3 vocabulary) |
| `secrets_touched` | `list[str]` | secret ids implicated |
| `reasons` | `list[str]` | free-text, **display only** — never routed on |
| `latency_ms` | `dict[str, float]` | keys `tier0`/`tier1`/`tier2`/`total`; a tier that did not run is **omitted** |
| `intervals` | `list[IntervalProposal]` | ledger updates for touched secrets, all `committed=false` (see §2) |
| `profile` | `ProfileProposal \| None` | k-anonymity snapshot for this query, or `None` |
| `rechecked` | `bool` | **required `true` when `tier == 2`**: the Tier-2 rewrite's `query` was re-checked by Tiers 0–1 (§8 postcondition) |

`IntervalProposal` = `{secret_id, dim, unit, prior:[lo,hi], before:[lo,hi], after:[lo,hi],
width_ratio, bits, status, basis}` (the node stamps `decision_seq` and `committed` when it
emits the event — see §2). `basis ∈ {"original","final"}` says whether the interval describes
the drafted query or the rewritten one.

`ProfileProposal` = `{revealed: dict[str,str], k: int, k_min: int, generalized_from: str | None}`.

### `SentQuery` (proposed)

`{query: str, hop: int, run_id: str}` — one per dispatched query. The guard reads the
cumulative `query` values for the ledger. Confirm with Hyeseong.

### `Policy` (skeleton — **owned by Hyeseong**, placeholder only)

`{tau: float, min_width_ratio: float, bound_tol: float, max_bits: float, k_min: int}`.
**TODO (Hyeseong):** confirm fields and whether `tau` is one knob that derives the others or
an independent strictness value. `policy_hash` in `run_started` is a stable hash of the
serialized policy.

---

## 2. Search (Atharva → Hyeseong's observer)

```python
def search(query: str, k: int = 5) -> list[Hit]      # Hit = {id, title, snippet, score}
```

Deterministic BM25 over the local patent corpus (HANDOFF §A4). `id` is an opaque string.

**Disclosure commit point = dispatch.** A query reaches the adversary (the search provider)
the instant it is handed to `search()`, *before* any result returns. Therefore the
`moat_guard`/`web_search` nodes commit the ledger (`secret_interval_update` with
`committed:true`) **at dispatch**, not on successful results. If `search()` raises *after*
dispatch, the query still counts as **sent** and the ledger stays committed — the node emits
`error{where:"search", dispatched:true}` (not a block). A failure *before* dispatch emits
`error{dispatched:false}` and commits nothing.

The `search_results` **event** carries only `{id, title, score}` per hit — the `snippet` is
dropped from the wire (it stays in the `search()` return for the observer).

---

## 3. Event contract `moat.v2` (Atharva ↔ Sebika)

### Envelope

Every event is one JSON object:

```text
{ "schema": "moat.v2", "run_id": str|null, "group_id": str, "lane": "baseline"|"moat"|null,
  "seq": int|null, "ts": "ISO-8601 UTC Z", "hop": int|null, "type": str, "data": {...} }
```

- **`group_id`** groups the (up to two) lanes of one compare run. **`run_id`** identifies one
  lane's run. **`lane`** is the display lane.
- `seq` is **group-stable**: assigned once at emission, strictly increasing across the whole
  group, **never reset on reconnect**. SSE maps `event:` = `type`, `id:` = `seq`.
- **`hello` is group-scoped**: `run_id`, `lane`, `hop`, and `seq` are all `null`. Because it
  carries no `id:`, a reconnect never rewinds the browser's `Last-Event-ID` cursor.
- `hop` = `0` for `run_started`/`replay_meta`, `null` for `hello`, otherwise 1-indexed per
  search hop. It stays constant across redrafts within a hop (only `web_search` / `skip_hop`
  advance it). `ingest` is pre-hop, so `node_entered{node:"ingest"}` carries `hop:0` (before
  the first search hop).
- Lane↔condition: `run_started.condition` ∈ `{none, prompt, moat, moat_prompt}`;
  `none`/`prompt` → `lane:"baseline"`, `moat`/`moat_prompt` → `lane:"moat"`. This pairing is
  **enforced** (`make_event`/`check_stream` reject a `run_started` whose condition and lane
  disagree).

### Reconnect / resume

- **Replay** honors `Last-Event-ID`: the server resumes by skipping recorded lines with
  `seq ≤ Last-Event-ID` (no buffer — `seq` is group-stable).
- **Live** has no backfill. On a reconnect it cannot resume; the client marks the view stale
  and re-requests a fresh stream (full replay). Scrubbing / rewind / speed is a separate
  replay-UI control (`replay_meta.speed`), not part of this contract.

### Run identity & completion

`run_started` is emitted **once per lane** and **binds `lane → run_id`**; its `data.lanes`
lists every lane the group will contain. The group is complete when a `run_complete` exists
for each announced **`(group_id, lane, run_id)`**. There is no group-level event.

### Event table

| `type` | `hop` | `data` |
|---|---|---|
| `hello` | null | `{schema, group_id}` — sent immediately on connect so headers flush through the Vite proxy |
| `run_started` | 0 | `{task_id, condition, tau, model, policy_hash, replay, lanes:["baseline","moat"]}` |
| `node_entered` | hop | `{node}` — node ∈ `{ingest, draft_query, moat_guard, passthrough, web_search, skip_hop, reflect, generate_report}` |
| `hop_started` | hop | `{hop, subgoal}` |
| `query_drafted` | hop | `{draft, latency_ms}` |
| `guard_decision` | hop | `{decision, tier, original, final, diff, leak_types, secrets_touched, reasons, latency_ms, block_kind, rechecked}` |
| `secret_interval_update` | hop | `{secret_id, dim, unit, prior:[lo,hi], before:[lo,hi], after:[lo,hi], width_ratio, bits, status, committed, basis, decision_seq}` |
| `profile_update` | hop | `{revealed:{attr:term}, k, k_min, generalized_from?, committed, status}` |
| `search_results` | hop | `{query, corpus, corpus_sha, hits:[{id,title,score}]}` |
| `hop_skipped` | hop | `{reason, detail}` |
| `injection_detected` | hop | `{doc_id, pattern, action}` |
| `observer_estimate` | hop | `{condition, attr, point, p10, p90}` |
| `report_chunk` | hop | `{text}` |
| `run_complete` | hop | `{status, hops, sent, rewritten, blocked, trace_url}` |
| `error` | hop | `{where, message, fail_closed:true, dispatched}` |
| `replay_meta` | 0 | `{recorded_at, source_run_id, speed}` |

### Semantics that Sebika's renderer depends on

- **`guard_decision`**
  - `decision ∈ {pass, rewrite, block}`. `pass` ⇒ `final == original`, `diff == []`,
    `leak_types == []`. `rewrite` ⇒ `final != original`, `diff != []`. `block` ⇒ `final` is
    `null` and nothing commits for this decision.
  - **The baseline lane always emits a `pass`** `guard_decision` (`tier:0`), so both lanes
    render symmetric cards from one renderer.
  - `diff` is `[{op, text}]`, `op ∈ {equal, insert, delete}`, `text` is the word-run
    (word-level `difflib`, computed once server-side). A replace renders as a `delete` run
    followed by an `insert` run.
  - `tier == 2` ⇒ `rechecked == true`.
- **`secret_interval_update`** (the leak meter). Two-phase per secret per hop:
  - `moat_guard` emits **previews** (`committed:false`). `web_search` emits `committed:true`
    **at dispatch**, only for `basis:"final"` intervals (the query that actually left).
  - A blocked/skipped query, and any narrowing present in `original` but not in `final`, stays
    as a permanent `committed:false`, `status:"prevented"` record. Earlier attempts' prevented
    previews are never overwritten.
  - `decision_seq` is the `seq` of the originating `guard_decision` — use it to match a commit
    to its preview (redrafts reuse the same `(run_id, hop, secret_id)`).
  - `status ∈ {safe, warning, prevented}`.
- **`profile_update`** (the attacker panel, k-anonymity). Proposal/commit like intervals:
  `moat_guard` emits the proposal (`committed:false`); `web_search` emits `committed:true` at
  dispatch for what went out; a skipped hop keeps it `status:"prevented"`. **The UI renders
  committed cumulative exposure only** — a prevented proposal must never show as revealed.
  A k-anonymity skip is conveyed by `hop_skipped{reason:"k_anonymity_unreachable"}`.
- **`injection_detected`** — `action ∈ {ignored, blocked}`. `ignored` = the injected
  instruction was neutralized and the hop **continues**; `blocked` = the whole doc was dropped.
- **`run_complete`** — **per-lane** counts. `sent` = queries dispatched across the boundary
  (pass + rewrite, including ones whose search later errored); `rewritten ⊆ sent`;
  `blocked` = queries/hops that never dispatched (pre-dispatch block + skipped hop).
  `status ∈ {completed, completed_with_skips, failed}`; `trace_url` nullable
  (`LANGSMITH_TRACING=false` by default).
- **`error`** — `where ∈ {guard, search, model, graph}`; `fail_closed` is always `true`.
  `where != "graph"` is hop-level → the UI renders a BLOCKED card unless `dispatched:true`
  (then the query was sent and the card shows an error, not a block). `where == "graph"` ends
  the run.
- **`observer_estimate`** — present in recorded/replay runs, optional in live.

### Vocabularies (frozen; extend only via a contract change)

- `LeakType`: `exact_value, unit_change, range, split, derived, relative, encoding,
  process_fingerprint, identifier, intent_profile`.
- `node`: `ingest, draft_query, moat_guard, passthrough, web_search, skip_hop, reflect,
  generate_report`.
- `run_complete.status`: `completed, completed_with_skips, failed`.
- `hop_skipped.reason`: `redrafts_exhausted, k_anonymity_unreachable, guard_exception`.
- `error.where`: `guard, search, model, graph`.
- `diff.op`: `equal, insert, delete`.
- interval/profile `status`: `safe, warning, prevented`.
- interval `basis`: `original, final`.

---

## 4. Open items (confirm before/at PR review)

- `Policy` internals + the `tau`↔policy relationship — **Hyeseong**.
- `SentQuery` shape — Hyeseong + Atharva.
- Interval `dim`/`unit` token set (e.g. `energy_density`/`Wh/kg`, `temperature`/`degC`) —
  Atharva + Hyeseong; currently free strings.
- `observer_estimate` live emission (eval-recorded for replay is the committed behavior) —
  Hyeseong.

Machine-checkable copy of this contract: `agent/events.py` (Pydantic v2 models + a cross-field
self-check that validates the fixtures in `runs/fixtures/`).
