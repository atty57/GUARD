"""Experiment conditions — the 2×2 ablation + the τ sweep (HANDOFF §10, issue #12).

Four conditions, all driven through the **one** agent graph (``agent.graph.stream``). A
condition is just a switch into that graph; it adds no second code path:

- ``none``        — no guard, no privacy note (the baseline);
- ``prompt``      — the MosaicLeaks privacy note in the draft system prompt, no guard;
- ``moat``        — the real ``guard.check`` at the trust boundary, no privacy note;
- ``moat_prompt`` — guard **and** privacy note.

The privacy note is handled inside the graph's prompts (``prompt``/``moat_prompt`` prepend it).
``moat``/``moat_prompt`` route through the real guard; ``none``/``prompt`` never call it. ``tau``
is a strictness override on the loaded policy for the sweep — it changes ``run_started.tau`` and
``policy_hash`` but nothing else about the run.

    from configs import run, CONDITIONS
    events = await run("demo", "moat", client=FakeClient())          # real guard
    events = await run("demo", "moat", tau=1.5, client=FakeClient()) # stricter sweep point
"""

from __future__ import annotations

from pathlib import Path

from agent.graph import collect, stream
from guard.policy import Policy, load_policy

CONDITIONS: tuple[str, ...] = ("none", "prompt", "moat", "moat_prompt")
_DEFAULT_POLICY = Path(__file__).resolve().parent.parent / "data" / "policy.example.yaml"


def uses_guard(condition: str) -> bool:
    """Whether this condition sends drafts through the guard (vs. passthrough)."""
    return condition in ("moat", "moat_prompt")


def make_policy(tau: float | None = None, *, policy_path=_DEFAULT_POLICY) -> Policy:
    """Load the policy, overriding ``tau`` for the sweep. Re-validates (tau must be > 0)."""
    policy = load_policy(policy_path)
    if tau is None or tau == policy.tau:
        return policy
    return Policy.model_validate({**policy.model_dump(), "tau": tau})


def _run_kwargs(condition: str, *, tau=None, policy=None, policy_path=_DEFAULT_POLICY, **extra):
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition {condition!r}; pick one of {CONDITIONS}")
    policy = policy if policy is not None else make_policy(tau, policy_path=policy_path)
    # none/prompt skip the guard entirely; moat/moat_prompt use the graph's real-guard default.
    # (Tests pass an explicit guard= here to inject StubGuard — extra overrides the default.)
    return {"policy": policy, **extra}


def stream_condition(task: str, condition: str, **kw):
    """Async iterator of ``moat.v2`` events for one condition (thin wrapper over the graph)."""
    return stream(task, condition, **_run_kwargs(condition, **kw))


async def run(task: str, condition: str, **kw) -> list:
    """Run one condition end to end and return its event list."""
    return await collect(task, condition, **_run_kwargs(condition, **kw))
