"""Ghost-reconcile matching: prove the bugs that left ghost_reconciled at
0 occurrences vs ghost_sync at 14 across the historical warehouse.

Bugs fixed by core.position_tracker.match_ghost_ledger_record:
  1. Side filter accepted str(None)=='none' as a real value, dropping every
     Binance income-ledger record (which emits side=None).
  2. Reconcile required exit_price > 0; Binance income ledger only carries
     the realized-pnl amount, so reconcile was impossible for Binance ghosts.
  3. 2h lookback window was too narrow under reconcile-cycle lag — extended
     to 6h at the call site (not in the helper since the helper is
     window-agnostic).

These tests cover the helper directly so future regressions surface fast.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "position_tracker_ghost_t", ROOT / "core" / "position_tracker.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["position_tracker_ghost_t"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def pt():
    return _load()


@dataclass
class StubPos:
    symbol: str = "BTC/USDT:USDT"
    side: str = "buy"
    size: float = 0.01
    entry_price: float = 70000.0
    open_time: float = 1000.0
    # Exchange-side conditional fields (read by _classify_conditional_fill).
    # Defaults keep older tests compatible — only the classification tests set them.
    stop_loss: float = 0.0
    take_profit: float = 0.0
    _exchange_sl: bool = False
    _exchange_tp: bool = False


# ── Bybit / Bitget happy path (exit_price present) ───────────────────────

def test_match_uses_exit_price_when_present(pt):
    pos = StubPos()
    records = [{
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "close_time": 2000.0,
        "exit_price": 70500.0,
        "realized_pnl": 5.0,
    }]
    exit_px, best = pt.match_ghost_ledger_record(pos, records)
    assert exit_px == 70500.0
    assert best is records[0]


def test_match_picks_most_recent_when_multiple(pt):
    pos = StubPos()
    records = [
        {"symbol": "BTC/USDT:USDT", "side": "buy",
         "close_time": 1500.0, "exit_price": 70100.0},
        {"symbol": "BTC/USDT:USDT", "side": "buy",
         "close_time": 2500.0, "exit_price": 70900.0},  # newer
        {"symbol": "BTC/USDT:USDT", "side": "buy",
         "close_time": 2000.0, "exit_price": 70500.0},
    ]
    exit_px, _ = pt.match_ghost_ledger_record(pos, records)
    assert exit_px == 70900.0


# ── Binance income-ledger path (side=None, exit_price=0) ─────────────────

def test_binance_side_none_is_not_filtered_out(pt):
    """BUG #1: str(None).lower()=='none' was treated as a real side."""
    pos = StubPos()
    records = [{
        "symbol": "BTC/USDT:USDT",
        "side": None,  # Binance income-ledger has no side
        "close_time": 2000.0,
        "exit_price": 0.0,
        "realized_pnl": -7.0,  # we paid; long position dropped
    }]
    exit_px, best = pt.match_ghost_ledger_record(pos, records)
    # Should match. Back-out: 70000 + (-7.0 / (0.01 * +1)) = 69300
    assert exit_px == pytest.approx(69300.0)
    assert best is records[0]


def test_back_out_exit_from_pnl_long_winner(pt):
    """Long, +PnL → exit_price = entry + (pnl / size)."""
    pos = StubPos(side="buy", entry_price=100.0, size=2.0)
    records = [{
        "symbol": "BTC/USDT:USDT", "side": None,
        "close_time": 2000.0, "exit_price": 0.0,
        "realized_pnl": 6.0,
    }]
    exit_px, _ = pt.match_ghost_ledger_record(pos, records)
    # 100 + (6 / (2 * +1)) = 103
    assert exit_px == pytest.approx(103.0)


def test_back_out_exit_from_pnl_short_winner(pt):
    """Short, +PnL means price went down → exit_price = entry - |pnl|/size."""
    pos = StubPos(side="sell", entry_price=100.0, size=2.0)
    records = [{
        "symbol": "BTC/USDT:USDT", "side": None,
        "close_time": 2000.0, "exit_price": 0.0,
        "realized_pnl": 6.0,
    }]
    exit_px, _ = pt.match_ghost_ledger_record(pos, records)
    # 100 + (6 / (2 * -1)) = 100 - 3 = 97
    assert exit_px == pytest.approx(97.0)


