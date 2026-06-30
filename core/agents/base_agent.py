"""BaseAgent abstract + Proposal dataclass."""
from __future__ import annotations

import abc
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Proposal:
    agent_id: str
    symbol: str
    side: str
    entry: float
    sl: float
    tp: float
    confidence: float
    reason: str
    ts: int = field(default_factory=lambda: int(time.time()))
    proposal_id: str = field(default_factory=lambda: f"p-{uuid.uuid4().hex[:8]}")
    # Phase 0a — context the forward resolver (0b) needs to replay this decision
    # on closed candles. Proposers should set these; defaults keep the Binance 1h
    # shadow path working without forcing every existing caller to change.
    venue: str = "binance"
    timeframe: str = "1h"
    horizon_bars: int = 0


class BaseAgent(abc.ABC):

    def __init__(self, name: str):
        self.name = name

    @abc.abstractmethod
    def propose(self, ctx: dict) -> Optional[Proposal]:
        raise NotImplementedError

    def on_outcome(self, proposal: Proposal, realized_pnl: float) -> None:
        return None
