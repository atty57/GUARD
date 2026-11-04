"""Scripted attack suite: guard recall per leak type and false positives (owner: Hyeseong).

This is a development suite, not a benchmark: report its numbers as development-suite
performance on Halden.

PLAN §10. Each attack is a query written to leak one Halden secret in one way; a benign control
is a query any battery researcher could send. Both go through ``guard.check`` exactly as the
agent's drafts do, with Tier 2 off so the result is deterministic and costs nothing.

- caught = the guard blocked or rewrote it before dispatch (anything but ``pass``)
- recall per leak type = caught / attacks of that type
- false positive rate = benign controls not passed / benign controls

The sets live in ``data/attacks.halden.jsonl`` and ``data/benign.halden.jsonl``. They were written
by hand by the guard's author, so recall here is an upper estimate; the misses are listed in the
details file so they can be read, not just counted.

    uv run python -m eval.attacks                                  # print, write the details file
    uv run python -m eval.attacks --into eval/eval_results.json    # also fill attacks / benign
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
DATA = _REPO / "data"
ATTACKS = DATA / "attacks.halden.jsonl"
BENIGN = DATA / "benign.halden.jsonl"
POLICY = DATA / "policy.example.yaml"
DETAILS = _REPO / "eval" / "attack_details.json"


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _history(queries: list[str]) -> list[dict]:
    return [{"query": q, "hop": i, "run_id": "attack-suite"} for i, q in enumerate(queries)]


async def run_suite(attacks: list[dict], benign: list[dict], policy) -> dict:
    """Run every attack and control through the guard. Returns results plus per-item details."""
    import guard.tier2
    from guard.check import check

    token = guard.tier2.configure(None)            # deterministic: Tiers 0-1 only
    try:
        by_type: dict[str, list[bool]] = defaultdict(list)
        missed, flagged = [], []
        for a in attacks:
            r = await check(a["query"], _history(a.get("history", [])), policy)
            caught = r.action != "pass"
            by_type[a["leak_type"]].append(caught)
            if not caught:
                missed.append({"id": a["id"], "leak_type": a["leak_type"], "query": a["query"],
                               "history": a.get("history", [])})
        for b in benign:
            r = await check(b["query"], [], policy)
            if r.action != "pass":
                flagged.append({"id": b["id"], "query": b["query"], "leak_types": r.leak_types,
                                "reasons": r.reasons})
    finally:
        guard.tier2.reset(token)

    results = [{"leak_type": t, "n": len(v), "recall": round(sum(v) / len(v), 4)}
               for t, v in by_type.items()]
    return {
        "attacks": results,
        "benign": {"n": len(benign), "false_positive_rate": round(len(flagged) / len(benign), 4)},
        "missed": missed,
        "false_positives": flagged,
    }


def fill(results_path: Path, suite: dict) -> None:
    """Write the suite into an eval_results.json, validated against the schema."""
    from eval.schema import EvalResults

    data = json.loads(results_path.read_text(encoding="utf-8"))
    data["attacks"], data["benign"] = suite["attacks"], suite["benign"]
    EvalResults.model_validate(data)
    results_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    from guard.policy import load_policy

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--attacks", type=Path, default=ATTACKS)
    ap.add_argument("--benign", type=Path, default=BENIGN)
    ap.add_argument("--policy", type=Path, default=POLICY)
    ap.add_argument("--details", type=Path, default=DETAILS)
    ap.add_argument("--into", type=Path, help="eval_results.json to fill attacks / benign in")
    args = ap.parse_args(argv)

    suite = asyncio.run(run_suite(_read(args.attacks), _read(args.benign), load_policy(args.policy)))
    caught = sum(round(a["recall"] * a["n"]) for a in suite["attacks"])
    total = sum(a["n"] for a in suite["attacks"])
    for a in suite["attacks"]:
        print(f"  {a['leak_type']:20s} {round(a['recall'] * a['n']):3d}/{a['n']}")
    b = suite["benign"]
    print(f"development suite: caught {caught}/{total}; benign wrongly stopped {len(suite['false_positives'])}/{b['n']}")
    args.details.write_text(json.dumps({k: suite[k] for k in ("missed", "false_positives")},
                                       indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.into:
        fill(args.into, suite)
        print(f"filled attacks / benign in {args.into}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
