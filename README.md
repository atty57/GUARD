# MOAT Guard

A privacy guard that sits at the **trust boundary** between a deep-research agent and web search. The agent reads internal battery-R&D docs and drafts search queries; MOAT Guard checks each query against the documents' secrets **and the full history of what's already been sent**, then **passes, rewrites, or blocks** it. A blocked query never reaches search.

It stops two things a "do not disclose" prompt can't:

- **direct leaks** — a secret value appears in a query;
- **mosaic leaks** — individually-safe queries that together narrow a secret or fingerprint a product.

Threat model: the **search provider is the adversary** and sees only the queries; the LLM provider is trusted.

> ⚠️ **Work in progress** — built for the Meta Global AI Developer Hackathon (Oct 26 – Nov 4, 2026). **Local-only: no hosting, no deployment.** Interfaces are frozen (`moat.v2`); the pipeline, guard, data, and UI are under active development.

## Team

| Role | Person | Owns |
|---|---|---|
| AI Agent Engineer | Atharva | Agent graph, model client, patent corpus + search, data generation, experiment runner |
| Security & Evaluation Lead | Hyeseong | Threat model, `guard.check()`, disclosure ledger, data spec, attacker/observer + scoring |
| Full-Stack Engineer | Sebika | FastAPI + SSE backend, React app, replay UI |

## Layout

```text
agent/   graph, model client, search, event models   (Atharva)
guard/   check, ledger, policy, tests                 (Hyeseong)
eval/    experiment runner, observer, scoring         (Atharva + Hyeseong)
data/    synthetic doc + secret generation
corpus/  USPTO patent corpus build (SQLite FTS5)
api/     FastAPI + SSE                                 (Sebika)
web/     Vite + React + TS + Tailwind                 (Sebika)
runs/    recorded runs + fixtures (replay)
docs/    PLAN, INTERFACES, DATA_SPEC
```

## Docs

- [docs/PLAN.md](docs/PLAN.md) — shared project plan (what, how, timeline, risks)
- [docs/INTERFACES.md](docs/INTERFACES.md) — frozen `moat.v2` contract: guard hook, `search()`, event stream
- [docs/DATA_SPEC.md](docs/DATA_SPEC.md) — dataset spec (secrets, splits, policy profile)
- [Project board](https://github.com/atty57/GUARD/projects) — tasks and milestones

## Conventions

- One branch per issue (`atharva/12-llm-client`), PR into `main`, `Closes #N` in the PR body.
- Changes to `docs/INTERFACES.md` or `agent/events.py` need a teammate's approval.
- No `.env` in git. All secrets in the repo are synthetic.

## Quickstart

_TODO: setup and run instructions once the pipeline lands._
