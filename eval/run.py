"""MOAT Guard experiment runner — A7 / issue #13 (HANDOFF §10, Part 2 A7).

Runs the eval matrix **conditions × tasks × seeds** (plus a τ sweep) through the one agent graph
(`agent.graph.stream`) with bounded concurrency, and writes the per-run files Hyeseong's observer /
`score.py` consume:

    runs/<label>/<task_id>_<seed>.jsonl       the full moat.v2 event stream (authoritative)
    runs/<label>/<task_id>_<seed>.meta.json   sidecar: status, cost, sent_queries, report, …

Correctness properties (see the approved plan):
- **Resumable (R3).** The *sidecar* is the done-marker, published atomically after the JSONL. A run
  is skipped only when its sidecar exists, its status is accepted, and its input fingerprint still
  matches — so a crash before the sidecar, a `failed` run, or changed inputs all re-run.
- **Budgets are hard caps (R4).** Each run gets a per-run `max_usd` + `max_tokens`; the global
  `--max-usd-total` reserves a run's budget *before* dispatch and persists committed spend across
  restarts via the sidecars, so concurrency can't overspend and a restart doesn't forget. When the
  cap is hit the runner stops admitting and keeps the finished runs.
- **Immutable per-run recordings (R5).** Each run records/replays from its own
  `runs/recordings/<run_id>.jsonl` (rewritten per attempt, never appended across attempts) — no
  lossy global merge. The accepted recording is named in the sidecar.
- **Faithful sent_queries (R6).** Derived from the approved query of each *dispatched* hop (recovers
  the query even when `search()` raised after dispatch), asserted against `run_complete.sent`.
- **No ground truth.** The runner reads tasks, internal docs and the policy — never
  `secrets_manifest.json` / observer labels. Leakage is scored downstream by Hyeseong.

CLI: ``uv run python -m eval.run --pilot``  (5 Halden × 4 conditions × seed 0).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent import graph as graphmod
from configs import CONDITIONS, make_policy
from guard.policy import policy_hash

_REPO = Path(__file__).resolve().parent.parent
RUNS = _REPO / "runs"
DATA = _REPO / "data"
ACCEPTED = {"completed", "completed_with_skips"}


# --- cells & plans -----------------------------------------------------------------------------
@dataclass(frozen=True)
class Cell:
    condition: str                 # base condition fed to the graph (none/prompt/moat/moat_prompt)
    tau: float | None              # τ-sweep override, or None
    task_id: str
    company: str
    slug: str
    seed: int

    @property
    def label(self) -> str:
        return self.condition if self.tau is None else f"{self.condition}@{self.tau:g}"

    @property
    def run_id(self) -> str:
        return f"{self.label}-{self.task_id}-s{self.seed}"

    def out_path(self, root: Path) -> Path:
        return root / self.label / f"{self.task_id}_{self.seed}.jsonl"

    def meta_path(self, root: Path) -> Path:
        return root / self.label / f"{self.task_id}_{self.seed}.meta.json"


@dataclass
class CellPlan:
    """A cell with its inputs resolved and validated — ready to run or skip."""
    cell: Cell
    question: str
    documents: list[dict]
    policy_path: Path
    policy_tau: float
    policy_hash: str
    model: str
    max_tokens: int | None
    corpus_sha: str
    llm_mode: str
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        payload = {
            "v": graphmod.GRAPH_BEHAVIOR_VERSION,
            "condition": self.cell.condition,
            "tau": self.cell.tau,
            "seed": self.cell.seed,
            "question": self.question,
            # ordered rendered inputs — reorder or truncation change the fingerprint (r2-C)
            "documents": [{"doc_id": d["doc_id"], "text": d["text"]} for d in self.documents],
            "model": self.model,
            "max_tokens": self.max_tokens,
            "policy_hash": self.policy_hash,
            "corpus_sha": self.corpus_sha,
            "llm_mode": self.llm_mode,
        }
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=False)
        self.fingerprint = hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --- task / company / document resolution (R1, R2) ---------------------------------------------
def load_tasks(path: Path, *, split=None, company=None, task=None) -> list[dict]:
    rows = [json.loads(ln) for ln in Path(path).read_text(encoding="utf-8").splitlines()
            if ln.strip()]
    return [r for r in rows
            if (split is None or r.get("split") == split)
            and (company is None or r.get("company") == company)
            and (task is None or r.get("task_id") == task)]


def _slugify(display: str) -> str:
    """Fallback display→slug: first alphanumeric token, lowercased ("Halden Cells" → "halden")."""
    tokens = re.findall(r"[A-Za-z0-9]+", display)
    return tokens[0].lower() if tokens else display.lower()


def resolve_slug(task: dict, companies_map: dict | None) -> str:
    if task.get("slug"):
        return task["slug"]
    disp = task.get("company", "")
    if companies_map and disp in companies_map:
        return companies_map[disp]
    return _slugify(disp)


def load_companies_map(path: Path | None) -> dict | None:
    if path and Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return None


def resolve_documents(task: dict, slug: str, docs_root: Path) -> list[dict]:
    """Load the internal docs named by the task's ``needs_docs`` (fail loud if any is missing)."""
    docs = []
    for doc_id in task.get("needs_docs") or []:
        p = docs_root / slug / f"{doc_id}.md"
        if not p.exists():
            raise FileNotFoundError(f"task {task.get('task_id')}: missing document {p}")
        docs.append({"doc_id": doc_id, "text": p.read_text(encoding="utf-8")})
    return docs


