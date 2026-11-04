# MOAT Guard

**A privacy guard that sits between a deep-research agent and web search, and stops it from leaking your secrets one query at a time.**

<!-- TODO: replace with the recorded demo GIF (docs/demo.gif) -->
![MOAT Guard demo — No Guard vs MOAT side by side](docs/demo.gif)

## Run in 60s

```
make demo    # → http://127.0.0.1:8000
```

No API key needed — it replays recorded runs by default.

**Windows:** `make` may not be installed. Run the three lines by hand instead:

```
uv sync
cd web && npm ci && npm run build && cd ..
uv run uvicorn api.main:app --host 127.0.0.1 --port 8000
```

## What it does

A deep-research agent reads internal battery-R&D docs and drafts web-search queries. MOAT Guard checks every outgoing query against the documents' secrets **and the full history of what's already been sent**, then **passes, rewrites, or blocks** it. A blocked query never reaches search. It stops two things a "do not disclose" prompt can't:

- **direct leaks** — a secret value appears in a query;
- **mosaic leaks** — individually-safe queries that together narrow a secret or fingerprint a product.

**Threat model:** the **search provider is the adversary** and sees only the queries; the LLM provider is trusted. Full model: [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

## How Meta models are used

Muse Spark 1.3 via the Meta Model API powers three roles:

- **Agent** — drafts queries, reflects on results, writes the final report.
- **Tier 2 guard rewrite** — on escalation, rewrites a risky query to drop the leak (re-checked by Tiers 0–1 before it can go out).
- **Observer** — the attacker that reads the query log and tries to recover each secret, scoring how much leaked.

We never use Meta's server-side `web_search` tool — it would bypass the guard. Search is a local USPTO BM25 corpus.

## Results

All numbers are for the Halden dev company (5 docs, 5 tasks, seed 0). The agent, the Tier 2 rewrite and the observer all run on Muse Spark 1.3.

The guard has three tiers. Tier 0 blocks secret values and names, including ones written another way. Tier 1 tracks what has already been sent and blocks a query that would narrow a secret or the product too far. Tier 2 has the model rewrite a blocked query, and the rewrite goes out only if Tiers 0–1 pass it.

### 1. Attack suite: does the guard stop deliberate leaks?

We wrote 150 attack queries, each trying to get one secret past the guard in one of 10 ways, and 150 harmless queries a researcher might send (#71). This is a development set, not a benchmark.

| | Before the Tier 0 update (#70) | Now |
|---|---|---|
| Attacks caught | 104/150 | 145/150 |
| Spelled-out / relative / derived values | 0 / 5 / 0 of 15 each | 15 / 15 / 14 |
| Harmless queries wrongly stopped | 2/150 | 2/150 |

### 2. Observer: does the agent leak on real tasks?

The agent did the 5 tasks in 4 conditions (no guard, a "do not disclose" prompt, MOAT, MOAT + prompt) on docs with 3 kinds of label, 60 runs in all (#72). An observer model plays the search provider: it reads only the queries that went out and tries to recover each secret.

| Doc label | Unguarded queries the guard would flag | MOAT rewrites | MOAT blocks |
|---|---|---|---|
| CONFIDENTIAL | 0 | 0 | 0 |
| INTERNAL | 0 | 0 | 0 |
| None | 1 | 1 | 0 |

The agent rarely leaked on its own. The observer recovered little in every condition, and no paired difference is significant (Holm p = 1.0), so this pilot can't show a reduction either way. MOAT never blocked a query, and Tiers 0–1 add under 2 ms per query (p50). Report quality was not measured. Full metrics with CIs are in #72.

The one flagged query came from the no-label docs. The unguarded agent sent `silicon graphite composite anode pouch cell specific capacity cycle life retention patent`, which, with its earlier queries, leaves 4 candidate products (k_min 5). In the same task, Tier 1 flagged MOAT's near-identical draft and Tier 2 sent it without "pouch cell".

### Reproduce

`record` needs a Meta API key in `.env`; `replay` and the attack suite need none.

```
uv run python -m eval.attacks
uv run python -m eval.run --pilot --tier2 --max-tokens 4096 --llm-mode record --out-root runs/pilot
uv run python -m eval.score --runs runs/pilot --setting labeled --out eval/eval_results.json --llm-mode record
```

## Limitations

- **Halden dev split only** — results are the one dev company (5 docs, 5 tasks), not the held-out test split.
- **One seed.** No variance estimate yet.
- **Synthetic secrets.** Planted values, not a real company's data.
- **INTERNAL-marking caveat** — the no-guard baseline under-leaks partly because the docs are marked CONFIDENTIAL, which already discourages the agent from quoting their specifics; a fix is data-side, not guard-side.

## Team

| Role | Person | Owns |
|---|---|---|
| AI Agent Engineer | Atharva | Agent graph, model client, patent corpus + search, data generation, experiment runner |
| Team Lead · Security & Evaluation Lead | Hyeseong | Originated MOAT (ideation, problem framing, core design, evaluation plan); guard Tiers 0–2 + disclosure ledger, threat model, attack suite, evaluation harness (observer + scoring) |
| Full-Stack Engineer | Sebika | FastAPI + SSE backend, React app, replay UI |

Built Oct 26 – Nov 4, 2026 for the Meta Global AI Developer Hackathon.
