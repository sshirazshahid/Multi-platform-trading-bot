"""Bundle-MR probes — LOG-ONLY forward paper test of the 2026-07-19 cloud
bundle-test survivors (ZfadeProbeAgent cfg365 / Rsi2TrackerProbeAgent cfg226).

THIS IS NOT A PIPELINE GO — READ THIS BEFORE READING ANY RESULT.

Provenance: the owner's cloud evidence test (paper_bundle_test, prereg.md +
shortlist.json, 2026-07-19) swept ~432 bracketed regime-filtered
mean-reversion configs (families MR-B/MR-C) over 2023-01..2025-06 IS and
evaluated a pre-selected shortlist ONCE on 2025-07..2026-07 OOS. Textbook
mean-reversion variants sit inside the 0/40 independent-toolchain refutation
(2026-06-13); this bracketed family is external evidence, not a reopen.
  cfg365 (MR-C 4h z-fade, THE CANDIDATE): OOS profitable on all 3 venue fee
  models (PF ~1.22) BUT FAILED gate G2 — OOS WR 70-71% sits ABOVE the frozen
  63-67 band — and is a 1-of-432 sweep survivor: plausible-UNCONFIRMED.
  cfg226 (MR-B 4h RSI-2, THE TRACKER): OOS WR in/near band (67-68%) but net
  NEGATIVE — kept solely to measure the band-vs-profit tension forward.
The probes exist ONLY because the owner explicitly directed implementing the
bundle report's forward paper test, mirroring the TsmomProbeAgent /
BreakoutProbeAgent precedent (2026-07-11). Expectation: NO-PROMOTE unless
forward evidence surprises. Promotion — if ever — is assessed PER ARM via the
frozen core/promotion_gate.py thresholds on >= 30 RESOLVED forward events
plus an explicit owner sign-off. Never here.

STRATEGY (frozen from the owner's cloud deliverables — this probe forward
tests THEIR configuration, not our reinterpretation; signal semantics mirror
paper_bundle_test/paper_trader.py::eval_signals EXACTLY):
  zfade_4h_cfg365 (MR-C): z-score of close over 20 bars crosses beyond
    +/-1.5, faded WITH the EMA200 trend side (price>EMA200 -> only LONG on
    z<-1.5; price<EMA200 -> only SHORT on z>+1.5).
    TP 1.0 x ATR(14), SL 2.4 x ATR(14), 12-bar time-stop.
  rsi2_4h_cfg226 (MR-B): RSI(2) < 10 with-trend long (price>EMA200); mirror
    short RSI(2) > 90 when price<EMA200.
    TP 0.8 x ATR(14), SL 2.0 x ATR(14), 12-bar time-stop.
- markets: BTC/ETH/SOL/BNB/XRP USDT linear perps, 4h bars, bybit data feed
  (the venue the TsmomProbeAgent pattern uses for data pulls)
- entry at the SIGNAL-BAR CLOSE (the owner's frozen spec); one position per
  (symbol, arm), no overlap, re-entry earliest one bar after the exit bar
- INDICATOR FIDELITY (deliberately mirrors the reference harness, even where
  it diverges from our other probes): ATR is the SIMPLE mean of the last 14
  true ranges (tr.rolling(14).mean() — NOT Wilder); EMA200 is
  ewm(span=200, adjust=False) with NO min_periods, warm-up guarded by the
  reference's len>=210 gate; RSI is the Wilder-ewm variant whose dn==0 case
  yields 50 (the reference's fillna(50)); zscore(20) uses ddof=1 std with
  std==0 -> no signal. History window ~260 bars mirrors the reference's
  klines limit=260.
- DISCLOSED DIVERGENCES from paper_trader.py: its maker-limit entry fill
  (cancel after 1 bar) and 4-open/venue exposure cap are its own equity-sim
  devices; per the owner's frozen spec this probe logs every (symbol, arm)
  signal with a signal-bar-close entry and lets the vetted resolver cost it
  (taker fees + slippage — a CONSERVATIVE stand-in for the maker-fill model).

TWO ARMS, TWO AGENT IDS: the promotion funnel keys lanes on
agent_id+timeframe and both arms are 4h, so each arm is its own class with
its own agent_id (ZfadeProbeAgent / Rsi2TrackerProbeAgent). Decision rows
carry model_version zfade_4h_cfg365_v1 / rsi2_4h_cfg226_v1.

SIZING IS NOTATIONAL ONLY: the bundle's own model — 3% of equity as notional
per position, 1x — is recorded so logged PnL is interpretable. No sizing
hook, no leverage, no order exists anywhere in this module.

FROZEN pre-outcome discriminating scores (the AUC promotion gate is
un-computable without one), frozen NOW before any outcome exists — NEVER
re-tune after outcomes accumulate; a new score is a new pre-registration:
  cfg365: score = tanh(|z_entry| / 3.0)
  cfg226: score = tanh((10 - RSI2) / 10) longs, tanh((RSI2 - 90) / 10) shorts

LOG-ONLY (charter, non-negotiable): these classes hold ONLY read-only
providers (OHLCV / market data / balance) plus the warehouse. They have no
reference to any order path and are structurally incapable of placing an
order.

Realized after-cost economics are produced by the vetted keystone resolver
(core/shadow_resolver.py) off the shadow_decisions rows these probes write.
This module performs NO PnL math on outcomes — custom PnL math in a probe is
a bug. closed_hint fields are OCCUPANCY BOOKKEEPING mirroring the resolver's
SL-first barrier logic; shadow_outcomes stays the only readable outcome. Per
the TP-probe precedent (core/agents/tp_probe_agent.py): never read a win- or
hit-rate here without the resolved shadow_outcomes.net_pnl next to it.
"""

