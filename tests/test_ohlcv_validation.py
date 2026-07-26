"""OHLCV integrity validator (Codex port, 2026-07-12).

Shared validator in exchanges/base.py applied in-path to fetch_ohlcv output
(kill-flag: config.OHLCV_VALIDATION_ENABLED, default True). Reference
semantics: Codex/bot/engine.py::_validate_candles, adapted to the ccxt
list-of-lists contract. Differences from Codex (deliberate):
  * the forming bar is KEPT (mcp_brain / the lanes drop it themselves);
  * failure returns [] — the existing no-data return every caller already
    handles — instead of raising (fail-closed for the data, fail-open for
    the bot).

Pins: the defect matrix, the caller contract (always a list; [] on defect),
the kill flag, and the in-path wiring for all venue clients.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exchanges.base import BaseExchange, closed_ohlcv, validate_ohlcv  # noqa: E402


def _candles(n=5, tf_s=3600, end_s=None, vol=10.0):
    """n valid 1h candles, most recent one still forming (open < 1 tf ago)."""
    end_s = time.time() if end_s is None else end_s
    out = []
    for i in range(n):
        ts_ms = int((end_s - (n - 1 - i) * tf_s) * 1000)
        o, c = 100.0, 101.0
        out.append([ts_ms, o, 102.0, 99.0, c, vol])
    return out


# ── defect matrix ────────────────────────────────────────────────────────
def test_valid_series_passes_through_unchanged():
    rows = _candles()
    assert validate_ohlcv(rows, "1h", symbol="BTC/USDT") is rows


def test_empty_and_none_pass_through():
    # empty = the existing no-data contract, NOT a defect (no log, no change)
    assert validate_ohlcv([], "1h") == []
    assert validate_ohlcv(None, "1h") is None


@pytest.mark.parametrize(
    "mutate, defect_word",
    [
        (lambda r: r[2].__setitem__(0, r[1][0]), "timestamp"),          # duplicate ts
        (lambda r: r[2].__setitem__(0, r[1][0] - 1), "timestamp"),      # decreasing ts
        (lambda r: r[2].__setitem__(4, float("nan")), "non-finite"),    # NaN close
        (lambda r: r[2].__setitem__(2, float("inf")), "non-finite"),    # inf high
        (lambda r: r[2].__setitem__(2, 90.0), "high"),                  # high < max(o,c)
        (lambda r: r[2].__setitem__(3, 100.5), "low"),                  # low > min(o,c)
        (lambda r: r[2].__setitem__(5, -1.0), "volume"),                # negative volume
        (lambda r: r[2].__setitem__(5, None), "malformed"),             # None field
        (lambda r: r.__setitem__(2, [123456789]), "malformed"),         # short row
    ],
)
def test_defect_returns_empty_and_warns(mutate, defect_word, capsys):
    from loguru import logger

    rows = _candles()
    mutate(rows)
    records = []
    sink = logger.add(lambda m: records.append(str(m)), level="WARNING")
    try:
        out = validate_ohlcv(rows, "1h", symbol="BTC/USDT", exchange="TestEx")
    finally:
        logger.remove(sink)
    assert out == []  # fail-closed for the data ...
    assert isinstance(out, list)  # ... same type every caller already handles
    warned = [r for r in records if "OHLCV" in r]
    assert len(warned) == 1, "exactly one WARN naming the defect"
    assert defect_word in warned[0]
    assert "BTC/USDT" in warned[0] and "TestEx" in warned[0]


def test_stale_series_rejected():
    # last candle open 10 timeframes ago -> the feed is stale
    rows = _candles(end_s=time.time() - 10 * 3600)
    assert validate_ohlcv(rows, "1h") == []


def test_future_dated_series_rejected():
    now = time.time()
    rows = _candles(end_s=now + 10)
    assert validate_ohlcv(rows, "1h", now_s=now) == []


def test_recent_closed_bar_not_stale():
    # last candle open 1.5 timeframes ago = the just-closed bar — fine
    rows = _candles(end_s=time.time() - 1.5 * 3600)
    assert validate_ohlcv(rows, "1h") == list(rows)


def test_forming_bar_is_kept():
    """Anti-Codex-semantics pin: we must NOT drop the forming bar — callers
    (mcp_brain 15m timing, the lanes' closed-bar filters) rely on receiving it."""
    rows = _candles()
    out = validate_ohlcv(rows, "1h")
    assert len(out) == len(rows)


def test_closed_ohlcv_removes_forming_bar_without_mutating_input():
    now = 1_800_000_000.0
    rows = _candles(n=4, tf_s=3600, end_s=now)

    out = closed_ohlcv(rows, "1h", now_s=now)

    assert out == rows[:-1]
    assert len(rows) == 4


def test_unparseable_timeframe_skips_staleness_only():
    # unknown tf: range/order checks still run, staleness is skipped
    rows = _candles(end_s=time.time() - 100 * 3600)
    assert validate_ohlcv(rows, "??") == list(rows)
    bad = _candles()
    bad[2][5] = -5.0
    assert validate_ohlcv(bad, "??") == []


# ── kill flag ────────────────────────────────────────────────────────────
def test_kill_flag_disables_validation(monkeypatch):
    import config

    monkeypatch.setattr(config, "OHLCV_VALIDATION_ENABLED", False, raising=False)
    rows = _candles()
    rows[2][5] = -1.0  # defective
    assert validate_ohlcv(rows, "1h") is rows  # returned untouched


def test_flag_default_is_enabled():
    import os

    import config

    if os.getenv("OHLCV_VALIDATION_ENABLED") is None:
        assert config.OHLCV_VALIDATION_ENABLED is True


# ── in-path wiring: BaseExchange + every venue client ────────────────────
class _FakeCcxt:
    def __init__(self, rows):
        self.rows = rows

    def fetch_ohlcv(self, symbol, timeframe, limit=100, params=None):
        return self.rows


class _StubClient(BaseExchange):
    def __init__(self, rows):
        self._rows = rows
        super().__init__("k", "s")

    def _init_exchange(self):
        self.exchange = _FakeCcxt(self._rows)
        self._connected = True


def test_base_fetch_ohlcv_validates_in_path():
    good = _candles()
    assert _StubClient(good).fetch_ohlcv("BTC/USDT", "1h") == good

    bad = _candles()
    bad[2][5] = -1.0
    out = _StubClient(bad).fetch_ohlcv("BTC/USDT", "1h")
    assert out == []
    assert isinstance(out, list)  # contract preserved: list in, list out


def test_bitget_client_wires_validator():
    """Bitget's fetch_ohlcv bypasses super().fetch_ohlcv (direct ccxt call),
    so it must call validate_ohlcv itself (source pin)."""
    src = (ROOT / "exchanges" / "bitget_client.py").read_text(encoding="utf-8")
    idx = src.index("def fetch_ohlcv")
    block = src[idx : idx + 1600]
    assert "validate_ohlcv(" in block


def test_binance_bybit_mexc_route_through_base():
    """Binance/Bybit/MEXC fetch_ohlcv delegate to super().fetch_ohlcv, which
    carries the validator — pin the delegation so a refactor to a direct ccxt
    call can't silently drop validation."""
    for fname in ("binance_client.py", "bybit_client.py", "mexc_client.py"):
        src = (ROOT / "exchanges" / fname).read_text(encoding="utf-8")
        idx = src.index("def fetch_ohlcv")
        block = src[idx : idx + 1600]
        assert "super().fetch_ohlcv(" in block, f"{fname} must delegate to base"
