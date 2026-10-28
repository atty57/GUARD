from fastapi import FastAPI

app = FastAPI(title="GUARD API")


@app.get("/api/health")
def health():
    return {"status": "ok"}