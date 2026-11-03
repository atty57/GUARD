"""Tier 2: rewrite a blocked query so it can still go out (owner: Hyeseong).

PLAN §8: a Muse Spark rewrite that runs only when Tiers 0-1 block a draft as `redraftable`,
and whose output is re-checked by Tiers 0-1 before it may leave (INTERFACES §1, `rechecked`).

The rewriter generalizes instead of deleting: a stated value becomes the class it belongs to
("272 Wh/kg" -> "high energy density"), a codename or product name is dropped, a narrow range
widens to the topic. This is the generalization step of classic de-identification (replace a
specific value with a broader one that keeps the data useful), applied to a search query.

What the rewriter sees: the blocked draft, the guard's reasons and the names of the protected
attributes. Reasons never carry a value or the matched text (P5 in THREAT_MODEL), so no secret
value is put into the prompt. The draft itself may contain one; it already sits inside the
trust boundary, and the model provider is trusted there (THREAT_MODEL §1, §6).

Off unless a model client is configured, so existing runs, recordings and tests are unchanged:

    import guard.tier2
    guard.tier2.configure(client)      # any object with `async complete(**chat_kwargs)`
    guard.tier2.configure(None)        # off again
"""

from __future__ import annotations

import json
import re

_CLIENT = None
MAX_TOKENS = 1024                      # retried once at double, like the agent graph (4k->8k rule)

SYSTEM = (
    "You rewrite web search queries for an R&D team so they reveal less about the team's own "
    "product, while still finding relevant public patents and papers.\n"
    "Keep the research topic. Remove or generalize:\n"
    "- specific figures for the protected attributes, and ranges or bounds that narrow them "
    "(write 'high energy density', not a number);\n"
    "- company names, product names and project codenames;\n"
    "- launch or production dates;\n"
    "- combinations of product traits that single the product out (keep only what the topic "
    "needs).\n"
    "Do not add facts that are not in the query. Reply with JSON only: {\"query\": \"...\"}. "
    "If the topic cannot be searched without the protected detail, reply {\"query\": \"\"}."
)


def configure(client) -> None:
    """Turn Tier 2 on with a model client, or off with None."""
    global _CLIENT
    _CLIENT = client


def enabled() -> bool:
    return _CLIENT is not None


def build_messages(draft: str, reasons: list[str], attributes: list[str]) -> list[dict]:
    why = "\n".join(f"- {r}" for r in reasons) or "- (none given)"
    protected = ", ".join(a.replace("_", " ") for a in attributes)
    user = (f"Query: {draft}\n\nWhy it was blocked:\n{why}\n\n"
            f"Protected attributes: {protected}")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def parse(text: str) -> str:
    """The rewritten query, or "" when the reply has none."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return ""
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return ""
    q = data.get("query") if isinstance(data, dict) else None
    return " ".join(q.split()) if isinstance(q, str) else ""


async def rewrite(draft: str, reasons: list[str], attributes: list[str], *, seed: int = 0) -> str:
    """Ask the model for a safer query. Returns "" when it gives none; raises if the call fails
    (the guard keeps its block in that case)."""
    if _CLIENT is None:
        return ""
    msgs = build_messages(draft, reasons, attributes)
    text = ""
    for cap in (MAX_TOKENS, MAX_TOKENS * 2):
        resp = await _CLIENT.complete(messages=msgs, temperature=0, seed=seed, max_tokens=cap)
        choice = resp.choices[0] if resp.choices else None
        text = (choice.message.content or "") if choice else ""
        if text.strip() and choice.finish_reason != "length":
            break
    return parse(text)


# --- live smoke check: python -m guard.tier2 ----------------------------------------------------
def _main() -> int:
    """Run blocked Halden drafts through the guard with Tier 2 on, against the live model.
    Drafts come from the command line, or three defaults. Needs MODEL_API_KEY; capped at $0.50."""
    import asyncio
    import sys
    from pathlib import Path

    import guard.tier2 as tier2   # under -m this file runs as __main__; check() reads guard.tier2
    from agent.llm import LLMClient
    from guard.check import check
    from guard.policy import load_policy

    policy = load_policy(Path(__file__).resolve().parent.parent / "data" / "policy.example.yaml")
    client = LLMClient(mode="live", max_usd=0.50)
    tier2.configure(client)
    drafts = sys.argv[1:] or [
        "pouch cell 272 Wh/kg high-nickel cathode calcination patents",
        "NCM 88/06/06 cathode calcination 745 °C prior art",
        "Project Ostrava EV platform battery cell supplier",
    ]

    async def run():
        for d in drafts:
            r = await check(d, [], policy)
            why = r.reasons[-1] if r.reasons else "no protected detail found"
            print(f"--- {d}\n  {r.action} (tier {r.tier}) -> {r.query!r}\n  {why}", flush=True)
    asyncio.run(run())
    print(f"spent ${client.cost_usd:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
