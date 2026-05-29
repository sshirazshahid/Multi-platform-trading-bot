# core/alpha_zoo/registry.py
"""Shared alpha-registry record.

`AlphaDef` lives here (rather than in `alphas.py`) so both the Kakushadze-101
catalog (`alphas.py`) and the GTJA-191 catalog (`alphas_gtja.py`) can import it
without a circular dependency.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from core.alpha_zoo.panel import Panel


@dataclass
class AlphaDef:
    id: str
    source: str                       # 'K101' | 'GTJA' | 'Qlib' | 'FF'
    fn: Callable[[Panel], pd.DataFrame] | None
    computable: bool = True
    needs: list[str] = field(default_factory=list)
    reason_if_dropped: str = ""
