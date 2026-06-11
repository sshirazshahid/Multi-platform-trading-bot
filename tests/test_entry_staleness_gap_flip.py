"""entry_invalidated gap-flip semantics — behavioral tests (2026-06-11).

Jun-11 evidence: ALL 85 entry_invalidated closes since Jun 4 were BUYS
killed at exactly the 30-min grace boundary with 4h gaps -1.6%..-4.8% —
a 4h EMA gap cannot move 2% in 30 minutes, so those longs were opened
when the 4h hypothesis was ALREADY invalid. The old rule (fire whenever
the current gap is >= threshold against) therefore structurally killed
every long in a 4h-bear regime, forcing an ~86% short book.

New semantics (owner-approved "ship it all"): fire ONLY when the gap
actually FLIPPED after entry. Born-invalid positions (gap already >=
threshold against the side AT THE ENTRY BAR) are exempt forever —
SL/TP/trailing manage them. Unknown entry-bar gap (no entry_ts, bar
outside the fetched window, EMA warmup, NaN, fetch failure) preserves
the OLD fire behavior — fail-conservative.

These tests exercise the real MCPBrain.is_entry_invalidated against
fake exchanges; the predicate replica lives in
tests/test_tier1_predictive_strategy.py.
"""
from __future__ import annotations

BASE_MS = 1_700_000_000_000          # arbitrary fixed bar-0 open (epoch ms)
FOURH_MS = 4 * 3600 * 1000


class FakeExchange:
    """Returns a canned ascending 4h candle array; counts fetches."""

    def __init__(self, candles):
        self.candles = candles
        self.calls = 0

    def fetch_ohlcv(self, symbol, timeframe, limit=250, market_type="spot"):
        self.calls += 1
        return self.candles


def _candles(closes):
    return [
        [BASE_MS + i * FOURH_MS, c, c, c, c, 1.0]
        for i, c in enumerate(closes)
    ]


def _uptrend(n=250):
    """Steadily rising closes → EMA20 > EMA50 at every warmed-up bar."""
    return _candles([1.0 + 0.01 * i for i in range(n)])


def _downtrend(n=250):
    """Steadily falling closes → EMA20 < EMA50 at every warmed-up bar."""
    return _candles([1000.0 - 1.0 * i for i in range(n)])


def _entry_ts(bar_idx):
    """Epoch seconds 2 minutes into bar `bar_idx`."""
    return (BASE_MS + bar_idx * FOURH_MS) / 1000.0 + 120.0


def _brain(fake, *, cached_gap_against_side):
    """Minimal MCPBrain with a warm single-coin indicator cache.

    cached_gap_against_side: 'buy' → cache says gap -0.5% (against longs);
    'sell' → cache says gap +0.5% (against shorts)."""
    from core.mcp_brain import MCPBrain
    b = MCPBrain.__new__(MCPBrain)
    if cached_gap_against_side == "buy":
        ema20, ema50 = 99.5, 100.0     # gap -0.5%
    else:
        ema20, ema50 = 100.5, 100.0    # gap +0.5%
    b._indicator_cache = {"ATOM": {"4h": {"ema20": ema20, "ema50": ema50}}}
    b._indicator_cache_time = 0
    b._entry_gap_memo = {}
    b._exchanges = {"fake": fake}
    return b


# ─── happy path: genuine flip fires ──────────────────────────────────


def test_flip_after_entry_long_fires():
    """Entered long while the 4h gap was favorable (uptrend candles);
    cache now says gap -0.5% → genuine flip → close."""
    fake = FakeExchange(_uptrend())
    b = _brain(fake, cached_gap_against_side="buy")
    fired, reason = b.is_entry_invalidated(
        symbol="ATOM/USDT:USDT", side="buy", gap_pct=0.15,
        entry_ts=_entry_ts(200))
    assert fired is True
    assert "flipped after entry" in reason
    assert "long invalidated" in reason


def test_flip_after_entry_short_fires():
    fake = FakeExchange(_downtrend())
    b = _brain(fake, cached_gap_against_side="sell")
    fired, reason = b.is_entry_invalidated(
        symbol="ATOM/USDT:USDT", side="sell", gap_pct=0.15,
        entry_ts=_entry_ts(200))
    assert fired is True
    assert "flipped after entry" in reason
    assert "short invalidated" in reason


# ─── born-invalid: exempt ─────────────────────────────────────────────


def test_born_invalid_long_exempt():
    """Entered long while the gap was ALREADY bear (downtrend candles)
    → exempt; SL/TP/trailing manage it."""
    fake = FakeExchange(_downtrend())
    b = _brain(fake, cached_gap_against_side="buy")
    fired, reason = b.is_entry_invalidated(
        symbol="ATOM/USDT:USDT", side="buy", gap_pct=0.15,
        entry_ts=_entry_ts(200))
    assert fired is False
    assert "born-invalid" in reason
    assert fake.calls == 1


