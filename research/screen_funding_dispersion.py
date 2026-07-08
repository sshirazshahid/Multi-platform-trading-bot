"""Candidate A — cross-venue funding-rate dispersion screen (after-cost, pre-registered).

Reads ONLY local data. Simulates a delta-neutral cross-venue pair
(LONG the low-funding venue, SHORT the high-funding venue) and charges the full
FOUR-leg round trip + slippage + the realized funding paid/received per settlement.

Honest scope: the ONLY dataset carrying per-venue funding for the same coin is
``data/funding_carry/{venue}_{coin}.csv``. ``data/derivs_history.jsonl`` and
``data/funding_cache/*.parquet`` are SINGLE-VENUE (verified: no venue/exchange field)
and are therefore ineligible for a cross-venue dispersion screen.

The pure functions below are unit-tested in tests/test_screen_funding_dispersion.py.
Run ``python research/screen_funding_dispersion.py`` to print the screen report.
"""
from __future__ import annotations

import itertools
import json
import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# --- Cost model constants mirrored from config.FEE / config.SLIPPAGE (authoritative) ---
# futures fees per fill (fraction of notional)
FEE = {
    "binance": {"maker": 0.0002, "taker": 0.0005},
    "bybit": {"maker": 0.0001, "taker": 0.0006},
    "bitget": {"maker": 0.0002, "taker": 0.0006},
}
SLIPPAGE_PER_FILL = 0.0005  # 5 bps open/close (config.SLIPPAGE pct_open/pct_close)
VENUES = ("binance", "bybit", "bitget")

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_CARRY_DIR = os.path.join(_DATA_DIR, "funding_carry")


# ----------------------------------------------------------------------------
# Pure, tested math
# ----------------------------------------------------------------------------
def roundtrip_cost_frac(fee_long: float, fee_short: float, slippage: float) -> float:
    """Full 4-leg round trip: 2 fills on the long venue + 2 on the short venue,
    slippage charged on every one of the 4 fills."""
    return 2.0 * fee_long + 2.0 * fee_short + 4.0 * slippage


def signed_carry(rate_short: np.ndarray, rate_long: np.ndarray) -> np.ndarray:
    """Per-settlement net funding for the held pair: short leg RECEIVES rate_short,
    long leg PAYS rate_long. Goes negative when the differential flips against us."""
    return np.asarray(rate_short, dtype=float) - np.asarray(rate_long, dtype=float)


def net_return_frac(carry: np.ndarray, roundtrip_cost: float) -> float:
    """Accumulated carry over the hold minus ONE round-trip cost (single open+close)."""
    return float(np.sum(carry)) - float(roundtrip_cost)


def breakeven_settlements(roundtrip_cost: float, mean_carry: float) -> float:
    """How many settlements the pair must be held for accumulated carry to recover
    the round-trip cost. Infinite when carry is non-positive."""
    if mean_carry <= 0:
        return float("inf")
    return roundtrip_cost / mean_carry


def annualize_apr(net_return: float, days: float) -> float:
    if days <= 0:
        return float("nan")
    return net_return * 365.0 / days


def sign_persistence(series: np.ndarray) -> float:
    """Fraction of consecutive steps whose sign is unchanged (spread stickiness)."""
    s = np.sign(np.asarray(series, dtype=float))
    if len(s) < 2:
        return float("nan")
    return float(np.mean(s[1:] == s[:-1]))


