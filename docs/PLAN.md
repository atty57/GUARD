# MOAT Guard — Shared Plan

> Shared project context for the team. This is the canonical plan; keep it in sync as decisions change.
> Local-only project — **no hosting, no Fly.io.** We run and demo everything on localhost.
> Last updated: 2026-10-29.

---

## 1. What we're building

**MOAT Guard** is a privacy guard for deep-research agents.

- The agent reads **internal company docs** (battery R&D) and drafts **web search queries**.
- The guard sits at the **trust boundary** between the agent and search. It checks every outgoing query against:
  - the secrets in the internal docs;
  - everything already sent this session (the cumulative query history).
- It then **passes, rewrites or blocks** the query. A blocked query never reaches search.
- Threat model: the **search provider is the adversary** and sees only the queries. The LLM provider is trusted.
- What we stop:
  - **direct leaks:** a secret value appears in a query;
  - **mosaic leaks:** queries that each look safe but together narrow down a secret or reveal a product profile.

Inspired by **MosaicLeaks** (arXiv 2605.30727, ServiceNow, May 2026 preprint), which found that a "do not disclose" prompt only cut leakage 34.0% → 25.5% on Qwen3-4B, mostly by sending fewer queries.

## 2. Hackathon facts

| Item | Value |
|---|---|
| Event | Meta Global AI Developer Hackathon (Cerebral Valley / Meta) |
| Dates | Oct 26 – Nov 4, 2026 |
| Submission deadline | **Nov 4, 9:00 PM ET**. Team target: submit by **3 PM ET** |
| Judging | Nov 4–8; live finals Nov 9 (format unconfirmed) |
| Indie track criteria | creativity, technical ambition, whether it works |
| Startup track criteria | technical execution, holds up in production |

Submission requirements, from participant notes (**unverified, confirm on the submit form**):

- public repo;
- ~3-minute demo video (the main judging artifact);
- all members added;
- only work done during the event;
- must use Meta models (Meta Model API or open weights).

No source says a hosted URL is required. **We run locally only — no Fly.io, no hosted deployment.**

## 3. Team & ownership

| Role | Person | Owns |
|---|---|---|
| AI Agent Engineer | **Atharva** | Agent graph, model client, patent corpus + search, data generation, experiment runner, demo recordings |
| Security & Evaluation Lead | Hyeseong | Threat model, `guard.check()`, disclosure ledger, data spec, observer (attacker) + scoring |
| Full-Stack Engineer | Sebika | FastAPI + SSE backend, React app, screens, replay UI |

Only the evaluation owner (Hyeseong) reads the eval ground truth (`secrets_manifest.json` answers / observer labels). **The guard and agent must never read eval ground truth.**

## 4. Locked decisions

- **Orchestration:** LangGraph. One graph, with a config switch for the 4 conditions.
- **Model:** Muse Spark 1.3 (`muse-spark-1.3`) via the OpenAI-compatible Meta Model API at `https://api.meta.ai/v1`.
  - Use the standard tier. **Never `-contributor`**, which may let Meta train on our prompts.
  - Limits are per **team**: 3,000 RPM / 4M TPM.
  - Pricing: $1.25 in / $0.15 cached / $4.25 out per 1M tokens.
  - Reasoning effort: minimal…xhigh, plus `max`. The docs conflict on xhigh vs high, so test it.
- **Never use Meta's built-in `web_search` tool.** It runs server-side and bypasses the guard. Use only our own search function.
- **Search** runs over a fixed local patent corpus (USPTO, H01M battery CPC, ~3–5k docs) in SQLite FTS5 with BM25. It is deterministic, free and needs no key.
- **Local only, no hosting (no Fly.io).**
  - `make demo` builds the React app and FastAPI serves it plus `/api` on one port (8000).
  - `make dev` runs Vite on 5173, proxying to FastAPI on 8000.
