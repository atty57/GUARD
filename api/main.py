import asyncio
import json

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from fake_events import FAKE_MOAT_RUN

app = FastAPI(title="GUARD API")


@app.get("/api/health")
def health():
    return {"status": "ok"}


async def fake_event_stream():
    for event in FAKE_MOAT_RUN:
        await asyncio.sleep(0.8)
        yield f"data: {json.dumps(event)}\n\n"


@app.get("/api/stream/fake")
def stream_fake():
    return StreamingResponse(fake_event_stream(), media_type="text/event-stream")