from __future__ import annotations

import math
import time
import uuid
from typing import Callable, Optional, Tuple

from core.agents.probe_common import (
    ATR_LEN,
    accrue_funding,
    ensure_schema,
    eval_gate,
    insert_row,
    monitor_open_barriers,
    probe_tick,
    write_shadow_decision,
)

# ── Frozen bundle constants (prereg.md + shortlist.json cfg365/cfg226) ───────
# Editing any of these is a NEW pre-registration, not a tweak. Pinned by
# tests/test_bundle_mr_probes.py::test_frozen_bundle_constants.
SYMBOLS = (
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
)
TIMEFRAME = "4h"
BAR_S = 4 * 3600
EMA_TREND_BARS = 200  # EMA200 trend-side gate (filt='ema200')
Z_WINDOW = 20  # cfg365 z-score window
Z_ENTRY = 1.5  # cfg365 entry threshold (z beyond +/-1.5)
RSI_LEN = 2  # cfg226 RSI period
RSI_THR = 10.0  # cfg226 thresholds: <10 long / >90 short
TSTOP_BARS = 12  # both arms: 12-bar time-stop (= resolver horizon)
MIN_BARS = 210  # paper_trader.py warm-up gate (len(df) < 210 -> no signal)
FETCH_BARS = 260  # paper_trader.py klines limit=260 (EMA/RSI history window)
STAKE_FRAC = 0.03  # bundle sizing: 3% of equity as notional (NOTATIONAL)
SCORE_Z_SCALE = 3.0  # FROZEN pre-outcome; never re-tune post-outcome
SCORE_RSI_SCALE = 10.0  # FROZEN pre-outcome; never re-tune post-outcome

# Per-arm shadow_decisions.model_version strings — importable so a probe
# rename can never silently desync a reporter.
ZFADE_MODEL_VERSION = "zfade_4h_cfg365_v1"
RSI2_MODEL_VERSION = "rsi2_4h_cfg226_v1"


# ── Pure indicator math (mirrors paper_trader.py pandas formulas) ────────────
def bundle_ema_last(closes, n: int) -> Optional[float]:
    """Last value of ewm(span=n, adjust=False).mean() with NO min_periods —
    the reference's ema(); warm-up is its separate len>=210 gate."""
    if n <= 0 or not closes:
        return None
    alpha = 2.0 / (n + 1.0)
    e = float(closes[0])
    for c in closes[1:]:
        e = alpha * float(c) + (1.0 - alpha) * e
    return e


def bundle_rsi_last(closes, n: int) -> Optional[float]:
    """Last RSI(n), the reference's exact variant: Wilder ewm(alpha=1/n,
    adjust=False) over clipped diffs; dn==0 -> rs NaN -> fillna(50)."""
    if n <= 0 or len(closes) < 2:
        return None
    alpha = 1.0 / n
    up = dn = None
    prev = float(closes[0])
    for c in closes[1:]:
        c = float(c)
        d = c - prev
        u, v = max(d, 0.0), max(-d, 0.0)
        up = u if up is None else alpha * u + (1.0 - alpha) * up
        dn = v if dn is None else alpha * v + (1.0 - alpha) * dn
        prev = c
    if dn == 0:
        return 50.0  # the reference's fillna(50) semantics — fidelity over textbook
    return 100.0 - 100.0 / (1.0 + up / dn)