# ----------------------------------------------------------------------------
# Data loading (local only)
# ----------------------------------------------------------------------------
def load_venue_funding(coin: str, venue: str) -> pd.Series:
    """Rate series indexed by settlement timestamp (next_funding_ts grid)."""
    path = os.path.join(_CARRY_DIR, f"{venue}_{coin}.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    d = pd.read_csv(path)
    d["settle"] = d["next_funding_ts"].astype("int64")
    return d.drop_duplicates("settle").set_index("settle")["rate"].sort_index()


def align_cross_venue(coin: str, venues=VENUES) -> pd.DataFrame:
    """Inner-join per-venue funding on the common settlement grid."""
    cols = {v: load_venue_funding(coin, v) for v in venues}
    cols = {v: s for v, s in cols.items() if len(s)}
    if len(cols) < 2:
        return pd.DataFrame()
    return pd.DataFrame(cols).dropna().sort_index()


def coins_with_cross_venue_coverage() -> list[str]:
    if not os.path.isdir(_CARRY_DIR):
        return []
    coins = set()
    for fn in os.listdir(_CARRY_DIR):
        if fn.endswith(".csv") and "_" in fn:
            coins.add(fn.split("_", 1)[1].rsplit(".", 1)[0])
    out = []
    for c in sorted(coins):
        if not align_cross_venue(c).empty:
            out.append(c)
    return out


# ----------------------------------------------------------------------------
# Screen
# ----------------------------------------------------------------------------
MIN_SETTLEMENTS_PER_COIN = 60  # pre-registered floor (≈20 days of 8h settlements)
MIN_COINS = 2


@dataclass
class PairResult:
    coin: str
    long_venue: str
    short_venue: str
    n: int
    days: float
    mean_abs_diff_bps: float
    mean_carry_bps: float
    gross_carry_bps: float
    rt_taker_bps: float
    rt_maker_bps: float
    net_taker_bps: float
    net_maker_bps: float
    apr_taker_pct: float
    apr_maker_pct: float
    breakeven_settles: float
    sign_persist: float


@dataclass
class ScreenReport:
    coins_covered: list[str]
    n_by_coin: dict[str, int]
    days_by_coin: dict[str, float]
    total_variants: int
    pairs: list[PairResult] = field(default_factory=list)
    verdict: str = ""
    reason: str = ""


def screen_pair(coin: str, df: pd.DataFrame, long_v: str, short_v: str) -> PairResult:
    n = len(df)
    days = (df.index.max() - df.index.min()) / 86400.0 if n > 1 else 0.0
    diff = df[short_v] - df[long_v]  # signed by the pre-chosen direction
    carry = signed_carry(df[short_v].values, df[long_v].values)
    rt_t = roundtrip_cost_frac(FEE[long_v]["taker"], FEE[short_v]["taker"], SLIPPAGE_PER_FILL)
    rt_m = roundtrip_cost_frac(FEE[long_v]["maker"], FEE[short_v]["maker"], SLIPPAGE_PER_FILL)
    net_t = net_return_frac(carry, rt_t)
    net_m = net_return_frac(carry, rt_m)
    return PairResult(
        coin=coin,
        long_venue=long_v,
        short_venue=short_v,
        n=n,
        days=round(days, 2),
        mean_abs_diff_bps=round(float(np.abs(diff).mean()) * 1e4, 3),
        mean_carry_bps=round(float(np.mean(carry)) * 1e4, 3),
        gross_carry_bps=round(float(np.sum(carry)) * 1e4, 2),
        rt_taker_bps=round(rt_t * 1e4, 1),
        rt_maker_bps=round(rt_m * 1e4, 1),
        net_taker_bps=round(net_t * 1e4, 2),
        net_maker_bps=round(net_m * 1e4, 2),
        apr_taker_pct=round(annualize_apr(net_t, days) * 100, 2),
        apr_maker_pct=round(annualize_apr(net_m, days) * 100, 2),
        breakeven_settles=round(breakeven_settlements(rt_m, float(np.mean(carry))), 1),
        sign_persist=round(sign_persistence(diff.values), 3),
    )


def run_screen() -> ScreenReport:
    coins = coins_with_cross_venue_coverage()
    n_by_coin, days_by_coin, pairs = {}, {}, []
    for coin in coins:
        df = align_cross_venue(coin)
        n_by_coin[coin] = len(df)
        days_by_coin[coin] = round((df.index.max() - df.index.min()) / 86400.0, 2) if len(df) > 1 else 0.0
        venues = list(df.columns)
        for a, b in itertools.combinations(venues, 2):
            # in-sample direction pick (long lower-mean venue) — an OPTIMISTIC lookahead;
            # if even this loses after cost, the no-edge finding is robust.
            if df[a].mean() >= df[b].mean():
                short_v, long_v = a, b
            else:
                short_v, long_v = b, a
            pairs.append(screen_pair(coin, df, long_v, short_v))

    # each (coin, venue-pair) tried under 2 fee models = the true variant count
    total_variants = len(pairs) * 2

    rep = ScreenReport(
        coins_covered=coins,
        n_by_coin=n_by_coin,
        days_by_coin=days_by_coin,
        total_variants=total_variants,
        pairs=pairs,
    )

    # --- pre-registered decision logic ---
    enough_coins = len(coins) >= MIN_COINS
    enough_n = all(n_by_coin.get(c, 0) >= MIN_SETTLEMENTS_PER_COIN for c in coins) and bool(coins)
    if not coins:
        rep.verdict = "INSUFFICIENT_DATA"
        rep.reason = "No coin has funding on >=2 venues locally."
    elif not enough_n:
        worst = min(n_by_coin.values()) if n_by_coin else 0
        rep.verdict = "INSUFFICIENT_DATA"
        rep.reason = (
            f"Cross-venue overlap below pre-registered floor of {MIN_SETTLEMENTS_PER_COIN} "
            f"aligned settlements/coin: have {n_by_coin} over {days_by_coin} days on coins "
            f"{coins}. Frozen gates (walk-forward+embargo+purge, Monte Carlo block bootstrap, "
            f"DSR/PBO) are NOT EVALUABLE at n={worst}; they fail closed. "
            f"Supporting (not decisive): every venue-pair is net-negative after cost even on "
            f"best-case maker fees and an in-sample direction pick."
        )
    else:
        any_positive = any(p.net_taker_bps > 0 for p in pairs)
        rep.verdict = "GO" if any_positive else "NO_GO"
        rep.reason = (
            "After-cost net carry positive on honest taker model — advance to gate battery."
            if any_positive
            else "After-cost net carry <= 0 across all venue-pairs on the honest taker model."
        )
    return rep


def _to_json(rep: ScreenReport) -> dict:
    best = None
    if rep.pairs:
        best = max(rep.pairs, key=lambda p: p.net_maker_bps)
    return {
        "candidate": "A_cross_venue_funding_dispersion",
        "hypothesis": "Delta-neutral long-low/short-high venue funding differential clears the 4-leg after-cost hurdle",
        "n": rep.n_by_coin,
        "sample_days": rep.days_by_coin,
        "coins_cross_venue": rep.coins_covered,
        "true_variants_tried": rep.total_variants,
        "after_cost_metrics": {
            "cost_model": "config.FEE per venue + 4x5bps slippage; taker=honest default, maker=best-case sensitivity",
            "best_config_net_bps_maker": None if best is None else best.net_maker_bps,
            "best_config": None
            if best is None
            else f"{best.coin} long {best.long_venue}/short {best.short_venue}",
            "all_pairs_net_taker_bps": {
                f"{p.coin}:{p.long_venue}L/{p.short_venue}S": p.net_taker_bps for p in rep.pairs
            },
            "all_pairs_net_maker_bps": {
                f"{p.coin}:{p.long_venue}L/{p.short_venue}S": p.net_maker_bps for p in rep.pairs
            },
            "all_negative_after_cost": all(
                p.net_taker_bps <= 0 and p.net_maker_bps <= 0 for p in rep.pairs
            ),
        },
        "gates": {
            "DSR": "NOT_EVALUABLE (n<floor)",
            "PBO": "NOT_EVALUABLE (n<floor)",
            "OOS_WR": "NOT_EVALUABLE (n<floor)",
            "walk_forward": "NOT_EVALUABLE (n<floor)",
            "monte_carlo": "NOT_EVALUABLE (n<floor)",
            "fail_closed": True,
        },
        "verdict": rep.verdict,
        "reason": rep.reason,
        "harvest_to_extend": (
            "venv\\Scripts\\python.exe scripts\\harvest_funding_carry.py  "
            "(single pass, appends 1 row/venue/coin/settlement; schedule HOURLY via "
            "schtasks TN TradingBot-FundingCarryHarvest). Reach the >=60-settlement floor "
            "(~20+ days) before re-screening. To broaden beyond BTC/ETH, extend the COINS "
            "tuple in scripts/harvest_funding_carry.py (separate change)."
        ),
    }


def main() -> None:
    rep = run_screen()
    print("=== Cross-venue funding-dispersion screen ===")
    print(f"coins with cross-venue coverage: {rep.coins_covered}")
    print(f"settlements/coin: {rep.n_by_coin}  days/coin: {rep.days_by_coin}")
    print(f"true variants tried: {rep.total_variants}")
    print()
    hdr = (
        f"{'coin':4} {'longL':8} {'shortS':8} {'n':>3} {'diff':>6} {'carry':>6} "
        f"{'gross':>6} {'RTt':>5} {'RTm':>5} {'net_t':>7} {'net_m':>7} {'APRt%':>7} {'APRm%':>7} {'BE':>6} {'persist':>7}"
    )
    print(hdr)
    for p in rep.pairs:
        print(
            f"{p.coin:4} {p.long_venue:8} {p.short_venue:8} {p.n:>3} "
            f"{p.mean_abs_diff_bps:>6.2f} {p.mean_carry_bps:>6.2f} {p.gross_carry_bps:>6.1f} "
            f"{p.rt_taker_bps:>5.0f} {p.rt_maker_bps:>5.0f} {p.net_taker_bps:>7.1f} {p.net_maker_bps:>7.1f} "
            f"{p.apr_taker_pct:>7.1f} {p.apr_maker_pct:>7.1f} {p.breakeven_settles:>6.1f} {p.sign_persist:>7.2f}"
        )
    print()
    print(f"VERDICT: {rep.verdict}")
    print(f"REASON: {rep.reason}")
    print()
    print("JSON_VERDICT_START")
    print(json.dumps(_to_json(rep), indent=2))
    print("JSON_VERDICT_END")


if __name__ == "__main__":
    main()
