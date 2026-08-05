"""Regression: VirtualWallet balance mutations must be serialized by a lock.

Audit 2026-06-04 (HIGH, paper-only): VirtualWallet had NO lock. on_open
(_balances[ex]=bal-cost), on_close (_balances[ex]=bal+proceeds) and the funding
path in order_manager (direct prev/prev-cost mutation) all did an unlocked
read-modify-write on the SAME per-exchange key. The bot hits ONE shared wallet
from several threads — the 5-min portfolio cycle (on_open), and the 10s SL/TP
daemon whose ThreadPoolExecutor fans (exchange, market_type) so a spot close and
a futures close on the same exchange run in parallel (on_close + funding). The
RMWs interleaved, an update was lost, and the paper balance drifted from true
paper PnL — corrupting the data the learning-first phase depends on. Plus two
concurrent _save() (plain write_text) could tear virtual_wallet.json.

Fix: a threading.RLock guards on_open/on_close/reset/apply_funding RMW+_save;
_save is atomic (tmp + os.replace); order_manager.accrue_paper_funding calls the
locked wallet.apply_funding instead of mutating _balances directly.

These tests are deterministic (no flaky stress loop): hold the lock and prove the
mutator blocks, plus source-level pins on the wiring.
"""
from __future__ import annotations

import threading
from pathlib import Path

from tests.order_manager_source import order_manager_impl_source

from core.virtual_wallet import VirtualWallet


def _wallet(tmp_path, monkeypatch):
    # isolate the on-disk file so _save doesn't touch the real data/ dir
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    w = VirtualWallet()
    w._enabled = True
    w._start = 1000.0
    w._balances = {}
    return w


def test_on_close_acquires_the_lock(tmp_path, monkeypatch):
    """While the wallet lock is held, on_close must BLOCK (proving it takes the
    lock), then complete once the lock is released."""
    w = _wallet(tmp_path, monkeypatch)
    done = threading.Event()

    def worker():
        w.on_close("binance", "BTC/USDT:USDT", "buy", 1.0, 110.0, 100.0,
                   0.0, 10.0, "futures", 1)
        done.set()

    w._lock.acquire()
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        # Held lock => the mutator cannot proceed.
        assert not done.wait(timeout=0.4), "on_close did NOT wait on self._lock"
    finally:
        w._lock.release()
    assert done.wait(timeout=3.0), "on_close never completed after lock release"
    t.join(timeout=3.0)


def test_on_open_acquires_the_lock(tmp_path, monkeypatch):
    w = _wallet(tmp_path, monkeypatch)
    done = threading.Event()

    def worker():
        w.on_open("binance", "BTC/USDT:USDT", "buy", 1.0, 100.0, 0.05, "futures", 2)
        done.set()

    w._lock.acquire()
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        assert not done.wait(timeout=0.4), "on_open did NOT wait on self._lock"
    finally:
        w._lock.release()
    assert done.wait(timeout=3.0), "on_open never completed after lock release"
    t.join(timeout=3.0)


def test_apply_funding_adjusts_balance(tmp_path, monkeypatch):
    """apply_funding debits on +cost, credits on -cost, returns new balance."""
    w = _wallet(tmp_path, monkeypatch)
    w._balances["binance"] = 500.0
    assert abs(w.apply_funding("binance", 3.0) - 497.0) < 1e-9   # +cost debits
    assert abs(w.balance("binance") - 497.0) < 1e-9
    assert abs(w.apply_funding("binance", -1.0) - 498.0) < 1e-9  # -cost credits


def test_wiring_source_pins():
    wsrc = Path("core/virtual_wallet.py").read_text(encoding="utf-8")
    assert "self._lock" in wsrc and "threading.RLock()" in wsrc
    # the three RMW mutators + funding must each take the lock
    for meth in ("def on_open", "def on_close", "def apply_funding", "def reset"):
        i = wsrc.index(meth)
        body = wsrc[i:i + 1400]
        assert "with self._lock:" in body, f"{meth} must hold self._lock"
    assert "os.replace(" in wsrc, "_save must be atomic (tmp + os.replace)"

    osrc = order_manager_impl_source()
    assert "self.wallet.apply_funding(" in osrc, (
        "accrue_paper_funding must use the locked wallet.apply_funding")
    assert "self.wallet._balances[name] = prev - cost" not in osrc, (
        "accrue_paper_funding must NOT mutate self.wallet._balances directly anymore")
