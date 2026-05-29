"""Multi-agent shadow build — Phase A.

Each entry-decision agent (ScalpAgent, TrendAgent, MeanReversionAgent)
proposes Proposal objects. RiskAgent vetoes; ExecutionAgent simulates
fills and writes shadow_decisions rows. NO live order placement during
Phase A.
"""
from .base_agent import BaseAgent, Proposal

__all__ = ["BaseAgent", "Proposal"]
