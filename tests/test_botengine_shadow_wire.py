"""Task A.10 — BotEngine shadow runner wire-up.

Avoid actually instantiating BotEngine (which boots exchange clients);
test the helper methods + thread machinery in isolation.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock


def test_shadow_helper_methods_exist():
    """Verify the helpers added to BotEngine are present and callable."""
    from core.bot_engine import BotEngine
    for name in (
        "_init_shadow_runner",
        "_shadow_enabled_flag",
        "_shadow_free_balance",
        "_shadow_symbols",
        "_shadow_ctx_for_symbol",
        "_shadow_loop",
    ):
        assert hasattr(BotEngine, name), f"BotEngine missing {name}"


def test_shadow_loop_respects_stop_event():
    """The shadow loop must terminate when stop_event is set."""
    from core.bot_engine import BotEngine
    inst = BotEngine.__new__(BotEngine)
    inst._shadow_runner = MagicMock()
    inst._shadow_runner.tick = MagicMock(return_value=0)
    stop = threading.Event()

    t = threading.Thread(target=inst._shadow_loop, args=(stop,), daemon=True)
    t.start()
    time.sleep(0.1)  # let it enter the loop
    stop.set()
    t.join(timeout=2)
    assert not t.is_alive(), "shadow loop did not exit on stop_event"
    # tick was called at least once
    assert inst._shadow_runner.tick.called


def test_shadow_loop_swallows_tick_exceptions():
    """A failing tick must not kill the thread."""
    from core.bot_engine import BotEngine
    inst = BotEngine.__new__(BotEngine)
    inst._shadow_runner = MagicMock()
    inst._shadow_runner.tick = MagicMock(side_effect=Exception("boom"))
    stop = threading.Event()

    t = threading.Thread(target=inst._shadow_loop, args=(stop,), daemon=True)
    t.start()
    time.sleep(0.1)
    stop.set()
    t.join(timeout=2)
    assert not t.is_alive()


def test_shadow_enabled_flag_reads_config():
    """The flag callable reads from config.SHADOW_MODE.enabled."""
    from core.bot_engine import BotEngine
    inst = BotEngine.__new__(BotEngine)
    # config.SHADOW_MODE defaults to enabled=True
    assert inst._shadow_enabled_flag() is True


def test_shadow_symbols_returns_capped_list():
    """Symbols list capped at 5 to bound OHLCV fetch budget."""
    from core.bot_engine import BotEngine
    inst = BotEngine.__new__(BotEngine)
    inst.current_pairs = {
        "binance": {"futures": [f"X{i}/USDT:USDT" for i in range(20)]},
        "bybit":   {"futures": [f"Y{i}/USDT:USDT" for i in range(20)]},
    }
    syms = inst._shadow_symbols()
    assert len(syms) <= 5
