"""
core/mcp_brain.py — Permanent facade for the scoring package.

All implementation lives under core/scoring/; this module re-exports the
public API so existing imports (bot_engine, tests, mcp_strategy_scorer) keep
working unchanged.
"""
import time

from loguru import logger

from core.scoring.brain import MCPBrain, score_take_profit_proximity
from core.scoring.constants import (
    ACCURACY_FILE,
    DATA_CACHE_TTL,
    DECISION_LOG,
    ENTRY_COOLDOWN,
    FETCH_TIMEOUT,
    POSITION_COOLDOWN,
    STATE_FILE,
)
from core.scoring.accuracy_tracker import AccuracyTracker
from core.scoring.data_sources import (
    EXCHANGE_CAPS,
    _GECKO_IDS,
    _calc_bb,
    _calc_ema,
    _calc_rsi,
    _http_get,
    _microstructure_features,
    compute_technicals,
    fetch_coingecko,
    fetch_crypto_com,
    fetch_funding_rates,
    fetch_open_interest,
    fetch_orderbook_depth,
)
from core.scoring.helpers import (
    _accband_tradfi_scope_reason,
    _apply_accuracy_target,
    _entry_score_floor,
    _format_scalp_rule_skip_reason,
    _max_flow_scalp_fallback_enabled,
    _safe_feat,
    _sigmoid,
)

__all__ = [
    "ACCURACY_FILE",
    "AccuracyTracker",
    "DATA_CACHE_TTL",
    "DECISION_LOG",
    "ENTRY_COOLDOWN",
    "EXCHANGE_CAPS",
    "FETCH_TIMEOUT",
    "MCPBrain",
    "POSITION_COOLDOWN",
    "STATE_FILE",
    "_GECKO_IDS",
    "_accband_tradfi_scope_reason",
    "_apply_accuracy_target",
    "_calc_bb",
    "_calc_ema",
    "_calc_rsi",
    "_http_get",
    "_entry_score_floor",
    "_format_scalp_rule_skip_reason",
    "_max_flow_scalp_fallback_enabled",
    "_microstructure_features",
    "_safe_feat",
    "_sigmoid",
    "compute_technicals",
    "fetch_coingecko",
    "fetch_crypto_com",
    "fetch_funding_rates",
    "fetch_open_interest",
    "fetch_orderbook_depth",
    "score_take_profit_proximity",
    "logger",
    "time",
]
