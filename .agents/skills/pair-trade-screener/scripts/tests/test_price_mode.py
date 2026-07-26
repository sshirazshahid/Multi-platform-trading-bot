"""Unit tests for pair-trade price-mode consistency (no live FMP calls)."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import find_pairs as fp


def _bar_list(n=260, *, price_key="close", start=100.0):
    """Newest-first FMP-shaped bars; newest price == start."""
    rows = []
    base = date(2025, 1, 1)
    for i in range(n):
        px = start + i * 0.1
        day = (base + timedelta(days=n - 1 - i)).isoformat()
        row = {"date": day, price_key: px}
        if price_key == "adjClose":
            row["close"] = px
        else:
            row["adjClose"] = px
        rows.append(row)
    return rows


def _resp(status, payload):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.raise_for_status = MagicMock()
    r.text = f"HTTP {status}"
    return r


def test_batch_uses_close_for_all_when_adj_partial(monkeypatch):
    """If adj works for A but 402 for B, both legs must use unadjusted close."""

    def fake_get(url, params=None, timeout=30):
        sym = (params or {}).get("symbol")
        if "dividend-adjusted" in url:
            if sym == "AAA":
                return _resp(200, _bar_list(price_key="adjClose", start=100.0))
            return _resp(402, {"Error Message": "Premium"})
        return _resp(
            200,
            _bar_list(price_key="close", start=50.0 if sym == "BBB" else 100.0),
        )

    monkeypatch.setattr(fp.time, "sleep", lambda *_a, **_k: None)
    with patch.object(fp.requests, "get", side_effect=fake_get):
        data = fp.fetch_price_data_batch(["AAA", "BBB"], api_key="k", lookback_days=730)

    assert set(data) == {"AAA", "BBB"}
    # Newest (iloc[-1]) == start for close re-fetch
    assert float(data["AAA"].iloc[-1]) == pytest.approx(100.0)
    assert float(data["BBB"].iloc[-1]) == pytest.approx(50.0)


def test_fetch_single_auto_prefers_adj():
    with patch.object(
        fp.requests,
        "get",
        return_value=_resp(200, _bar_list(price_key="adjClose")),
    ):
        series, mode = fp.fetch_historical_prices("AAA", "k", lookback_days=730, mode="auto")
    assert mode == "adj"
    assert series is not None
    assert len(series) >= 250


def test_rejects_non_bar_list():
    with patch.object(fp.requests, "get", return_value=_resp(200, [1, 2, 3])):
        series, mode = fp.fetch_historical_prices("AAA", "k", mode="close")
    assert series is None and mode is None
