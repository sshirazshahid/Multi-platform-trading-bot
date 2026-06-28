"""Shared stdlib-only stats helpers for the offline research labs.

Extracted 2026-06-21 from dca_rebalance_lab / funding_carry_lab, which each
carried a byte-identical ``_percentiles`` and a duplicated moving-block
bootstrap loop. Centralised here so a fix to the percentile/quantile math or
the resampler reaches both labs. No bot/exchange/ccxt imports — fully testable.
"""

from __future__ import annotations


def _percentiles(values) -> dict:
    """Linear-interpolation percentile summary of ``values``."""
    s = sorted(values)
    n = len(s)

    def q(p):
        if n == 1:
            return s[0]
        idx = p * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        return s[lo] * (1 - (idx - lo)) + s[hi] * (idx - lo)

    return {
        "p5": q(0.05),
        "p25": q(0.25),
        "p50": q(0.50),
        "p75": q(0.75),
        "p95": q(0.95),
        "mean": sum(s) / n,
        "min": s[0],
        "max": s[-1],
    }


def resample_blocks(series, n: int, block: int, rng) -> list:
    """Moving-block bootstrap: resample ``series`` to length ``n`` by
    concatenating random contiguous blocks of size ``block``, preserving
    short-horizon autocorrelation. Shared core of the labs' bootstrap helpers;
    the exact loop is kept so a seeded ``rng`` draws identically to before.
    """
    out: list = []
    while len(out) < n:
        start = rng.randrange(0, len(series))
        out.extend(series[start : start + block])
    return out[:n]
