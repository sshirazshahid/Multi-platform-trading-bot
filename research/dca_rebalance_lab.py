"""dca_rebalance_lab.py — offline, deterministic backtest lab for the two
evidence-backed SPOT sleeves: dollar-cost averaging (DCA) and threshold
rebalancing.

Why this exists
===============
The deep-research passes (2026-06-20) found that, unlike the directional /
chart-pattern strategies (all NO_EDGE after costs), DCA and periodic rebalancing
have genuine *risk-adjusted* support. This module lets you measure that on any
price series WITHOUT exchange API keys or network: it is pure-Python, takes plain
close-price lists, models realistic fees + slippage, and compares each sleeve to
the honest baseline (lump-sum / buy-and-hold).

It is intentionally free of bot/exchange/ccxt imports so it is fully unit-testable
(see tests/test_dca_rebalance_lab.py) and safe to run anywhere. Fee defaults match
the live DCAStrategy (0.1% spot taker, strategies/dca_strategy.py).

Honesty note: a backtest is necessary but NOT sufficient evidence. Before any real
capital, run multiple regimes (bull/bear/chop), out-of-sample windows, and a
Monte-Carlo reshuffle, and remember spot DCA's edge is *lower risk*, not a profit
guarantee — it still loses money if the asset trends down over the horizon.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

try:
    from research._stats import _percentiles, resample_blocks
except ImportError:  # run as a script (python research/dca_rebalance_lab.py)
    from _stats import _percentiles, resample_blocks

DEFAULT_FEE_PCT = 0.001  # 0.1% spot taker, matches strategies/dca_strategy.py
DEFAULT_SLIPPAGE_PCT = 0.0005  # 5 bps per fill, conservative for liquid spot


@dataclass
class SimResult:
    """Outcome of one simulation, with an equity curve and after-cost metrics."""

    label: str
    invested: float  # total cash actually deployed
    final_value: float  # portfolio value at the last bar (coins + cash)
    total_return_pct: float  # final_value / invested - 1, in %
    max_drawdown_pct: float  # worst peak-to-trough of the equity curve, in %
    sharpe: float  # annualized Sharpe of per-bar equity returns
    n_trades: int  # number of fee-paying transactions
    fees_paid: float
    equity_curve: list[float] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


# ── metric helpers ────────────────────────────────────────────────────


def _max_drawdown_pct(curve: list[float]) -> float:
    """Worst peak-to-trough decline of an equity curve, as a positive percent."""
    peak = -math.inf
    mdd = 0.0
    for v in curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > mdd:
                mdd = dd
    return mdd * 100.0


def _sharpe(curve: list[float], periods_per_year: int) -> float:
    """Annualized Sharpe from per-bar returns of an equity curve (rf=0)."""
    rets = []
    for i in range(1, len(curve)):
        prev = curve[i - 1]
        if prev > 0:
            rets.append(curve[i] / prev - 1.0)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(periods_per_year)


def _cost_mult_buy(fee_pct: float, slippage_pct: float) -> float:
    """Fraction of spent cash that becomes asset value after costs."""
    return max(0.0, 1.0 - fee_pct - slippage_pct)


def _validate(prices: list[float]) -> None:
    if not prices or len(prices) < 2:
        raise ValueError("need at least 2 prices")
    if any(p <= 0 for p in prices):
        raise ValueError("prices must be positive")


# ── single-asset sleeves ──────────────────────────────────────────────


def simulate_lump_sum(
    prices,
    capital=1000.0,
    fee_pct=DEFAULT_FEE_PCT,
    slippage_pct=DEFAULT_SLIPPAGE_PCT,
    periods_per_year=365,
) -> SimResult:
    """Deploy all capital at bar 0 and hold (the honest baseline / HODL)."""
    _validate(prices)
    eff = capital * _cost_mult_buy(fee_pct, slippage_pct)
    coins = eff / prices[0]
    curve = [coins * p for p in prices]
    return SimResult(
        label="lump_sum_hold",
        invested=capital,
        final_value=curve[-1],
        total_return_pct=(curve[-1] / capital - 1.0) * 100.0,
        max_drawdown_pct=_max_drawdown_pct(curve),
        sharpe=_sharpe(curve, periods_per_year),
        n_trades=1,
        fees_paid=capital * (fee_pct + slippage_pct),
        equity_curve=curve,
        extra={"coins": coins, "avg_cost": capital / coins if coins else 0.0},
    )


def simulate_dca(
    prices,
    capital=1000.0,
    interval_bars=1,
    fee_pct=DEFAULT_FEE_PCT,
    slippage_pct=DEFAULT_SLIPPAGE_PCT,
    dip_threshold=None,
    dip_multiplier=1.0,
    periods_per_year=365,
) -> SimResult:
    """Dollar-cost average: spend equal installments every `interval_bars`.

    Total deployed never exceeds `capital`. If `dip_threshold` is set (e.g. 0.05),
    a buy whose price is >= that fraction below the previous buy price spends
    `dip_multiplier`x the base installment, drawn from remaining cash (front-
    loading on dips, as the live DCAStrategy does). Uninvested cash earns nothing.
    """
    _validate(prices)
    buy_bars = list(range(0, len(prices), max(1, interval_bars)))
    base_amount = capital / len(buy_bars)
    cash = capital
    coins = 0.0
    fees_paid = 0.0
    n_trades = 0
    last_buy_price = None
    mult = _cost_mult_buy(fee_pct, slippage_pct)
    buy_set = set(buy_bars)
    curve = []
    for i, price in enumerate(prices):
        if i in buy_set and cash > 1e-12:
            amount = base_amount
            if (
                dip_threshold is not None
                and last_buy_price is not None
                and price <= last_buy_price * (1.0 - dip_threshold)
            ):
                amount = base_amount * dip_multiplier
            amount = min(amount, cash)
            coins += (amount * mult) / price
            fees_paid += amount * (fee_pct + slippage_pct)
            cash -= amount
            last_buy_price = price
            n_trades += 1
        curve.append(coins * price + cash)
    invested = capital - cash  # cash never spent (short series) isn't "invested"
    final_value = curve[-1]
    avg_cost = (invested / coins) if coins else 0.0
    return SimResult(
        label="dca",
        invested=invested,
        final_value=final_value,
        total_return_pct=((final_value / capital) - 1.0) * 100.0,
        max_drawdown_pct=_max_drawdown_pct(curve),
        sharpe=_sharpe(curve, periods_per_year),
        n_trades=n_trades,
        fees_paid=fees_paid,
        equity_curve=curve,
        extra={"coins": coins, "avg_cost": avg_cost, "uninvested_cash": cash, "n_buys": n_trades},
    )


# ── Monte-Carlo robustness (moving-block bootstrap of returns) ────────


def _log_returns(prices) -> list[float]:
    return [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]


def _bootstrap_path(p0: float, rets: list[float], n: int, block: int, rng) -> list[float]:
    """Build one synthetic price path of length n via a moving-block bootstrap
    of historical log-returns, compounding from p0. Preserves short-horizon
    autocorrelation via `block`."""
    if not rets or n < 2:
        return [p0] * max(1, n)
    out = resample_blocks(rets, n - 1, block, rng)
    path = [p0]
    for r in out:
        path.append(path[-1] * math.exp(r))
    return path


_SIMS = {"dca": simulate_dca, "lump_sum": simulate_lump_sum}


def monte_carlo(
    prices, strategy="dca", n_paths=1000, block=20, seed=0, periods_per_year=365, **sim_kw
) -> dict:
    """Moving-block-bootstrap Monte-Carlo robustness for a single-asset sleeve.

    Resamples blocks of historical log-returns into `n_paths` synthetic price
    paths, runs `strategy` ('dca' or 'lump_sum') on each, and returns percentile
    bands of total return %, max drawdown %, and Sharpe. Answers "how much does
    the headline number depend on the one path history happened to take?".
    Deterministic given `seed`. Pass strategy kwargs (capital, interval_bars,
    dip_*) via **sim_kw — but only those the chosen strategy accepts.
    """
    _validate(prices)
    if strategy not in _SIMS:
        raise ValueError(f"unknown strategy '{strategy}'; use {list(_SIMS)}")
    if n_paths < 1:
        raise ValueError("n_paths must be >= 1")
    if block < 1:
        raise ValueError("block must be >= 1")
    sim = _SIMS[strategy]
    rets = _log_returns(prices)
    rng = random.Random(seed)
    n = len(prices)
    ret_pcts, dds, sharpes = [], [], []
    for _ in range(n_paths):
        path = _bootstrap_path(prices[0], rets, n, block, rng)
        r = sim(path, periods_per_year=periods_per_year, **sim_kw)
        ret_pcts.append(r.total_return_pct)
        dds.append(r.max_drawdown_pct)
        sharpes.append(r.sharpe)
    return {
        "strategy": strategy,
        "n_paths": n_paths,
        "block": block,
        "return_pct": _percentiles(ret_pcts),
        "max_drawdown_pct": _percentiles(dds),
        "sharpe": _percentiles(sharpes),
    }


# ── multi-asset basket ────────────────────────────────────────────────


def _basket_validate(price_series: dict, targets: dict) -> int:
    if not price_series or not targets:
        raise ValueError("price_series and targets required")
    if set(price_series) != set(targets):
        raise ValueError("price_series and targets must cover the same assets")
    if abs(sum(targets.values()) - 1.0) > 1e-6:
        raise ValueError("target weights must sum to 1.0")
    lengths = {len(v) for v in price_series.values()}
    if len(lengths) != 1:
        raise ValueError("all asset price series must be the same length")
    n = lengths.pop()
    if n < 2:
        raise ValueError("need at least 2 bars")
    return n


def _basket_value(coins: dict, prices_at: dict) -> float:
    return sum(coins[a] * prices_at[a] for a in coins)


def simulate_buy_and_hold_basket(
    price_series,
    targets,
    capital=1000.0,
    fee_pct=DEFAULT_FEE_PCT,
    slippage_pct=DEFAULT_SLIPPAGE_PCT,
    periods_per_year=365,
) -> SimResult:
    """Buy the target weights once at bar 0 and never rebalance (basket HODL)."""
    n = _basket_validate(price_series, targets)
    mult = _cost_mult_buy(fee_pct, slippage_pct)
    coins = {a: (capital * targets[a] * mult) / price_series[a][0] for a in targets}
    curve = [_basket_value(coins, {a: price_series[a][i] for a in targets}) for i in range(n)]
    return SimResult(
        label="basket_hold",
        invested=capital,
        final_value=curve[-1],
        total_return_pct=(curve[-1] / capital - 1.0) * 100.0,
        max_drawdown_pct=_max_drawdown_pct(curve),
        sharpe=_sharpe(curve, periods_per_year),
        n_trades=len(targets),
        fees_paid=capital * (fee_pct + slippage_pct),
        equity_curve=curve,
        extra={"final_coins": coins},
    )


def simulate_threshold_rebalance(
    price_series,
    targets,
    capital=1000.0,
    threshold=0.05,
    fee_pct=DEFAULT_FEE_PCT,
    slippage_pct=DEFAULT_SLIPPAGE_PCT,
    periods_per_year=365,
) -> SimResult:
    """Rebalance a basket back to `targets` whenever any asset's weight drifts
    more than `threshold` (absolute) from target. Fees+slippage charged on the
    traded notional each rebalance (the "rebalancing premium" net of cost)."""
    n = _basket_validate(price_series, targets)
    mult = _cost_mult_buy(fee_pct, slippage_pct)
    coins = {a: (capital * targets[a] * mult) / price_series[a][0] for a in targets}
    fees_paid = capital * (fee_pct + slippage_pct)
    n_trades = len(targets)
    curve = []
    for i in range(n):
        px = {a: price_series[a][i] for a in targets}
        total = _basket_value(coins, px)
        curve.append(total)
        if total <= 0:
            continue
        drift = {a: (coins[a] * px[a] / total) - targets[a] for a in targets}
        if max(abs(d) for d in drift.values()) > threshold:
            # Rebalance to targets; charge cost on the notional that changes hands.
            traded_notional = sum(abs(targets[a] * total - coins[a] * px[a]) for a in targets) / 2.0
            cost = traded_notional * (fee_pct + slippage_pct)
            total_after = total - cost
            fees_paid += cost
            n_trades += 1
            coins = {a: (total_after * targets[a]) / px[a] for a in targets}
    final_value = curve[-1]
    return SimResult(
        label="threshold_rebalance",
        invested=capital,
        final_value=final_value,
        total_return_pct=(final_value / capital - 1.0) * 100.0,
        max_drawdown_pct=_max_drawdown_pct(curve),
        sharpe=_sharpe(curve, periods_per_year),
        n_trades=n_trades,
        fees_paid=fees_paid,
        equity_curve=curve,
        extra={"rebalances": n_trades - len(targets)},
    )


# ── convenience comparison + CLI ──────────────────────────────────────


def compare_single_asset(
    prices, capital=1000.0, interval_bars=1, periods_per_year=365, **kw
) -> dict:
    """Run lump-sum vs DCA on one asset; return {label: SimResult}."""
    return {
        "lump_sum_hold": simulate_lump_sum(
            prices, capital=capital, periods_per_year=periods_per_year, **kw
        ),
        "dca": simulate_dca(
            prices,
            capital=capital,
            interval_bars=interval_bars,
            periods_per_year=periods_per_year,
            **kw,
        ),
    }


def _fmt(r: SimResult) -> str:
    return (
        f"  {r.label:<20} return={r.total_return_pct:+7.2f}%  "
        f"maxDD={r.max_drawdown_pct:6.2f}%  Sharpe={r.sharpe:+5.2f}  "
        f"trades={r.n_trades}  fees={r.fees_paid:.2f}"
    )


if __name__ == "__main__":
    # Self-contained demo on a synthetic V-shaped market (fall then recover),
    # the textbook case where DCA's lower drawdown shows. Replace `prices` with
    # real closes (e.g. exported from an exchange / the CoinDesk MCP) for a real
    # study — and run multiple regimes + out-of-sample before trusting it.
    random.seed(1)
    fall = [100 * (1 - 0.01 * i) for i in range(40)]  # -40% drawdown
    recover = [fall[-1] * (1 + 0.012 * i) for i in range(60)]  # recovery
    prices = [p * (1 + random.uniform(-0.01, 0.01)) for p in fall + recover]

    print("Single-asset: lump-sum vs DCA (synthetic V-shape, daily) ---------")
    for r in compare_single_asset(prices, capital=1000.0, interval_bars=5).values():
        print(_fmt(r))

    print("\nBasket: buy-and-hold vs threshold rebalance ----------------------")
    series = {
        "A": prices,
        "B": [p * (1 + 0.3 * math.sin(i / 8)) for i, p in enumerate(prices)],
    }
    tgt = {"A": 0.5, "B": 0.5}
    print(_fmt(simulate_buy_and_hold_basket(series, tgt, capital=1000.0)))
    print(_fmt(simulate_threshold_rebalance(series, tgt, capital=1000.0, threshold=0.05)))
