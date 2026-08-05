"""Trading bot terminal dashboard package."""
from __future__ import annotations

from dashboard.app import background_fetch, main, parse_args
from dashboard.balances import extract_all_coins, extract_usdt
from dashboard.fetcher import LiveFetcher
from dashboard.health import build_health_status_payload
from dashboard.loaders import (
    load_auto_mut,
    load_block_reasons,
    load_mode,
    load_news,
    load_positions,
    load_post_mortem,
    load_risk_state,
    load_warehouse_stats,
)
from dashboard.render import render
from dashboard.stats import (
    calc_daily_pnl,
    calc_exchange_stats,
    calc_hourly_heatmap,
    calc_stats,
    calc_strategy_stats,
    calc_unrealized,
    calc_weekly_stats,
    sparkline,
    _filter_bot_trades,
    _filter_real_trades,
    _is_bot_trade,
    _is_real_trade,
    _whole_pnl,
)
from dashboard.term import (
    clr,
    col,
    enable_ansi,
    fg_str,
    pnl_str,
    pnl_str_short,
    vljust,
    vlen,
    vrjust,
    wr_col,
)

__all__ = [
    "LiveFetcher",
    "background_fetch",
    "build_health_status_payload",
    "calc_daily_pnl",
    "calc_exchange_stats",
    "calc_hourly_heatmap",
    "calc_stats",
    "calc_strategy_stats",
    "calc_unrealized",
    "calc_weekly_stats",
    "clr",
    "col",
    "enable_ansi",
    "extract_all_coins",
    "extract_usdt",
    "fg_str",
    "load_auto_mut",
    "load_block_reasons",
    "load_mode",
    "load_news",
    "load_positions",
    "load_post_mortem",
    "load_risk_state",
    "load_warehouse_stats",
    "main",
    "parse_args",
    "pnl_str",
    "pnl_str_short",
    "render",
    "sparkline",
    "vljust",
    "vlen",
    "vrjust",
    "wr_col",
    "_filter_bot_trades",
    "_filter_real_trades",
    "_is_bot_trade",
    "_is_real_trade",
    "_whole_pnl",
]

