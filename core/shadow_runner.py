"""ShadowRunner — orchestrates per-tick agent flow.

Subscribes to BotEngine's existing OHLCV cache (no extra exchange calls)
and runs the AgentCoordinator. NEVER places orders.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from loguru import logger

from core.agents.coordinator import AgentCoordinator
from core.agents.execution_agent import ExecutionAgent
from core.agents.liquidity_agent import LiquidityAgent
from core.agents.mean_reversion_agent import MeanReversionAgent
from core.agents.pattern_agent import PatternAgent
from core.agents.projected_sizer import make_sizer
from core.agents.risk_agent import RiskAgent
from core.agents.scalp_agent import ScalpAgent
from core.agents.tp_probe_agent import TPProbeAgent
from core.agents.trend_agent import TrendAgent


class ShadowRunner:

    def __init__(
        self,
        *,
        warehouse,
        ctx_provider: Callable[[str], Optional[dict]],
        free_balance_provider: Callable[[], float],
        symbols_provider: Callable[[], list],
        blacklist: Optional[set] = None,
        allowed_hours: Optional[set] = None,
        enabled_flag: Callable[[], bool] = lambda: True,
    ):
        self._wh = warehouse
        self._ctx_provider = ctx_provider
        self._symbols_provider = symbols_provider
        self._enabled = enabled_flag
        agents = [
            TrendAgent(), ScalpAgent(), MeanReversionAgent(),
            PatternAgent(), LiquidityAgent(),
            # 2026-07-05 owner /goal probe: measures the geometric ~78% TP-hit
            # ceiling on the mover universe, scored after costs by the
            # resolver. Hit-rate is only readable NEXT TO resolved net_pnl.
            TPProbeAgent(),
        ]
        self._coord = AgentCoordinator(
            agents=agents,
            risk_agent=RiskAgent(),
            execution_agent=ExecutionAgent(warehouse=warehouse),
            sizer_fn=make_sizer(free_balance_provider=free_balance_provider),
            blacklist=blacklist if blacklist is not None else set(),
            allowed_hours=allowed_hours,
        )
        self._lock = threading.Lock()
        self._tick_count = 0
        self._proposal_count = 0

    def tick(self) -> int:
        if not self._enabled():
            return 0
        try:
            symbols = self._symbols_provider() or []
        except Exception as e:
            logger.debug(f"[Shadow] symbols_provider error: {e}")
            return 0
        emitted = 0
        for sym in symbols:
            try:
                ctx = self._ctx_provider(sym)
                if ctx is None:
                    continue
                rows = self._coord.tick(ctx)
                emitted += len(rows)
            except Exception as e:
                logger.debug(f"[Shadow] {sym} tick error: {e}")
        with self._lock:
            self._tick_count += 1
            self._proposal_count += emitted
        if emitted:
            logger.info(f"[Shadow] tick: {emitted} proposals across {len(symbols)} symbols")
        return emitted

    def stats(self) -> dict:
        with self._lock:
            return {"ticks": self._tick_count, "proposals": self._proposal_count}
