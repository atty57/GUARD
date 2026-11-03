"""Tests for the backend the web app talks to (api/main.py, Seb).

Each test points the API at a temporary folder (pytest's tmp_path) instead of the real runs/ or
eval/ folders, so the tests never touch real recordings and always start from a known state.
Run with `make test` (or `uv run pytest -q api/tests`).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.main as main
from agent.events import EventAdapter

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "runs" / "fixtures" / "compare_run.jsonl"
RESULTS = REPO / "eval" / "eval_results.json"

client = TestClient(main.app)


def sse_events(body: str) -> list:
    """Turn an SSE response body back into contract-checked events."""
    return [EventAdapter.validate_json(line.removeprefix("data: "))
            for line in body.splitlines() if line.startswith("data: ")]


@pytest.fixture
def runs(tmp_path, monkeypatch):
    """Empty fixtures/ and live/ folders the API reads instead of the real ones."""
    fixtures, live = tmp_path / "fixtures", tmp_path / "live"
    fixtures.mkdir()
    live.mkdir()
    monkeypatch.setattr(main, "FIXTURES_DIR", fixtures)
    monkeypatch.setattr(main, "LIVE_DIR", live)
    return fixtures, live


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


# --- replay list and stream ----------------------------------------------------------------

def test_replay_list_labels_fixtures_and_live_runs(runs):
    fixtures, live = runs
    shutil.copy(FIXTURE, fixtures / "compare_run.jsonl")
    shutil.copy(FIXTURE, live / "live_demo_2026-11-01_100000_abcd1234.jsonl")
    shutil.copy(FIXTURE, live / "live_demo_2026-11-01_100500_ef567890.jsonl.part")  # cut off

    listed = {r["name"]: r for r in client.get("/api/replay").json()["runs"]}

    assert set(listed) == {"compare_run", "live_demo_2026-11-01_100000_abcd1234"}  # no .part
    assert listed["compare_run"]["synthetic"] is True
    assert listed["compare_run"]["recorded_at"] is None
    live_run = listed["live_demo_2026-11-01_100000_abcd1234"]
    assert live_run["synthetic"] is False
    assert live_run["recorded_at"] is not None                # taken from the hello event
    assert live_run["lanes"] == ["baseline", "moat"]


def test_replay_stream_sends_every_recorded_event(runs):
    fixtures, _ = runs
    shutil.copy(FIXTURE, fixtures / "compare_run.jsonl")

    res = client.get("/api/replay/compare_run/stream?delay=0")

    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-cache"
    recorded = [line for line in FIXTURE.read_text().splitlines() if line.strip()]
    assert len(sse_events(res.text)) == len(recorded)


def test_replay_stream_unknown_name_is_404(runs):
    assert client.get("/api/replay/nope/stream?delay=0").status_code == 404


def test_replay_stream_rejects_a_broken_recording(runs):
    fixtures, _ = runs
    (fixtures / "broken.jsonl").write_text('{"not": "a moat.v2 event"}\n')
    with pytest.raises(Exception):                             # fails loudly, never streams junk
        client.get("/api/replay/broken/stream?delay=0")


# --- eval results (dashboard) --------------------------------------------------------------

def test_eval_missing_file_is_404(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "EVAL_RESULTS", tmp_path / "eval_results.json")
    res = client.get("/api/eval")
    assert res.status_code == 404
    assert res.headers["cache-control"] == "no-cache"          # a 404 must not be cached either


def test_eval_serves_the_results_file(tmp_path, monkeypatch):
    path = tmp_path / "eval_results.json"
    shutil.copy(RESULTS, path)
    monkeypatch.setattr(main, "EVAL_RESULTS", path)

    res = client.get("/api/eval")

    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-cache"
    assert res.json()["schema"] == "moat.eval.v1"
    assert {c["condition"] for c in res.json()["conditions"]} == {"none", "prompt", "moat", "moat_prompt"}


def test_eval_rejects_a_file_that_breaks_the_schema(tmp_path, monkeypatch):
    bad = json.loads(RESULTS.read_text())
    bad["conditions"] = bad["conditions"][:3]                  # one condition missing
    path = tmp_path / "eval_results.json"
    path.write_text(json.dumps(bad))
    monkeypatch.setattr(main, "EVAL_RESULTS", path)

    assert client.get("/api/eval").status_code == 500


# --- live runs -----------------------------------------------------------------------------

def test_live_rejects_an_unknown_baseline(runs):
    _, live = runs
    assert client.get("/api/live/stream?baseline=moat").status_code == 422
    assert list(live.iterdir()) == []                          # rejected before any run started


def test_live_runs_are_saved_with_unique_names(runs):
    """Two live runs back to back: both finish, each is saved to its own file, no .part left.
    (They may stop early on a replay miss without an API key; they're still saved.)"""
    _, live = runs
    bodies = [client.get("/api/live/stream?task=demo").text for _ in range(2)]

    saved = sorted(live.iterdir())
    assert len(saved) == 2 and all(p.suffix == ".jsonl" for p in saved)
    for body in bodies:
        events = sse_events(body)
        assert sum(e.type == "run_complete" for e in events) == 2   # baseline + moat both end
    group_ids = {sse_events(b)[0].group_id[:8] for b in bodies}
    assert all(any(g in p.name for p in saved) for g in group_ids)   # file name = its group id


# --- policy --------------------------------------------------------------------------------

def test_policy_limits_follow_tau():
    loose = client.get("/api/policy?tau=0.5").json()
    strict = client.get("/api/policy?tau=2").json()
    assert loose["tau"] == 0.5 and strict["tau"] == 2
    dim = next(iter(loose["secrets"]))
    assert strict["secrets"][dim]["min_width"] >= loose["secrets"][dim]["min_width"]   # stricter = wider


def test_policy_rejects_a_non_positive_tau():
    assert client.get("/api/policy?tau=0").status_code == 422


# --- built web app (make demo) -------------------------------------------------------------

def test_dist_file_never_leaves_web_dist(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("app")
    (dist / "favicon.svg").write_text("icon")
    (tmp_path / "secret.txt").write_text("secret")
    monkeypatch.setattr(main, "WEB_DIST", dist)

    assert main.dist_file("favicon.svg") == (dist / "favicon.svg").resolve()   # real build file
    for sneaky in ["../secret.txt", "../../etc/passwd", "/etc/passwd", "some/page", ""]:
        assert main.dist_file(sneaky) == dist / "index.html", sneaky         # everything else: the app
