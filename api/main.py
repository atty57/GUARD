import asyncio
import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

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
# The disclosure policy (Hyeseong's guard/policy.py reads the same file).
POLICY_PATH = Path(__file__).resolve().parent.parent / "data" / "policy.example.yaml"
# Recorded model answers, so a live run works with no API key (LLM_MODE=replay, the default).
# Only covers task "demo" for now; other tasks fail closed until a key or more recordings exist.
LLM_FIXTURE = Path(__file__).resolve().parent.parent / "runs" / "recordings" / "llm_demo.jsonl"


@app.get("/api/health")
def health():
    return {"status": "ok"}


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


def replayable() -> dict[str, tuple[Path, bool]]:
    """Every run that can be replayed: name -> (file, synthetic?).

    runs/fixtures/ is hand-written test data (synthetic). runs/live/ holds recordings of real
    agent runs (not synthetic). Only finished recordings end in .jsonl; a run still streaming,
    or one that was cut off, is a .jsonl.part file and is left out.
    """
    runs = {p.stem: (p, True) for p in sorted(FIXTURES_DIR.glob("*.jsonl"))}
    # Newest live run first.
    runs.update({p.stem: (p, False) for p in sorted(LIVE_DIR.glob("*.jsonl"), reverse=True)})
    return runs


def recorded_at(path: Path) -> str | None:
    """When a run was recorded: the timestamp on its first event (the group's `hello`)."""
    with path.open(encoding="utf-8") as f:
        first = f.readline()
    return json.loads(first).get("ts") if first.strip() else None


@app.get("/api/replay")
def list_replays():
    """Recorded runs the UI can replay, with their lanes, so the UI can lay out columns
    before the stream starts. `synthetic` tells the UI whether to label it as test data;
    `recorded_at` (real recordings only) lets it say when the run actually happened."""
    return {"runs": [{"name": name, "lanes": lanes_in(path), "synthetic": synthetic,
                      "recorded_at": None if synthetic else recorded_at(path)}
                     for name, (path, synthetic) in replayable().items()]}


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

async def live_stream(task: str):
    """Run the baseline lane, then the MOAT lane, through Atharva's agent graph.

    We pass the real guard in on purpose: until A6 (#45) merges, the graph's default guard
    is a pass-through stub that lets every query out.
    """
    policy = load_policy(POLICY_PATH)
    client = LLMClient(record_path=LLM_FIXTURE)

    # Save the run while it streams. Write to "<name>.jsonl.part" first and rename it to
    # ".jsonl" only once the run has finished, so a run cut off halfway (tab closed, server
    # stopped) never shows up in the replay list looking like a complete recording.
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"live_{task}_{datetime.now():%Y-%m-%d_%H%M%S}"
    part = LIVE_DIR / f"{name}.jsonl.part"
    with part.open("w", encoding="utf-8") as recording:
        async for event in stream_group(task, ["none", "moat"], guard=moat_guard,
                                        client=client, policy=policy):
            # Same one-event-per-line format as the fixtures, so replay reads it unchanged.
            recording.write(event.model_dump_json(by_alias=True) + "\n")
            recording.flush()
            yield to_sse(event)
    part.rename(LIVE_DIR / f"{name}.jsonl")


@app.get("/api/live/stream")
def live(task: str = "demo"):
    return StreamingResponse(
        live_stream(task),
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
