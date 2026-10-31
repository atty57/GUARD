import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

from agent.events import EventAdapter, to_sse
from guard.policy import load_policy

app = FastAPI(title="GUARD API")

# Recorded runs live in runs/fixtures/ at the repo root (one event per line, moat.v2 format).
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "runs" / "fixtures"
# The disclosure policy (Hyeseong's guard/policy.py reads the same file).
POLICY_PATH = Path(__file__).resolve().parent.parent / "data" / "policy.example.yaml"


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


@app.get("/api/replay")
def list_replays():
    """Recorded runs the UI can replay, with their lanes, so the UI can lay out columns
    before the stream starts."""
    # Everything in runs/fixtures/ is hand-written test data, not a recording of a real agent
    # run, so it's marked synthetic and the UI labels it. Real recordings come later (S4).
    return {"runs": [{"name": p.stem, "lanes": lanes_in(p), "synthetic": True}
                     for p in sorted(FIXTURES_DIR.glob("*.jsonl"))]}


@app.get("/api/replay/{name}/stream")
def replay(name: str, delay: float = 0.6):
    # Only allow names of files that actually exist in runs/fixtures/.
    available = {p.stem: p for p in FIXTURES_DIR.glob("*.jsonl")}
    if name not in available:
        raise HTTPException(status_code=404, detail=f"No recorded run named {name!r}")
    return StreamingResponse(
        replay_stream(available[name], delay),
        media_type="text/event-stream",
        # Tell browsers/proxies not to cache or buffer the stream (PLAN.md §11 SSE gotchas).
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
