import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

app = FastAPI(title="GUARD API")

# Recorded runs live in runs/fixtures/ at the repo root (one event per line, moat.v2 format).
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "runs" / "fixtures"


@app.get("/api/health")
def health():
    return {"status": "ok"}


# --- Replay a recorded run in the moat.v2 SSE format ---

def to_sse(event: dict) -> str:
    """One event -> one SSE frame: `event:` is the type, `id:` is seq (omitted when null)."""
    lines = [f"event: {event['type']}"]
    if event.get("seq") is not None:
        lines.append(f"id: {event['seq']}")
    lines.append(f"data: {json.dumps(event)}")
    return "\n".join(lines) + "\n\n"


async def replay_stream(path: Path, delay: float):
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
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
