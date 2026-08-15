"""Regression: Bitget UTA futures balance must not be discarded as a failure.

2026-08-15: logs showed `[Bitget] fetch_balance futures: all methods failed`
firing every few seconds for hours. The venue was NOT failing — the account
is a UTA (Unified Trading Account) holding SUI/FET/BGB and zero USDT, and
ccxt returned a perfectly good balance dict on the FIRST attempt. The loop
in BitgetClient.fetch_balance only accepted a response when
`bal.get("USDT")` was truthy, so a successful call on an account with no
USDT looked identical to five failed API calls:

    for params in [...]:
        try:
            bal = ...fetch_balance(params)
            usdt = bal.get("USDT") or bal.get("free", {}).get("USDT")
            if usdt is not None:      # <- None on a real UTA response
                return bal
        except Exception:
            pass                      # <- and every real error was swallowed
    logger.debug("all methods failed")   # <- lies: nothing failed
    return {}

Two defects, one symptom:
  1. success-detection keyed on a coin balance that may legitimately be
     absent (returns {} => callers see "no balance / venue down");
  2. bare `except Exception: pass` erased the real reason, so the log line
     could never say what actually went wrong.

The fix accepts any well-formed ccxt balance mapping and reports the last
real error when every attempt genuinely fails.

Run: venv/Scripts/python.exe -m pytest tests/test_bitget_uta_balance.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import exchanges.bitget_client as bitget_client  # noqa: E402

_LOGURU_SINK: list = []


@pytest.fixture(autouse=True)
def _loguru_to_list():
    """The module logs via loguru, which bypasses pytest's caplog."""
    from loguru import logger

    _LOGURU_SINK.clear()
    sink_id = logger.add(lambda m: _LOGURU_SINK.append(str(m)), level="DEBUG")
    yield
    logger.remove(sink_id)


class _RawExchange:
    """Minimal ccxt stand-in: records params, returns a scripted result."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []
        self.options = {"defaultType": "swap"}

    def fetch_balance(self, params=None):
        self.calls.append(dict(params or {}))
        r = self._results[min(len(self.calls) - 1, len(self._results) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


def _client(raw):
    import threading

    c = object.__new__(bitget_client.BitgetClient)
    c.exchange = raw
    c._connected = True
    c._uta = True
    c._defaultType_lock = threading.RLock()  # normally built in __init__
    return c


# A REAL Bitget UTA futures response: coins held, no USDT key at all.
UTA_BALANCE = {
    "info": [{"coin": "SUI", "equity": "93.93597", "available": "93.93597"}],
    "SUI": {"free": 93.93597, "used": 0.0, "total": 93.93597},
    "FET": {"free": 390.609, "used": 0.0, "total": 390.609},
    "free": {"SUI": 93.93597, "FET": 390.609},
    "used": {"SUI": 0.0, "FET": 0.0},
    "total": {"SUI": 93.93597, "FET": 390.609},
}


def test_uta_balance_without_usdt_is_returned_not_discarded():
    """THE defect: a good response for a USDT-less UTA account was dropped."""
    raw = _RawExchange([UTA_BALANCE])
    got = _client(raw).fetch_balance("futures")
    assert got == UTA_BALANCE, "a valid UTA balance must be returned, not {}"
    assert len(raw.calls) == 1, "must accept the FIRST good response, not retry 5x"


def test_zero_usdt_balance_is_a_real_answer():
    """USDT present but zero is a legitimate balance, not a failure."""
    bal = {"info": [], "USDT": {"free": 0.0, "used": 0.0, "total": 0.0},
           "free": {"USDT": 0.0}, "used": {"USDT": 0.0}, "total": {"USDT": 0.0}}
    raw = _RawExchange([bal])
    assert _client(raw).fetch_balance("futures") == bal


def test_funded_usdt_account_still_works():
    """Classic funded account keeps working (no regression)."""
    bal = {"info": [], "USDT": {"free": 5000.0, "used": 0.0, "total": 5000.0},
           "free": {"USDT": 5000.0}, "used": {}, "total": {"USDT": 5000.0}}
    raw = _RawExchange([bal])
    assert _client(raw).fetch_balance("futures") == bal


def test_falls_through_variants_until_one_succeeds():
    """A venue rejecting the first params must still be retried."""
    import ccxt

    raw = _RawExchange([ccxt.ExchangeError("40034 param error"), UTA_BALANCE])
    got = _client(raw).fetch_balance("futures")
    assert got == UTA_BALANCE
    assert len(raw.calls) == 2


def test_genuine_total_failure_returns_empty_and_names_the_reason():
    """When everything really fails, the log must carry the REAL error."""
    import ccxt

    raw = _RawExchange([ccxt.AuthenticationError("40085 UTA mismatch")])
    got = _client(raw).fetch_balance("futures")
    assert got == {}
    blob = " ".join(_LOGURU_SINK)
    assert "40085" in blob or "AuthenticationError" in blob, (
        "a total failure must report the underlying error, not just "
        "'all methods failed'"
    )


def test_non_mapping_response_is_rejected():
    """A malformed (non-dict) response must not be passed off as a balance."""
    raw = _RawExchange([["not", "a", "balance"]])
    assert _client(raw).fetch_balance("futures") == {}


def test_default_type_restored_to_spot_after_futures_fetch():
    """The shared defaultType must be reset so later spot calls are correct."""
    raw = _RawExchange([UTA_BALANCE])
    c = _client(raw)
    c.fetch_balance("futures")
    assert raw.options["defaultType"] == "spot"
