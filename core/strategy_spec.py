"""core/strategy_spec.py — declarative StrategySpec layer (Phase B, PAPER-only).

A ``StrategySpec`` is the single source of truth for one strategy: what it trades,
on which venues, the data it needs, its entry/exit rules, sizing, and risk limits,
plus its validation/promotion status. Specs round-trip to ``data/strategy_specs/``
and register through the EXISTING EvidenceRegistry (``core.decision.promotion_loop``),
so the honest-gate ledger and the NO_EDGE precondition check apply automatically.

Nothing here places orders or drives execution — it is a declarative record that the
deterministic ``MCPStrategyScorer`` reads to know which approved symbols to score.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

SPEC_DIR = Path("data/strategy_specs")

_LIST_FIELDS = ("venues", "symbols", "data_required")
_DICT_FIELDS = ("entry_rules", "exit_rules", "sizing", "risk_limits")


@dataclass
class StrategySpec:
    """Declarative strategy definition (see module docstring)."""

    id: str
    family: str = ""
    market_type: str = "futures"
    venues: list = field(default_factory=list)
    symbols: list = field(default_factory=list)
    data_required: list = field(default_factory=list)
    entry_rules: dict = field(default_factory=dict)
    exit_rules: dict = field(default_factory=dict)
    sizing: dict = field(default_factory=dict)
    risk_limits: dict = field(default_factory=dict)
    validation_status: str = "untested"
    promotion_status: str = "untested"

    # ── serialization ──────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> StrategySpec:
        data = dict(payload or {})
        known = {f: data.get(f) for f in cls.__dataclass_fields__}
        # Normalize container fields so equality survives JSON round-trips.
        for f in _LIST_FIELDS:
            known[f] = list(known.get(f) or [])
        for f in _DICT_FIELDS:
            known[f] = dict(known.get(f) or {})
        known["id"] = str(known.get("id") or "")
        return cls(**known)

    # ── EvidenceRegistry registration ──────────────────────────────────
    def register(
        self,
        *,
        registry_path: Path | str | None = None,
        new_info_source: str | None = None,
    ) -> dict:
        """Register/refresh this spec in the EvidenceRegistry ledger.

        Maps the spec's rules/data into the evidence row keyed by ``self.id`` and
        records a deterministic fingerprint over (rules, data_sources). Raises
        ``NoEdgeReplayError`` if the fingerprint was previously marked NO_EDGE and
        no ``new_info_source`` is supplied (the ledger's dead-config guard).
        """
        from core.decision.promotion_loop import ACTIVE_STRATEGIES_PATH, register_evidence

        rules = {"entry": self.entry_rules, "exit": self.exit_rules,
                 "sizing": self.sizing, "risk_limits": self.risk_limits}
        return register_evidence(
            self.id,
            rules=rules,
            data_sources=self.data_required,
            promotion_status=self.promotion_status,
            universe_construction_method={
                "family": self.family,
                "market_type": self.market_type,
                "venues": sorted(self.venues),
                "symbols": sorted(self.symbols),
            },
            new_info_source=new_info_source,
            path=registry_path or ACTIVE_STRATEGIES_PATH,
        )


# ── file I/O ───────────────────────────────────────────────────────────
def save_spec(spec: StrategySpec, *, directory: Path | str = SPEC_DIR) -> Path:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{spec.id}.json"
    p.write_text(json.dumps(spec.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return p


def load_spec(spec_id: str, *, directory: Path | str = SPEC_DIR) -> StrategySpec:
    p = Path(directory) / f"{spec_id}.json"
    return StrategySpec.from_dict(json.loads(p.read_text(encoding="utf-8")))


def load_all_specs(*, directory: Path | str = SPEC_DIR) -> list[StrategySpec]:
    d = Path(directory)
    if not d.exists():
        return []
    specs: list[StrategySpec] = []
    for p in sorted(d.glob("*.json")):
        try:
            specs.append(StrategySpec.from_dict(json.loads(p.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return specs


def approved_symbols(specs: list[StrategySpec] | None) -> set[str]:
    """Union of base symbols across specs whose promotion_status is active-paper.

    Returns an empty set when no specs are approved — the scorer treats an empty
    set as 'no restriction' so default runtime (no specs) is unchanged.
    """
    out: set[str] = set()
    for s in specs or []:
        if str(s.promotion_status).lower() in ("active-paper", "approved", "promoted"):
            for sym in s.symbols:
                out.add(str(sym).split("/")[0].upper())
    return out