def sma_atr_last(candles, n: int = ATR_LEN) -> Optional[float]:
    """Last tr.rolling(n).mean() — the reference's SIMPLE-mean ATR (NOT
    Wilder). TR uses the previous close; None until n+1 candles exist."""
    if n <= 0 or len(candles) < n + 1:
        return None
    total = 0.0
    for i in range(len(candles) - n, len(candles)):
        try:
            high, low = float(candles[i][2]), float(candles[i][3])
            prev_close = float(candles[i - 1][4])
        except (IndexError, TypeError, ValueError):
            return None
        total += max(high - low, abs(high - prev_close), abs(low - prev_close))
    return total / float(n)


def zscore_last(closes, n: int = Z_WINDOW) -> Optional[float]:
    """Last (close - rolling(n).mean()) / rolling(n).std(ddof=1); None when
    history is short or std is 0 (the reference's replace(0, nan))."""
    if n <= 1 or len(closes) < n:
        return None
    window = [float(c) for c in closes[-n:]]
    mean = sum(window) / n
    var = sum((c - mean) ** 2 for c in window) / (n - 1)
    sd = math.sqrt(var)
    if sd <= 0 or not math.isfinite(sd):
        return None
    return (window[-1] - mean) / sd


def zfade_signal(z, *, close: float, trend, z_entry: float = Z_ENTRY) -> int:
    """cfg365: +1 fade a dip above EMA200 / -1 fade a pop below it / 0 flat."""
    if z is None or trend is None:
        return 0
    if z < -z_entry and close > trend:
        return 1
    if z > z_entry and close < trend:
        return -1
    return 0


def rsi2_signal(r, *, close: float, trend, thr: float = RSI_THR) -> int:
    """cfg226: +1 oversold above EMA200 / -1 overbought below it / 0 flat."""
    if r is None or trend is None:
        return 0
    if r < thr and close > trend:
        return 1
    if r > 100.0 - thr and close < trend:
        return -1
    return 0


def zfade_score(z) -> float:
    """FROZEN pre-outcome discriminating score: tanh(|z_entry| / 3.0)."""
    try:
        return math.tanh(abs(float(z)) / SCORE_Z_SCALE)
    except (TypeError, ValueError):
        return 0.0


def rsi2_score(r, sig: int) -> float:
    """FROZEN pre-outcome discriminating score: tanh((10 - RSI2) / 10) for
    longs, tanh((RSI2 - 90) / 10) for shorts."""
    try:
        r = float(r)
    except (TypeError, ValueError):
        return 0.0
    edge = (RSI_THR - r) if sig > 0 else (r - (100.0 - RSI_THR))
    return math.tanh(edge / SCORE_RSI_SCALE)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_bundle_mr_probe (
    proposal_id                TEXT PRIMARY KEY,
    symbol                     TEXT,
    venue                      TEXT,
    arm                        TEXT,    -- 'zfade_cfg365' | 'rsi2_cfg226'
    side                       TEXT,    -- 'buy' | 'sell'
    signal_bar_ts              INTEGER, -- open ts of the signal bar (= decision ts)
    entry_px                   REAL,    -- signal-bar close (frozen spec fill)
    signal_value               REAL,    -- ┐ as-of-entry signal inputs:
    ema_trend                  REAL,    -- │ z_entry | RSI2, EMA200,
    atr_entry                  REAL,    -- ┘ SMA-ATR(14); written once
    sl_px                      REAL,
    tp_px                      REAL,
    max_hold_bars              INTEGER, -- 12 (the bundle time-stop)
    stake_frac                 REAL,    -- 0.03 (bundle sizing, notational)
    notional_usd               REAL,
    units                      REAL,
    score                      REAL,    -- FROZEN per-arm score (see header)
    realized_funding_rate_sum  REAL,
    last_funding_bucket        INTEGER,
    closed_hint_ts             INTEGER, -- occupancy bookkeeping ONLY (see header)
    closed_hint_reason         TEXT,    -- stop_loss | take_profit | time
    created_ts                 INTEGER
);
CREATE INDEX IF NOT EXISTS idx_bundle_mr_probe_arm
    ON shadow_bundle_mr_probe(symbol, arm);

