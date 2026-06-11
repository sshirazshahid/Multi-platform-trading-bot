"""core/portfolio_risk.py — Portfolio Expected-Shortfall soft sizing cap.

2026-06-11 (owner: make the bot "technically/mathematically/financially
advanced"). Replaces the static notional-bucket correlation caps with a
real portfolio risk measure:

    EWMA (RiskMetrics, lambda=0.94) covariance over 1h returns of the
    open book's bases  ->  parametric Expected Shortfall in USD over a
    holding-period horizon  ->  SOFT size taper on new entries when the
    projected book ES exceeds a budget (fraction of equity).

Signed USD legs net properly: long-BTC + short-ETH partially offsets,
which static buckets (assuming zero cross-group correlation — measured
BTC/DOT EWMA rho ~0.85) cannot represent.

RISK infrastructure only — no alpha claim. Fail-OPEN everywhere: any
data gap or error returns factor 1.0 and the trade proceeds at chain
size. Never blocks (UNBLOCK stance); taper floor 0.25.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np
import pandas as pd
from loguru import logger

# phi(Phi^-1(q)) / (1-q) — parametric normal ES multipliers (verified
# numerically; a table avoids a scipy dependency for the inverse CDF).
ES_MULT = {0.95: 2.0627, 0.975: 2.3378, 0.99: 2.6652}

DEFAULTS = {
    "enabled": True,
    "q": 0.975,
    "lambda": 0.94,
    "horizon_hours": 4.0,
    "budget_pct": 0.005,
    "floor": 0.25,
    "bars": 240,
    "min_bars": 60,
    "cache_ttl_sec": 1800,
}


def ewma_cov(returns: pd.DataFrame, lam: float = 0.94) -> np.ndarray:
    """RiskMetrics zero-mean EWMA covariance. Rows oldest-first.

    Sigma = X' diag(w) X with w_k = (1-lam) * lam^(T-1-k), normalized to
    sum to 1 (finite-sample correction)."""
    X = returns.to_numpy(dtype=float)
    t = X.shape[0]
    w = (1.0 - lam) * lam ** np.arange(t - 1, -1, -1)
    w = w / w.sum()
    return X.T @ (X * w[:, None])


@dataclass
class ESDecision:
    """Outcome of one ES evaluation. ok=False means a fail-open path."""
    factor: float = 1.0
    es_open_usd: Optional[float] = None
    es_projected_usd: Optional[float] = None
    budget_usd: Optional[float] = None
    ok: bool = False
    reason: str = ""
    legs: int = 0
    legs_dropped: int = 0
    ts: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        def _r(v):
            return None if v is None else round(float(v), 2)
        return {
            "factor": round(float(self.factor), 3),
            "es_open_usd": _r(self.es_open_usd),
            "es_projected_usd": _r(self.es_projected_usd),
            "budget_usd": _r(self.budget_usd),
            "ok": self.ok,
            "reason": self.reason[:120],
            "legs": self.legs,
            "legs_dropped": self.legs_dropped,
            "ts": self.ts,
        }


class PortfolioRisk:
    """EWMA-covariance parametric ES of the open book, with soft taper."""

    def __init__(self, exchanges: Optional[dict] = None,
                 cfg: Optional[dict] = None):
        self._exchanges = exchanges or {}
        merged = dict(DEFAULTS)
        merged.update(cfg or {})
        self._cfg = merged
        self._closes: Dict[str, pd.Series] = {}
        self._closes_at: Dict[str, float] = {}
        self._last: Optional[ESDecision] = None

    # ── data ──────────────────────────────────────────────────────────

    def _fetch_closes(self, base: str):
        """1h close Series for `base` (ts-ms index), TTL-cached.
        Perp first, spot fallback, across all venues. None on failure."""
        now = time.time()
        ttl = float(self._cfg.get("cache_ttl_sec", 1800))
        if base in self._closes and now - self._closes_at.get(base, 0) < ttl:
            return self._closes[base]
        bars = int(self._cfg.get("bars", 240))
        min_bars = int(self._cfg.get("min_bars", 60))
        raw = None
        for ex in (self._exchanges or {}).values():
            for sym, mt in ((f"{base}/USDT:USDT", "futures"),
                            (f"{base}/USDT", "spot")):
                try:
                    raw = ex.fetch_ohlcv(sym, "1h", limit=bars, market_type=mt)
                except Exception:
                    raw = None
                if raw and len(raw) >= min_bars:
                    break
            if raw and len(raw) >= min_bars:
                break
        if not raw or len(raw) < min_bars:
            return None
        s = pd.Series([float(c[4]) for c in raw],
                      index=[int(c[0]) for c in raw], dtype=float)
        s = s[~s.index.duplicated(keep="last")].sort_index()
        self._closes[base] = s
        self._closes_at[base] = now
        return s

    def _aligned_returns(self, bases):
        """Inner-joined 1h returns DataFrame for `bases`.
        Returns (DataFrame, n_dropped). Empty frame on failure."""
        frames = {}
        dropped = 0
        for b in bases:
            s = self._fetch_closes(b)
            if s is None:
                dropped += 1
                continue
            frames[b] = s
        if not frames:
            return pd.DataFrame(), dropped
        df = pd.DataFrame(frames).dropna()
        rets = df.pct_change().dropna().tail(int(self._cfg.get("bars", 240)))
        if len(rets) < int(self._cfg.get("min_bars", 60)):
            return pd.DataFrame(), dropped + len(frames)
        return rets, dropped

    # ── core ──────────────────────────────────────────────────────────

    @staticmethod
    def _signed_legs(open_positions) -> Dict[str, float]:
        """{base: signed leveraged USD notional} from open positions.
        entry_price * size is already the leveraged notional (the
        correlation_manager convention); buy=+, sell=-."""
        legs: Dict[str, float] = {}
        for p in open_positions or []:
            try:
                sym = getattr(p, "symbol", None) or (
                    p.get("symbol", "") if isinstance(p, dict) else "")
                side = str(getattr(p, "side", None) or (
                    p.get("side", "") if isinstance(p, dict) else "")).lower()
                entry = float(getattr(p, "entry_price", None) or (
                    p.get("entry_price", 0) if isinstance(p, dict) else 0) or 0)
                size = float(getattr(p, "size", None) or (
                    p.get("size", 0) if isinstance(p, dict) else 0) or 0)
            except Exception:
                continue
            if not sym or entry <= 0 or size <= 0:
                continue
            base = sym.split("/")[0].split(":")[0].upper()
            sign = 1.0 if side == "buy" else -1.0
            legs[base] = legs.get(base, 0.0) + sign * entry * size
        return legs

    def _es_usd(self, cov, order, legs_map) -> float:
        """Parametric ES (USD) of signed legs over the configured horizon."""
        x = np.array([legs_map.get(b, 0.0) for b in order], dtype=float)
        var = float(x @ cov @ x)
        sigma = math.sqrt(max(var, 0.0))
        q = float(self._cfg.get("q", 0.975))
        mult = ES_MULT.get(q, ES_MULT[0.975])
        h = float(self._cfg.get("horizon_hours", 4.0))
        return sigma * mult * math.sqrt(max(h, 0.0))

    def evaluate_candidate(self, open_positions, cand_base: str,
                           cand_side: str, cand_notional_usd: float,
                           equity: float) -> ESDecision:
        """Soft taper factor for a candidate entry. Fail-open factor 1.0."""
        try:
            if not equity or float(equity) <= 0:
                d = ESDecision(reason="no_equity")
                self._last = d
                return d
            cand_base = str(cand_base).upper()
            legs = self._signed_legs(open_positions)
            sign = 1.0 if str(cand_side).lower() == "buy" else -1.0
            legs_all = dict(legs)
            legs_all[cand_base] = (legs.get(cand_base, 0.0)
                                   + sign * float(cand_notional_usd))
            bases = sorted(legs_all.keys())
            rets, dropped = self._aligned_returns(bases)
            if rets.empty or cand_base not in rets.columns:
                d = ESDecision(reason=f"no_data:{cand_base}",
                               legs=len(bases), legs_dropped=dropped)
                self._last = d
                return d
            order = list(rets.columns)
            cov = ewma_cov(rets, float(self._cfg.get("lambda", 0.94)))
            es_open = self._es_usd(cov, order, legs)
            es_proj = self._es_usd(cov, order, legs_all)
            budget = float(equity) * float(self._cfg.get("budget_pct", 0.005))
            if es_proj <= budget or es_proj <= 0:
                factor = 1.0
            else:
                factor = max(float(self._cfg.get("floor", 0.25)),
                             min(1.0, budget / es_proj))
            d = ESDecision(factor=factor, es_open_usd=es_open,
                           es_projected_usd=es_proj, budget_usd=budget,
                           ok=True, reason="ok",
                           legs=len(order), legs_dropped=dropped)
            self._last = d
            return d
        except Exception as e:
            logger.debug(f"[PortfolioES] evaluate_candidate failed: {e}")
            d = ESDecision(reason=f"error:{str(e)[:80]}")
            self._last = d
            return d

    def evaluate_book(self, open_positions, equity) -> ESDecision:
        """ES of the open book only — heartbeat/dashboard refresh."""
        try:
            legs = self._signed_legs(open_positions)
            budget = (float(equity) * float(self._cfg.get("budget_pct", 0.005))
                      if equity and float(equity) > 0 else None)
            if not legs:
                d = ESDecision(factor=1.0, es_open_usd=0.0,
                               es_projected_usd=0.0, budget_usd=budget,
                               ok=True, reason="empty_book")
                self._last = d
                return d
            bases = sorted(legs.keys())
            rets, dropped = self._aligned_returns(bases)
            if rets.empty:
                d = ESDecision(reason="no_data:book",
                               legs=len(bases), legs_dropped=dropped)
                self._last = d
                return d
            order = list(rets.columns)
            cov = ewma_cov(rets, float(self._cfg.get("lambda", 0.94)))
            es_open = self._es_usd(cov, order, legs)
            d = ESDecision(factor=1.0, es_open_usd=es_open,
                           es_projected_usd=es_open, budget_usd=budget,
                           ok=True, reason="ok",
                           legs=len(order), legs_dropped=dropped)
            self._last = d
            return d
        except Exception as e:
            logger.debug(f"[PortfolioES] evaluate_book failed: {e}")
            d = ESDecision(reason=f"error:{str(e)[:80]}")
            self._last = d
            return d

    def last_snapshot(self) -> Optional[dict]:
        return self._last.as_dict() if self._last else None
