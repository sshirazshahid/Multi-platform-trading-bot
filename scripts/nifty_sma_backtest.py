"""Nifty 50 SMA-crossover backtesting engine — self-contained, 5y daily, honest benchmark.

Trades the NSE Nifty 50 index (Yahoo `^NSEI`) with a Simple-Moving-Average crossover:
LONG when SMA(fast) > SMA(slow), else FLAT (cash). Decisions use only data up to the
decision bar (signal at close[t] earns the close[t]->close[t+1] return — no look-ahead).
Costs (commission+slippage) are charged on every position change.

EVERY result is reported against BUY-AND-HOLD, because on a long-trending index the honest
question is not "did SMA make money" (beta will) but "did timing beat just holding, on
return AND on risk-adjusted / drawdown terms." Several standard SMA pairs are shown side by
side; per the project's own hyperopt lesson, the best in-sample pair is NOT presented as an
edge — picking it would be overfitting.

No new dependencies (stdlib + requests + pandas + numpy). Data cached to
data/nifty50_daily.csv. Records reports/nifty_sma_backtest_<date>.md (+ .json + equity CSV).
Read-only on the live bot (this is NSE equities research, separate from the crypto engine).
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
SYMBOL = "^NSEI"
PERIODS_PER_YEAR = 252
COST_ONEWAY = 5.0 / 1e4          # 5 bps commission+slippage per position change
# (fast, slow) SMA pairs to compare; 50/200 = the classic "golden cross"
CONFIGS = [(10, 30), (20, 50), (50, 100), (50, 200)]
_UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124 Safari/537.36")}


# ---- data ------------------------------------------------------------------
def fetch_index(symbol: str = "^NSEI", rng: str = "5y", *, use_cache: bool = True) -> pd.DataFrame:
    """Daily index OHLC via Yahoo v8 chart API (UA + 429 backoff); cache per symbol+range.

    `rng="maxdaily"` uses explicit period1/period2 (since 2000-01-01) to force full DAILY
    history — `range=max` silently returns MONTHLY bars. Works for any Yahoo index symbol
    (^NSEI, ^BSESN, ^GSPC, ^IXIC, ^DJI, ^FTSE, ^N225, ^GDAXI, ...)."""
    import requests
    slug = "".join(ch if ch.isalnum() else "_" for ch in symbol)
    cache = DATA / f"index_{slug}_{rng}.csv"
    enc = symbol.replace("^", "%5E")
    base = f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}"
    if rng == "maxdaily":
        import time as _t
        url = f"{base}?period1=946684800&period2={int(_t.time())}&interval=1d"  # 2000-01-01->now
    else:
        url = f"{base}?range={rng}&interval=1d"
    last_err = None
    for attempt in range(5):
        try:
            r = requests.get(url, headers=_UA, timeout=25)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            ts = res["timestamp"]
            q = res["indicators"]["quote"][0]
            adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose")
            df = pd.DataFrame({
                "date": [datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat() for t in ts],
                "open": q["open"], "high": q["high"], "low": q["low"],
                "close": adj if adj else q["close"], "volume": q.get("volume"),
            }).dropna(subset=["close"]).reset_index(drop=True)
            DATA.mkdir(exist_ok=True)
            df.to_csv(cache, index=False)
            return df
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1 + attempt)
    if use_cache and cache.exists():
        print(f"[warn] fetch failed ({last_err}); using cached {cache}")
        return pd.read_csv(cache)
    raise RuntimeError(f"could not fetch {symbol} and no cache: {last_err}")


def fetch_nifty(rng: str = "5y", *, use_cache: bool = True) -> pd.DataFrame:
    """Nifty 50 (^NSEI) convenience wrapper around fetch_index."""
    return fetch_index("^NSEI", rng, use_cache=use_cache)


# ---- pure backtest core (tested) -------------------------------------------
def sma(close: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(close).rolling(n).mean().to_numpy()


def sma_crossover_position(close: np.ndarray, fast: int, slow: int,
                           allow_short: bool = False) -> np.ndarray:
    """Position at each bar from data UP TO that bar (no look-ahead).
    +1 long when SMA(fast)>SMA(slow); 0 flat (or -1 short if allow_short)."""
    f, s = sma(close, fast), sma(close, slow)
    pos = np.where(f > s, 1.0, (-1.0 if allow_short else 0.0))
    pos = np.where(np.isnan(f) | np.isnan(s), 0.0, pos)   # flat during warmup
    return pos


def run_backtest(close: np.ndarray, pos: np.ndarray, cost_oneway: float = COST_ONEWAY) -> dict:
    """Apply position to the NEXT bar's return; charge cost on turnover. Pure."""
    close = np.asarray(close, dtype=float)
    ret = np.zeros_like(close)
    ret[1:] = close[1:] / close[:-1] - 1.0
    prev = np.concatenate([[0.0], pos[:-1]])          # position held into bar t
    turn = np.abs(pos - prev)
    strat = prev * ret - cost_oneway * turn
    equity = np.cumprod(1.0 + strat)
    return {"ret": ret, "strat_ret": strat, "equity": equity, "pos": pos}


