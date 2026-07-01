"""Regression: BaseExchange classifies rate-limit / throttle errors as transient.

Gap (2026-06-28): exchanges/base.py only string-matched "429"/"rate limit", so a
Bybit per-IP HTTP 403 "access too frequent" ban (or retCode 10006 "Too many
visits") was classified NON-transient -> failed instantly with no backoff, which
could drop a stop-loss. Now these (and the ccxt RateLimitExceeded/DDoSProtection
types) retry with backoff. create_order still does not retry (no duplicate risk).
"""
import ccxt

from exchanges.base import _is_transient_error


def test_ccxt_typed_throttle_exceptions_are_transient():
    assert _is_transient_error(ccxt.RateLimitExceeded("429")) is True
    assert _is_transient_error(ccxt.DDoSProtection("403 Forbidden")) is True


def test_bybit_throttle_messages_are_transient():
    assert _is_transient_error(Exception("bybit retCode 10006 Too many visits!")) is True
    assert _is_transient_error(Exception("HTTP 403 access too frequent")) is True
    assert _is_transient_error(Exception("DDoSProtection triggered")) is True


def test_existing_transients_still_classified():
    assert _is_transient_error(Exception("429 Too Many Requests")) is True
    assert _is_transient_error(Exception("ETIMEDOUT")) is True


def test_non_throttle_errors_are_not_transient():
    assert _is_transient_error(Exception("insufficient balance")) is False
    assert _is_transient_error(Exception("position not found")) is False


def test_no_bare_403_false_positive():
    # A "403" substring inside an order id / price must NOT be treated as a throttle.
    assert _is_transient_error(Exception("order id 1403 filled at 2403.5")) is False
