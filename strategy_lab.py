"""strategy_lab.py — Offline research runner for experimental strategies.

A standalone CLI to backtest / paper-soak three experimental strategies
(asian_range, dow_swing, bb_squeeze) WITHOUT touching the running bot.

ISOLATION GUARANTEES (the live bot is running in PAPER right now):
  * --backtest loads OHLCV from data/ohlcv_cache/*.parquet OFFLINE (no network,
    no exchange). It is in-memory only and writes NO state files.
  * --paper uses a MINIMAL isolated simulator. It NEVER imports/calls
    core.order_manager or core.virtual_wallet, and it physically cannot write
    data/virtual_wallet.json or data/positions.json — its wallet/positions live
    under data/lab/ and it appends to journal/YYYY-MM-DD.md.

CAUSALITY: every generate_signal(df) uses ONLY rows 0..len(df)-1 (the last row
is the decision bar). The backtester feeds df.iloc[:i], so the strategy may read
the whole window it is given but never a future bar. Indicators used here are
rolling/expanding/ewm only — strictly causal.

These strategies are RESEARCH-ONLY. They are not wired into any live pool,
bot_engine, or main.py.

Usage:
  python strategy_lab.py --backtest --strategy bb_squeeze --symbol BTC/USDT --days 120
  python strategy_lab.py --paper   --strategy asian_range --symbol BTC/USDT --days 30
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from utils.indicators import atr, bb_squeeze, ema, rsi

# NOTE: core.backtester / core.walk_forward are imported LAZILY inside
# run_backtest() only. Importing them eagerly would trigger core/__init__.py,
# which pulls in core.order_manager + core.virtual_wallet. The --paper path
# must never load those modules, so we keep the top level clean of any core.*
# import and defer the backtest-only imports to the --backtest code path.

# ---------------------------------------------------------------------------- #
# Paths (all relative to repo root). The lab owns ONLY data/lab/ and journal/.  #
# ---------------------------------------------------------------------------- #
REPO_ROOT = Path(__file__).resolve().parent
CACHE_DIR = REPO_ROOT / "data" / "ohlcv_cache"
LAB_DIR = REPO_ROOT / "data" / "lab"
JOURNAL_DIR = REPO_ROOT / "journal"

# Hard guard: these singleton files belong to the RUNNING bot. The lab must
# never address them. We assert against accidental path construction.
_FORBIDDEN = {
    (REPO_ROOT / "data" / "virtual_wallet.json").resolve(),
    (REPO_ROOT / "data" / "positions.json").resolve(),
}

DEFAULT_TIMEFRAME = "1h"
BARS_PER_DAY = {"15m": 96, "1h": 24, "4h": 6, "1d": 1}


# ---------------------------------------------------------------------------- #
# Strategies — research-only, strictly causal generate_signal(df).             #
#                                                                              #
# Contract (matches BaseStrategy.generate_signal): given a DataFrame window     #
# whose LAST row is the decision bar, return a signal in {"buy","sell",None}.   #
# We return the bare signal (the backtester unwraps tuples or bare strings).    #
# ---------------------------------------------------------------------------- #


class _LabStrategy:
    """Lightweight strategy base — no OrderManager / RiskManager dependency.

    We deliberately do NOT subclass strategies.base_strategy.BaseStrategy: that
    base pulls in OrderManager (which can touch live state). The backtester only
    needs `.name` and `.generate_signal(df)`, so we provide exactly that.
    """

    name = "lab"

    def generate_signal(self, df: pd.DataFrame):  # pragma: no cover - abstract
        raise NotImplementedError

    # Backtester calls run() only via live paths; never invoked in the lab.
    def run(self, exchange, symbol):  # pragma: no cover - unused offline
        return None


class AsianRangeStrategy(_LabStrategy):
    """Asian-session range breakout.

    Track the high/low of the most RECENT Asian session (UTC 00:00-08:00) using a
    causal rolling window over session-masked bars. When a later (out-of-session)
    bar's close breaks above that recent session high -> buy; below it -> sell. A
    small RSI filter avoids breakouts into exhausted moves.

    Causal: the session extremes come from a trailing rolling window (only past
    and current bars); the breakout test reads only the current/last bar. Using a
    rolling — not all-time-cumulative — window keeps the range tight enough to
    actually break (a cumulative extreme widens forever and never triggers).
    """

    name = "asian_range"
    _SESSION_START = 0
    _SESSION_END = 7  # inclusive -> 00:00..07:59 UTC
    _WINDOW = 48  # ~2 days of bars: captures the latest session range

    def generate_signal(self, df: pd.DataFrame):
        if len(df) < 50:
            return None
        close = df["close"]
        # Derive UTC hour-of-day as a Series aligned to df (so .where/.iloc work
        # regardless of index type). Prefer the 'ts' column (unix seconds) the
        # lab loader always provides; fall back to a DatetimeIndex.
        if "ts" in df.columns:
            hours = ((df["ts"] // 3600) % 24).astype(int)
        elif isinstance(df.index, pd.DatetimeIndex):
            hours = pd.Series(df.index.hour, index=df.index)
        else:
            return None
        in_session = (hours >= self._SESSION_START) & (hours <= self._SESSION_END)
        # Rolling extremes over recent SESSION bars only (causal: past+current).
        sess_high = df["high"].where(in_session).rolling(self._WINDOW, min_periods=1).max()
        sess_low = df["low"].where(in_session).rolling(self._WINDOW, min_periods=1).min()

        hi = sess_high.iloc[-1]
        lo = sess_low.iloc[-1]
        if not np.isfinite(hi) or not np.isfinite(lo):
            return None
        # Only trade OUTSIDE the session (don't fade the range being built).
        if bool(in_session.iloc[-1]):
            return None

        c = float(close.iloc[-1])
        r = rsi(close, 14)
        rv = float(r.iloc[-1]) if np.isfinite(r.iloc[-1]) else 50.0

        if c > hi and rv < 75:
            return "buy"
        if c < lo and rv > 25:
            return "sell"
        return None


class DowSwingStrategy(_LabStrategy):
    """Dow-theory style swing follower.

    Trades in the direction of the higher-timeframe trend (fast EMA vs slow EMA),
    entering on a pullback that reasserts: price reclaims the fast EMA from below
    in an uptrend (buy) or loses it from above in a downtrend (sell).

    Causal: EMAs are ewm (only past data); the reclaim test compares the last two
    bars relative to the fast EMA at those bars.
    """

    name = "dow_swing"
    _FAST = 20
    _SLOW = 50

    def generate_signal(self, df: pd.DataFrame):
        if len(df) < max(self._SLOW + 5, 50):
            return None
        close = df["close"]
        fast = ema(close, self._FAST)
        slow = ema(close, self._SLOW)

        f_now, f_prev = float(fast.iloc[-1]), float(fast.iloc[-2])
        s_now = float(slow.iloc[-1])
        c_now, c_prev = float(close.iloc[-1]), float(close.iloc[-2])

        uptrend = f_now > s_now
        downtrend = f_now < s_now

        # Reclaim of the fast EMA in the trend direction.
        reclaim_up = c_prev <= f_prev and c_now > f_now
        reclaim_down = c_prev >= f_prev and c_now < f_now

        if uptrend and reclaim_up:
            return "buy"
        if downtrend and reclaim_down:
            return "sell"
        return None


class BBSqueezeStrategy(_LabStrategy):
    """Bollinger-band squeeze breakout (TTM-style).

    While volatility is coiled (bb_squeeze True) the strategy waits. On the first
    bar where the squeeze RELEASES (was squeezed last bar, not squeezed now), it
    takes the breakout in the direction of the bar's momentum (close vs EMA20),
    with an ATR sanity gate so we only act on a genuine expansion.

    Causal: bb_squeeze() (utils.indicators) is rolling/expanding only; the
    release test compares the last two squeeze flags.
    """

    name = "bb_squeeze"

    def generate_signal(self, df: pd.DataFrame):
        if len(df) < 60:
            return None
        close = df["close"]
        high = df["high"]
        low = df["low"]

        sq = bb_squeeze(close, high, low)
        if len(sq) < 2:
            return None
        was_squeezed = bool(sq.iloc[-2])
        now_squeezed = bool(sq.iloc[-1])
        released = was_squeezed and not now_squeezed
        if not released:
            return None

        # Direction from momentum vs EMA20; magnitude gate via ATR.
        e = ema(close, 20)
        a = atr(high, low, close, 14)
        c = float(close.iloc[-1])
        em = float(e.iloc[-1])
        av = float(a.iloc[-1]) if np.isfinite(a.iloc[-1]) else 0.0
        if av <= 0:
            return None
        # Require the breakout bar to clear the EMA by a fraction of ATR.
        if c > em + 0.25 * av:
            return "buy"
        if c < em - 0.25 * av:
            return "sell"
        return None


STRATEGIES = {
    "asian_range": AsianRangeStrategy,
    "dow_swing": DowSwingStrategy,
    "bb_squeeze": BBSqueezeStrategy,
}


# ---------------------------------------------------------------------------- #
# Offline OHLCV loading                                                         #
# ---------------------------------------------------------------------------- #


def _base_from_symbol(symbol: str) -> str:
    """'BTC/USDT' -> 'BTC'.  'BTC/USDT:USDT' -> 'BTC'."""
    s = symbol.split(":", 1)[0]
    if "/" in s:
        return s.split("/", 1)[0]
    # Already a base, or 'BTCUSDT'
    if s.upper().endswith("USDT"):
        return s[:-4]
    return s


def _cache_path(symbol: str, timeframe: str) -> Path:
    base = _base_from_symbol(symbol)
    return CACHE_DIR / f"{base}-USDT_{timeframe}.parquet"


def load_ohlcv(symbol: str, timeframe: str, days: int | None) -> pd.DataFrame:
    """Load OHLCV from the local parquet cache OFFLINE.

    Returns a DataFrame indexed by a DatetimeIndex with columns
    [open, high, low, close, volume] — the shape core.backtester expects.
    Also keeps a 'ts' (unix seconds) column for hour-of-day logic.
    """
    path = _cache_path(symbol, timeframe)
    if not path.exists():
        raise FileNotFoundError(
            f"No offline cache for {symbol} @ {timeframe}: {path} not found. "
            f"(strategy_lab never hits the network.)"
        )
    raw = pd.read_parquet(path)
    needed = {"ts", "open", "high", "low", "close", "volume"}
    missing = needed - set(raw.columns)
    if missing:
        raise ValueError(f"{path} missing columns: {sorted(missing)}")

    raw = raw.sort_values("ts").reset_index(drop=True)

    if days and days > 0:
        n = days * BARS_PER_DAY.get(timeframe, 24)
        raw = raw.tail(n).reset_index(drop=True)

    df = raw.copy()
    df["timestamp"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    df = df.set_index("timestamp")
    # Keep 'ts' as a column for any hour-of-day indicators; index is the DT.
    df = df[["ts", "open", "high", "low", "close", "volume"]]
    return df


# ---------------------------------------------------------------------------- #
# --backtest                                                                    #
# ---------------------------------------------------------------------------- #


def run_backtest(
    strategy_name: str, symbol: str, days: int | None, timeframe: str = DEFAULT_TIMEFRAME
) -> int:
    # Lazy import: keep core.* (and its transitive order_manager/virtual_wallet
    # load) off the --paper path entirely.
    from core.backtester import Backtester

    strat = STRATEGIES[strategy_name]()
    df = load_ohlcv(symbol, timeframe, days)
    if len(df) < 100:
        print(
            f"[lab] WARNING: only {len(df)} bars loaded — results will be unreliable (want 200+)."
        )

    print("=" * 62)
    print(f"  STRATEGY LAB  --  BACKTEST  ({strategy_name})")
    print(f"  symbol={symbol}  tf={timeframe}  bars={len(df)}  days={days if days else 'all'}")
    print("=" * 62)

    bt = Backtester()
    result = bt.run(strat, df, symbol)

    # ---- core.backtester already logged a detailed table; print a digest ----
    print("\n  --- DIGEST (in-memory only, no state files written) ---")
    print(f"  Total trades : {result.total_trades}")
    print(f"  Win rate     : {result.win_rate:.2f}%")
    print(f"  Profit factor: {result.profit_factor:.3f}")
    print(f"  Sharpe       : {result.sharpe_ratio:.3f}")
    print(f"  Max drawdown : {result.max_drawdown:.2f}%")
    print(f"  Total PnL    : {result.total_pnl:+.4f}")
    if result.test:
        print(
            f"  TEST(30%) WR : {result.test.win_rate:.2f}%  "
            f"trades={result.test.total_trades}  "
            f"PnL={result.test.total_pnl:+.4f}"
        )
    verdict = "OVERFIT" if result.overfit_warning else "OK"
    print(f"  Overfit flag : {verdict}")
    print(
        f"  Approval     : {'APPROVED' if result.approved else 'REJECTED'} ({result.reject_reason})"
    )

    # ---- Walk-forward pass over the same OFFLINE series ----
    _run_walk_forward(strat, df, symbol, timeframe)
    return 0


def _run_walk_forward(
    strat, df: pd.DataFrame, symbol: str, timeframe: str, n_splits: int = 4
) -> None:
    """Run a WalkForward CV: backtest each (train,test) fold's test window.

    Uses an embargo to avoid leakage at the fold boundary. Each fold's TEST
    window is backtested independently; we report mean OOS win-rate / PF so a
    strategy that only works on one slice is exposed.
    """
    # Lazy import (see run_backtest) — backtest-only, off the --paper path.
    from core.backtester import Backtester
    from core.walk_forward import WalkForward

    n = len(df)
    if n < 120:
        print("\n  [walk-forward] too few bars for CV — skipped.")
        return

    embargo = max(1, BARS_PER_DAY.get(timeframe, 24))  # ~1 day gap
    wf = WalkForward(n_splits=n_splits, embargo_bars=embargo, anchored=True)
    bt = Backtester()

    print("\n" + "=" * 62)
    print(f"  WALK-FORWARD ({n_splits} folds, embargo={embargo} bars)")
    print("=" * 62)

    oos_wr: list[float] = []
    oos_pf: list[float] = []
    oos_trades = 0
    for fold, (_train_idx, test_idx) in enumerate(wf.split(n), start=1):
        if len(test_idx) < 60:
            continue
        seg = df.iloc[test_idx[0] : test_idx[-1] + 1]
        # Backtester needs >= warmup(50)+a few bars in its own split; seg is OOS.
        try:
            r = bt.run(strat, seg, symbol)
        except Exception as e:  # defensive — keep CV going
            print(f"  fold {fold}: error {e}")
            continue
        oos_wr.append(r.win_rate)
        # core.backtester reports PF as wins/0.001 when a fold has no losses;
        # cap it so the mean isn't dominated by a single loss-free slice.
        oos_pf.append(min(r.profit_factor, 10.0))
        oos_trades += r.total_trades
        print(
            f"  fold {fold}: bars={len(seg)}  trades={r.total_trades}  "
            f"WR={r.win_rate:.1f}%  PF={r.profit_factor:.2f}  "
            f"PnL={r.total_pnl:+.3f}"
        )

    if oos_wr:
        print("  " + "-" * 58)
        print(
            f"  mean OOS WR={np.mean(oos_wr):.1f}%  "
            f"mean OOS PF={np.mean(oos_pf):.2f}  "
            f"total OOS trades={oos_trades}"
        )
        robust = np.mean(oos_pf) >= 1.0 and oos_trades >= n_splits
        print(f"  walk-forward verdict: {'ROBUST' if robust else 'NOT ROBUST'}")
    else:
        print("  no fold produced enough trades to judge.")
    print("=" * 62)


# ---------------------------------------------------------------------------- #
# --paper : MINIMAL ISOLATED SIMULATOR (data/lab/ only, never the bot's files)  #
# ---------------------------------------------------------------------------- #


class LabPaperSimulator:
    """A self-contained paper simulator with an ISOLATED wallet + positions.

    State lives under data/lab/:
        data/lab/wallet.json     {"balance": float, "start": float}
        data/lab/positions.json  {"open": [...], "closed": [...]}

    It does NOT import core.order_manager or core.virtual_wallet. A defensive
    guard refuses to write any path that resolves to the bot's singleton files.
    """

    WALLET_FILE = LAB_DIR / "wallet.json"
    POS_FILE = LAB_DIR / "positions.json"

    SL_PCT = 0.040
    TP_PCT = 0.100
    FEE_PCT = 0.0007
    RISK_PCT = 0.05  # fraction of balance notionally allocated per trade

    def __init__(self, start_balance: float = 1000.0):
        LAB_DIR.mkdir(parents=True, exist_ok=True)
        self.wallet = self._load_json(
            self.WALLET_FILE,
            {
                "balance": start_balance,
                "start": start_balance,
            },
        )
        self.positions = self._load_json(self.POS_FILE, {"open": [], "closed": []})

    # -- safe IO ----------------------------------------------------------- #
    @staticmethod
    def _assert_safe(path: Path) -> None:
        if path.resolve() in _FORBIDDEN:
            raise RuntimeError(
                f"REFUSED: {path} is a live-bot singleton file. strategy_lab must never write it."
            )

    @classmethod
    def _load_json(cls, path: Path, default: dict) -> dict:
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                return dict(default)
        return dict(default)

    @classmethod
    def _save_json(cls, path: Path, obj: dict) -> None:
        cls._assert_safe(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, indent=2))

    # -- simulation -------------------------------------------------------- #
    def simulate(self, strategy_name: str, symbol: str, df: pd.DataFrame) -> dict:
        """Replay df bar-by-bar, opening/closing one position at a time.

        Strictly causal: at bar i the signal uses df.iloc[:i+1] (rows 0..i).
        Fills happen at the next bar's open to avoid look-ahead on the signal bar.
        """
        strat = STRATEGIES[strategy_name]()
        balance = float(self.wallet["balance"])
        in_trade = False
        side = ""
        entry = sl = tp = 0.0
        opened_log: list[dict] = []
        closed_log: list[dict] = []

        warmup = 60
        n = len(df)
        for i in range(warmup, n - 1):
            window = df.iloc[: i + 1]  # rows 0..i — causal
            nxt = df.iloc[i + 1]
            high, low, op = float(nxt["high"]), float(nxt["low"]), float(nxt["open"])

            if in_trade:
                exit_price = None
                reason = None
                if side == "buy":
                    if low <= sl:
                        exit_price, reason = sl, "stop_loss"
                    elif high >= tp:
                        exit_price, reason = tp, "take_profit"
                else:
                    if high >= sl:
                        exit_price, reason = sl, "stop_loss"
                    elif low <= tp:
                        exit_price, reason = tp, "take_profit"
                if reason:
                    if side == "buy":
                        ret = (exit_price - entry) / entry
                    else:
                        ret = (entry - exit_price) / entry
                    pnl = balance * self.RISK_PCT * (ret - 2 * self.FEE_PCT)
                    balance += pnl
                    rec = {
                        "symbol": symbol,
                        "side": side,
                        "entry": entry,
                        "exit": exit_price,
                        "reason": reason,
                        "pnl": round(pnl, 6),
                        "ts": int(nxt["ts"]),
                    }
                    closed_log.append(rec)
                    self.positions["closed"].append(rec)
                    in_trade = False
                    self.positions["open"] = []
                continue

            sig = strat.generate_signal(window)
            if sig in ("buy", "sell"):
                in_trade = True
                side = sig
                entry = op
                if side == "buy":
                    sl = entry * (1 - self.SL_PCT)
                    tp = entry * (1 + self.TP_PCT)
                else:
                    sl = entry * (1 + self.SL_PCT)
                    tp = entry * (1 - self.TP_PCT)
                pos = {
                    "symbol": symbol,
                    "side": side,
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "ts": int(nxt["ts"]),
                }
                opened_log.append(pos)
                self.positions["open"] = [pos]

        self.wallet["balance"] = round(balance, 6)
        self._save_json(self.WALLET_FILE, self.wallet)
        self._save_json(self.POS_FILE, self.positions)

        summary = {
            "strategy": strategy_name,
            "symbol": symbol,
            "bars": n,
            "opened": len(opened_log),
            "closed": len(closed_log),
            "wins": sum(1 for c in closed_log if c["pnl"] > 0),
            "losses": sum(1 for c in closed_log if c["pnl"] <= 0),
            "net_pnl": round(sum(c["pnl"] for c in closed_log), 6),
            "end_balance": self.wallet["balance"],
            "start_balance": self.wallet["start"],
        }
        return summary


def _append_journal(summary: dict) -> Path:
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = JOURNAL_DIR / f"{day}.md"
    wr = 0.0
    if summary["closed"]:
        wr = summary["wins"] / summary["closed"] * 100
    entry = (
        f"\n### strategy_lab paper (isolated) — "
        f"{datetime.now(timezone.utc).strftime('%H:%M:%SZ')}\n\n"
        f"- strategy: `{summary['strategy']}`  symbol: `{summary['symbol']}`\n"
        f"- bars: {summary['bars']}  opened: {summary['opened']}  "
        f"closed: {summary['closed']}\n"
        f"- wins/losses: {summary['wins']}/{summary['losses']}  "
        f"win-rate: {wr:.1f}%\n"
        f"- net PnL: {summary['net_pnl']:+.4f}  "
        f"balance: {summary['start_balance']:.2f} -> "
        f"{summary['end_balance']:.2f}\n"
        f"- isolation: wallet/positions under `data/lab/`; "
        f"live bot files untouched.\n"
    )
    with path.open("a", encoding="utf-8") as f:
        f.write(entry)
    return path


def run_paper(
    strategy_name: str, symbol: str, days: int | None, timeframe: str = DEFAULT_TIMEFRAME
) -> int:
    df = load_ohlcv(symbol, timeframe, days)
    if len(df) < 80:
        print(f"[lab] WARNING: only {len(df)} bars — paper run will be thin.")

    print("=" * 62)
    print(f"  STRATEGY LAB  --  PAPER (ISOLATED)  ({strategy_name})")
    print(f"  symbol={symbol}  tf={timeframe}  bars={len(df)}  days={days if days else 'all'}")
    print("  wallet/positions -> data/lab/  |  journal -> journal/<date>.md")
    print("  (core.order_manager / core.virtual_wallet are NOT imported)")
    print("=" * 62)

    sim = LabPaperSimulator()
    summary = sim.simulate(strategy_name, symbol, df)
    jpath = _append_journal(summary)

    wr = (summary["wins"] / summary["closed"] * 100) if summary["closed"] else 0.0
    print(
        f"  opened={summary['opened']}  closed={summary['closed']}  "
        f"wins/losses={summary['wins']}/{summary['losses']}  WR={wr:.1f}%"
    )
    print(
        f"  net PnL={summary['net_pnl']:+.4f}  "
        f"balance {summary['start_balance']:.2f} -> {summary['end_balance']:.2f}"
    )
    print(
        f"  state: {sim.WALLET_FILE.relative_to(REPO_ROOT)} , {sim.POS_FILE.relative_to(REPO_ROOT)}"
    )
    print(f"  journal: {jpath.relative_to(REPO_ROOT)}")
    print("=" * 62)
    return 0


# ---------------------------------------------------------------------------- #
# CLI                                                                           #
# ---------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="strategy_lab.py",
        description="Offline strategy research runner (isolated from the live bot).",
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--backtest", action="store_true", help="Offline backtest + walk-forward (no state files)."
    )
    mode.add_argument(
        "--paper",
        action="store_true",
        help="Isolated paper sim (writes only data/lab/ + journal/).",
    )
    p.add_argument(
        "--strategy",
        required=True,
        choices=sorted(STRATEGIES),
        help="Which experimental strategy to run.",
    )
    p.add_argument("--symbol", default="BTC/USDT", help="Symbol, e.g. BTC/USDT (default).")
    p.add_argument(
        "--days", type=int, default=None, help="Trim to the last N days of bars (default: all)."
    )
    p.add_argument(
        "--timeframe",
        default=DEFAULT_TIMEFRAME,
        choices=sorted(BARS_PER_DAY),
        help=f"Candle timeframe (default: {DEFAULT_TIMEFRAME}).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.backtest:
            return run_backtest(args.strategy, args.symbol, args.days, args.timeframe)
        return run_paper(args.strategy, args.symbol, args.days, args.timeframe)
    except FileNotFoundError as e:
        print(f"[lab] {e}", file=sys.stderr)
        return 2
    except Exception as e:  # surface, don't crash silently
        print(f"[lab] ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
