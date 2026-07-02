"""core/market_data_ledger.py — Phase C: formalized immutable market-data accessor.

A thin, back-compatible facade over the existing feed layer. Two jobs:

  1. ``mark_index(symbol)`` — per-venue mark + index price frame across all three
     venues, using the new ``BaseExchange.fetch_mark_index`` (absent before Phase
     C). Returns an IMMUTABLE mapping (``MappingProxyType``) so a consumer cannot
     mutate a shared snapshot — the "ledger" invariant.

  2. ``derivs(coins)`` — per-coin cross-venue funding/OI frame via the existing
     ``cross_venue`` harvesters (real, guarded, rate-limited fetch behind the
     ``_get`` seam; fixture-testable, no unconditional live call).

No live call at import; nothing here is wired to the order path. ``DataCoordinator``
(feature feeds) is untouched — this formalizes the per-venue microstructure side.
"""

from __future__ import annotations

import time
from types import MappingProxyType
from typing import Any, Mapping

from core.data_sources.cross_venue import (
    BinanceFundingMetaHarvester,
    BitgetDerivsHarvester,
    BitgetFundingMetaHarvester,
    BybitDerivsHarvester,
    BybitFundingMetaHarvester,
    cross_venue_quote_frame,
)

_EMPTY_MI = {"mark": None, "index": None, "ts": None}


class MarketDataLedger:
    """Immutable per-venue frame accessor over exchange clients + derivs harvesters."""

    def __init__(
        self,
        clients: Mapping[str, Any] | None = None,
        *,
        harvesters: Mapping[str, Any] | None = None,
        funding_harvesters: Mapping[str, Any] | None = None,
    ):
        # {venue: exchange_client}. Client must expose ``fetch_mark_index``.
        self._clients: dict[str, Any] = dict(clients or {})
        self._harvesters: dict[str, Any] | None = (
            dict(harvesters) if harvesters is not None else None
        )
        self._funding_harvesters: dict[str, Any] | None = (
            dict(funding_harvesters) if funding_harvesters is not None else None
        )

    # ------------------------------------------------------------- mark/index
    def mark_index(
        self, symbol: str, market_type: str = "futures"
    ) -> Mapping[str, Mapping[str, Any]]:
        """Return an immutable ``{venue: {'mark','index','ts'}}`` frame.

        Fail-open per venue: a client that raises or is missing the method
        contributes a neutral all-None record rather than breaking the frame.
        """
        frame: dict[str, Mapping[str, Any]] = {}
        for venue, client in self._clients.items():
            rec = dict(_EMPTY_MI)
            fn = getattr(client, "fetch_mark_index", None)
            if callable(fn):
                try:
                    got = fn(symbol, market_type) or {}
                    rec = {
                        "mark": got.get("mark"),
                        "index": got.get("index"),
                        "ts": got.get("ts"),
                    }
                except Exception:  # noqa: BLE001
                    rec = dict(_EMPTY_MI)
            frame[venue] = MappingProxyType(rec)
        return MappingProxyType(frame)

    # ------------------------------------------------------------------ derivs
    def _venue_harvesters(self) -> dict[str, Any]:
        if self._harvesters is not None:
            return self._harvesters
        return {
            "bybit": BybitDerivsHarvester(),
            "bitget": BitgetDerivsHarvester(),
        }

    # ----------------------------------------------------------------- funding
    def _venue_funding_harvesters(self) -> dict[str, Any]:
        if self._funding_harvesters is not None:
            return self._funding_harvesters
        return {
            "binance": BinanceFundingMetaHarvester(),
            "bybit": BybitFundingMetaHarvester(),
            "bitget": BitgetFundingMetaHarvester(),
        }

    def funding(
        self, coins: list[str], *, force: bool = False
    ) -> Mapping[str, Mapping[str, Mapping[str, Any]]]:
        """Immutable ``{coin: {venue: {rate, interval_hours, next_funding_ts,
        age_sec, stale, ...}}}`` funding-metadata frame (Phase 1, F1 data spine).

        Fail-open per venue: a harvester that errors contributes a stale record
        (``age_sec=inf``) rather than raising into the caller.
        """
        now = time.time()
        frame: dict[str, dict[str, Any]] = {c: {} for c in coins}
        for venue, harv in self._venue_funding_harvesters().items():
            try:
                snap = harv.snapshot(coins, force=force)
            except Exception:  # noqa: BLE001
                snap = {}
            for coin in coins:
                rec = dict(snap.get(coin) or {})
                if not rec or rec.get("stale") or rec.get("ts") is None:
                    rec = {
                        "venue": venue,
                        "symbol": rec.get("symbol", coin),
                        "rate": rec.get("rate"),
                        "interval_hours": rec.get("interval_hours"),
                        "next_funding_ts": rec.get("next_funding_ts"),
                        "ts": rec.get("ts"),
                        "age_sec": float("inf"),
                        "stale": True,
                    }
                else:
                    rec["age_sec"] = max(0.0, now - float(rec["ts"]))
                frame[coin][venue] = MappingProxyType(rec)
        return MappingProxyType(
            {c: MappingProxyType(v) for c, v in frame.items()}
        )

    def derivs(
        self, coins: list[str], *, force: bool = False
    ) -> Mapping[str, dict[str, Any]]:
        """Per-coin cross-venue funding/OI/mid frame (guarded, rate-limited)."""
        harv = self._venue_harvesters()
        snaps = {v: h.snapshot(coins, force=force) for v, h in harv.items()}
        return cross_venue_quote_frame(snaps)
