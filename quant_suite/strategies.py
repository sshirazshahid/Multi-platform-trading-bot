"""
strategies.py - rule-based strategy registry (pure price/quant; no sentiment).
Strategies are selected by the HMM regime. Each entry declares regimes, risk,
status, an honest 'edge' note (from backtests), and a callable hook.
NOTE: implemented != profitable. Mean-reversion family (xsec/pca/pairs) tested
NEGATIVE in the current trending regime -> gated to RANGE and OFF by default.
Only confluence_trend has a positive validated edge right now.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import dca, execution, grid, kalman_pairs, pca_factor_neutral, rebalance, vpin, xsec_meanrev
from . import engine as E
from . import hmm_regime as H


@dataclass
class Strategy:
    name: str
    description: str
    regimes: list
    risk: str
    status: str  # "live" (implemented+validated) | "scaffold"
    edge: str  # honest backtest note
    fn: object = None
    input: str = "df"  # df | panel | pair | dict | none


REGISTRY: dict = {}


def register(s):
    REGISTRY[s.name] = s
    return s


register(
    Strategy(
        "confluence_trend",
        "Multi-factor + 4h regime-trend confluence",
        ["TREND_UP", "TREND_DOWN"],
        "moderate",
        "live",
        "POSITIVE: +0.75%/trade, PF 1.65 (validated)",
        lambda d: E.latest_signal(E.prep(d), "confluence"),
        "df",
    )
)
register(
    Strategy(
        "hmm_regime",
        "Hidden Markov statistical regime detector (gate)",
        ["*"],
        "n/a",
        "live",
        "tool (regime classifier)",
        lambda d: H.detect_regime(d),
        "df",
    )
)
register(
    Strategy(
        "xsec_meanrev",
        "Cross-sectional mean reversion (market-neutral L/S)",
        ["RANGE", "HIGH_VOL"],
        "moderate",
        "live",
        "NEGATIVE in trend (-16.7%); for RANGE regimes only, OFF by default",
        lambda p: xsec_meanrev.current_signals(p),
        "panel",
    )
)
register(
    Strategy(
        "pca_factor_neutral",
        "PCA factor-neutral L/S on return residuals",
        ["RANGE", "HIGH_VOL"],
        "moderate",
        "live",
        "NEGATIVE in trend (-9.1%); RANGE only, OFF by default",
        lambda p: pca_factor_neutral.residual_signals(p),
        "panel",
    )
)
register(
    Strategy(
        "kalman_pairs",
        "Kalman dynamic-hedge pairs (e.g. ETH/BTC) spread z-score",
        ["*"],
        "moderate",
        "live",
        "NEGATIVE on ETH/BTC this window (PF 0.29); needs cointegrated pair + range, OFF by default",
        lambda y, x: kalman_pairs.current(y, x),
        "pair",
    )
)
register(
    Strategy(
        "vpin_gate",
        "VPIN order-flow toxicity execution gate",
        ["*"],
        "n/a",
        "live",
        "tool (throttles entries on toxic flow)",
        lambda d: vpin.execution_gate(d),
        "df",
    )
)
register(
    Strategy(
        "grid",
        "Grid trading for sideways/range markets",
        ["RANGE"],
        "low",
        "live",
        "range-dependent; profits in range, bleeds in trend",
        lambda d: grid.grid_from_atr(d),
        "df",
    )
)
register(
    Strategy(
        "dca",
        "Dollar-cost-averaging accumulation (dip-weighted)",
        ["TREND_UP", "RANGE"],
        "low-moderate",
        "live",
        "accumulation (path-dependent)",
        lambda d: dca.backtest(d),
        "df",
    )
)
register(
    Strategy(
        "rebalance",
        "Portfolio rebalancing to target weights",
        ["*"],
        "low",
        "live",
        "tool (maintenance)",
        lambda h, t: rebalance.rebalance(h, t),
        "dict",
    )
)
register(
    Strategy(
        "twap_pov_exec",
        "TWAP/POV/iceberg execution planner (LOXM analog)",
        ["*"],
        "n/a",
        "live",
        "tool (slice scheduler; never places orders)",
        lambda side, qty: execution.plan(side, qty),
        "none",
    )
)
register(
    Strategy(
        "funding_carry",
        "Funding/carry-aware entries (real 8h funding + trend confirm; Pine #4 port)",
        ["*"],
        "moderate",
        "live",
        "NEGATIVE: 3y/31-major screen 2026-06-10 = NO_EDGE (all 6 variants IS mean "
        "-0.15..-0.42%/trade net, 0/6 FDR, best OOS -0.30%/trade DSR~0; "
        "reports/funding_carry_screen_2026-06-10.md). Fail-closed without funding frame; OFF",
        lambda d: E.latest_signal(E.prep(d), "carry"),
        "df",
    )
)


def catalog() -> list:
    return [
        {
            "name": s.name,
            "status": s.status,
            "edge": s.edge,
            "regimes": s.regimes,
            "risk": s.risk,
            "input": s.input,
            "desc": s.description,
        }
        for s in REGISTRY.values()
    ]


def select_for_regime(label: str) -> list:
    """LIVE *directional* strategies whose edge applies to the current regime
    (tools like hmm/vpin/exec/rebalance excluded; negative-edge ones excluded)."""
    pos = {"confluence_trend", "xsec_meanrev", "pca_factor_neutral", "grid", "dca", "kalman_pairs"}
    return [
        s.name
        for s in REGISTRY.values()
        if s.name in pos and ("*" in s.regimes or label in s.regimes)
    ]
