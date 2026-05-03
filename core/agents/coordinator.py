"""AgentCoordinator — parallel proposals, serial veto + execution."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, Optional

from .base_agent import BaseAgent, Proposal
from .execution_agent import ExecutionAgent
from .risk_agent import RiskAgent


class AgentCoordinator:

    def __init__(
        self,
        agents: Iterable[BaseAgent],
        risk_agent: RiskAgent,
        execution_agent: ExecutionAgent,
        sizer_fn: Optional[Callable[[Proposal], tuple[float, float]]] = None,
        blacklist: Optional[set] = None,
        allowed_hours: Optional[set] = None,
    ):
        self.agents = list(agents)
        self.risk_agent = risk_agent
        self.execution_agent = execution_agent
        self.sizer_fn = sizer_fn or (lambda p: (8.0, 200.0))
        self.blacklist = blacklist if blacklist is not None else set()
        self.allowed_hours = allowed_hours

    def tick(self, ctx: dict) -> list[dict]:
        """Run one tick: every agent proposes (in parallel), risk vetoes,
        execution logs. Returns list of shadow_decisions row dicts."""
        proposals = self._gather_proposals(ctx)
        rows: list[dict] = []
        for prop in proposals:
            verdict = self.risk_agent.review(
                prop, blacklist=self.blacklist, allowed_hours=self.allowed_hours,
            )
            if verdict.approved:
                cur, alt = self.sizer_fn(prop)
                rows.append(self.execution_agent.execute_shadow(
                    prop,
                    projected_notional_current=cur,
                    projected_notional_alt=alt,
                ))
            else:
                rows.append(self.execution_agent.log_veto(
                    prop, vetoed_by=self.risk_agent.name, reason=verdict.reason,
                ))
        return rows

    def _gather_proposals(self, ctx: dict) -> list[Proposal]:
        if not self.agents:
            return []
        out: list[Proposal] = []
        with ThreadPoolExecutor(max_workers=max(1, len(self.agents))) as pool:
            futs = {pool.submit(a.propose, ctx): a for a in self.agents}
            for f in as_completed(futs):
                try:
                    p = f.result()
                except Exception:
                    p = None
                if p is not None:
                    out.append(p)
        return out
