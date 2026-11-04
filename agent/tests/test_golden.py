from copy import deepcopy
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from agent.events import EventAdapter, check_stream
from agent.golden import build, observer_events, read_json

SOURCE = Path(__file__).resolve().parents[2] / "runs/golden/sources/profile_rewrite"


def test_compare_preserves_queries_decisions_and_report():
    events = build(SOURCE)
    check_stream([EventAdapter.validate_python(e) for e in events], "profile golden")
    for cond, lane in (("none", "baseline"), ("moat", "moat")):
        original = [json.loads(l) for l in (SOURCE / f"{cond}.jsonl").read_text(encoding="utf-8").splitlines()]
        for typ in ("query_drafted", "guard_decision", "search_results", "report_chunk", "profile_update", "run_complete"):
            assert [e["data"] for e in events if e["lane"] == lane and e["type"] == typ] == [
                e["data"] for e in original if e["type"] == typ]
        estimates = [e for e in events if e["lane"] == lane and e["type"] == "observer_estimate"]
        assert len(estimates) == 8
        complete = next(e for e in events if e["lane"] == lane and e["type"] == "run_complete")
        last_search = max(e["seq"] for e in events if e["lane"] == lane and e["type"] == "search_results")
        assert all(last_search < e["seq"] < complete["seq"] for e in estimates)
    rewrite = [e for e in events if e["type"] == "guard_decision" and e["data"]["decision"] == "rewrite"]
    assert len(rewrite) == 1 and rewrite[0]["data"]["rechecked"]
    assert "pouch cell" in rewrite[0]["data"]["original"]
    assert "pouch cell" not in rewrite[0]["data"]["final"]


def test_wrong_observer_log_or_truncation_rejected():
    meta = read_json(SOURCE / "moat.meta.json")
    record = read_json(SOURCE / "moat.observer.json")
    template = build(SOURCE)[-1]
    dimensions = read_json(SOURCE / "manifest.json")["dimensions"]
    bad = deepcopy(record)
    bad["request"]["messages"] = [{"role": "user", "content": "(no queries)"}]
    with pytest.raises(ValueError, match="dispatched queries"):
        observer_events(bad, meta, dimensions, template)
    bad = deepcopy(record)
    bad["response"]["choices"][0]["finish_reason"] = "length"
    with pytest.raises(ValueError, match="complete response"):
        observer_events(bad, meta, dimensions, template)


def test_published_golden_matches_builder_and_api(monkeypatch):
    import api.main as api
    path = SOURCE.parents[1] / "profile_rewrite.jsonl"
    expected = build(SOURCE)
    assert [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] == expected
    # Any attempt to construct a model client during playback is a bug.
    monkeypatch.setattr(api, "LLMClient", lambda **kw: pytest.fail("Replay attempted a model call"))
    with TestClient(api.app) as client:
        entry = next(r for r in client.get("/api/replay").json()["runs"] if r["name"] == "golden_profile_rewrite")
        assert entry["kind"] == "golden" and entry["lanes"] == ["baseline", "moat"]
        response = client.get("/api/replay/golden_profile_rewrite/stream?delay=0")
        assert response.status_code == 200
        received = [json.loads(l[6:]) for l in response.text.splitlines() if l.startswith("data: ")]
        assert received == expected