def test_back_out_exit_from_pnl_long_loser(pt):
    pos = StubPos(side="buy", entry_price=100.0, size=2.0)
    records = [{
        "symbol": "BTC/USDT:USDT", "side": None,
        "close_time": 2000.0, "exit_price": 0.0,
        "realized_pnl": -4.0,
    }]
    exit_px, _ = pt.match_ghost_ledger_record(pos, records)
    # 100 + (-4 / (2 * +1)) = 98
    assert exit_px == pytest.approx(98.0)


def test_explicit_exit_price_takes_priority_over_back_out(pt):
    """When venue gives an exit_price, use it even if pnl is also present."""
    pos = StubPos(side="buy", entry_price=100.0, size=2.0)
    records = [{
        "symbol": "BTC/USDT:USDT", "side": "buy",
        "close_time": 2000.0,
        "exit_price": 105.0,
        "realized_pnl": 8.0,  # would imply 104.0 if we backed out
    }]
    exit_px, _ = pt.match_ghost_ledger_record(pos, records)
    assert exit_px == 105.0


# ── Filter tests ─────────────────────────────────────────────────────────

def test_symbol_mismatch_excludes_record(pt):
    pos = StubPos()
    records = [{
        "symbol": "ETH/USDT:USDT", "side": "buy",
        "close_time": 2000.0, "exit_price": 70500.0,
    }]
    exit_px, best = pt.match_ghost_ledger_record(pos, records)
    assert exit_px == 0.0
    assert best is None


def test_side_explicit_mismatch_excludes_record(pt):
    """If venue reports a side that's neither ours nor its mirror, skip."""
    pos = StubPos(side="buy")
    records = [{
        "symbol": "BTC/USDT:USDT",
        "side": "limit",  # nonsense side
        "close_time": 2000.0, "exit_price": 70500.0,
    }]
    exit_px, _ = pt.match_ghost_ledger_record(pos, records)
    assert exit_px == 0.0


def test_side_mirror_accepted(pt):
    """Bybit reports the closing-order side (opposite of entry).
    A long position should match a record with side='sell' (the closing trade)."""
    pos = StubPos(side="buy")
    records = [{
        "symbol": "BTC/USDT:USDT",
        "side": "sell",  # closing-order side for a long
        "close_time": 2000.0, "exit_price": 70500.0,
    }]
    exit_px, _ = pt.match_ghost_ledger_record(pos, records)
    assert exit_px == 70500.0


def test_close_time_before_open_excludes_record(pt):
    pos = StubPos(open_time=5000.0)
    records = [{
        "symbol": "BTC/USDT:USDT", "side": "buy",
        "close_time": 4000.0,  # before the position was opened — stale
        "exit_price": 70500.0,
    }]
    exit_px, _ = pt.match_ghost_ledger_record(pos, records)
    assert exit_px == 0.0


def test_close_time_zero_excludes_record(pt):
    pos = StubPos()
    records = [{
        "symbol": "BTC/USDT:USDT", "side": "buy",
        "close_time": 0,  # malformed timestamp
        "exit_price": 70500.0,
    }]
    exit_px, _ = pt.match_ghost_ledger_record(pos, records)
    assert exit_px == 0.0


def test_empty_records_returns_zero(pt):
    pos = StubPos()
    exit_px, best = pt.match_ghost_ledger_record(pos, [])
    assert exit_px == 0.0
    assert best is None


def test_back_out_skipped_when_zero_pnl_and_zero_exit(pt):
    """Degenerate row with no exit_price AND no realized_pnl can't reconcile."""
    pos = StubPos()
    records = [{
        "symbol": "BTC/USDT:USDT", "side": None,
        "close_time": 2000.0, "exit_price": 0.0, "realized_pnl": 0.0,
    }]
    exit_px, best = pt.match_ghost_ledger_record(pos, records)
    assert exit_px == 0.0
    assert best is None


def test_back_out_skipped_when_size_zero(pt):
    """Position with size=0 would divide by zero — skip the back-out."""
    pos = StubPos(size=0.0)
    records = [{
        "symbol": "BTC/USDT:USDT", "side": None,
        "close_time": 2000.0, "exit_price": 0.0, "realized_pnl": -5.0,
    }]
    exit_px, _ = pt.match_ghost_ledger_record(pos, records)
    assert exit_px == 0.0


