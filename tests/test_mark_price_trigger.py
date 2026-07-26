"""C3 (tpbot retrofit): mark-price trigger basis for SL/TP conditionals.

Gap (survey 2026-07-08): no live code ever set a trigger price basis, so all
three venues ran their defaults — Binance CONTRACT_PRICE (last), Bybit
LastPrice, Bitget mark_price (ccxt 4.5.54 defaults tpsl triggerType to
'mark_price' already). Last-price triggering is wick-sensitive to single
rogue prints and desynced from the mark-price liquidation engine.

``SLTP_TRIGGER_MARK_PRICE`` (config, default ON) makes the basis explicit on
every venue via the pure builder ``build_sl_tp_order_params``:

  Binance USD-M -> workingType=MARK_PRICE
  Bybit v5      -> triggerBy=MarkPrice + slTriggerBy/tpTriggerBy=MarkPrice
                   (ccxt maps all three into the v5 request — bybit.py
                   request['triggerBy'/'slTriggerBy'/'tpTriggerBy'])
  Bitget        -> triggerType=mark_price (explicit; matches the ccxt
                   default, read then omitted by ccxt — never collides with
                   the triggerPrice/stopLossPrice mutual-exclusivity rule)

Flag OFF must be byte-identical to the legacy params (WR-floor rule: the
semantic change is opt-out-able and documented in CLAUDE.md gotchas).

Materially this changes Binance and Bybit only; Bitget was already on mark
price via the ccxt default — asserting it explicitly here pins that default
against future ccxt upgrades.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import config
from core.order_manager import build_sl_tp_order_params


# ── Binance ──────────────────────────────────────────────────────────────────
def test_binance_sl_flag_on_sets_working_type_mark_price():
    order_type, params = build_sl_tp_order_params(
        "binance", side="buy", oneway=True, trigger_price=95.0, is_sl=True, mark_trigger=True
    )
    assert order_type == "STOP_MARKET"
    assert params["workingType"] == "MARK_PRICE"
    assert params["stopPrice"] == 95.0
    assert params["reduceOnly"] is True


def test_binance_tp_flag_on_sets_working_type_mark_price():
    order_type, params = build_sl_tp_order_params(
        "binance", side="buy", oneway=True, trigger_price=110.0, is_sl=False, mark_trigger=True
    )
    assert order_type == "TAKE_PROFIT_MARKET"
    assert params["workingType"] == "MARK_PRICE"


def test_binance_flag_off_is_byte_identical_legacy():
    _, params = build_sl_tp_order_params(
        "binance", side="buy", oneway=True, trigger_price=95.0, is_sl=True, mark_trigger=False
    )
    assert params == {"stopPrice": 95.0, "reduceOnly": True}


def test_binance_hedge_mode_position_side_preserved():
    _, params = build_sl_tp_order_params(
        "binance", side="sell", oneway=False, trigger_price=105.0, is_sl=True, mark_trigger=True
    )
    assert params["positionSide"] == "SHORT"
    assert params["workingType"] == "MARK_PRICE"


# ── Bybit ────────────────────────────────────────────────────────────────────
def test_bybit_sl_flag_on_sets_mark_price_trigger_by():
    _, params = build_sl_tp_order_params(
        "bybit", side="buy", oneway=True, trigger_price=95.0, is_sl=True, mark_trigger=True
    )
    assert params["triggerBy"] == "MarkPrice"
    assert params["slTriggerBy"] == "MarkPrice"
    assert "tpTriggerBy" not in params
    # direction semantics untouched (Apr-21 lesson stays locked)
    assert params["triggerDirection"] == "below"
    assert params["stopLossPrice"] == 95.0


def test_bybit_tp_flag_on_sets_mark_price_trigger_by():
    _, params = build_sl_tp_order_params(
        "bybit", side="buy", oneway=True, trigger_price=110.0, is_sl=False, mark_trigger=True
    )
    assert params["triggerBy"] == "MarkPrice"
    assert params["tpTriggerBy"] == "MarkPrice"
    assert "slTriggerBy" not in params
    assert params["triggerDirection"] == "above"


def test_bybit_flag_off_has_no_trigger_by_keys():
    _, params = build_sl_tp_order_params(
        "bybit", side="buy", oneway=True, trigger_price=95.0, is_sl=True, mark_trigger=False
    )
    for k in ("triggerBy", "slTriggerBy", "tpTriggerBy"):
        assert k not in params


# ── Bitget ───────────────────────────────────────────────────────────────────
def test_bitget_sl_flag_on_sets_trigger_type_mark_price():
    _, params = build_sl_tp_order_params(
        "bitget", side="buy", oneway=True, trigger_price=95.0, is_sl=True, mark_trigger=True
    )
    assert params["triggerType"] == "mark_price"
    # mutual-exclusivity rule unchanged: only ONE of the trigger price keys
    assert "stopLossPrice" in params
    assert "triggerPrice" not in params
    assert "takeProfitPrice" not in params


def test_bitget_tp_flag_on_sets_trigger_type_mark_price():
    _, params = build_sl_tp_order_params(
        "bitget", side="sell", oneway=True, trigger_price=90.0, is_sl=False, mark_trigger=True
    )
    assert params["triggerType"] == "mark_price"
    assert "takeProfitPrice" in params


def test_bitget_flag_off_has_no_trigger_type():
    _, params = build_sl_tp_order_params(
        "bitget", side="buy", oneway=True, trigger_price=95.0, is_sl=True, mark_trigger=False
    )
    assert "triggerType" not in params


# ── config-driven default (mark_trigger=None reads the flag) ─────────────────
def test_default_reads_config_flag_on(monkeypatch):
    monkeypatch.setattr(config, "SLTP_TRIGGER_MARK_PRICE", True, raising=False)
    _, params = build_sl_tp_order_params(
        "binance", side="buy", oneway=True, trigger_price=95.0, is_sl=True
    )
    assert params.get("workingType") == "MARK_PRICE"


def test_default_reads_config_flag_off(monkeypatch):
    monkeypatch.setattr(config, "SLTP_TRIGGER_MARK_PRICE", False, raising=False)
    _, params = build_sl_tp_order_params(
        "binance", side="buy", oneway=True, trigger_price=95.0, is_sl=True
    )
    assert params == {"stopPrice": 95.0, "reduceOnly": True}


def test_config_flag_exists_and_defaults_on():
    # The flag must exist in config (env-overridable) and default ON per the
    # approved plan. Inspect the declared fallback rather than the imported
    # runtime value because a developer's .env may deliberately exercise the
    # documented revert switch.
    source = Path(config.__file__).read_text(encoding="utf-8")
    assert 'os.getenv("SLTP_TRIGGER_MARK_PRICE", "true")' in source
    assert isinstance(getattr(config, "SLTP_TRIGGER_MARK_PRICE", None), bool)


@pytest.mark.parametrize("venue", ["binance", "bybit", "bitget"])
def test_flag_off_matches_pre_c3_shapes_all_venues(venue):
    """Regression pin: flag OFF must produce exactly the pre-C3 key set."""
    _, sl = build_sl_tp_order_params(
        venue, side="buy", oneway=True, trigger_price=95.0, is_sl=True, mark_trigger=False
    )
    for k in ("workingType", "triggerBy", "slTriggerBy", "tpTriggerBy", "triggerType"):
        assert k not in sl, f"{venue} SL flag-off leaked {k}"