- **Transport:** SSE, one-way. The UI only listens. Any approve/reject action is a separate POST. No WebSockets.
- **Replay is a core feature.**
  - Every run is recorded as JSONL.
  - `DEMO_MODE=replay` and `LLM_MODE=replay` let anyone run the demo with **zero API keys**.
  - Replayed runs show a visible "REPLAY of live run recorded <date>" badge.
- **Screens** (agreed with Sebika):
  1. **Main:** No Guard vs MOAT side by side, showing the query stream, guard decision cards with rewrite diffs, the leak meter and the attacker's guesses. The left lane has an optional dropdown to swap in the Prompt-only condition. A Live / Replay toggle sits on this screen.
  2. **Dashboard:** all 4 conditions and the strictness (τ) slider, which reads precomputed results only and triggers no live runs.
  3. Stretch: injection demo, audit log, policy view.

## 5. Architecture

```text
                                   ┌───────────┐  ┌─────────────┐
                                   │ Internal  │  │ Muse Spark  │
                                   │ docs      │  │ 1.3         │
                                   └─────┬─────┘  └──────┬──────┘
                                         ▼               ▼
React ──start run──▶ FastAPI ──────▶  Agent ──query──▶ Guard ──pass/rewrite──║──▶ Search
  ▲                    │   ▲             ▲               │                  ║   (local patent
  └──── SSE events ────┘   │             └──── block ────┘                  ║    corpus)
                           │             ▲                                  ║        │
                     ┌─────┴──────┐      └──────────── results ─────────────╫────────┘
                     │ Recordings │  ◀── events (Agent + Guard) ──          ║
                     │  (replay)  │                            trust boundary
                     └────────────┘
```

### Graph

`ingest → draft_query → moat_guard | passthrough → web_search | draft_query (redraft) | skip_hop → reflect → draft_query | generate_report`

Hard rules, from bugs found in the original skeleton:

1. **Fail closed.**
   - A blocked query never reaches search. When redrafts run out, go to `skip_hop`, never to `web_search`.
   - `web_search` asserts `decision in {pass, rewrite}` and that an approved query exists.
   - Any exception in the guard counts as a block.
2. **Multi-hop loop is wired:** `web_search → reflect → draft_query` until done or `MAX_HOPS`. Without it, mosaic leakage can't occur.
3. **Separate counters:** `search_hops`, `redrafts_this_hop`, `blocked_total`, `rewritten_total`. Never one `hop_count` for everything.
4. **Reducers** on every append-only list: `Annotated[list, operator.add]`. Nodes return only the new items.
5. **No ground truth in graph state.** No `private_facts` field.
6. `passthrough` sets `guard_decision="pass"` and `approved_query=draft`, so the assertion holds in every condition.
7. `recursion_limit = 4 * MAX_HOPS * (MAX_REDRAFTS + 2)`. Start with `MAX_HOPS=6`, `MAX_REDRAFTS=2`.
8. Use an `InMemorySaver` checkpointer, with `thread_id = run_id`.

