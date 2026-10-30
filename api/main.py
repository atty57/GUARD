import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from fake_events import FAKE_MOAT_RUN

app = FastAPI(title="GUARD API")

# Recorded runs live in runs/fixtures/ at the repo root (one event per line, moat.v2 format).
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "runs" / "fixtures"


@app.get("/api/health")
def health():
    return {"status": "ok"}


# --- Old fake stream (removed in step 7c, once the frontend uses fixtures) ---

async def fake_event_stream():
    for event in FAKE_MOAT_RUN:
        await asyncio.sleep(0.8)
        yield f"data: {json.dumps(event)}\n\n"


@app.get("/api/stream/fake")
def stream_fake():
    return StreamingResponse(fake_event_stream(), media_type="text/event-stream")


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


@app.get("/api/replay/{name}/stream")
def replay(name: str, delay: float = 0.6):
    # Only allow names of files that actually exist in runs/fixtures/.
    available = {p.stem: p for p in FIXTURES_DIR.glob("*.jsonl")}
    if name not in available:
        raise HTTPException(status_code=404, detail=f"No recorded run named {name!r}")
    return StreamingResponse(replay_stream(available[name], delay), media_type="text/event-stream")
