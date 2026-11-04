import asyncio
import json
import uuid
from typing import Literal
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import guard.check as moat_guard  # Hyeseong's real guard (the module with async check())
from agent.events import EventAdapter, to_sse
from agent.graph import stream_group
from agent.llm import LLMClient
from guard.policy import load_policy

app = FastAPI(title="GUARD API")

# Recorded runs live in runs/fixtures/ at the repo root (one event per line, moat.v2 format).
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "runs" / "fixtures"
# Every live run is saved here as it streams, so it can be replayed later (e.g. for the video).
LIVE_DIR = Path(__file__).resolve().parent.parent / "runs" / "live"
# Golden recordings: the prepared demo runs for the video (Atharva, A8 #14). They may land in
# runs/golden/ or as runs/golden_*.jsonl (PLAN.md), so both places are read.
RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"
GOLDEN_DIR = RUNS_DIR / "golden"
# The disclosure policy (Hyeseong's guard/policy.py reads the same file).
POLICY_PATH = Path(__file__).resolve().parent.parent / "data" / "policy.example.yaml"
# Recorded model answers, so a live run works with no API key (LLM_MODE=replay, the default).
# Only covers task "demo" for now; other tasks fail closed until a key or more recordings exist.
LLM_FIXTURE = Path(__file__).resolve().parent.parent / "runs" / "recordings" / "llm_demo.jsonl"
# Experiment results for the dashboard (S5). Written by Hyeseong's eval/score.py; until the real
# run lands it holds placeholder numbers with "placeholder": true. Format: eval/schema.py.
EVAL_RESULTS = Path(__file__).resolve().parent.parent / "eval" / "eval_results.json"
# The built React app (`npm run build` in web/). Only there after a build, e.g. `make demo`.
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


@app.get("/api/health")
def health():
    return {"status": "ok"}


NO_CACHE = {"Cache-Control": "no-cache"}


@app.get("/api/eval")
def eval_results():
    """The experiment results the dashboard shows, checked against the moat.eval.v1 schema.

    Read from disk on every request, so a new eval_results.json shows up without a restart.
    Every answer says "no-cache", so the browser asks again each time instead of reusing an old
    answer (it had kept a 404 from when the file was missing).
    """
    if not EVAL_RESULTS.is_file():
        raise HTTPException(status_code=404, detail="No eval results yet (eval/eval_results.json)",
                            headers=NO_CACHE)
    # Imported here rather than at the top, so the rest of the API still starts on a checkout
    # without eval/schema.py (Hyeseong's #53).
    from pydantic import ValidationError
    from eval.schema import load_results
    try:
        results = load_results(EVAL_RESULTS)
    except ValidationError as exc:
        # A results file that doesn't match the schema is a bug to fix, not something to show.
        raise HTTPException(status_code=500, headers=NO_CACHE,
                            detail=f"eval_results.json doesn't match moat.eval.v1: {exc}") from exc
    return JSONResponse(results.model_dump(mode="json", by_alias=True), headers=NO_CACHE)


# --- Replay a recorded run in the moat.v2 SSE format ---

# to_sse() comes from agent/events.py (Atharva's contract code), so replay and live runs
# send byte-for-byte the same SSE frames.

async def replay_stream(path: Path, delay: float):
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        # Check each recorded line against the moat.v2 contract before sending it, so a
        # broken recording fails loudly here instead of confusing the UI.
        event = EventAdapter.validate_json(line)
        yield to_sse(event)
        await asyncio.sleep(delay)


def lanes_in(path: Path) -> list[str]:
    """Which lanes a recorded run contains, read from its run_started events."""
    lanes: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("type") == "run_started":
            for lane in event["data"]["lanes"]:
                if lane not in lanes:
                    lanes.append(lane)
    return lanes


def replayable() -> dict[str, tuple[Path, str]]:
    """Every run that can be replayed: name -> (file, kind), in the order the picker shows them.

    kind is "golden" (prepared demo runs, listed first since they're the ones for the video),
    "fixture" (hand-written test data in runs/fixtures/) or "live" (real runs saved by Run live,
    newest first). Only finished recordings end in .jsonl; a .jsonl.part file is left out.
    Golden names start with "golden_", so they can't clash with a fixture's name.
    """
    runs: dict[str, tuple[Path, str]] = {}
    for p in sorted(GOLDEN_DIR.glob("*.jsonl")):
        runs[p.stem if p.stem.startswith("golden_") else f"golden_{p.stem}"] = (p, "golden")
    for p in sorted(RUNS_DIR.glob("golden_*.jsonl")):
        runs.setdefault(p.stem, (p, "golden"))
    runs.update({p.stem: (p, "fixture") for p in sorted(FIXTURES_DIR.glob("*.jsonl"))})
    runs.update({p.stem: (p, "live") for p in sorted(LIVE_DIR.glob("*.jsonl"), reverse=True)})
    return runs


def recorded_at(path: Path) -> str | None:
    """When a run was recorded: the timestamp on its first event (the group's `hello`)."""
    with path.open(encoding="utf-8") as f:
        first = f.readline()
    return json.loads(first).get("ts") if first.strip() else None