def resolve_policy_path(slug: str, forced: Path | None) -> Path:
    if forced is not None:
        return Path(forced)
    p = DATA / f"policy.{slug}.yaml"
    if p.exists():
        return p
    if slug == "halden":                      # dev default: policy.example.yaml *is* Halden's
        return DATA / "policy.example.yaml"
    raise FileNotFoundError(f"no policy for company slug {slug!r} ({p} absent)")


# --- corpus preflight (R7) ---------------------------------------------------------------------
def corpus_preflight(allow_stub: bool) -> str:
    """Return the corpus sha, or 'stub' with --allow-stub. Refuse to silently use the stub."""
    try:
        from agent.search import corpus_db_exists
        have_db = corpus_db_exists()
    except Exception:
        have_db = False
    if have_db:
        try:
            from agent.search import corpus_meta
            sha = corpus_meta()["sha"]
        except Exception:
            sha = "unknown"
        man = _REPO / "corpus" / "manifest.json"
        if man.exists():
            want = json.loads(man.read_text(encoding="utf-8")).get("corpus_sha")
            if want and sha not in ("unknown", want):
                raise SystemExit(f"corpus sha mismatch: DB {sha} != manifest {want}")
        return sha
    if allow_stub:
        return "stub"
    raise SystemExit("no built corpus DB — run `uv run python -m corpus.build`, "
                     "or pass --allow-stub for keyless/stub runs")


# --- matrix ------------------------------------------------------------------------------------
def build_matrix(conditions, tasks, seeds, taus, companies_map) -> list[Cell]:
    cells = []
    tau_variants = [None] if not taus else list(taus)
    for t in tasks:
        slug = resolve_slug(t, companies_map)
        for cond in conditions:
            # τ only varies the guard lanes; none/prompt have a single (None) variant.
            variants = tau_variants if cond in ("moat", "moat_prompt") else [None]
            for tau in variants:
                for seed in seeds:
                    cells.append(Cell(condition=cond, tau=tau, task_id=t["task_id"],
                                      company=t.get("company", ""), slug=slug, seed=seed))
    return cells


