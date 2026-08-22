"""Verification for screen 90 — the four ways a backtest lies, pinned.

Every prior sweep in this repo that produced an impressive winner produced it
through one of these, so each gets a test that FAILS when the defect is
reintroduced rather than a comment saying it was checked:

  1. The frozen hypothesis moves after outcomes are seen  -> hash abort.
  2. The indicator silently differs from the audited one  -> RSI parity.
  3. The backtest sees the future                         -> look-ahead trio.
  4. The null contains the alternative                    -> surrogate calibration.

Run: venv/Scripts/python.exe -m pytest tests/test_screen_rsi_mr_powered.py -v
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import research.screen_rsi_mr_powered as S  # noqa: E402


# ------------------------------------------------------------------ fixtures
def _synthetic(n: int = 400, seed: int = 7) -> dict:
    """A deterministic OHLCV panel with valid bar geometry."""
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0, 0.02, n)
    close = 100.0 * np.exp(np.cumsum(ret))
    open_ = np.empty(n)
    open_[0] = 100.0
    open_[1:] = close[:-1] * np.exp(rng.normal(0.0, 0.003, n - 1))
    span = np.abs(rng.normal(0.0, 0.01, n))
    high = np.maximum(open_, close) * (1.0 + span)
    low = np.minimum(open_, close) * (1.0 - span)
    return {
        "ts": (1685120400 + np.arange(n) * 86400).astype(np.int64),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
    }


# --------------------------------------------------- 1. the frozen hypothesis
def test_prereg_hash_matches_the_frozen_document():
    """The constant in the screen must equal the document on disk.

    If this drifts, every claim the screen makes is unanchored: nothing proves
    the hypothesis predated the outcome.
    """
    actual = hashlib.sha256(S.PREREG.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    assert actual == S.PREREG_SHA, (
        "prereg content and the screen's frozen hash disagree; the run is void"
    )


def test_hash_check_actually_fires_on_a_one_byte_change(monkeypatch, tmp_path):
    """A hash check nobody has seen fail is not a hash check.

    Revert-in-place: point the screen at a copy with ONE byte appended and
    assert it refuses to run. Without this, `verify_prereg` could be silently
    non-discriminating (the 2026-06 DSR-gate incident, in miniature).
    """
    tampered = tmp_path / "prereg.md"
    tampered.write_bytes(S.PREREG.read_bytes() + b".")
    monkeypatch.setattr(S, "PREREG", tampered)
    with pytest.raises(SystemExit, match="PREREG HASH MISMATCH"):
        S.verify_prereg()


# ---------------------------------------------------------- 2. RSI provenance
def test_rsi_matches_an_independent_wilder_recursion():
    """utils.indicators.rsi must equal a hand-rolled Wilder recursion exactly.

    Three other rsi() copies exist in this repo and disagree on the zero-loss
    branch (NaN / 50.0 / 100.0). At RSI(2) zero-loss windows are common, so the
    wrong copy silently changes which bars are 'oversold'. Pin the one used.
    """
    from utils.indicators import rsi as canonical

    panel = _synthetic(300)
    close = pd.Series(panel["close"])
    for period in (2, 14):
        got = canonical(close, period).to_numpy(dtype=float)
        d = np.diff(panel["close"], prepend=np.nan)
        gain = np.where(d > 0, d, 0.0)
        loss = np.where(d < 0, -d, 0.0)
        alpha = 1.0 / period  # Wilder smoothing == ewm(com=period-1)
        # SEED WITH THE FIRST OBSERVATION, not zero: that is what pandas
        # ewm(adjust=False) does. Seeding at zero disagrees by up to 45 RSI
        # points over the warm-up (measured) — which is exactly why run_cell
        # discards 5 time constants before taking any signal.
        ag, al = gain[1], loss[1]
        want = np.full(len(close), np.nan)
        want[1] = np.nan if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
        for i in range(2, len(close)):
            ag = alpha * gain[i] + (1 - alpha) * ag
            al = alpha * loss[i] + (1 - alpha) * al
            want[i] = np.nan if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
        both = np.isfinite(got) & np.isfinite(want)
        assert both.sum() > 100, f"RSI({period}): too few comparable bars"
        assert np.nanmax(np.abs(got[both] - want[both])) == 0.0, (
            f"RSI({period}) diverges from the Wilder recursion"
        )


# ------------------------------------------------------------ 3. look-ahead
def test_entry_never_fills_on_the_signal_bar(monkeypatch):
    """The entry must be the NEXT bar's open, never the signal bar's close.

    Same-bar fill is the single most common way a mean-reversion backtest
    invents edge: the oversold close IS the low, so filling there books the
    bounce for free.
    """
    panel = _synthetic(600)
    captured: dict = {}
    real = S._rsi_atr

    def spy(p, length):
        r, a = real(p, length)
        captured["rsi"] = r
        return r, a

    monkeypatch.setattr(S, "_rsi_atr", spy)
    out = S.run_cell(panel, 2, "long", 0.0)
    assert out["ts"].size > 0, "no trades produced; the invariant would be vacuous"

    rsi = captured["rsi"]
    lo, _ = S.ENTRY_THR[2]
    idx = {int(t): k for k, t in enumerate(panel["ts"])}
    for t in out["ts"]:
        k = idx[int(t)]
        assert k >= 1, "an entry was booked on bar 0, before any signal could exist"
        assert np.isfinite(rsi[k - 1]) and rsi[k - 1] < lo, (
            "an entry was booked on a bar whose PREVIOUS bar did not trigger"
        )


def test_stop_fill_is_never_better_than_the_stop_price():
    """A gap THROUGH the stop must fill at the open, not at the stop.

    Filling at the unreachable stop price is free money on exactly the days
    that hurt most: SPY 2020-03-16 gapped -10.4%, CRUDE 1991-01-16 -23.4%.
    """
    # Long enough to clear run_cell's 5-time-constant indicator warm-up.
    n = 200
    gap_at = 150
    ts = (1685120400 + np.arange(n) * 86400).astype(np.int64)
    close = np.full(n, 100.0)
    # A steady decline into the gap drives RSI(2) to oversold -> long signal.
    close[gap_at - 20 : gap_at] = np.linspace(140.0, 100.0, 20)
    open_ = close.copy()
    high = close * 1.01
    low = close * 0.99
    # A catastrophic gap down, far below any 3-ATR stop.
    open_[gap_at] = 50.0
    high[gap_at] = 51.0
    low[gap_at] = 45.0
    close[gap_at] = 47.0
    panel = {"ts": ts, "open": open_, "high": high, "low": low, "close": close}
    out = S.run_cell(panel, 2, "long", 0.0)
    assert out["gross"].size, "no trade produced; the invariant would be vacuous"
    # Some trade must carry the full gap loss. If any fill were granted at the
    # stop price the market never traded through, the worst loss would be small.
    assert out["gross"].min() < -0.30, (
        "a gap-through stop filled at the stop price instead of the open"
    )


def test_stop_hits_are_monotone_as_the_stop_tightens(monkeypatch):
    """Tightening the stop can only ever hit it MORE often.

    A non-monotone count means the stop comparison is reading the wrong bar or
    the wrong side of the bar — the defect that hides behind plausible output.
    """
    panel = _synthetic(800, seed=11)
    counts = []
    for mult in (5.0, 3.0, 2.0, 1.0, 0.5):
        monkeypatch.setattr(S, "STOP_ATR", mult)
        out = S.run_cell(panel, 14, "long", 0.0)
        # A stop exit is the only way to lose more than the stop distance.
        thresh = -0.9 * mult * float(np.median(out["risk"])) if out["risk"].size else 0.0
        counts.append(int((out["gross"] <= thresh).sum()))
    assert counts == sorted(counts), (
        f"stop-hit counts not monotone in stop tightness: {counts}"
    )


def test_exit_never_precedes_entry():
    panel = _synthetic(500, seed=3)
    out = S.run_cell(panel, 2, "long", 0.0)
    assert out["bars_held"].size > 0
    assert (out["bars_held"] >= 1).all(), "a trade exited on or before its entry bar"
    assert (out["bars_held"] <= S.MAX_HOLD).all(), "a trade outlived the frozen max hold"


def test_short_pnl_divides_by_the_entry_price_not_the_exit():
    """Short P&L is (entry - exit)/ENTRY. The tempting form divides by EXIT.

    `entry/exit - 1` and `1 - exit/entry` share a sign but not a magnitude:
    the first understates losses and overstates gains, and by Jensen's
    inequality E[entry/exit] > 1 even when E[exit] = entry, so it books a
    positive expectancy for shorts on a pure martingale. That is a bias
    pointed exactly at the side of the book this screen is testing.
    """
    n = 200
    entry_bar = 150
    ts = (1685120400 + np.arange(n) * 86400).astype(np.int64)
    close = np.full(n, 100.0)
    close[entry_bar - 20 : entry_bar] = np.linspace(60.0, 100.0, 20)  # RSI -> overbought
    close[entry_bar + 1 :] = 110.0  # a 10% adverse move against the short
    open_ = close.copy()
    panel = {
        "ts": ts,
        "open": open_,
        "high": close * 1.001,
        "low": close * 0.999,
        "close": close,
    }
    out = S.run_cell(panel, 2, "short", 0.0)
    assert out["gross"].size, "no short trade produced; the test would be vacuous"
    worst = float(out["gross"].min())
    # entry 100 -> exit 110 is exactly -10% on entry notional. The wrong form
    # would report -9.09% (100/110 - 1) and flatter the short side.
    assert worst < -0.095, (
        f"short loss {worst:.4f} is smaller than the -10% the price move implies; "
        "P&L is probably being divided by the exit price"
    )


def test_long_and_short_paths_are_mirror_images(monkeypatch):
    """The decisive check on the short side: mirror the market, flip the side.

    Feed the long path a series and the short path that series' multiplicative
    mirror, with RSI supplied as 100-RSI and the risk unit held identical. The
    two must select the SAME exit bars. If they do not, the short branch has a
    logic bug — which is the only thing that separates "shorts really are worse
    on this data" from "shorts are computed wrong."

    Gross returns agree only to first order, and that is arithmetic rather than
    a defect: a long earns (X-E)/E where the mirrored short earns (X-E)/X.
    """
    rng = np.random.default_rng(21)
    n = 600
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.015, n)))
    span = np.abs(rng.normal(0, 0.008, n))
    base = {
        "ts": (1685120400 + np.arange(n) * 86400).astype(np.int64),
        "open": close.copy(),
        "high": close * (1 + span),
        "low": close * (1 - span),
        "close": close,
    }
    K2 = float(close[0] * close[-1])  # multiplicative mirror constant
    mirror = {
        "ts": base["ts"],
        "open": K2 / base["open"],
        "high": K2 / base["low"],  # high and low swap under inversion
        "low": K2 / base["high"],
        "close": K2 / base["close"],
    }
    rsi = np.full(n, 50.0)
    rsi[100:] = rng.uniform(0, 100, n - 100)

    def fake(panel, length):
        mirrored = panel["close"][0] > base["close"][0] * 1.0001 or np.allclose(
            panel["close"], mirror["close"]
        )
        r = (100.0 - rsi) if mirrored else rsi
        return r, 0.02 * panel["close"]  # atr_frac == 0.02 in BOTH worlds

    monkeypatch.setattr(S, "_rsi_atr", fake)
    long_out = S.run_cell(base, 2, "long", 0.0)
    short_out = S.run_cell(mirror, 2, "short", 0.0)

    assert long_out["ts"].size > 5, "too few trades; the mirror test would be vacuous"
    assert long_out["ts"].size == short_out["ts"].size, (
        f"mirror produced a different trade count: {long_out['ts'].size} long "
        f"vs {short_out['ts'].size} short — the short branch selects different entries"
    )
    assert (long_out["ts"] == short_out["ts"]).all(), "mirrored entries differ"
    assert (long_out["bars_held"] == short_out["bars_held"]).all(), (
        "mirrored trades exited on different bars — the short exit/stop logic "
        "is not the mirror of the long one"
    )
    assert (np.sign(long_out["gross"]) == np.sign(short_out["gross"])).all(), (
        "a mirrored pair disagreed on whether the trade won"
    )


def test_trades_never_overlap():
    """Overlapping trades would count the same excursion many times.

    RSI(2) fires on long consecutive runs; without the block, n inflates and
    the pooled t-test's independence assumption is destroyed.
    """
    panel = _synthetic(900, seed=13)
    out = S.run_cell(panel, 2, "long", 0.0)
    idx = {int(t): k for k, t in enumerate(panel["ts"])}
    entries = [idx[int(t)] for t in out["ts"]]
    exits = [e + int(b) - 1 for e, b in zip(entries, out["bars_held"])]
    for a, b in zip(exits, entries[1:]):
        assert b > a, "a trade opened before the previous one closed"


# ---------------------------------------------------------- 4. null calibration
def test_surrogate_preserves_the_mean_exactly():
    """The 2026-08 defect, pinned: runs 1-3 inflated the benchmark by 56%.

    If the surrogate's drift differs from the original's, the null is easier or
    harder to beat than the real series for a reason unrelated to signal.
    """
    rng = np.random.default_rng(0)
    for seed in (1, 2, 3):
        panel = _synthetic(1000, seed=seed)
        sur = S.surrogate(panel, rng)
        r0 = np.diff(np.log(panel["close"]))
        r1 = np.diff(np.log(sur["close"]))
        assert abs(r0.mean() - r1.mean()) < 1e-12, "surrogate drift is not re-centred"


def test_surrogate_preserves_volatility_and_fat_tails():
    """Sign randomisation must destroy DIRECTION and keep everything else.

    An i.i.d. bootstrap would flatten the kurtosis, making the null too easy to
    beat and manufacturing survivors.
    """
    rng = np.random.default_rng(0)
    panel = _synthetic(2000, seed=5)
    sur = S.surrogate(panel, rng)
    r0 = np.diff(np.log(panel["close"]))
    r1 = np.diff(np.log(sur["close"]))
    # Sign-flipping alone preserves r**2 EXACTLY. The re-centering shift delta
    # (added so the surrogate's drift matches the original's) then moves the
    # second moment by 2*delta*mean(s*r) + delta**2 — measured ~1.6e-4 relative.
    # That is the price of an exactly-matched mean and is not a defect; assert
    # the magnitudes are preserved to well inside it.
    assert abs((r0**2).mean() - (r1**2).mean()) / (r0**2).mean() < 1e-3, (
        "surrogate changed |returns| by more than the re-centering shift explains"
    )
    assert abs(r0.std() - r1.std()) / r0.std() < 1e-2, "surrogate changed volatility"
    k0 = float(((r0 - r0.mean()) ** 4).mean() / r0.var() ** 2)
    k1 = float(((r1 - r1.mean()) ** 4).mean() / r1.var() ** 2)
    assert abs(k0 - k1) < 0.5 * k0, f"surrogate flattened the tails: kurt {k0:.1f} -> {k1:.1f}"


def test_surrogate_bar_geometry_stays_valid():
    """high >= max(open, close) and low <= min(open, close), always.

    When a segment flips, high and low must SWAP about the open. Skip the swap
    and a flipped down-bar keeps a down-bar's excursion, biasing every stop.
    """
    rng = np.random.default_rng(0)
    for seed in (1, 4, 9):
        sur = S.surrogate(_synthetic(1500, seed=seed), rng)
        assert (sur["high"] >= np.maximum(sur["open"], sur["close"]) - 1e-9).all()
        assert (sur["low"] <= np.minimum(sur["open"], sur["close"]) + 1e-9).all()
        assert (sur["low"] > 0).all(), "surrogate produced a non-positive price"


# ------------------------------------------------------------- 5. bookkeeping
def test_timestamps_are_epoch_seconds_not_milliseconds():
    """A REAL epoch column compared against ISO or ms silently matches nothing.

    This repo has been bitten by exactly that (a '0 rows in 6h' report on a
    table holding 6,884).
    """
    from research._ohlcv_cache import _normalise

    df = pd.DataFrame(
        {
            "ts": [1685120400_000, 1685124000_000],  # milliseconds
            "open": [1.0, 1.0],
            "high": [1.0, 1.0],
            "low": [1.0, 1.0],
            "close": [1.0, 1.0],
            "volume": [0.0, 0.0],
        }
    )
    out = _normalise(df)
    assert out["ts"].tolist() == [1685120400, 1685124000], "ms rows were not converted"
    assert (out["ts"] < 10**12).all()


def test_resample_drops_the_forming_bar():
    """A partial trailing bucket is the classic look-ahead. It must be dropped."""
    from research._ohlcv_cache import resample

    n = 25  # 25 hourly bars = six full 4h buckets + one 1-bar partial
    df = pd.DataFrame(
        {
            "ts": 1685120400 + np.arange(n) * 3600,
            "open": np.ones(n),
            "high": np.ones(n),
            "low": np.ones(n),
            "close": np.ones(n),
            "volume": np.zeros(n),
        }
    )
    out = resample(df, 14400)
    assert len(out) < 7, "a forming (partial) bucket survived resampling"


def test_the_grid_is_exactly_eight_cells():
    """N=8 is the whole design. If the grid grows, the MDE arithmetic is stale."""
    assert len(S.RSI_LENS) * len(S.SIDES) * len(S.TIMEFRAMES) == 8
    assert S.N_TRIALS == 8, "DSR multiplicity must equal the grid size"


def test_screen_cannot_authorize_a_live_trade():
    """A refuted family must not reach the live path through this screen."""
    src = Path(S.__file__).read_text(encoding="utf-8")
    assert '"live_trade_authorized": False' in src
    assert '"promotion": "NONE"' in src
    assert "CANNOT produce a GO" in src, "the ceiling must be stated in the module"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
