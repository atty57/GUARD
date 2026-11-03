"""Small statistics helpers for eval/score.py (owner: Hyeseong).

Pure Python on purpose: dependencies were frozen on Nov 1 and the run sizes are small
(tens of runs per condition), so numpy/scipy would add nothing but install weight.

- ``bootstrap_ci``: percentile bootstrap of the mean. The CI of a rate stays in [0, 1].
- ``wilcoxon``: two-sided signed-rank test on paired differences. Exact null distribution
  up to 20 non-zero pairs, normal approximation (with tie correction) above that.
- ``holm``: Holm step-down adjustment, monotone and capped at 1.
"""

from __future__ import annotations

import math
import random
from statistics import mean


def bootstrap_ci(xs: list[float], *, n_resamples: int = 10_000, seed: int = 0,
                 level: float = 0.95) -> tuple[float, float]:
    if not xs:
        raise ValueError("bootstrap_ci needs at least one value")
    if len(xs) == 1:
        return xs[0], xs[0]
    rng = random.Random(seed)
    n = len(xs)
    means = sorted(sum(rng.choice(xs) for _ in range(n)) / n for _ in range(n_resamples))
    alpha = (1 - level) / 2
    lo = means[int(math.floor(alpha * (n_resamples - 1)))]
    hi = means[int(math.ceil((1 - alpha) * (n_resamples - 1)))]
    m = mean(xs)
    # Percentile ends always bracket the sample mean in practice; clamp for tiny samples.
    return min(lo, m), max(hi, m)


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    return ranks


def wilcoxon(diffs: list[float]) -> float:
    """Two-sided p-value for H0: paired differences are symmetric around 0.

    Zero differences are dropped (Wilcoxon's convention). Returns 1.0 when nothing is left.
    """
    d = [x for x in diffs if x != 0]
    n = len(d)
    if n == 0:
        return 1.0
    ranks = _ranks([abs(x) for x in d])
    w_plus = sum(r for r, x in zip(ranks, d) if x > 0)
    total = n * (n + 1) / 2
    stat = min(w_plus, total - w_plus)
    if n <= 20:
        # Exact: count sign assignments whose positive-rank sum is <= stat. Ranks may be
        # half-integers under ties, so work in doubled units.
        r2 = [int(round(2 * r)) for r in ranks]
        counts = {0: 1}
        for r in r2:
            nxt = dict(counts)
            for s, c in counts.items():
                nxt[s + r] = nxt.get(s + r, 0) + c
            counts = nxt
        s2 = int(round(2 * stat))
        tail = sum(c for s, c in counts.items() if s <= s2)
        return min(1.0, 2 * tail / 2 ** n)
    # Normal approximation with tie correction.
    ties: dict[float, int] = {}
    for r in ranks:
        ties[r] = ties.get(r, 0) + 1
    var = n * (n + 1) * (2 * n + 1) / 24 - sum(t ** 3 - t for t in ties.values()) / 48
    if var <= 0:
        return 1.0
    z = (stat - total / 2) / math.sqrt(var)
    return min(1.0, math.erfc(abs(z) / math.sqrt(2)))


def holm(ps: list[float]) -> list[float]:
    m = len(ps)
    order = sorted(range(m), key=lambda i: ps[i])
    out = [0.0] * m
    running = 0.0
    for k, i in enumerate(order):
        running = max(running, (m - k) * ps[i])
        out[i] = min(1.0, running)
    return out


def mcnemar(b: int, c: int) -> float:
    """Exact two-sided McNemar test on discordant pairs: ``b`` = a hit / b missed,
    ``c`` = a missed / b hit. Binomial(b + c, 0.5); 1.0 when there are no discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)