def plan_cells(cells, tasks_by_id, docs_root, forced_policy, *, model, max_tokens, corpus_sha,
               llm_mode, single_company_forced) -> list[CellPlan]:
    """Resolve + validate every cell's inputs *before* any spend (R2)."""
    if single_company_forced and len({c.company for c in cells}) > 1:
        raise SystemExit("--policy forces one policy but the matrix spans multiple companies; "
                         "drop --policy to resolve per-company policies, or filter to one company")
    plans = []
    phash_cache: dict[tuple, tuple] = {}
    for c in cells:
        task = tasks_by_id[c.task_id]
        documents = resolve_documents(task, c.slug, docs_root)
        ppath = resolve_policy_path(c.slug, forced_policy)
        key = (ppath, c.tau)
        if key not in phash_cache:
            pol = make_policy(c.tau, policy_path=ppath)
            phash_cache[key] = (pol.tau, policy_hash(pol))
        tau, phash = phash_cache[key]
        plans.append(CellPlan(cell=c, question=task.get("question", c.task_id),
                              documents=documents, policy_path=ppath, policy_tau=tau,
                              policy_hash=phash, model=model, max_tokens=max_tokens,
                              corpus_sha=corpus_sha, llm_mode=llm_mode))
    return plans


# --- result extraction (R6) --------------------------------------------------------------------
def extract_results(events: list[dict]):
    """(sent_queries, report, run_complete). sent_queries = approved query of each dispatched hop."""
    approved: dict = {}            # hop -> final approved query (from guard_decision / passthrough)
    sent: list = []
    report_parts: list[str] = []
    rc = None
    for e in events:
        t, d, hop = e.get("type"), e.get("data") or {}, e.get("hop")
        if t == "guard_decision" and d.get("decision") in ("pass", "rewrite"):
            approved[hop] = d.get("final")
        elif t == "search_results":
            sent.append(d.get("query"))
        elif t == "error" and d.get("where") == "search" and d.get("dispatched"):
            sent.append(approved.get(hop))     # dispatched then search raised — still adversary-seen
        elif t == "report_chunk":
            report_parts.append(d.get("text", ""))
        elif t == "run_complete":
            rc = d
    return sent, "".join(report_parts), rc


# --- atomic publish (R3) -----------------------------------------------------------------------
def _rel(path: Path) -> str:
    """Repo-relative path when under the repo, else absolute (out-root may be elsewhere)."""
    try:
        return str(path.relative_to(_REPO))
    except ValueError:
        return str(path)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)          # atomic on POSIX and Windows


def read_sidecar(cell: Cell, root: Path) -> dict | None:
    p = cell.meta_path(root)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def is_done(plan: CellPlan, root: Path) -> bool:
    m = read_sidecar(plan.cell, root)
    return bool(m and m.get("status") in ACCEPTED
                and m.get("input_fingerprint") == plan.fingerprint)


# --- running one cell --------------------------------------------------------------------------
def recording_path(cell: Cell, root: Path) -> Path:
    return root / "recordings" / f"{cell.run_id}.jsonl"


def default_client_factory(plan: CellPlan, rec_path: Path, *, per_run_max_usd: float):
    """Build the real LLMClient for this run (one immutable recording file per attempt, R5)."""
    from agent.llm import LLMClient
    mode = plan.llm_mode
    if mode == "record" and rec_path.exists():
        rec_path.unlink()          # fresh attempt — never append onto an interrupted one
    rec_path.parent.mkdir(parents=True, exist_ok=True)
    return LLMClient(mode=mode, model=plan.model, record_path=rec_path,
                     max_usd=per_run_max_usd)


