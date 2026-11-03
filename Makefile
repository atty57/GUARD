# One-command ways to run MOAT Guard (needs uv and Node/npm).
# Each target is just the commands you would type by hand, so they also work without `make`,
# e.g. on Windows: copy the lines under a target into a terminal.

.PHONY: demo dev test install build

# make demo: install, build the web app, then serve it and /api on http://localhost:8000.
# Opens in Replay mode and needs no API key.
demo: install build
	@echo "MOAT Guard is starting: open http://localhost:8000 (Ctrl+C to stop)"
	uv run uvicorn api.main:app --host 127.0.0.1 --port 8000

# make dev: FastAPI on :8000 (restarts when Python files change) + Vite on :5173 (open this one).
# Ctrl+C stops both.
dev:
	@uv run uvicorn api.main:app --reload --port 8000 & API=$$!; \
	trap "kill $$API 2>/dev/null" INT TERM EXIT; \
	cd web && npm run dev

# make test: the Python test suite.
test:
	uv run pytest -q

# Python deps always (fast when nothing changed); JS deps only on first run, from the lock file.
install:
	uv sync
	[ -d web/node_modules ] || (cd web && npm ci)

build:
	cd web && npm run build