def _max_drawdown(equity: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity)
    return float((equity / peak - 1.0).min())


def metrics(strat_ret: np.ndarray, equity: np.ndarray, pos: np.ndarray | None = None,
            ppy: int = PERIODS_PER_YEAR) -> dict:
    r = np.asarray(strat_ret, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 2:
        return {"n": n}
    total = float(equity[-1] - 1.0)
    years = n / ppy
    cagr = float(equity[-1] ** (1 / years) - 1.0) if equity[-1] > 0 and years > 0 else float("nan")
    sd = r.std(ddof=1)
    sharpe = float(r.mean() / sd * np.sqrt(ppy)) if sd > 0 else 0.0
    downside = r[r < 0].std(ddof=1) if (r < 0).any() else 0.0
    sortino = float(r.mean() / downside * np.sqrt(ppy)) if downside > 0 else 0.0
    mdd = _max_drawdown(equity)
    calmar = float(cagr / abs(mdd)) if mdd < 0 else float("nan")
    out = {
        "n": n, "total_return": total, "cagr": cagr, "sharpe": sharpe,
        "sortino": sortino, "max_drawdown": mdd, "calmar": calmar,
        "volatility": float(sd * np.sqrt(ppy)),
    }
    if pos is not None:
        out["time_in_market"] = float(np.mean(pos != 0))
        out["n_trades"] = int(np.sum(np.abs(np.diff(np.concatenate([[0.0], pos]))) > 0))
    return out


def _trade_winrate(close: np.ndarray, pos: np.ndarray, cost_oneway: float = COST_ONEWAY) -> float:
    """Per-round-trip win rate for a long/flat position series."""
    close = np.asarray(close, dtype=float)
    wins = trades = 0
    entry = None
    for t in range(1, len(pos)):
        if pos[t - 1] == 0 and pos[t] == 1:          # enter long at bar t (earns from t->...)
            entry = close[t]
        elif entry is not None and pos[t - 1] == 1 and pos[t] == 0:  # exit at bar t
            trades += 1
            if close[t] / entry - 1.0 - 2 * cost_oneway > 0:
                wins += 1
            entry = None
    if entry is not None:                            # still open at end → mark at last close
        trades += 1
        if close[-1] / entry - 1.0 - 2 * cost_oneway > 0:
            wins += 1
    return float(wins / trades) if trades else 0.0


# ---- report ----------------------------------------------------------------
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    df = fetch_nifty("5y")
    close = df["close"].to_numpy(dtype=float)
    dates = df["date"].tolist()
    print(f"Nifty 50 (^NSEI): {len(close)} daily bars, {dates[0]} -> {dates[-1]}")

    # buy & hold
    bh = run_backtest(close, np.ones_like(close), cost_oneway=0.0)
    bh_m = metrics(bh["strat_ret"], bh["equity"])

    rows = []
    equity_cols = {"date": dates, "buy_and_hold": bh["equity"].tolist()}
    for fast, slow in CONFIGS:
        pos = sma_crossover_position(close, fast, slow)
        bt = run_backtest(close, pos)
        m = metrics(bt["strat_ret"], bt["equity"], pos)
        m["win_rate"] = _trade_winrate(close, pos)
        m["config"] = f"{fast}/{slow}"
        m["beats_bh_return"] = m["total_return"] > bh_m["total_return"]
        m["beats_bh_sharpe"] = m["sharpe"] > bh_m["sharpe"]
        rows.append(m)
        equity_cols[f"sma_{fast}_{slow}"] = bt["equity"].tolist()

    today = date.today().isoformat()
    REPORTS.mkdir(exist_ok=True)
    pd.DataFrame(equity_cols).to_csv(REPORTS / f"nifty_sma_equity_{today}.csv", index=False)

    L = [f"# Nifty 50 — SMA crossover backtest — {today}", "",
         f"**Index:** NSE Nifty 50 (`^NSEI`, Yahoo). **Window:** {dates[0]} → {dates[-1]} "
         f"({len(close)} daily bars ≈ {len(close)/PERIODS_PER_YEAR:.1f}y). **Rule:** LONG when "
         f"SMA(fast)>SMA(slow) else FLAT; cost {COST_ONEWAY*1e4:.0f}bps/turn; no look-ahead.", "",
         "## Buy & Hold (benchmark)",
         f"- Total **{bh_m['total_return']*100:+.1f}%** | CAGR **{bh_m['cagr']*100:+.1f}%** | "
         f"Sharpe **{bh_m['sharpe']:.2f}** | MaxDD **{bh_m['max_drawdown']*100:.1f}%** | "
         f"Vol **{bh_m['volatility']*100:.1f}%**", "",
         "## SMA crossover configs vs buy & hold", "",
         "| config | Total % | CAGR % | Sharpe | Sortino | MaxDD % | Calmar | Vol % | "
         "time-in-mkt | trades | win% | beats B&H (ret/Sharpe) |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in rows:
        L.append(
            f"| {m['config']} | {m['total_return']*100:+.1f} | {m['cagr']*100:+.1f} | "
            f"{m['sharpe']:.2f} | {m['sortino']:.2f} | {m['max_drawdown']*100:.1f} | "
            f"{m.get('calmar', float('nan')):.2f} | {m['volatility']*100:.1f} | "
            f"{m['time_in_market']*100:.0f}% | {m['n_trades']} | {m['win_rate']*100:.0f} | "
            f"{'Y' if m['beats_bh_return'] else 'N'}/{'Y' if m['beats_bh_sharpe'] else 'N'} |")

    any_beat_ret = any(m["beats_bh_return"] for m in rows)
    any_beat_sharpe = any(m["beats_bh_sharpe"] for m in rows)
    L += ["", "## Honest read", "",
          f"- On **total return**, {'some SMA configs beat' if any_beat_ret else 'NO SMA config beat'} "
          "buy-and-hold; "
          f"on **risk-adjusted (Sharpe)**, {'some beat' if any_beat_sharpe else 'none beat'} it.",
          "- SMA crossover's structural trade-off: it sits in cash during downtrends, so it "
          "usually cuts **drawdown** but gives up upside + pays whipsaw costs in chop. On a "
          "long-trending index, buy-and-hold is a stiff benchmark.",
          "- ⚠ Do NOT cherry-pick the best row and call it an edge — that's in-sample selection "
          "(the project's hyperopt-trap finding). A real verdict needs out-of-sample / walk-forward "
          "validation across the parameter family, not the prettiest backtest.",
          "", "_Equity curves: reports/nifty_sma_equity_%s.csv. Long/flat (no shorting); index "
          "has no dividend/split adjustment beyond Yahoo adjclose._" % today]

    out_md = REPORTS / f"nifty_sma_backtest_{today}.md"
    out_md.write_text("\n".join(L), encoding="utf-8")
    (REPORTS / f"nifty_sma_backtest_{today}.json").write_text(json.dumps({
        "date": today, "symbol": SYMBOL, "bars": len(close),
        "window": [dates[0], dates[-1]], "buy_and_hold": bh_m, "configs": rows,
    }, indent=2, default=str), encoding="utf-8")

    print(f"\nBuy&Hold: total {bh_m['total_return']*100:+.1f}%  CAGR {bh_m['cagr']*100:+.1f}%  "
          f"Sharpe {bh_m['sharpe']:.2f}  MaxDD {bh_m['max_drawdown']*100:.1f}%")
    for m in rows:
        print(f"  SMA {m['config']:>7}: total {m['total_return']*100:+6.1f}%  CAGR "
              f"{m['cagr']*100:+5.1f}%  Sharpe {m['sharpe']:.2f}  MaxDD {m['max_drawdown']*100:6.1f}%  "
              f"trades {m['n_trades']:>2}  beatsB&H ret/SR={'Y' if m['beats_bh_return'] else 'N'}/"
              f"{'Y' if m['beats_bh_sharpe'] else 'N'}")
    print(f"Report: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