# ── Real-world scenario: today's 04-27 ghosts ────────────────────────────

def test_link_ghost_reconciled_via_explicit_exit(pt):
    """Today's id=485 LINK ghost succeeded — Bybit/Bitget gave exit_price."""
    pos = StubPos(symbol="LINK/USDT:USDT", side="buy",
                  entry_price=14.27, size=1.05, open_time=1777231410.0)
    records = [{
        "symbol": "LINK/USDT:USDT", "side": "buy",
        "close_time": pos.open_time + 6300.0,  # ~1.77h later
        "exit_price": 13.64,  # SL fill below entry
        "realized_pnl": -0.66,
    }]
    exit_px, _ = pt.match_ghost_ledger_record(pos, records)
    assert exit_px == 13.64


def test_btc_ghost_now_reconciles_via_back_out(pt):
    """Today's id=487 BTC ghost was Binance — failed reconcile pre-fix.
    Post-fix: side=None doesn't filter, exit backed out from pnl."""
    pos = StubPos(symbol="BTC/USDT:USDT", side="buy",
                  entry_price=43250.0, size=0.0023, open_time=1777230660.0)
    records = [{
        "symbol": "BTC/USDT:USDT", "side": None,    # Binance income-ledger
        "close_time": pos.open_time + 27800.0,
        "exit_price": 0.0,                          # Binance never has it
        "realized_pnl": -2.07,                      # actual loss
    }]
    exit_px, _ = pt.match_ghost_ledger_record(pos, records)
    # 43250 + (-2.07 / (0.0023 * 1)) = 43250 - 900 = 42350
    assert exit_px == pytest.approx(42350.0, rel=1e-4)


# ── Exchange-side conditional fill reclassification (2026-05-24) ─────────
#
# When sync_with_exchanges detects a ghost AND the reconciled exit price
# matches an exchange-placed SL or TP within 50bps, the close should be
# labelled "stop_loss" / "take_profit" instead of "ghost_reconciled" /
# "ghost_sync". Reserves ghost_* for genuinely-unexpected closes.

def test_classify_exchange_sl_within_tolerance(pt):
    """Buy with _exchange_sl=True, exit ~7bps below SL → stop_loss."""
    pos = StubPos(_exchange_sl=True, stop_loss=70000.0)
    assert pt._classify_conditional_fill(pos, 69995.0) == "stop_loss"


def test_classify_exchange_tp_within_tolerance(pt):
    """Buy with _exchange_tp=True, exit ~7bps below TP → take_profit."""
    pos = StubPos(_exchange_tp=True, take_profit=71400.0)
    assert pt._classify_conditional_fill(pos, 71350.0) == "take_profit"


def test_classify_keeps_ghost_when_mid_range(pt):
    """Both flags True, exit equidistant from SL and TP → None
    (caller keeps the ghost_* label)."""
    pos = StubPos(_exchange_sl=True, _exchange_tp=True,
                  stop_loss=69000.0, take_profit=71000.0)
    # exit at 70000 is 1.45% from SL and 1.41% from TP — neither within 50bps
    assert pt._classify_conditional_fill(pos, 70000.0) is None


def test_classify_skips_when_flag_false(pt):
    """Local-monitor SL (_exchange_sl=False) at the SL level → None,
    even when stop_loss attribute matches. We only reclassify
    exchange-placed conditionals — local-monitor closes route through
    the normal close_position path."""
    pos = StubPos(_exchange_sl=False, stop_loss=70000.0)
    assert pt._classify_conditional_fill(pos, 70000.0) is None


def test_classify_handles_mark_price_path(pt):
    """The two-pass-failed branch passes the mark/ticker fallback price
    instead of the ledger exit. Helper still classifies correctly when
    the fallback price lands near SL — covers the ghost_sync site."""
    pos = StubPos(_exchange_sl=True, stop_loss=70000.0,
                  side="sell")  # sell-side too
    mark_price = 70030.0  # ~4bps above SL — short-side SL trigger price
    assert pt._classify_conditional_fill(pos, mark_price) == "stop_loss"
