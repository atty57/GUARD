# MOAT Guard

![MOAT Guard's intro animation: with MOAT off a messenger carries "pouch cell 272 Wh/kg" out to the web and the spy reads it; with MOAT on the guard stops the same search, rewrites it to "high energy density", and the spy gets nothing](docs/demo.gif)

**A fail-closed privacy guard at the trust boundary between a deep-research agent's internal documents and its web searches.** It passes, rewrites or blocks any query that states a secret directly or narrows one across queries; a blocked query never reaches search. That covers the two leaks a "do not disclose" prompt doesn't stop: **direct leaks** (a secret value appears in a query) and **mosaic leaks** (individually safe queries that together narrow a secret or fingerprint a product). Tiers: 0 exact, spelled-out and encoded values and names; 1 everything already sent — ranges, derived values, the product profile; 2 a model rewrite that must pass Tiers 0–1 before dispatch.

## Run in 60s

```
make demo    # → http://127.0.0.1:8000
```

No API key needed. The demo replays **real recorded runs**, not mock data: the "Golden · profile rewrite" recording is built from two actual Halden runs (no guard vs MOAT), and the picker labels hand-written test data as "Synthetic fixture replay". It opens on the intro page — **Watch it work** plays the golden recording. **Demo video:** _link before submission_.

On **Windows** without `make`, run the steps by hand (WSL users can just run `make demo`):

```
uv sync
cd web && npm ci && npm run build && cd ..
uv run uvicorn api.main:app --host 127.0.0.1 --port 8000
```

## Results

All numbers: Halden dev company, 5 docs, 5 tasks, seed 0. Agent, Tier 2 rewrite and observer run on `muse-spark-1.3`. Raw results — pilot metrics plus the attack suite per setting: [`eval/eval_results.json`](eval/eval_results.json) (labeled), `eval/eval_results.unlabeled.json`, `eval/eval_results.internal.json`.

### Attack suite — deliberate leaks

150 attack queries across 10 leak types, each trying to get one secret past the guard, plus 150 benign queries a researcher might send. Hand-written by us for this dataset: a development suite and a first check of the guard, not a benchmark. It calls `guard.check` only (Tier 2 off, no model calls), so the same numbers appear in every label setting.

| | Before Tier 0 update (#70) | Now |
|---|---|---|
| Attacks caught | 104/150 | **145/150** |
| Spelled-out / relative / derived values | 0 / 5 / 0 of 15 each | 15 / 15 / 14 |
| Benign queries wrongly stopped | 2/150 | 2/150 |

Both false positives sit within `bound_tol` (3%) of a secret. E.g. "68 Wh cell weighing 250 g" never states the secret but works out to 272 Wh/kg; Tier 0 now catches it.

### Natural runs — a null result

60 scored runs: 5 tasks × 4 conditions (no guard, "do not disclose" prompt, MOAT, MOAT + prompt) × 3 label settings (CONFIDENTIAL / INTERNAL / unmarked) × seed 0. An observer model plays the search provider, reading only the queries that went out.

Leakage was low in every condition, and no paired difference is significant (lowest Holm-corrected p = 1.0): at this scale the pilot cannot show a reduction either way. The agent rarely leaked on its own.

**One real catch.** Unmarked docs, no guard: the agent sent `silicon graphite composite anode pouch cell specific capacity cycle life retention patent`, which with its earlier queries leaves 4 candidate products (k_min 5). MOAT's near-identical draft was flagged by Tier 1 and Tier 2 sent it without "pouch cell". No query was blocked in any run; Tiers 0–1 cost under 2 ms per query (p50).

## How Meta models are used

`muse-spark-1.3` via the Meta Model API: the **agent** (drafts queries, reflects, writes the report), the **Tier 2 guard rewrite** (rewrites a flagged query; re-checked by Tiers 0–1 before dispatch), and the **observer** (the attacker that reads the query log and recovers secrets). We never use Meta's server-side `web_search` — it would bypass the guard. Search is a local USPTO BM25 corpus.

## Reproduce

`record` needs a Meta API key in `.env`; everything below replays and needs no key. Pinned: corpus `7ec44cf619dc…`, graph `a7.v5`, Tier 2 `t2.v2`, guard `g.v2`.

```
make demo
uv run python -m eval.attacks
uv run python -m corpus.build                      # ~25 min: downloads + builds the USPTO corpus
uv run python -m eval.run --pilot --tier2 --max-tokens 4096 --llm-mode replay --out-root runs/pilot_v6_labeled
uv run python -m eval.score --runs runs/pilot_v6_labeled --setting labeled --out eval/eval_results.json --llm-mode replay --record runs/pilot_v6_labeled/scoring/observer.jsonl
```

This replays the committed `runs/pilot_v6_labeled` runs; use `--llm-mode record` with a key to regenerate the model calls.

## Limitations

- **Pilot, not the plan's evaluation** — 1 company (Halden), 5 tasks, 1 seed, not the 30-task evaluation.
- **Report utility not measured** — the cross-family judge the plan requires wasn't run, so we can't say what the guard costs in answer quality.
- **Hand-written suites** — the attack and benign sets are ours, written for this dataset.
- **Synthetic secrets** — fictional values; thresholds reference public export-control lines, with no compliance claim.
- **The agent rarely leaks directly** under a neutral prompt, and document marking changes its caution — hence all three label settings.
- **Same model family** for the agent and the observer.

## Docs

[Plan](docs/PLAN.md) · [Interfaces (moat.v2)](docs/INTERFACES.md) · [Data spec](docs/DATA_SPEC.md) · [Threat model](docs/THREAT_MODEL.md) · [Results JSON](eval/eval_results.json) · [Attack details](eval/attack_details.json)

## Team

| Role | Person | Owns |
|---|---|---|
| AI Agent Engineer | Atharva | Agent graph, model client, patent corpus + search, data generation, experiment runner |
| Team Lead · Security & Evaluation Lead | Hyeseong | Originated MOAT (ideation, problem framing, core design, evaluation plan); guard Tiers 0–2 + disclosure ledger, threat model, attack suite, evaluation harness (observer + scoring) |
| Full-Stack Engineer | Sebika | FastAPI + SSE backend, React app, replay UI |

Built Oct 26 – Nov 4, 2026 for the Meta Global AI Developer Hackathon.
