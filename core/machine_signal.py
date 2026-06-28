"""Deterministic machine signal adapter.

This is the production-facing wrapper around ``MachineStrategyEngine``. It
keeps the same ``analyze_portfolio`` contract used by MCPBrain and TSMOMSignal,
so BotEngine can switch signal sources without changing execution, risk, or
warehouse plumbing.

The module is deliberately mechanical: OHLCV in, action dictionaries out. No
sentiment, no narrative chart reading, no LLM calls.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from core.adaptive_config import (
    ADAPTIVE_MACHINE_CONFIG_PATH,
    load_adaptive_machine_config,
    merge_machine_config,
)
from core.machine_strategy_engine import MachineStrategyConfig, MachineStrategyEngine

DEFAULT_MACHINE_SOURCE = "machine"
MACHINE_DECISION_LOG = Path("data/machine_decisions.jsonl")


class MachineSignal:
    """Drop-in machine-only signal source for BotEngine."""

    def __init__(self, exchanges: dict, config: dict[str, Any] | None = None):
        self.exchanges = exchanges or {}
        self._base_config = dict(config or {})
        self.config = dict(self._base_config)
        self.source = DEFAULT_MACHINE_SOURCE
        self._adaptive_path = Path(
            self.config.get("adaptive_config_path") or ADAPTIVE_MACHINE_CONFIG_PATH
        )
        self._adaptive_mtime: float | None = None
        self._adaptive_reason = "not_checked"
        self._configure_from_dict(self.config)
        self.last_status: list[dict[str, Any]] = []

    def _configure_from_dict(self, config: dict[str, Any]) -> None:
        self.config = dict(config or {})
        self.timeframe = str(self.config.get("timeframe", "15m"))
        self.market_type = str(self.config.get("market_type", "futures")).lower()
        if self.market_type not in {"spot", "futures"}:
            self.market_type = "futures"
        self.lookback = int(self.config.get("lookback_candles", 220))
        self.max_actions = int(self.config.get("max_actions_per_cycle", 3))
        self.exchange_preference = [
            str(x).lower() for x in self.config.get("exchange_preference", [])
        ]
        self.universe = [
            _base(x) for x in self.config.get("universe", [])
            if _base(x)
        ]
        self.default_leverage = int(self.config.get("default_leverage", 1))
        self.size_pct = float(self.config.get("size_pct", 3.0))
        self.min_quote_volume = float(self.config.get("min_quote_volume", 0.0))
        self.max_spread_bps = float(self.config.get("max_spread_bps", 0.0))
        self.execution_confidence_floor = _clamp_unit(
            self.config.get("execution_confidence_floor", 0.50),
            default=0.50,
        )

        engine_cfg = MachineStrategyConfig(
            min_score=float(self.config.get("min_score", 0.34)),
            rr=float(self.config.get("rr", 1.8)),
            atr_period=int(self.config.get("atr_period", 14)),
            atr_stop_mult=float(self.config.get("atr_stop_mult", 1.6)),
            min_stop_pct=float(self.config.get("min_stop_pct", 0.0035)),
            max_stop_pct=float(self.config.get("max_stop_pct", 0.025)),
            scalp_time_stop_bars=int(self.config.get("scalp_time_stop_bars", 12)),
            allow_short_spot=bool(self.config.get("allow_short_spot", False)),
            detector_params=dict(self.config.get("detector_params") or {}),
            confirmation_detectors=tuple(self.config.get("confirmation_detectors") or ()),
            unconfirmed_min_score=float(self.config.get("unconfirmed_min_score", 0.0) or 0.0),
            weights=dict(self.config.get("weights") or {}),
        )
        self.engine = MachineStrategyEngine(engine_cfg)

    def _refresh_adaptive_config(self) -> None:
        try:
            mtime = self._adaptive_path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime is None:
            if self._adaptive_mtime is not None:
                self._adaptive_mtime = None
                self._adaptive_reason = "missing"
                self._configure_from_dict(self._base_config)
            return
        result = load_adaptive_machine_config(self._adaptive_path)
        self._adaptive_mtime = mtime
        self._adaptive_reason = result.reason
        self._configure_from_dict(merge_machine_config(self._base_config, result))

    def analyze_portfolio(
        self,
        coins: list,
        open_positions: list,
        exchange_balances: dict,
        risk_envelope: dict,
        recent_trades: list,
        news_context: dict | None = None,
    ) -> list[dict[str, Any]]:
        """Return OPEN/CLOSE actions matching the BotEngine action contract."""
        del exchange_balances, risk_envelope, recent_trades, news_context
        self._refresh_adaptive_config()

        actions: list[dict[str, Any]] = []
        status: list[dict[str, Any]] = []

        for pos in open_positions or []:
            close = self._time_stop_action(pos)
            if close is not None:
                _append_machine_decision(close)
                actions.append(close)

        occupied_bases = self._occupied_bases(open_positions)
        opens: list[dict[str, Any]] = []
        for base in self._candidates(coins):
            if base in occupied_bases:
                status.append({"base": base, "state": "occupied"})
                continue

            fetched = self._fetch_frame(base)
            if fetched is None:
                status.append({"base": base, "state": "no_data"})
                continue
            symbol, ex_name, frame, ticker_diag = fetched

            decision = self.engine.score_frame(symbol, frame, market_type=self.market_type)
            row = {
                "base": base,
                "state": decision.action.lower(),
                "side": decision.side,
                "score": round(decision.score, 4),
                "reasons": list(decision.reasons),
                "ticker": ticker_diag,
            }
            status.append(row)
            if decision.action == "OPEN":
                opens.append(self._open_action(decision, ex_name))

        opens.sort(key=lambda a: float(a.get("confidence", 0.0)), reverse=True)
        actions.extend(opens[: self.max_actions])
        self.last_status = status
        return actions

    def watch_summary(self) -> str:
        if not self.last_status:
            return "no machine symbols evaluated yet"
        opens = [s for s in self.last_status if s.get("state") == "open"]
        held = sum(1 for s in self.last_status if s.get("state") == "occupied")
        top = sorted(
            self.last_status,
            key=lambda s: float(s.get("score") or 0.0),
            reverse=True,
        )[:5]
        parts = [
            f"{s.get('base')}={s.get('state')}:{float(s.get('score') or 0.0):.2f}"
            for s in top
        ]
        return (
            f"machine tf={self.timeframe} opens={len(opens)} occupied={held} | "
            + " ".join(parts)
        )

    def _candidates(self, coins: list) -> list[str]:
        seen: set[str] = set()
        passed = [_base(c) for c in (coins or []) if _base(c)]
        passed_set = set(passed)
        source = self.universe or passed
        out: list[str] = []
        for b in source:
            if passed_set and b not in passed_set:
                continue
            if b not in seen:
                seen.add(b)
                out.append(b)
        return out

    @staticmethod
    def _occupied_bases(open_positions: list) -> set[str]:
        out: set[str] = set()
        for p in open_positions or []:
            b = _base(_get(p, "symbol", ""))
            if b:
                out.add(b)
        return out

    def _time_stop_action(self, pos: Any) -> dict[str, Any] | None:
        if not is_machine_position(pos):
            return None
        age_min = _safe_float(_get(pos, "age_min", _get(pos, "duration_minutes", 0.0)))
        max_min = _timeframe_minutes(self.timeframe) * int(
            self.config.get("scalp_time_stop_bars", 12)
        )
        if max_min <= 0 or age_min < max_min:
            return None
        return {
            "type": "CLOSE",
            "symbol": _get(pos, "symbol", ""),
            "exchange": str(_get(pos, "exchange", "")).lower(),
            "market_type": _get(pos, "market_type", self.market_type),
            "side": _get(pos, "side", "buy"),
            "leverage": int(_get(pos, "leverage", 1) or 1),
            "size_pct": 0.0,
            "sl_pct": 0.0,
            "tp_pct": 0.0,
            "confidence": 0.70,
            "reason": "machine_time_stop",
            "rationale": f"age {age_min:.0f}m >= {max_min:.0f}m",
            "position_id": _get(pos, "id", ""),
            "decision_id": str(uuid.uuid4()),
            "source": self.source,
            "strategy": "machine_strategy_engine",
        }

    def _fetch_frame(self, base: str) -> tuple[str, str, pd.DataFrame, dict] | None:
        symbol = _symbol_for(base, self.market_type)
        for ex_name, ex in self._ordered_exchanges():
            ticker_ok, ticker_diag = self._ticker_gate(ex, symbol)
            if not ticker_ok:
                continue
            try:
                raw = ex.fetch_ohlcv(
                    symbol,
                    self.timeframe,
                    limit=self.lookback,
                    market_type=self.market_type,
                )
            except TypeError:
                try:
                    raw = ex.fetch_ohlcv(symbol, self.timeframe, limit=self.lookback)
                except Exception:
                    raw = None
            except Exception:
                raw = None
            frame = _ohlcv_frame(_closed_rows(raw, self.timeframe))
            if frame is not None and len(frame) >= 40:
                return symbol, ex_name, frame, ticker_diag
        return None

    def _ordered_exchanges(self) -> list[tuple[str, Any]]:
        items = [(str(k).lower(), v) for k, v in self.exchanges.items()]
        if not self.exchange_preference:
            return items
        rank = {name: i for i, name in enumerate(self.exchange_preference)}
        return sorted(items, key=lambda kv: rank.get(kv[0], 10_000))

    def _ticker_gate(self, exchange: Any, symbol: str) -> tuple[bool, dict]:
        if not hasattr(exchange, "fetch_ticker"):
            return True, {"state": "ticker_unavailable"}
        try:
            ticker = exchange.fetch_ticker(symbol, self.market_type)
        except TypeError:
            try:
                ticker = exchange.fetch_ticker(symbol)
            except Exception:
                return True, {"state": "ticker_failed"}
        except Exception:
            return True, {"state": "ticker_failed"}

        diag = {"state": "ticker_ok"}
        qv = _first_float(ticker, ("quoteVolume", "quote_volume", "quoteVolume24h"))
        if qv is not None:
            diag["quote_volume"] = qv
            if self.min_quote_volume > 0 and qv < self.min_quote_volume:
                diag["state"] = "quote_volume_below_min"
                return False, diag

        bid = _first_float(ticker, ("bid", "bidPrice"))
        ask = _first_float(ticker, ("ask", "askPrice"))
        if bid and ask and bid > 0 and ask >= bid:
            mid = (bid + ask) / 2.0
            spread_bps = (ask - bid) / mid * 10_000.0 if mid > 0 else 0.0
            diag["spread_bps"] = spread_bps
            if self.max_spread_bps > 0 and spread_bps > self.max_spread_bps:
                diag["state"] = "spread_above_max"
                return False, diag
        return True, diag

    def _open_action(self, decision, ex_name: str) -> dict[str, Any]:
        action = decision.as_action_dict()
        raw_score = _clamp_unit(decision.score)
        raw_confidence = _clamp_unit(decision.confidence)
        execution_confidence = max(raw_confidence, self.execution_confidence_floor)
        score_100 = round(raw_score * 100.0, 2)
        diagnostics = dict(decision.diagnostics or {})
        diagnostics.update(
            {
                "machine_score_raw": round(raw_score, 4),
                "machine_confidence_raw": round(raw_confidence, 4),
                "execution_confidence": round(execution_confidence, 4),
                "execution_confidence_floor": round(self.execution_confidence_floor, 4),
            }
        )
        if execution_confidence > raw_confidence:
            reason = str(action.get("reason", "")).strip()
            suffix = f"exec_conf_floor={execution_confidence:.2f}"
            action["reason"] = f"{reason}; {suffix}" if reason else suffix
        action.update(
            {
                "exchange": ex_name,
                "leverage": self.default_leverage,
                "size_pct": self.size_pct,
                "confidence": round(execution_confidence, 4),
                "source": self.source,
                "strategy": "machine_strategy_engine",
                "decision_id": str(uuid.uuid4()),
                "score": score_100,
                "mcp_score": score_100,
                "machine_score": score_100,
                "machine_score_raw": round(raw_score, 4),
                "machine_confidence_raw": round(raw_confidence, 4),
                "execution_confidence_floor": round(self.execution_confidence_floor, 4),
                "time_stop_bars": int(self.config.get("scalp_time_stop_bars", 12)),
                "machine_diagnostics": diagnostics,
            }
        )
        _append_machine_decision(action)
        return action


def is_machine_action(action: Any) -> bool:
    return str((action or {}).get("source", "")).lower() == DEFAULT_MACHINE_SOURCE


def is_machine_position(pos: Any) -> bool:
    strategy = str(_get(pos, "strategy", "") or "").lower()
    source = str(_get(pos, "source", "") or "").lower()
    return strategy == DEFAULT_MACHINE_SOURCE or source == DEFAULT_MACHINE_SOURCE


def _base(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    return raw.split("/")[0].split(":")[0].replace("-USDT", "")


def _symbol_for(base: str, market_type: str) -> str:
    return f"{base}/USDT:USDT" if market_type == "futures" else f"{base}/USDT"


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _clamp_unit(value: Any, *, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = default
    if not pd.notna(v):
        v = default
    return max(0.0, min(1.0, v))


def _first_float(d: dict, keys: tuple[str, ...]) -> float | None:
    for k in keys:
        try:
            v = d.get(k)
        except AttributeError:
            return None
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _ohlcv_frame(rows: list | None) -> pd.DataFrame | None:
    if not rows:
        return None
    out = []
    for row in rows:
        if len(row) < 6:
            continue
        try:
            ts = float(row[0])
            if ts > 10_000_000_000:
                ts /= 1000.0
            out.append(
                [
                    int(ts),
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                    float(row[4]),
                    float(row[5]),
                ]
            )
        except (TypeError, ValueError):
            continue
    if not out:
        return None
    return pd.DataFrame(out, columns=["ts", "open", "high", "low", "close", "volume"])


def _closed_rows(rows: list | None, timeframe: str) -> list:
    if not rows:
        return []
    tf_ms = _timeframe_ms(timeframe)
    if tf_ms <= 0:
        return list(rows)
    now_ms = int(time.time() * 1000)
    closed = []
    for row in rows:
        try:
            ts = float(row[0])
            if ts < 10_000_000_000:
                ts *= 1000.0
            if ts + tf_ms <= now_ms:
                closed.append(row)
        except Exception:
            continue
    return closed


def _timeframe_ms(timeframe: str) -> int:
    tf = str(timeframe or "").strip().lower()
    if not tf:
        return 0
    units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
    try:
        return int(tf[:-1] or "1") * units.get(tf[-1], 0)
    except Exception:
        return 0


def _timeframe_minutes(timeframe: str) -> float:
    ms = _timeframe_ms(timeframe)
    return ms / 60_000.0 if ms > 0 else 0.0


def _append_machine_decision(action: dict[str, Any]) -> None:
    try:
        MACHINE_DECISION_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "entry" if action.get("type") == "OPEN" else "close",
            "source": DEFAULT_MACHINE_SOURCE,
            "decision_id": action.get("decision_id"),
            "symbol": action.get("symbol"),
            "side": action.get("side"),
            "score": action.get("machine_score_raw"),
            "confidence": action.get("confidence"),
            "reason": action.get("reason"),
            "diagnostics": action.get("machine_diagnostics") or {},
        }
        with MACHINE_DECISION_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        if MACHINE_DECISION_LOG.stat().st_size > 2_000_000:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            archive = MACHINE_DECISION_LOG.with_name(
                f"{MACHINE_DECISION_LOG.stem}.{stamp}{MACHINE_DECISION_LOG.suffix}"
            )
            MACHINE_DECISION_LOG.replace(archive)
            MACHINE_DECISION_LOG.touch()
    except Exception:
        return