@app.get("/api/replay")
def list_replays():
    """Recorded runs the UI can replay, with their lanes, so the UI can lay out columns
    before the stream starts. `kind` (golden / fixture / live) tells the UI how to label it;
    `synthetic` (= a hand-written fixture) is kept for older clients. `recorded_at` (real
    recordings only) lets it say when the run actually happened."""
    return {"runs": [{"name": name, "lanes": lanes_in(path), "kind": kind,
                      "synthetic": kind == "fixture",
                      "recorded_at": None if kind == "fixture" else recorded_at(path)}
                     for name, (path, kind) in replayable().items()]}


@app.get("/api/replay/{name}/stream")
def replay(name: str, delay: float = 0.6):
    # Only allow names of files that actually exist in runs/fixtures/ or runs/live/.
    available = {name: path for name, (path, _) in replayable().items()}
    if name not in available:
        raise HTTPException(status_code=404, detail=f"No recorded run named {name!r}")
    return StreamingResponse(
        replay_stream(available[name], delay),
        media_type="text/event-stream",
        # Tell browsers/proxies not to cache or buffer the stream (PLAN.md §11 SSE gotchas).
        headers={"Cache-Control": "no-cache"},
    )


# --- Live run: actually run the agent and stream its events as they happen ---

async def live_stream(task: str, baseline: str = "none"):
    """Run the left lane (No guard or Prompt only), then the MOAT lane, through the agent graph.

    We pass the real guard in on purpose: until A6 (#45) merges, the graph's default guard
    is a pass-through stub that lets every query out.
    """
    policy = load_policy(POLICY_PATH)
    client = LLMClient(record_path=LLM_FIXTURE)

    # Save the run while it streams. Write to "<name>.jsonl.part" first and rename it to
    # ".jsonl" only once the run has finished, so a run cut off halfway (tab closed, server
    # stopped) never shows up in the replay list looking like a complete recording.
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    # The group id makes the name unique, so two runs started in the same second never share
    # a file (or a .part file). The same id goes into the run's events.
    group_id = uuid.uuid4().hex
    name = f"live_{task}_{datetime.now():%Y-%m-%d_%H%M%S}_{group_id[:8]}"
    part = LIVE_DIR / f"{name}.jsonl.part"
    with part.open("w", encoding="utf-8") as recording:
        async for event in stream_group(task, [baseline, "moat"], guard=moat_guard,
                                        client=client, policy=policy, group_id=group_id):
            # Same one-event-per-line format as the fixtures, so replay reads it unchanged.
            recording.write(event.model_dump_json(by_alias=True) + "\n")
            recording.flush()
            yield to_sse(event)
    part.rename(LIVE_DIR / f"{name}.jsonl")


@app.get("/api/live/stream")
def live(task: str = "demo", baseline: Literal["none", "prompt"] = "none"):
    # baseline = the left lane's condition: "none" (No guard) or "prompt" (Prompt only: the
    # agent is told not to disclose secrets, but nothing checks its queries). Anything else
    # is rejected by FastAPI with a 422 before a run starts.
    return StreamingResponse(
        live_stream(task, baseline),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/api/policy")
def policy_limits(tau: float | None = Query(default=None, gt=0)):
    """Per-secret policy limits at a given strictness (tau), keyed by `dim`.

    The leak meter uses these to call a guess "Exposed" exactly when it breaks the policy,
    instead of one fixed cutoff for every secret. Numbers come from guard/policy.py, so the
    UI and the guard agree on what counts as a leak.
    """
    policy = load_policy(POLICY_PATH)
    if tau is not None:
        policy = policy.model_copy(update={"tau": tau})
    secrets = {}
    for spec in policy.secrets:
        if spec.kind != "numeric":
            continue
        secrets[spec.dim] = {
            "secret_id": spec.secret_id,
            "unit": spec.unit,
            "prior": list(spec.prior),
            "resolution": spec.resolution,
            "min_width": policy.effective_min_width(spec),
            "bits_budget": policy.bits_budget(spec),
            "threshold": spec.threshold,
        }
    return {"tau": policy.tau, "secrets": secrets}


# --- make demo: serve the built React app on the same port as /api ---
# Keep this at the END of the file. FastAPI tries routes in the order they are added, and the
# catch-all below matches every path, so every /api route must be added before it.
# Only active when web/dist/ exists. In development (no build) nothing changes: Vite serves the
# page on :5173 and passes /api calls through to this server.
if WEB_DIST.is_dir():
    # The JS/CSS bundles that index.html loads.
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def web_app(path: str):
        # A mistyped API address should still be an API 404, not the web page.
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        # Other files from the build (e.g. the favicon). The parents check stops paths like
        # "../../secret" from reading files outside web/dist/.
        file = (WEB_DIST / path).resolve()
        if path and file.is_file() and WEB_DIST.resolve() in file.parents:
            return FileResponse(file)
        # Everything else gets the app itself.
        return FileResponse(WEB_DIST / "index.html")