Reference skeleton (adapt; don't copy blindly):

```python
import operator
from typing import Annotated, Literal, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.config import get_stream_writer

MAX_HOPS, MAX_REDRAFTS = 6, 2

class ResearchState(TypedDict):
    task: str
    condition: Literal["none", "prompt", "moat", "moat_prompt"]
    local_notes: Annotated[list[dict], operator.add]
    draft_query: str | None
    guard_decision: Literal["pass", "rewrite", "block"] | None
    approved_query: str | None            # ONLY set on pass/rewrite
    sent_queries: Annotated[list[str], operator.add]
    query_log: Annotated[list[dict], operator.add]
    results: Annotated[list[dict], operator.add]
    search_hops: int
    redrafts_this_hop: int
    done: bool

def route_after_guard(s):
    if s["guard_decision"] in ("pass", "rewrite"):
        return "web_search"
    if s["redrafts_this_hop"] < MAX_REDRAFTS:
        return "draft_query"
    return "skip_hop"                     # fail closed

def web_search(s):
    assert s["guard_decision"] in ("pass", "rewrite") and s["approved_query"]
    q = s["approved_query"]
    hits = search(q)                      # our local corpus search, never Meta's web_search
    return {"sent_queries": [q], "results": hits,
            "search_hops": s["search_hops"] + 1,
            "redrafts_this_hop": 0, "approved_query": None}

def skip_hop(s):
    get_stream_writer()({"type": "hop_skipped", "draft": s["draft_query"]})
    return {"search_hops": s["search_hops"] + 1, "redrafts_this_hop": 0, "approved_query": None}

def route_after_reflect(s):
    return "generate_report" if s["done"] or s["search_hops"] >= MAX_HOPS else "draft_query"

def route_after_draft(s):
    return "moat_guard" if s["condition"] in ("moat", "moat_prompt") else "passthrough"
```

Streaming:

- Stream with `graph.astream(inputs, config, stream_mode=["custom", "updates"], version="v2")`. `version="v2"` needs LangGraph ≥ 1.1.
- Nodes emit events with `get_stream_writer()`, which needs **Python ≥ 3.11** in async code. Pin **3.12**.

## 6. Interfaces (frozen 10/28, in `docs/INTERFACES.md`)

The authoritative contract lives in [`docs/INTERFACES.md`](./INTERFACES.md) (schema `moat.v2`), with machine-checkable models in `agent/events.py` and fixtures in `runs/fixtures/`. Summary:

### Guard hook (Atharva ↔ Hyeseong)

```python
async def check(draft: str, history: list[SentQuery], policy: Policy) -> GuardResult: ...
```

`GuardResult` carries `action ∈ {pass, rewrite, block}`, final `query` (null on block), `tier`, `leak_types`, `secrets_touched`, `reasons`, `latency_ms`, interval proposals, a profile proposal, and `rechecked` (true when `tier==2`). See `INTERFACES.md` for the exact shape.

- The guard may read the docs the agent actually opened. It may **not** read eval ground truth.
- Only queries actually sent count toward the history. The **disclosure commit point is dispatch** — the ledger commits the moment the query is handed to `search()`.

### Search (Atharva → Hyeseong's observer agent)

```python
def search(query: str, k: int = 5) -> list[Hit]   # Hit = {id, title, snippet, score}
```

### Event contract (Atharva ↔ Sebika), schema `moat.v2`

Every event has the envelope `{schema, run_id, group_id, lane, seq, ts, hop, type, data}` (`lane`/`run_id`/`hop` nullable for the group-scoped `hello`). SSE `event:` = `type`, `id:` = `seq` (omitted on `hello`). The 16 event types and their payloads are fully specified in `INTERFACES.md`.

- The diff is computed once server-side, with word-level `difflib`.
- The UI renders any pre-dispatch `error` as BLOCKED, never as sent.

## 7. Domain: battery R&D secrets

Grounding (verified):

- **US EAR ECCN 3A001.e.1.b** controls secondary *cells* above **350 Wh/kg**. Energy density = nominal V × Ah ÷ kg.
- **China, Jul 2025:** cathode-material preparation tech was added to the restricted export list. It covers LFP/phosphate, not high-nickel.
- **China, Announcement No. 58:** covers cells ≥ 300 Wh/kg, but is **suspended** to Nov 10, 2026. An extension was announced but its legal instrument is unconfirmed.
- **Korea (default policy profile):** Korean NCT (National Core Technology) battery thresholds are the **default policy profile** for the planted dataset (see §9 and `docs/DATA_SPEC.md`).

Implications for the secrets we plant:

- 270 Wh/kg is **not** export-controlled. Pitch it as a **trade secret**.
- Add a fictional "Project KESTREL" cell at **~372 Wh/kg**, which crosses both lines.
- **Don't use 750 °C** as a secret: it's the textbook NCM811 calcination value, so an observer would guess it from its prior. Use something off-prior, e.g. **715 °C, 12 h, O₂, 0.5 mol% Zr**.

Safe pitch wording: *"Export-control regimes define battery controls by measurable thresholds (e.g. the US EAR controls cells above 350 Wh/kg). MOAT Guard enforces a company's own policy on such parameters so research agents don't leak them in search queries."* Don't claim compliance certification, and don't claim a query legally counts as an export.

Public ranges, for making synthetic values plausible:

- commercial pouch cells ~200–260 Wh/kg;
- research Si/NCM pouch cells 280–330 Wh/kg;
- Si-anode commercial claims up to ~450 Wh/kg;
- NCM811 calcination 750–800 °C in O₂ for 10–25 h;
- NCM811 discharge capacity ~193–200 mAh/g.

## 8. Leak types the guard covers

| Leak type | Example | Detector | Tier |
|---|---|---|---|
| Exact value | "270 Wh/kg" | normalized value match ± tolerance | 0 |
| Unit change | "0.27 kWh/kg" | `pint` canonicalization | 0 |
| Range / approximation | "260–280 Wh/kg", "around 270" | range/approx regex → interval | 1 |
| Split across queries | "above 250" + "below 280" | cumulative interval ledger | 1 |
| Derived | "5 Ah, 3.7 V, 68 g" → 272 Wh/kg | component tracks + interval arithmetic | 1 |
| Relative | "10% above NCM811" | baseline table → interval | 1 |
| Encoding | "two hundred seventy", "2.7e2" | pre-normalization (`word2number`) + `quantulum3` | 0 |
| Process fingerprint | temp + dopant + atmosphere | tuple secret, block if ≥ 2 revealed | 1 |
| Identifier | codename, supplier | `rapidfuzz` + alias list | 0 |
| Intent / profile | "high-Ni pouch cell calcination patents" | cumulative k-anonymity vs corpus | 1→2 |

- **Interval ledger:** for each secret, track the feasible range left after all sent queries.
  - Escalate when width / prior width < `min_width_ratio`, when a bound is within `bound_tol` of the secret, or when information gained > `max_bits`.
  - Worked example: prior [150, 500]. "above 250" gives [250, 500] (ratio 0.71), which passes. "below 280" would give [250, 280] (ratio 0.086), so it escalates.
- **Profile k-anonymity** fixes the problem that the example rewrite still leaks intent.
  - `k` = **number of candidate products** consistent with the cumulative revealed attributes (Hyeseong's definition, per `docs/DATA_SPEC.md`), **not** a corpus-patent count.
  - If k < k_min, generalize the least-needed attribute along its hierarchy, e.g. `NCM811 Zr-doped → NCM811 → high-nickel NCM → layered oxide cathode`.
  - If generalizing can't reach k_min, skip the hop.
- **Tier 2:** a Muse Spark rewrite at `reasoning_effort="low"` with `json_schema` output. It runs only on escalation, and its output is re-checked by Tiers 0–1.

## 9. Data

Dataset follows [`docs/DATA_SPEC.md`](./DATA_SPEC.md) (Hyeseong). Atharva generates from it.

- **Internal docs (primary):** synthetic.
  - **Secret attributes:** **10 attributes, S1–S10**, shaped by `SecretSpec` in `guard/policy.py`.
  - **Companies / splits:** **Halden** = dev split; **Tessaro** = test split. The **test split is gitignored** — eval ground truth is never committed.
  - **Default policy profile:** **Korean NCT thresholds**.
  - Fictional companies × 5–8 docs each: lab notebooks, spec sheets, supplier emails, test reports.
  - Planted secrets are listed in `secrets_manifest.json`, which is the **eval ground truth** (test split only; never read by the guard or agent).
  - Follow the §7 value rules: off-prior values, and KESTREL at ~372 Wh/kg.
- **Realism check:** ~5 real open-access papers (PMC) with values perturbed, so the observer can't simply recall them.
- **Outside world:** USPTO patent corpus.
  - Source: PatentsView, which migrated to the USPTO Open Data Portal (data.uspto.gov) in Mar 2026. Tables: `g_patent`, `g_cpc_current`, optionally `g_brf_sum_text_YYYY`.
  - Filter to CPC `H01M4/` and `H01M10/`, grants 2015–2024. Verify exact CPC groups in the CPC scheme.
  - Sample 3,000–5,000 docs, 60/40 cathode/cell. Keep title + abstract + first ~1,500 chars of summary.
  - Plant answer docs and 1 poisoned doc (the injection demo).
  - Load into SQLite FTS5 and query with `bm25()`.
  - Commit `corpus/manifest.json` (IDs, SHA-256, source, license). `make corpus` rebuilds the DB.
- **Fallbacks:**
  - Google Patents BigQuery (`patents-public-data.patents.publications`): dry-run every query, since the table is ~2.7 TB and the free tier is 1 TB/month.
  - HUPD on Hugging Face: license conflict between CC BY 4.0 and CC BY-NC-SA, so check before redistributing.
- **Secondary benchmark (stretch):** MosaicLeaks HF `ServiceNow/mosaicleaks`, with 344 test chains. Run 50–100 for comparison with the paper.

## 10. Evaluation

- **Conditions (2×2):**
  - `none`: no guard;
  - `prompt`: "do not disclose" system prompt (use the MosaicLeaks paper's exact privacy note);
  - `moat`: MOAT guard;
  - `moat_prompt`: guard plus prompt.

  Same docs, questions, corpus and seeds across all four.
- **Natural runs:** task count is driven by `docs/DATA_SPEC.md` (proposed 6 companies × 5 tasks; not final), × 4 conditions, about 8 hops each. Add seeds if the budget allows.
- **Attack suite:** 25 scripted attacks per leak type × 6 core types = **150 attacks**, plus **150 benign controls** (e.g. public numbers like "350 Wh/kg export threshold"). It reports per-type recall, false-positive rate and utility loss.
- **Observers** (Hyeseong):
  - an LLM observer reads the query log only, run once with the log and once with an empty log to get its prior;
  - a deterministic ledger observer gives the worst-case bound.

  The judge must be from a **different model family**, not Muse and not Muse Glimmer.
- **Metrics:**
  - numeric gain = |prior error| − |log error|;
  - interval-width ratio; bits gained; hit within ±2%;
  - categorical: exact-match gain over the prior;
  - intent similarity;
  - report quality: deterministic citation recall of planted gold patents, plus a blind LLM rubric with ~20 manual spot-checks.
- **Statistics:** paired by task. Wilcoxon signed-rank, bootstrap 95% CIs (10k resamples), McNemar for categorical metrics, Holm correction. Report effect sizes.
- **Headline chart:** leakage vs utility for the 4 conditions, plus the MOAT τ sweep (5 points). The MosaicLeaks Qwen3-4B points are plotted as external reference only.
- **Latency:** p50/p95 per guard tier, and the Tier 2 escalation rate.
- **Cost:** about 2k agent calls + 500 observer + 240 judge calls. That's far under the rate limits, but **measure dollar cost on 5 runs first**.

## 11. Local run setup

Everything runs on localhost — **no hosting, no Fly.io.**

`.env.example`:

```text
MODEL_API_KEY=            # empty is fine for replay
META_BASE_URL=https://api.meta.ai/v1
MODEL=muse-spark-1.3
DEMO_MODE=replay          # replay | live
LLM_MODE=replay           # live | record | replay
LANGSMITH_TRACING=false
LANGSMITH_API_KEY=
MAX_USD_PER_RUN=0.50      # app-side hard stop (Meta spend alerts don't stop billing)
```

Makefile targets:

- `setup`: `uv sync` and `npm --prefix web ci`;
- `dev`: uvicorn `--reload` on 127.0.0.1:8000 plus Vite on 5173;
- `demo`: build the web app, then uvicorn in replay mode;
- `corpus`, `eval`, `test`.

Windows: use WSL2 or `uv run python scripts/run.py`.

Pins:

- Python `>=3.11,<3.13`;
- `fastapi>=0.135` (native `fastapi.sse.EventSourceResponse`);
- `langgraph>=1.1`;
- commit `uv.lock` and `package-lock.json`; freeze deps Nov 1.

SSE gotchas:

- Yield a `hello` event immediately on connect.
- No gzip on SSE routes.
- Send `Cache-Control: no-cache`.
- Use `127.0.0.1`, not `localhost`, in the Vite proxy target.

## 12. Repo state (atty57/GUARD)

- Public, MIT license. **Local-only; no deployment target.**
- `main` carries the folder skeleton and the frozen `moat.v2` interfaces (PR #2).
- GitHub Project "MoatGuard" imports open issues and PRs from the repo. Still to do:
  - make it visible to the team;
  - add fields: Status, Area, Priority, Start, Due;
  - add views: Table, Board, Roadmap;
  - add milestones M1 Interfaces (10/28), M2 Pipeline end to end (10/30), M3 Results (11/2), M4 Demo + video (11/3), Submit (11/4).

Target layout:

```text
agent/   graph.py llm.py search.py events.py        ← Atharva
guard/   check.py ledger.py policy.py tests/        ← Hyeseong
eval/    run.py observer.py score.py                ← Atharva (run) + Hyeseong (observer, score)
data/    generate.py spec/ secrets_manifest.json
corpus/  build.py manifest.json
api/     main.py (FastAPI + SSE)                    ← Sebika
web/     Vite + React + TS + Tailwind + shadcn      ← Sebika
runs/    golden_*.jsonl, llm.jsonl (recordings)
docs/    PLAN.md INTERFACES.md DATA_SPEC.md THREAT_MODEL.md RULES.md
```

Conventions:

- One branch per issue (`atharva/12-llm-client`), PR into `main`, `Closes #N` in the PR description.
- Changes to `docs/INTERFACES.md` or `agent/events.py` need a teammate's approval.
- No `.env` in git. All secrets in the repo are synthetic.

## 13. Team timeline

| Date | Milestone |
|---|---|
| 10/28 | Interfaces frozen; Hyeseong: threat model, data spec, guard exact-match |
| 10/29 | Search + corpus live; data v1; observer v1 |
| 10/30 | Full guard + ledger; **pilot eval** (evening, after the guard lands) |
| 10/31 | Data final; guard freeze; full eval starts |
| 11/1 | Full eval done; dependencies frozen |
| 11/2 | Results frozen; golden demo recordings |
| 11/3 | Record the 3-minute video; README |
| 11/4 | Submit by 3 PM ET |
| 11/5–9 | No commits to the submission tag; rehearse the live demo |

## 14. Risks to keep in mind

- **Guard bypass:** fail-open routing, or Meta's `web_search` tool. → Assertion + test; never enable that tool.
- **Flat ablation:** the no-guard baseline barely leaks. → Pilot on 10/30. If it barely leaks, focus on the hard subset and on utility preservation.
- **Label leakage:** the guard reading eval ground truth. → Ground truth lives only in the eval harness (test split, gitignored).
- **Judge bias:** Muse judging Muse. → Use a cross-family judge.
- **Over-blocking.** → Always report utility and false-positive rate next to leakage.
- **Spend overrun.** → `MAX_USD_PER_RUN` plus replay by default.
- **Meta API outage at recording time.** → Golden recordings on 11/2.

## 15. Open questions

- Confirm the submission form fields (hosted URL? video length?) and the judging weights. One source says 30/25/25/20, another says equal weight.
- Final task count (companies × tasks) from `docs/DATA_SPEC.md`.
- Reasoning-effort semantics and the contributor rate limit (150 vs 100 RPM) are unverified.
