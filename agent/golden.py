"""Build a keyless compare replay from archived runs and real observer responses.

No model, search, or scoring calls. Source files remain unchanged.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
from itertools import zip_longest
import json
from pathlib import Path

from agent.events import EventAdapter, check_stream


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    """sha256 of the file's lines with CRLF normalized to LF.

    The archived sources are committed as LF, but a checkout with ``core.autocrlf`` (or a
    committed ``.gitattributes``) may hand us CRLF bytes. The recorded digests were taken
    over LF content, so hash the normalized text instead of the platform-dependent bytes.
    """
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_lane(path: Path, meta_path: Path, condition: str) -> tuple[list[dict], dict]:
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    check_stream([EventAdapter.validate_python(e) for e in events], str(path))
    meta = read_json(meta_path)
    if digest(path) != meta["events_sha256"]:
        raise ValueError(f"Source digest mismatch: {path}")
    if meta["condition"] != condition or meta["status"] not in {"completed", "completed_with_skips"}:
        raise ValueError(f"Wrong condition or incomplete run: {path}")
    started = next(e for e in events if e["type"] == "run_started")
    if (started["run_id"], started["data"]["condition"], started["data"]["policy_hash"]) != (
        meta["run_id"], condition, meta["policy_hash"]
    ):
        raise ValueError(f"Source identity mismatch: {path}")
    if events[-1]["type"] != "run_complete" or events[-1]["data"]["status"] != meta["status"]:
        raise ValueError(f"Missing matching completion: {path}")
    dispatched = [e["data"]["query"] for e in events if e["type"] == "search_results"]
    if dispatched != meta["sent_queries"]:
        raise ValueError(f"Source query log mismatch (including failed dispatches): {path}")
    return events, meta


def observer_events(record: dict, meta: dict, dimensions: dict[str, str], template: dict) -> list[dict]:
    """Use only the response to this lane's complete sent-query log, never a baseline."""
    log = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(meta["sent_queries"]))
    expected = f"Search queries they sent, in order:\n{log}\n\nParameters to estimate:"
    users = [m["content"] for m in record["request"]["messages"] if m["role"] == "user"]
    if len(users) != 1 or expected not in users[0] or f"Company: {meta['company']}\n" not in users[0]:
        raise ValueError("Observer recording does not match this lane's dispatched queries")
    choice = record["response"]["choices"][0]
    if choice["finish_reason"] != "stop":
        raise ValueError("Observer recording is not a complete response")
    # Require actual recorded numeric fields. Never fill missing values or synthesize ranges.
    content = choice["message"]["content"].strip()
    if content.startswith("```json") and content.endswith("```"):
        content = content[7:-3].strip()
    numeric = json.loads(content)["numeric"]
    out = []
    for sid, dim in dimensions.items():
        row = numeric[sid]
        point, lo, hi = (float(row[k]) for k in ("point", "p10", "p90"))
        if not lo <= point <= hi:
            raise ValueError(f"Invalid observer range for {sid}")
        e = deepcopy(template)
        e["type"] = "observer_estimate"
        e["data"] = {"condition": meta["condition"], "attr": dim, "point": point, "p10": lo, "p90": hi}
        EventAdapter.validate_python(e)  # includes finite-number validation
        out.append(e)
    return out


def build(source: Path) -> list[dict]:
    manifest = read_json(source / "manifest.json")
    for rel, sha in manifest["sha256"].items():
        if digest(source / rel) != sha:
            raise ValueError(f"Archived source changed: {rel}")
    lanes = []
    metas = []
    group = manifest["group_id"]
    for condition in ("none", "moat"):
        original, meta = load_lane(source / f"{condition}.jsonl", source / f"{condition}.meta.json", condition)
        metas.append(meta)
        lane = []
        for event in original:
            if event["type"] == "hello":
                continue
            e = deepcopy(event)
            e["group_id"] = group
            if e["type"] == "run_started":
                e["data"]["lanes"] = ["baseline", "moat"]
                e["data"]["replay"] = True
            if e["type"] == "run_complete":
                record = read_json(source / f"{condition}.observer.json")
                lane.extend(observer_events(record, meta, manifest["dimensions"], e))
            lane.append(e)
            if e["type"] == "run_started":
                replay = deepcopy(e)
                replay["type"] = "replay_meta"
                replay["data"] = {"recorded_at": meta["started_at"], "source_run_id": meta["run_id"], "speed": 1.0}
                lane.append(replay)
        lanes.append(lane)
    for key in ("task_id", "seed", "company", "policy_hash", "corpus_sha", "graph_behavior_version"):
        if metas[0][key] != metas[1][key]:
            raise ValueError(f"Compare inputs differ: {key}")
    # Stable presentation interleaving, not a claim that the original runs were simultaneous.
    merged = [e for pair in zip_longest(*lanes) for e in pair if e is not None]
    decisions = {}
    for seq, event in enumerate(merged):
        if event["type"] == "guard_decision":
            decisions[(event["run_id"], event["seq"])] = seq
        if event["type"] == "secret_interval_update":
            event["data"]["decision_seq"] = decisions[(event["run_id"], event["data"]["decision_seq"])]
        event["seq"] = seq
    hello = {"schema": "moat.v2", "type": "hello", "group_id": group, "run_id": None,
             "lane": None, "hop": None, "seq": None, "ts": metas[0]["started_at"],
             "data": {"schema": "moat.v2", "group_id": group}}
    result = [hello, *merged]
    check_stream([EventAdapter.validate_python(e) for e in result], "golden compare")
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=Path("runs/golden/sources/profile_rewrite"))
    ap.add_argument("--out", type=Path, default=Path("runs/golden/profile_rewrite.jsonl"))
    args = ap.parse_args()
    events = build(args.source)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(e, ensure_ascii=False, allow_nan=False) + "\n" for e in events), encoding="utf-8")
    print(f"Validated {len(events)} events: {args.out}")


if __name__ == "__main__":
    main()
