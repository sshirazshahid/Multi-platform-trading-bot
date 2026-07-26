"""Replay the deterministic MachineStrategyEngine on cached OHLCV.

This is the after-cost research gate for ``SIGNAL_SOURCE=machine``. It never
talks to an exchange and never places orders.

Backtest discipline:
* decision at bar t uses rows <= t only;
* entry fills at bar t+1 open with configured slippage;
* SL/TP are scanned from entry bar onward;
* if one candle touches both SL and TP, the stop wins;
* fees and slippage are subtracted from every trade.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.machine_strategy_engine import MachineStrategyConfig, MachineStrategyEngine
from core.strategy_readiness import (
    DEFAULT_THRESHOLDS,
    StrategyReadinessThresholds,
    evaluate_records,
)


class DatasetSelectionError(ValueError):
    """Raised when a cache file cannot be assigned to one market unambiguously."""


@dataclass(frozen=True)
class OHLCVDataset:
    path: str
    market_type: str
    symbol: str
    base: str
    classification: str
    metadata_path: str | None = None


@dataclass(frozen=True)
class DatasetSelection:
    requested_market_type: str
    datasets: tuple[OHLCVDataset, ...]
    issues: tuple[str, ...]
    discovered_files: int
    classified_counts: dict[str, int]
    max_files: int

    @property
    def files(self) -> list[str]:
        return [dataset.path for dataset in self.datasets]

    def as_dict(self) -> dict[str, Any]:
        reason = None
        if not self.datasets:
            reason = (
                f"no explicitly classified {self.requested_market_type} OHLCV cache "
                "was found; refusing to infer the market from an ambiguous filename"
            )
        return {
            "requested_market_type": self.requested_market_type,
            "eligible": bool(self.datasets),
            "reason": reason,
            "discovered_files": self.discovered_files,
            "classified_counts": dict(self.classified_counts),
            "selected_count": len(self.datasets),
            "max_files": self.max_files,
            "selection_method": "sha256_symbol_sample_v1" if self.max_files > 0 else "all",
            "datasets": [asdict(dataset) for dataset in self.datasets],
            "issues": list(self.issues),
        }


_MARKET_ALIASES = {
    "spot": "spot",
    "futures": "futures",
    "future": "futures",
    "perp": "futures",
    "perpetual": "futures",
    "swap": "futures",
}
_QUOTE_CODES = ("USDT", "USDC")


def _canonical_market(value: Any) -> str | None:
    return _MARKET_ALIASES.get(str(value or "").strip().lower())


def _metadata_for(path: Path) -> tuple[dict[str, Any] | None, Path | None]:
    candidates = (Path(str(path) + ".meta.json"), path.with_suffix(".meta.json"))
    seen: set[Path] = set()
    found: list[tuple[dict[str, Any], Path]] = []
    for candidate in candidates:
        if candidate in seen or not candidate.exists():
            continue
        seen.add(candidate)
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception as exc:
            raise DatasetSelectionError(f"invalid metadata {candidate}: {exc}") from exc
        if not isinstance(payload, dict):
            raise DatasetSelectionError(f"metadata {candidate} must be a JSON object")
        found.append((payload, candidate))
    if len(found) > 1 and found[0][0] != found[1][0]:
        raise DatasetSelectionError(
            f"conflicting metadata sidecars for {path}: {found[0][1]} and {found[1][1]}"
        )
    return found[0] if found else (None, None)


def _identity_from_symbol(symbol: Any) -> tuple[str, str, str] | None:
    raw = str(symbol or "").strip().upper()
    if "/" not in raw:
        return None
    base, contract = raw.split("/", 1)
    quote, separator, settlement = contract.partition(":")
    base = base.strip()
    quote = quote.strip()
    settlement = settlement.strip()
    if not base or quote not in _QUOTE_CODES:
        return None
    if separator and settlement != quote:
        return None
    return base, quote, "futures" if separator else "spot"


def classify_ohlcv_path(path: str | Path, timeframe: str) -> OHLCVDataset:
    """Classify one cache path using an explicit cache key, directory, or sidecar.

    ``core.feature_store`` serializes ``BTC/USDT`` as ``BTC-USDT`` and
    ``BTC/USDT:USDT`` as ``BTC-USDTUSDT``.  Those cache identities are treated as
    authoritative. Explicit ``*_spot`` / ``*_futures`` markers, a ``spot`` /
    ``futures`` directory, or a JSON sidecar with ``market_type`` are also accepted.
    Conflicting identities are rejected instead of guessed.
    """
    p = Path(path)
    suffix = f"_{timeframe}"
    stem = p.stem
    if not stem.endswith(suffix):
        raise DatasetSelectionError(f"{p} does not end with {suffix!r}")
    identity = stem[: -len(suffix)]

    metadata, metadata_path = _metadata_for(p)
    explicit_markets: list[tuple[str, str]] = []
    metadata_identity = None
    if metadata is not None:
        market = _canonical_market(metadata.get("market_type") or metadata.get("market"))
        if market is None:
            raise DatasetSelectionError(f"metadata {metadata_path} needs market_type=spot|futures")
        explicit_markets.append(("metadata", market))
        if metadata.get("symbol"):
            metadata_identity = _identity_from_symbol(metadata.get("symbol"))
            if metadata_identity is None:
                raise DatasetSelectionError(
                    f"metadata {metadata_path} has an invalid unified symbol"
                )
            if metadata_identity[2] != market:
                raise DatasetSelectionError(
                    f"metadata market_type conflicts with symbol in {metadata_path}"
                )

    marker_match = re.search(
        r"(?:_|-)(spot|futures|future|perp|perpetual|swap)$", identity, re.IGNORECASE
    )
    if marker_match:
        marker_market = _canonical_market(marker_match.group(1))
        assert marker_market is not None
        explicit_markets.append(("filename_marker", marker_market))
        identity = identity[: marker_match.start()]

    directory_markets = {
        market for part in p.parent.parts if (market := _canonical_market(part)) is not None
    }
    if len(directory_markets) > 1:
        raise DatasetSelectionError(f"conflicting market directories for {p}")
    if directory_markets:
        explicit_markets.append(("directory", next(iter(directory_markets))))

    symbol_match = re.fullmatch(
        r"([A-Z0-9][A-Z0-9._-]*)-(USDT|USDC)(USDT|USDC)?",
        identity.upper(),
    )
    if symbol_match:
        base, quote, settlement = symbol_match.groups()
        if settlement is not None and settlement != quote:
            raise DatasetSelectionError(f"mismatched quote/settlement cache key: {p}")
        filename_market = "futures" if settlement else "spot"
        # An explicit marker/directory/sidecar may label the otherwise plain symbol
        # spelling. Repeated settlement (USDTUSDT) remains an explicit futures key
        # and must never conflict with another declaration.
        if settlement or not explicit_markets:
            explicit_markets.append(("cache_key", filename_market))
    elif metadata_identity:
        base, quote, _ = metadata_identity
    else:
        raise DatasetSelectionError(f"unrecognized OHLCV cache identity: {p.name}")

    markets = {market for _, market in explicit_markets}
    if len(markets) != 1:
        declarations = ", ".join(f"{source}={market}" for source, market in explicit_markets)
        raise DatasetSelectionError(f"conflicting market identity for {p}: {declarations}")
    if not markets:
        raise DatasetSelectionError(f"no explicit market identity for {p}")
    market_type = next(iter(markets))
    if metadata_identity:
        metadata_base, metadata_quote, _ = metadata_identity
        if metadata_base != base or metadata_quote != quote:
            raise DatasetSelectionError(
                f"metadata symbol {metadata_base}/{metadata_quote} conflicts with "
                f"filename {base}/{quote}: {p}"
            )
    symbol = f"{base}/{quote}:{quote}" if market_type == "futures" else f"{base}/{quote}"
    classification = "+".join(source for source, _ in explicit_markets)
    return OHLCVDataset(
        path=str(p),
        market_type=market_type,
        symbol=symbol,
        base=base,
        classification=classification,
        metadata_path=str(metadata_path) if metadata_path else None,
    )


def load_ohlcv(path: str) -> pd.DataFrame:
    if path.endswith(".parquet"):
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    df = df.copy()
    if "timestamp" in df.columns and "ts" not in df.columns:
        df["ts"] = pd.to_datetime(df["timestamp"], utc=True).astype("int64") // 1_000_000_000
    required = ["ts", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{path} missing columns: {missing}")
    out = df[required].copy()
    for column in required:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    if not np.isfinite(out[required].to_numpy(dtype=float)).all():
        raise ValueError(f"{path} contains non-finite OHLCV values")
    if out["ts"].duplicated().any():
        raise ValueError(f"{path} contains duplicate timestamps")
    if (
        (out["high"] < out[["open", "close", "low"]].max(axis=1))
        | (out["low"] > out[["open", "close", "high"]].min(axis=1))
        | (out[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (out["volume"] < 0)
    ).any():
        raise ValueError(f"{path} contains invalid OHLCV relationships")
    return out.sort_values("ts").reset_index(drop=True)


def symbol_from_path(path: str, timeframe: str, market_type: str | None = None) -> str:
    dataset = classify_ohlcv_path(path, timeframe)
    if market_type is not None and dataset.market_type != str(market_type).lower():
        raise DatasetSelectionError(f"{path} is {dataset.market_type}, not requested {market_type}")
    return dataset.symbol


def ret_for(side: str, entry: float, exit_px: float) -> float:
    if side == "buy":
        return exit_px / entry - 1.0
    return entry / exit_px - 1.0


def bracket_outcome(
    df: pd.DataFrame,
    entry_idx: int,
    side: str,
    sl_pct: float,
    tp_pct: float,
    max_hold: int,
    *,
    fee_bps_side: float,
    entry_slip_bps: float,
    tp_slip_bps: float,
    stop_slip_bps: float,
) -> tuple[float, str, int]:
    open_px = float(df["open"].iloc[entry_idx])
    if side == "buy":
        entry = open_px * (1.0 + entry_slip_bps / 10_000.0)
        sl = entry * (1.0 - sl_pct)
        tp = entry * (1.0 + tp_pct)
    else:
        entry = open_px * (1.0 - entry_slip_bps / 10_000.0)
        sl = entry * (1.0 + sl_pct)
        tp = entry * (1.0 - tp_pct)

    end = min(entry_idx + max_hold, len(df) - 1)
    fee_rt = 2.0 * fee_bps_side / 10_000.0
    for j in range(entry_idx, end + 1):
        hi = float(df["high"].iloc[j])
        lo = float(df["low"].iloc[j])
        hit_sl = lo <= sl if side == "buy" else hi >= sl
        hit_tp = hi >= tp if side == "buy" else lo <= tp
        if not (hit_sl or hit_tp):
            continue
        if hit_sl:
            exit_px = (
                sl * (1.0 - stop_slip_bps / 10_000.0)
                if side == "buy"
                else sl * (1.0 + stop_slip_bps / 10_000.0)
            )
            return ret_for(side, entry, exit_px) - fee_rt, "stop_loss", j - entry_idx
        exit_px = (
            tp * (1.0 - tp_slip_bps / 10_000.0)
            if side == "buy"
            else tp * (1.0 + tp_slip_bps / 10_000.0)
        )
        return ret_for(side, entry, exit_px) - fee_rt, "take_profit", j - entry_idx

    close_px = float(df["close"].iloc[end])
    exit_px = (
        close_px * (1.0 - tp_slip_bps / 10_000.0)
        if side == "buy"
        else close_px * (1.0 + tp_slip_bps / 10_000.0)
    )
    return ret_for(side, entry, exit_px) - fee_rt, "time_stop", end - entry_idx


def run_one(df: pd.DataFrame, symbol: str, engine: MachineStrategyEngine, args) -> list[dict]:
    trades: list[dict] = []
    next_free_idx = max(40, int(args.min_bars))
    max_i = len(df) - 2
    i = next_free_idx
    signals = engine.detector_frame(df) if not args.exact else None
    atr_pct = engine.atr_pct_series(df) if not args.exact else None
    while i <= max_i:
        if args.exact:
            window = df.iloc[: i + 1]
            decision = engine.score_frame(symbol, window, market_type=args.market_type)
            detectors = dict(decision.diagnostics.get("detectors") or {})
        else:
            assert signals is not None and atr_pct is not None
            detectors = {name: int(signals[name].iloc[i]) for name in signals.columns}
            decision = engine.decision_from_detector_values(
                symbol,
                detectors,
                atr_pct=float(atr_pct.iloc[i]),
                market_type=args.market_type,
            )
        if decision.action != "OPEN":
            i += int(args.step)
            continue
        ret, outcome, bars = bracket_outcome(
            df,
            i + 1,
            decision.side,
            decision.stop_loss_pct,
            decision.take_profit_pct,
            decision.time_stop_bars,
            fee_bps_side=args.fee_bps_side,
            entry_slip_bps=args.entry_slip_bps,
            tp_slip_bps=args.tp_slip_bps,
            stop_slip_bps=args.stop_slip_bps,
        )
        trades.append(
            {
                "symbol": symbol,
                "ts": int(df["ts"].iloc[i + 1]),
                "side": decision.side,
                "ret": ret,
                "outcome": outcome,
                "bars": bars,
                "score": decision.score,
                "reasons": list(decision.reasons),
                "detectors": {k: int(v) for k, v in detectors.items() if int(v) != 0},
                "detector_votes": {k: int(v) for k, v in detectors.items()},
            }
        )
        i = i + 1 + max(1, bars)
    return trades


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    ordered = sorted(
        trades,
        key=lambda trade: (
            float(trade.get("ts") or 0.0),
            str(trade.get("symbol") or ""),
            str(trade.get("side") or ""),
        ),
    )
    rets = np.array([float(t["ret"]) for t in ordered], dtype=float)
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    equity = np.cumsum(rets)
    peaks = np.maximum.accumulate(np.concatenate(([0.0], equity)))
    drawdowns = peaks[1:] - equity
    return {
        "n": int(len(rets)),
        "ts_start": int(ordered[0]["ts"]),
        "ts_end": int(ordered[-1]["ts"]),
        "win_rate": float((rets > 0).mean()),
        "avg_ret": float(rets.mean()),
        "sum_ret": float(rets.sum()),
        "max_drawdown": float(drawdowns.max()) if len(drawdowns) else 0.0,
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(wins.sum() / abs(losses.sum())) if abs(losses.sum()) > 0 else None,
        "median_score": float(np.median([t["score"] for t in ordered])),
    }


def fold_summaries(trades: list[dict]) -> dict:
    if len(trades) < 30:
        return {}
    ordered = sorted(
        trades,
        key=lambda trade: (float(trade.get("ts") or 0.0), str(trade.get("symbol") or "")),
    )
    ts = np.array([t["ts"] for t in ordered], dtype=float)
    q1, q2 = np.quantile(ts, [1 / 3, 2 / 3])
    folds = {"fold1": [], "fold2": [], "fold3": []}
    for t in ordered:
        key = "fold1" if t["ts"] <= q1 else ("fold2" if t["ts"] <= q2 else "fold3")
        folds[key].append(t)
    return {k: summarize(v) for k, v in folds.items()}


def detector_summaries(trades: list[dict]) -> dict:
    buckets: dict[str, list[dict]] = {}
    for t in trades:
        for name, value in (t.get("detectors") or {}).items():
            side = "bull" if int(value) > 0 else "bear"
            buckets.setdefault(f"{name}:{side}", []).append(t)
            buckets.setdefault(name, []).append(t)
    return {k: summarize(v) for k, v in sorted(buckets.items())}


def _representative_key(dataset: OHLCVDataset) -> tuple[str, str]:
    digest = hashlib.sha256(
        f"machine-replay-v1:{dataset.market_type}:{dataset.base}".encode()
    ).hexdigest()
    return digest, dataset.path


def select_datasets(args) -> DatasetSelection:
    """Return one unambiguous file per base for the requested market.

    The bounded sample is selected by a stable symbol hash, not by the first
    alphabetic names in the directory. Any same-market base collision is excluded
    entirely so CSV/parquet copies or venue variants cannot double-count a coin.
    """
    cache_dir = Path(args.cache_dir)
    market_type = str(args.market_type).lower()
    if market_type not in {"spot", "futures"}:
        raise ValueError(f"unsupported market_type: {market_type}")
    discovered = (
        sorted(
            {
                *cache_dir.rglob(f"*_{args.timeframe}.parquet"),
                *cache_dir.rglob(f"*_{args.timeframe}.csv"),
            },
            key=lambda path: str(path).lower(),
        )
        if cache_dir.exists()
        else []
    )
    classified_counts = {"spot": 0, "futures": 0, "rejected": 0}
    issues: list[str] = []
    by_base: dict[str, OHLCVDataset] = {}
    collided: set[str] = set()
    for path in discovered:
        try:
            dataset = classify_ohlcv_path(path, args.timeframe)
        except DatasetSelectionError as exc:
            classified_counts["rejected"] += 1
            issues.append(str(exc))
            continue
        classified_counts[dataset.market_type] += 1
        if dataset.market_type != market_type:
            continue
        if dataset.base in by_base:
            collided.add(dataset.base)
            issues.append(
                f"duplicate {market_type} base {dataset.base}: "
                f"{by_base[dataset.base].path} and {dataset.path}; excluding both"
            )
            continue
        by_base[dataset.base] = dataset
    for base in collided:
        by_base.pop(base, None)

    datasets = list(by_base.values())
    max_files = max(0, int(getattr(args, "max_files", 0) or 0))
    if max_files:
        datasets = sorted(datasets, key=_representative_key)[:max_files]
    datasets.sort(key=lambda dataset: (dataset.base, dataset.path))
    return DatasetSelection(
        requested_market_type=market_type,
        datasets=tuple(datasets),
        issues=tuple(issues),
        discovered_files=len(discovered),
        classified_counts=classified_counts,
        max_files=max_files,
    )


def files_for(args) -> list[str]:
    return select_datasets(args).files


def _chronological_trades(trades: list[dict]) -> list[dict]:
    return sorted(
        trades,
        key=lambda trade: (
            float(trade.get("ts") or 0.0),
            str(trade.get("symbol") or ""),
            str(trade.get("side") or ""),
        ),
    )


def prepare_replay_datasets(args) -> tuple[list[tuple[OHLCVDataset, pd.DataFrame]], dict]:
    """Load and validate the selected market-specific replay panel once."""
    selection = select_datasets(args)
    minimum_symbols = max(1, int(getattr(args, "minimum_symbols", 8) or 8))
    minimum_history_bars = max(
        int(getattr(args, "min_bars", 80) or 80) + 2,
        int(getattr(args, "minimum_history_bars", 500) or 500),
    )
    max_bars = max(0, int(getattr(args, "max_bars", 0) or 0))
    loaded: list[tuple[OHLCVDataset, pd.DataFrame]] = []
    file_audit: list[dict[str, Any]] = []
    reasons: list[str] = []
    for dataset in selection.datasets:
        try:
            frame = load_ohlcv(dataset.path)
        except Exception as exc:
            file_audit.append(
                {
                    "path": dataset.path,
                    "symbol": dataset.symbol,
                    "eligible": False,
                    "reason": str(exc),
                }
            )
            continue
        if max_bars > 0:
            frame = frame.tail(max_bars).reset_index(drop=True)
        enough_history = len(frame) >= minimum_history_bars
        file_audit.append(
            {
                "path": dataset.path,
                "symbol": dataset.symbol,
                "market_type": dataset.market_type,
                "rows": len(frame),
                "ts_start": int(frame["ts"].iloc[0]) if len(frame) else None,
                "ts_end": int(frame["ts"].iloc[-1]) if len(frame) else None,
                "eligible": enough_history,
                "reason": None
                if enough_history
                else (f"history {len(frame)} < {minimum_history_bars} bars"),
            }
        )
        if enough_history:
            loaded.append((dataset, frame))

    if not selection.datasets:
        reasons.append(f"no explicitly classified {selection.requested_market_type} cache files")
    if len(loaded) < minimum_symbols:
        reasons.append(f"eligible_symbols {len(loaded)} < {minimum_symbols}")
    rejected_history = sum(1 for row in file_audit if not row["eligible"])
    audit = {
        "eligible": not reasons,
        "market_type": selection.requested_market_type,
        "minimum_symbols": minimum_symbols,
        "minimum_history_bars": minimum_history_bars,
        "eligible_symbols": len(loaded),
        "reasons": reasons,
        "warnings": (
            [f"{rejected_history} selected files failed schema/history validation"]
            if rejected_history
            else []
        ),
        "selection": selection.as_dict(),
        "files": file_audit,
    }
    return loaded, audit


def replay_chronological_holdout(
    df: pd.DataFrame,
    symbol: str,
    engine: MachineStrategyEngine,
    args,
) -> tuple[list[dict], list[dict], dict]:
    """Replay disjoint chronological selection/validation slices.

    The selection slice ends before an embargo at least as long as the maximum
    holding period. Validation receives past-only indicator warm-up rows, but only
    trades whose entry timestamp lies in the untouched validation slice are kept.
    """
    validation_fraction = float(getattr(args, "validation_fraction", 0.30) or 0.30)
    if not 0.10 <= validation_fraction <= 0.50:
        raise ValueError("validation_fraction must be between 0.10 and 0.50")
    embargo_bars = max(
        int(getattr(args, "embargo_bars", 24) or 24),
        int(getattr(args, "time_stop_bars", 12) or 12),
    )
    min_bars = max(40, int(getattr(args, "min_bars", 80) or 80))
    validation_start = int(len(df) * (1.0 - validation_fraction))
    selection_stop = validation_start - embargo_bars
    minimum_slice = min_bars + 2
    protocol = {
        "name": "chronological_holdout_v1",
        "eligible": True,
        "rows": len(df),
        "validation_fraction": validation_fraction,
        "embargo_bars": embargo_bars,
        "selection_stop_index_exclusive": selection_stop,
        "validation_start_index": validation_start,
        "selection_ts_end": None,
        "validation_ts_start": None,
        "reason": None,
    }
    if selection_stop < minimum_slice or len(df) - validation_start < 2:
        protocol["eligible"] = False
        protocol["reason"] = (
            f"history cannot support min_bars={min_bars}, embargo={embargo_bars}, "
            f"validation_fraction={validation_fraction:.2f}"
        )
        return [], [], protocol

    selection_frame = df.iloc[:selection_stop].reset_index(drop=True)
    validation_ts_start = int(df["ts"].iloc[validation_start])
    warmup_start = max(0, validation_start - min_bars)
    validation_frame = df.iloc[warmup_start:].reset_index(drop=True)
    selection_trades = run_one(selection_frame, symbol, engine, args)
    validation_trades = [
        trade
        for trade in run_one(validation_frame, symbol, engine, args)
        if int(trade.get("ts") or 0) >= validation_ts_start
    ]
    protocol["selection_ts_end"] = int(selection_frame["ts"].iloc[-1])
    protocol["validation_ts_start"] = validation_ts_start
    return (
        _chronological_trades(selection_trades),
        _chronological_trades(validation_trades),
        protocol,
    )


def _float_grid(text: str) -> list[float]:
    values = []
    for raw in str(text or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        values.append(float(raw))
    return values


def _readiness_for_trades(
    trades: list[dict], args, label: str, *, evidence_blockers: list[str] | None = None
) -> dict:
    thresholds = StrategyReadinessThresholds(
        min_trades=int(args.promotion_min_trades),
        min_profit_factor=float(args.promotion_min_profit_factor),
        min_avg_pnl=0.0,
        min_win_rate=DEFAULT_THRESHOLDS.min_win_rate,
        min_positive_folds=DEFAULT_THRESHOLDS.min_positive_folds,
        min_fold_trades=DEFAULT_THRESHOLDS.min_fold_trades,
        max_drawdown_to_gross_profit=DEFAULT_THRESHOLDS.max_drawdown_to_gross_profit,
        folds=DEFAULT_THRESHOLDS.folds,
        lookback_days=DEFAULT_THRESHOLDS.lookback_days,
    )
    records = [
        {
            "ts": t.get("ts"),
            "pnl": t.get("ret"),
            "strategy": "machine_replay",
            "detectors": t.get("detectors") or {},
        }
        for t in _chronological_trades(trades)
    ]
    report = evaluate_records(records, thresholds=thresholds, name=label)
    blockers = [str(reason) for reason in (evidence_blockers or []) if str(reason)]
    if blockers:
        report["ready"] = False
        report["verdict"] = "PAPER_ONLY"
        report["evidence_eligible"] = False
        report["reasons"] = list(report.get("reasons") or []) + [
            f"research_integrity: {reason}" for reason in blockers
        ]
    else:
        report["evidence_eligible"] = True
    return report


def _detector_params(args) -> dict:
    return {
        "ema_rsi": {
            "fast_ema": int(args.ema_rsi_fast_ema),
            "slow_ema": int(args.ema_rsi_slow_ema),
            "trend_ema": int(args.ema_rsi_trend_ema),
            "rsi_period": int(args.ema_rsi_rsi_period),
            "rsi_signal_ema": int(args.ema_rsi_rsi_signal_ema),
            "trend_slope_bars": int(args.ema_rsi_trend_slope_bars),
            "min_trend_slope": float(args.ema_rsi_min_trend_slope),
            "min_ema_spread_pct": float(args.ema_rsi_min_ema_spread_pct),
            "max_ema_spread_pct": float(args.ema_rsi_max_ema_spread_pct),
            "long_rsi_floor": float(args.ema_rsi_long_rsi_floor),
            "long_rsi_ceiling": float(args.ema_rsi_long_rsi_ceiling),
            "short_rsi_floor": float(args.ema_rsi_short_rsi_floor),
            "short_rsi_ceiling": float(args.ema_rsi_short_rsi_ceiling),
            "hold_bars": int(args.ema_rsi_hold_bars),
        }
    }


def run_grid(args) -> dict:
    results = {}
    datasets, dataset_audit = prepare_replay_datasets(args)
    detector_params = _detector_params(args)
    combinations = [
        (min_score, rr, atr_stop_mult)
        for min_score in _float_grid(args.min_score_grid)
        for rr in _float_grid(args.rr_grid)
        for atr_stop_mult in _float_grid(args.atr_stop_mult_grid)
    ]
    for min_score, rr, atr_stop_mult in combinations:
        label = f"score={min_score:g}|rr={rr:g}|atr={atr_stop_mult:g}"
        cfg = MachineStrategyConfig(
            min_score=min_score,
            rr=rr,
            atr_stop_mult=atr_stop_mult,
            min_stop_pct=args.min_stop_pct,
            max_stop_pct=args.max_stop_pct,
            scalp_time_stop_bars=args.time_stop_bars,
            detector_params=detector_params,
            confirmation_detectors=tuple(
                s.strip() for s in str(args.confirmation_detectors or "").split(",") if s.strip()
            ),
            unconfirmed_min_score=float(args.unconfirmed_min_score),
        )
        engine = MachineStrategyEngine(cfg)
        selection_trades: list[dict] = []
        validation_trades: list[dict] = []
        split_protocols: list[dict] = []
        for dataset, df in datasets:
            selected, validated, protocol = replay_chronological_holdout(
                df, dataset.symbol, engine, args
            )
            protocol = {"symbol": dataset.symbol, **protocol}
            split_protocols.append(protocol)
            selection_trades.extend(selected)
            validation_trades.extend(validated)
        selection_trades = _chronological_trades(selection_trades)
        validation_trades = _chronological_trades(validation_trades)
        evidence_blockers = list(dataset_audit.get("reasons") or [])
        evidence_blockers.extend(
            f"{protocol['symbol']}: {protocol.get('reason')}"
            for protocol in split_protocols
            if not protocol.get("eligible")
        )
        if len(combinations) > 1:
            evidence_blockers.append(
                "the untouched validation slice was inspected by multiple parameter "
                "variants; run one preregistered configuration for readiness evidence"
            )
        results[label] = {
            "params": {
                "min_score": min_score,
                "rr": rr,
                "atr_stop_mult": atr_stop_mult,
                "confirmation_detectors": [
                    s.strip()
                    for s in str(args.confirmation_detectors or "").split(",")
                    if s.strip()
                ],
                "unconfirmed_min_score": float(args.unconfirmed_min_score),
            },
            "headline_sample": "untouched_validation",
            "summary": summarize(validation_trades),
            "validation_summary": summarize(validation_trades),
            "selection_summary": summarize(selection_trades),
            "folds": fold_summaries(validation_trades),
            "by_side": {
                "buy": summarize([t for t in validation_trades if t["side"] == "buy"]),
                "sell": summarize([t for t in validation_trades if t["side"] == "sell"]),
            },
            "by_detector": detector_summaries(validation_trades),
            "readiness": _readiness_for_trades(
                validation_trades,
                args,
                label,
                evidence_blockers=evidence_blockers,
            ),
            "split_protocols": split_protocols,
            "sample_reasons": sorted(
                {r for t in validation_trades[:100] for r in t.get("reasons", [])}
            )[:20],
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "exact_prefix" if args.exact else "fast_precomputed",
        "market_type": args.market_type,
        "files": [dataset.path for dataset, _ in datasets],
        "dataset_audit": dataset_audit,
        "evaluation_protocol": {
            "name": "chronological_holdout_v1",
            "headline_sample": "untouched_validation",
            "parameter_variants": len(combinations),
            "multi_variant_readiness_blocked": len(combinations) > 1,
        },
        "results": results,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cache-dir", default="data/ohlcv_cache")
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--market-type", default="futures", choices=["spot", "futures"])
    p.add_argument("--min-score-grid", default="0.25,0.34,0.45")
    p.add_argument("--rr", type=float, default=1.8)
    p.add_argument("--rr-grid", default="")
    p.add_argument("--atr-stop-mult", type=float, default=1.6)
    p.add_argument("--atr-stop-mult-grid", default="")
    p.add_argument("--min-stop-pct", type=float, default=0.0035)
    p.add_argument("--max-stop-pct", type=float, default=0.025)
    p.add_argument("--time-stop-bars", type=int, default=12)
    p.add_argument("--fee-bps-side", type=float, default=6.0)
    p.add_argument("--entry-slip-bps", type=float, default=5.0)
    p.add_argument("--tp-slip-bps", type=float, default=5.0)
    p.add_argument("--stop-slip-bps", type=float, default=10.0)
    p.add_argument("--min-bars", type=int, default=80)
    p.add_argument("--step", type=int, default=1)
    p.add_argument("--max-files", type=int, default=0)
    p.add_argument("--max-bars", type=int, default=0)
    p.add_argument("--minimum-symbols", type=int, default=8)
    p.add_argument("--minimum-history-bars", type=int, default=500)
    p.add_argument("--validation-fraction", type=float, default=0.30)
    p.add_argument("--embargo-bars", type=int, default=24)
    p.add_argument("--exact", action="store_true")
    p.add_argument("--promotion-min-trades", type=int, default=100)
    p.add_argument("--promotion-min-profit-factor", type=float, default=1.20)
    p.add_argument("--confirmation-detectors", default="ema_rsi")
    p.add_argument("--unconfirmed-min-score", type=float, default=0.40)
    p.add_argument("--ema-rsi-fast-ema", type=int, default=9)
    p.add_argument("--ema-rsi-slow-ema", type=int, default=21)
    p.add_argument("--ema-rsi-trend-ema", type=int, default=50)
    p.add_argument("--ema-rsi-rsi-period", type=int, default=14)
    p.add_argument("--ema-rsi-rsi-signal-ema", type=int, default=5)
    p.add_argument("--ema-rsi-trend-slope-bars", type=int, default=4)
    p.add_argument("--ema-rsi-min-trend-slope", type=float, default=0.00005)
    p.add_argument("--ema-rsi-min-ema-spread-pct", type=float, default=0.0002)
    p.add_argument("--ema-rsi-max-ema-spread-pct", type=float, default=0.035)
    p.add_argument("--ema-rsi-long-rsi-floor", type=float, default=45.0)
    p.add_argument("--ema-rsi-long-rsi-ceiling", type=float, default=70.0)
    p.add_argument("--ema-rsi-short-rsi-floor", type=float, default=30.0)
    p.add_argument("--ema-rsi-short-rsi-ceiling", type=float, default=55.0)
    p.add_argument("--ema-rsi-hold-bars", type=int, default=3)
    p.add_argument("--output", default="")
    args = p.parse_args()
    if not args.rr_grid:
        args.rr_grid = str(args.rr)
    if not args.atr_stop_mult_grid:
        args.atr_stop_mult_grid = str(args.atr_stop_mult)

    report = run_grid(args)
    for label, payload in report["results"].items():
        s = payload["summary"]
        print(
            f"{label} n={s.get('n', 0)} "
            f"WR={s.get('win_rate', 0.0) * 100:.1f}% "
            f"EV={s.get('avg_ret', 0.0) * 100:+.3f}% "
            f"sum={s.get('sum_ret', 0.0) * 100:+.1f}% "
            f"PF={s.get('profit_factor')} "
            f"gate={payload['readiness']['verdict']}"
        )

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not report["dataset_audit"]["eligible"]:
        print(
            "dataset gate: PAPER_ONLY — "
            + "; ".join(report["dataset_audit"].get("reasons") or ["ineligible panel"])
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