CREATE TABLE IF NOT EXISTS shadow_bundle_mr_mtm (
    proposal_id     TEXT,
    bar_ts          INTEGER,
    mark_px         REAL,
    unrealized_ret  REAL,     -- side-signed: >0 = position in profit
    PRIMARY KEY (proposal_id, bar_ts)
);
"""


class _BundleMrProbeBase:
    """Shared per-(symbol) 4h evaluator + log-only proposer for one bundle
    arm. Subclasses pin the arm's frozen constants and signal hook."""

    # Subclass contract (frozen per arm):
    name: str  # agent_id on decision rows
    model_version: str
    ARM: str  # shadow_bundle_mr_probe.arm value
    TP_ATR: float
    SL_ATR: float
    PID_PREFIX: str

    def __init__(
        self,
        *,
        warehouse,
        ohlcv_provider: Callable[[str, str, str, int], list],
        market_data_provider: Callable[[str, str], dict],
        account_balance_provider: Callable[[], float],
        now_fn: Callable[[], float] = time.time,
        venue: str = "bybit",
        symbols=SYMBOLS,
    ):
        self._wh = warehouse
        self._ohlcv = ohlcv_provider
        self._market_data = market_data_provider
        self._balance = account_balance_provider
        self._now = now_fn
        self._venue = venue
        self._symbols = tuple(symbols)
        # expected-bar no-op guards (probe_common eval_gate / monitor):
        # (symbol, tf) -> open ts of the last bar already evaluated/fetched.
        self._bar_seen: dict = {}
        self._mon_seen: dict = {}
        ensure_schema(self._wh, _SCHEMA)

    # ── public tick ──────────────────────────────────────────────────────
    def tick(self) -> dict:
        """One probe cycle: update MTM/hints on open holds FIRST, then
        evaluate the latest closed 4h bar per symbol. NEVER raises upward."""
        units = [(symbol, (symbol,)) for symbol in self._symbols]
        return probe_tick(self, units, log_tag=self.name)

    # ── arm hook ─────────────────────────────────────────────────────────
    def _signal(self, closes, trend) -> Tuple[int, Optional[float], float]:
        """(signal, as-of-entry signal value, frozen score). Overridden."""
        raise NotImplementedError

    # ── entry evaluation ─────────────────────────────────────────────────
    def _eval_entry(self, symbol: str, now: int) -> int:
        gate = eval_gate(
            self,
            symbol=symbol,
            tf=TIMEFRAME,
            bar_s=BAR_S,
            fetch_bars=FETCH_BARS,
            min_bars=MIN_BARS,
            last_sql="SELECT signal_bar_ts, closed_hint_ts, max_hold_bars "
            "FROM shadow_bundle_mr_probe WHERE symbol=? AND arm=? "
            "ORDER BY signal_bar_ts DESC LIMIT 1",
            last_params=(symbol, self.ARM),
            hold_col="max_hold_bars",
            now=now,
        )
        if gate is None:
            return 0
        candles, latest_ts = gate

        closes = [float(c[4]) for c in candles]
        atrv = sma_atr_last(candles, ATR_LEN)
        trend = bundle_ema_last(closes, EMA_TREND_BARS)
        if atrv is None or atrv <= 0 or trend is None:
            return 0  # the reference's np.isfinite(a) and a > 0 guard
        sig, sig_val, score = self._signal(closes, trend)
        if sig == 0 or sig_val is None:
            return 0  # flat state — a state signal is not logged bar-by-bar

        entry_px = closes[-1]  # signal-bar close (frozen spec)
        sl_px = entry_px - sig * self.SL_ATR * atrv
        tp_px = entry_px + sig * self.TP_ATR * atrv
        if sl_px <= 0 or tp_px <= 0:
            return 0
        try:
            equity = float(self._balance() or 0.0)
        except Exception:
            equity = 0.0
        notional = max(equity, 0.0) * STAKE_FRAC
        units = notional / entry_px if entry_px > 0 else 0.0
        side = "buy" if sig > 0 else "sell"
        pid = f"{self.PID_PREFIX}{uuid.uuid4().hex[:10]}"

        # One shadow_decisions row the keystone resolver replays into
        # shadow_outcomes: SL-first, fees + slippage, censoring-guarded time
        # exit at the 12-bar time-stop. sim_pnl stays NULL — the resolver
        # owns the after-cost net.
        write_shadow_decision(
            self._wh,
            ts=latest_ts,
            model_version=self.model_version,
            symbol=symbol,
            side=side,
            agent_id=self.name,
            proposal_id=pid,
            notional=notional,
            entry_px=entry_px,
            sl_px=sl_px,
            tp_px=tp_px,
            venue=self._venue,
            timeframe=TIMEFRAME,
            horizon_bars=TSTOP_BARS,
        )
        row = {
            "proposal_id": pid,
            "symbol": symbol,
            "venue": self._venue,
            "arm": self.ARM,
            "side": side,
            "signal_bar_ts": int(latest_ts),
            "entry_px": float(entry_px),
            "signal_value": float(sig_val),
            "ema_trend": float(trend),
            "atr_entry": float(atrv),
            "sl_px": float(sl_px),
            "tp_px": float(tp_px),
            "max_hold_bars": int(TSTOP_BARS),
            "stake_frac": float(STAKE_FRAC),
            "notional_usd": float(notional),
            "units": float(units),
            "score": float(score),
            "realized_funding_rate_sum": 0.0,
            "last_funding_bucket": None,
            "closed_hint_ts": None,
            "closed_hint_reason": None,
            "created_ts": int(now),
        }
        # Plain INSERT: an entry row is written exactly once, never replaced.
        insert_row(self._wh, "shadow_bundle_mr_probe", row)
        return 1

    # ── monitoring: per-bar MTM + occupancy hints + funding ──────────────
    def _monitor_open(self, now: int) -> int:
        return monitor_open_barriers(
            self,
            now,
            probe_table="shadow_bundle_mr_probe",
            mtm_table="shadow_bundle_mr_mtm",
            rows_sql=(
                "SELECT proposal_id, symbol, side, signal_bar_ts, entry_px, "
                "sl_px, tp_px, max_hold_bars, last_funding_bucket, "
                "realized_funding_rate_sum FROM shadow_bundle_mr_probe "
                f"WHERE closed_hint_ts IS NULL AND arm='{self.ARM}'"
            ),
            frame_of=lambda r: (TIMEFRAME, BAR_S, int(r["max_hold_bars"])),
        )

    def _accrue_funding(self, row: dict, now: int) -> None:
        """Book the current funding print once per 8h settlement bucket while
        the position is open. Missing funding is never guessed."""
        accrue_funding(
            self._wh,
            table="shadow_bundle_mr_probe",
            market_data=self._market_data,
            venue=self._venue,
            row=row,
            now=now,
        )