async def run_cell(plan: CellPlan, *, root: Path, client_factory, guard=None, search=None) -> dict:
    """Run one cell to completion and publish its JSONL + sidecar atomically. Returns the sidecar.

    A hard external kill leaves *neither* file (we publish only at the end), so a restart re-runs
    it. A graph-level failure yields a terminal ``run_complete(status="failed")`` here, which the
    sidecar records and resume re-runs (status not accepted).
    """
    c = plan.cell
    rec = recording_path(c, root)
    client = client_factory(plan, rec)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    events = []
    async for evt in graphmod.stream(c.task_id, c.condition, client=client,
                                     policy=make_policy(c.tau, policy_path=plan.policy_path),
                                     run_id=c.run_id, question=plan.question,
                                     documents=plan.documents, seed=c.seed,
                                     max_tokens=plan.max_tokens, guard=guard, search=search):
        events.append(evt)

    lines = [e.model_dump_json(by_alias=True) for e in events]
    dict_events = [json.loads(x) for x in lines]
    sent, report, rc = extract_results(dict_events)
    status = (rc or {}).get("status", "failed")
    if rc is not None and len(sent) != rc.get("sent"):
        print(f"WARN {c.run_id}: sent_queries {len(sent)} != run_complete.sent {rc.get('sent')} "
              f"— marking invalid", file=sys.stderr)
        status = "invalid"        # fail loud; not accepted ⇒ re-runs next invocation

    meta = {
        "run_id": c.run_id, "label": c.label, "condition": c.condition, "tau": c.tau,
        "task_id": c.task_id, "company": c.company, "slug": c.slug, "seed": c.seed,
        "model": plan.model, "policy_hash": plan.policy_hash,
        "input_fingerprint": plan.fingerprint,
        "graph_behavior_version": graphmod.GRAPH_BEHAVIOR_VERSION,
        "recording_file": _rel(rec) if rec.exists() else None,
        "status": status, "llm_mode": plan.llm_mode, "corpus_sha": plan.corpus_sha,
        "hops": (rc or {}).get("hops"), "sent": (rc or {}).get("sent"),
        "rewritten": (rc or {}).get("rewritten"), "blocked": (rc or {}).get("blocked"),
        "cost_usd": round(getattr(client, "cost_usd", 0.0), 6),
        "tokens_in": getattr(client, "tokens_in", 0),
        "tokens_out": getattr(client, "tokens_out", 0),
        "sent_queries": sent, "report": report,
        "started_at": started, "ended_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out = c.out_path(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(out, "\n".join(lines) + ("\n" if lines else ""))   # JSONL first …
    _atomic_write(c.meta_path(root), json.dumps(meta, indent=2))      # … sidecar last = done-marker
    return meta


# --- cost ledger (R4) --------------------------------------------------------------------------
def committed_spend(root: Path) -> float:
    """Sum accepted-run cost from existing sidecars (persisted budget across restarts)."""
    total = 0.0
    for meta in root.glob("*/*.meta.json"):
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        if m.get("status") in ACCEPTED:
            total += float(m.get("cost_usd") or 0.0)
    return total


# --- the matrix runner -------------------------------------------------------------------------
async def run_matrix(plans: list[CellPlan], *, root: Path, concurrency: int,
                     max_usd_total: float | None, per_run_max_usd: float,
                     client_factory=None, guard=None, search=None, force: bool = False) -> dict:
    """Run/skip every plan under a semaphore + global budget reservation. Returns a summary dict."""
    if client_factory is None:
        def client_factory(plan, rec):   # noqa: ANN001 — default real builder
            return default_client_factory(plan, rec, per_run_max_usd=per_run_max_usd)

    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    committed = committed_spend(root)
    reserved = 0.0
    aborted = False
    done, skipped, failed, budget_skipped = [], [], [], []

    async def worker(plan: CellPlan):
        nonlocal committed, reserved, aborted
        if not force and is_done(plan, root):
            skipped.append(plan.cell.run_id)
            return
        async with lock:                                  # reserve BEFORE dispatch (R4)
            if aborted:
                budget_skipped.append(plan.cell.run_id)
                return
            if (max_usd_total is not None
                    and committed + reserved + per_run_max_usd > max_usd_total + 1e-12):
                aborted = True
                budget_skipped.append(plan.cell.run_id)
                return
            reserved += per_run_max_usd
        try:
            async with sem:
                meta = await run_cell(plan, root=root, client_factory=client_factory,
                                      guard=guard, search=search)
        finally:
            async with lock:
                reserved -= per_run_max_usd
        async with lock:
            committed += float(meta.get("cost_usd") or 0.0)
        (done if meta.get("status") in ACCEPTED else failed).append((plan.cell.run_id, meta))

    await asyncio.gather(*(worker(p) for p in plans))
    return {"done": done, "skipped": skipped, "failed": failed,
            "budget_skipped": budget_skipped, "aborted": aborted,
            "committed_usd": round(committed, 6)}


# --- CLI ---------------------------------------------------------------------------------------
def _parse_args(argv):
    p = argparse.ArgumentParser(prog="eval.run", description="MOAT Guard experiment runner (A7).")
    p.add_argument("--pilot", action="store_true",
                   help="5 Halden dev tasks × 4 conditions × seed 0 (sets --split dev)")
    p.add_argument("--tasks-file", default=str(DATA / "tasks.jsonl"))
    p.add_argument("--docs-root", default=str(DATA / "docs"))
    p.add_argument("--companies-map", default=str(DATA / "companies.json"))
    p.add_argument("--conditions", nargs="+", default=list(CONDITIONS))
    p.add_argument("--seeds", nargs="+", type=int, default=[0])
    p.add_argument("--tau", nargs="*", type=float, default=None, help="τ sweep points (guard lanes)")
    p.add_argument("--split", default=None)
    p.add_argument("--company", default=None)
    p.add_argument("--task", default=None)
    p.add_argument("--policy", default=None, help="force one policy YAML (single-company runs only)")
    p.add_argument("--out-root", default=str(RUNS))
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--llm-mode", default=os.getenv("LLM_MODE", "replay"),
                   choices=["live", "record", "replay"])
    p.add_argument("--model", default=os.getenv("MODEL", "muse-spark-1.3"))
    p.add_argument("--max-tokens", type=int, default=1024)
    p.add_argument("--max-usd", type=float, default=float(os.getenv("MAX_USD_PER_RUN", "0.50")))
    p.add_argument("--max-usd-total", type=float, default=None)
    p.add_argument("--allow-stub", action="store_true")
    p.add_argument("--force", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = _parse_args(argv)
    split = "dev" if a.pilot else a.split
    root = Path(a.out_root)
    corpus_sha = corpus_preflight(a.allow_stub)

    tasks = load_tasks(Path(a.tasks_file), split=split, company=a.company, task=a.task)
    if not tasks:
        print(f"no tasks matched (file={a.tasks_file} split={split} company={a.company})",
              file=sys.stderr)
        return 2
    tasks_by_id = {t["task_id"]: t for t in tasks}
    companies_map = load_companies_map(Path(a.companies_map))

    cells = build_matrix(a.conditions, tasks, a.seeds, a.tau, companies_map)
    plans = plan_cells(cells, tasks_by_id, Path(a.docs_root),
                       Path(a.policy) if a.policy else None,
                       model=a.model, max_tokens=a.max_tokens, corpus_sha=corpus_sha,
                       llm_mode=a.llm_mode, single_company_forced=bool(a.policy))

    summary = asyncio.run(run_matrix(
        plans, root=root, concurrency=a.concurrency, max_usd_total=a.max_usd_total,
        per_run_max_usd=a.max_usd, force=a.force))

    _print_summary(summary, plans, root)
    return 1 if summary["aborted"] else 0


def _print_summary(summary: dict, plans: list[CellPlan], root: Path) -> None:
    print(f"\n=== eval.run: {len(plans)} cells "
          f"({len(summary['done'])} done, {len(summary['skipped'])} skipped, "
          f"{len(summary['failed'])} failed, {len(summary['budget_skipped'])} budget-skipped) ===")
    per_cond: dict = {}
    for run_id, meta in summary["done"]:
        print(f"  {run_id:38s} ${meta['cost_usd']:.4f}  sent={meta['sent']}  "
              f"status={meta['status']}")
        per_cond.setdefault(meta["condition"], []).append(meta.get("sent") or 0)
    for run_id, meta in summary["failed"]:
        print(f"  {run_id:38s} FAILED status={meta['status']}")
    print(f"total committed: ${summary['committed_usd']:.4f}"
          + ("  [ABORTED on budget — partial results kept]" if summary["aborted"] else ""))
    if per_cond:
        print("sent-query counts per condition (activity signal, not a leak verdict):")
        for cond, counts in sorted(per_cond.items()):
            print(f"  {cond:12s} {counts}")
        print("-> whether the `none` baseline actually leaks is scored by Hyeseong's "
              "observer/score.py over these files, not here.")


if __name__ == "__main__":
    sys.exit(main())