def test_born_invalid_short_exempt():
    fake = FakeExchange(_uptrend())
    b = _brain(fake, cached_gap_against_side="sell")
    fired, reason = b.is_entry_invalidated(
        symbol="ATOM/USDT:USDT", side="sell", gap_pct=0.15,
        entry_ts=_entry_ts(200))
    assert fired is False
    assert "born-invalid" in reason


def test_same_bar_entry_resolves_to_forming_bar():
    """Entry inside the LAST (forming) bar → prefix = full window →
    entry gap ~= current candle gap. On a downtrend long that means
    born-invalid → exempt (codifies 'a 4h gap can't move 2% in 30min')."""
    fake = FakeExchange(_downtrend())
    b = _brain(fake, cached_gap_against_side="buy")
    fired, reason = b.is_entry_invalidated(
        symbol="ATOM/USDT:USDT", side="buy", gap_pct=0.15,
        entry_ts=_entry_ts(249))
    assert fired is False
    assert "born-invalid" in reason


# ─── unknown entry gap: old behavior preserved (fail-conservative) ────


def test_entry_ts_zero_old_fire():
    """entry_ts=0 (knob off / legacy caller) → old fire-on-current-gap."""
    fake = FakeExchange(_uptrend())
    b = _brain(fake, cached_gap_against_side="buy")
    fired, reason = b.is_entry_invalidated(
        symbol="ATOM/USDT:USDT", side="buy", gap_pct=0.15, entry_ts=0.0)
    assert fired is True
    assert "long invalidated" in reason
    assert fake.calls == 0  # unknown short-circuits before any fetch


def test_entry_before_window_old_fire():
    """Entry predates the fetched window (idx < EMA warmup) → unknown
    → old fire."""
    fake = FakeExchange(_uptrend())
    b = _brain(fake, cached_gap_against_side="buy")
    fired, reason = b.is_entry_invalidated(
        symbol="ATOM/USDT:USDT", side="buy", gap_pct=0.15,
        entry_ts=(BASE_MS / 1000.0) - 999_999.0)
    assert fired is True
    assert "long invalidated" in reason


def test_empty_fetch_old_fire():
    fake = FakeExchange([])
    b = _brain(fake, cached_gap_against_side="buy")
    fired, reason = b.is_entry_invalidated(
        symbol="ATOM/USDT:USDT", side="buy", gap_pct=0.15,
        entry_ts=_entry_ts(200))
    assert fired is True
    assert "long invalidated" in reason


def test_nan_closes_old_fire():
    """All-NaN closes → NaN EMAs → unknown → old fire. (All-NaN, not a
    single NaN: pandas ewm skips isolated NaNs and would still produce a
    finite EMA.)"""
    fake = FakeExchange(_candles([float("nan")] * 250))
    b = _brain(fake, cached_gap_against_side="buy")
    fired, reason = b.is_entry_invalidated(
        symbol="ATOM/USDT:USDT", side="buy", gap_pct=0.15,
        entry_ts=_entry_ts(200))
    assert fired is True
    assert "entry gap unknown" in reason
    assert "long invalidated" in reason


# ─── still-valid current gap: no entry-gap fetch at all ──────────────


def test_still_valid_no_fetch():
    """When the current gap is NOT against the side, the entry-bar gap
    is never fetched (the lazy path only runs when the old rule would
    fire)."""
    from core.mcp_brain import MCPBrain
    fake = FakeExchange(_uptrend())
    b = MCPBrain.__new__(MCPBrain)
    b._indicator_cache = {"ATOM": {"4h": {"ema20": 100.5, "ema50": 100.0}}}
    b._indicator_cache_time = 0
    b._entry_gap_memo = {}
    b._exchanges = {"fake": fake}
    fired, reason = b.is_entry_invalidated(
        symbol="ATOM/USDT:USDT", side="buy", gap_pct=0.15,
        entry_ts=_entry_ts(200))
    assert fired is False
    assert "still valid" in reason
    assert fake.calls == 0


# ─── memoization: one fetch per position lifetime ─────────────────────


def test_entry_gap_memoized():
    fake = FakeExchange(_downtrend())
    b = _brain(fake, cached_gap_against_side="buy")
    ts = _entry_ts(200)
    b.is_entry_invalidated(symbol="ATOM/USDT:USDT", side="buy",
                           gap_pct=0.15, entry_ts=ts)
    b.is_entry_invalidated(symbol="ATOM/USDT:USDT", side="buy",
                           gap_pct=0.15, entry_ts=ts)
    assert fake.calls == 1
    b.is_entry_invalidated(symbol="ATOM/USDT:USDT", side="buy",
                           gap_pct=0.15, entry_ts=_entry_ts(150))
    assert fake.calls == 2
