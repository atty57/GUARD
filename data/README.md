# Halden development dataset (A5 v1)

This is the first development company, not the final six-company benchmark. The five
committed internal documents use deterministic canned prose (`--llm fake`). They are
synthetic development data, not recordings of a live model generation.

The script substitutes S1–S10 from `policy.example.yaml`; the model receives placeholders,
not those values. This existing example file is Halden's canonical policy. The manifest
is evaluation ground truth and must never be loaded into the research graph.

Build the real patent corpus and generate the development artifacts:

```powershell
uv run python -m corpus.build
uv run python -m data.generate --company halden --llm fake
uv run pytest -q
```

For model-written prose, use `--llm real` (the default) with `LLM_MODE=record` and a Meta
API key. Subsequent `LLM_MODE=replay` runs use the same recorded requests without a key.
Retries include validation feedback; the entire generation shares one async event loop.

Gold labels are deliberately **pending**: `gold_candidates.halden.json` lists retrieval
candidates for human relevance review, while `tasks.jsonl` contains empty `gold_patents`.
BM25 rank alone does not establish relevance. The corpus may need expansion to support
the market and competitor questions. Reviewed labels survive regeneration; changed task
definitions with existing labels require review before the script overwrites them.

Hyeseong still needs to validate answerability (DATA_SPEC check 4), at least three relevant
gold patents per task (check 5), and the zero-query observer prior (check 8). Halden's
values remain provisional until that prior check passes. The other development company,
four held-out companies, and realism documents are later A5 work; held-out artifacts must
live under the gitignored `data/heldout/` directory.

The poisoned page contains one instruction and is explicitly registered as synthetic.
Search returns its complete short text so the instruction reaches the model, while
ordinary patents retain their compact snippets. Poisoned pages never count as gold.

The corpus manifest records `base_corpus_sha` (the frozen real ID set), `overlay_sha`
(all planted row contents), and `corpus_sha` (the effective combined identity emitted
in search events). `ids`, `doc_count`, and `counts` describe the real patent snapshot;
`planted` lists the additional synthetic pages. `python -m corpus.build` restores all
committed poisoned pages after rebuilding. To restore Halden alone without generation:

```powershell
uv run python -m data.generate --company halden --replant
```

`CORPUS_DB` selects the database for both planting and retrieval. Its sibling
`manifest.json` is used by default; set `CORPUS_MANIFEST` for a differently named manifest.
The script checks their base identities before writing dataset outputs. Tests use small
temporary databases and never plant pages into the working corpus. Downloads and all
`.db` files remain uncommitted.