class ZfadeProbeAgent(_BundleMrProbeBase):
    """cfg365 CANDIDATE arm (MR-C 4h z-fade). G2-FAIL + 1-of-432 survivor —
    plausible-unconfirmed, NOT a GO. See module header."""

    name = "ZfadeProbeAgent"
    model_version = ZFADE_MODEL_VERSION
    ARM = "zfade_cfg365"
    TP_ATR = 1.0
    SL_ATR = 2.4
    PID_PREFIX = "zf-"

    def _signal(self, closes, trend) -> Tuple[int, Optional[float], float]:
        z = zscore_last(closes, Z_WINDOW)
        sig = zfade_signal(z, close=closes[-1], trend=trend)
        return sig, z, zfade_score(z) if sig else 0.0


class Rsi2TrackerProbeAgent(_BundleMrProbeBase):
    """cfg226 TRACKER arm (MR-B 4h RSI-2). In/near band but net NEGATIVE OOS —
    tracks the band-vs-profit tension, NOT a GO. See module header."""

    name = "Rsi2TrackerProbeAgent"
    model_version = RSI2_MODEL_VERSION
    ARM = "rsi2_cfg226"
    TP_ATR = 0.8
    SL_ATR = 2.0
    PID_PREFIX = "r2-"

    def _signal(self, closes, trend) -> Tuple[int, Optional[float], float]:
        r = bundle_rsi_last(closes, RSI_LEN)
        sig = rsi2_signal(r, close=closes[-1], trend=trend)
        return sig, r, rsi2_score(r, sig) if sig else 0.0
