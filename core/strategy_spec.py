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

_ACTIVE_PAPER_STATUSES = frozenset({"active-paper", "approved", "promoted"})
_FUTURES_MARKET_TYPES = frozenset({"futures", "future", "perpetual", "perp", "swap"})


@dataclass
class StrategySpec:
    """Declarative strategy definition (see module docstring)."""

    id: str
    strategy_version: str = ""
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
        known["strategy_version"] = str(known.get("strategy_version") or "")
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
                "strategy_version": self.strategy_version,
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


class LoadedStrategySpecs(list[StrategySpec]):
    """Loaded specs plus non-fatal parse errors.

    Research callers can still inspect every valid spec when one file is broken,
    while an execution boundary can detect ``errors`` and fail closed instead of
    silently treating a partially loaded directory as fully approved.
    """

    def __init__(self, specs=(), *, errors=()):
        super().__init__(specs)
        self.errors = tuple(str(error) for error in errors)


def load_all_specs(*, directory: Path | str = SPEC_DIR) -> LoadedStrategySpecs:
    d = Path(directory)
    if not d.exists():
        return LoadedStrategySpecs()
    specs: list[StrategySpec] = []
    errors: list[str] = []
    for p in sorted(d.glob("*.json")):
        try:
            specs.append(StrategySpec.from_dict(json.loads(p.read_text(encoding="utf-8"))))
        except Exception as exc:
            errors.append(f"{p}: {type(exc).__name__}: {exc}")
    return LoadedStrategySpecs(specs, errors=errors)


class StrategySpecValidationError(ValueError):
    """An active execution spec is incomplete or internally inconsistent."""


def _base_symbol(value: object) -> str:
    raw = str(value or "").strip().upper()
    base = raw.split("/", 1)[0].split(":", 1)[0].strip()
    if not base or base == "*" or not base.replace("-", "").isalnum():
        raise StrategySpecValidationError(f"invalid executable symbol: {value!r}")
    return base


def approved_paper_futures_routes(
    specs: list[StrategySpec] | None,
) -> dict[str, frozenset[str]]:
    """Return the explicitly approved ``base -> venues`` execution routes.

    Only versioned, active PAPER futures specs contribute routes. Research,
    shadow, spot, and unversioned specs contribute nothing. An active spec that
    is malformed raises ``StrategySpecValidationError`` so the caller can deny
    all executable OPENs rather than use a partial or ambiguous universe.
    """

    routes: dict[str, set[str]] = {}
    for spec in specs or []:
        if not isinstance(spec, StrategySpec):
            raise StrategySpecValidationError(
                f"unexpected strategy spec type: {type(spec).__name__}"
            )
        status = str(spec.promotion_status or "").strip().lower()
        if status not in _ACTIVE_PAPER_STATUSES:
            continue
        if not str(spec.id or "").strip():
            raise StrategySpecValidationError("active spec id is blank")
        if not str(spec.strategy_version or "").strip():
            raise StrategySpecValidationError(
                f"active spec {spec.id!r} has no strategy_version"
            )
        market_type = str(spec.market_type or "").strip().lower()
        if market_type not in _FUTURES_MARKET_TYPES:
            continue
        if not spec.symbols:
            raise StrategySpecValidationError(
                f"active futures spec {spec.id!r} has an empty symbol universe"
            )
        venues = {
            str(venue or "").strip().lower()
            for venue in spec.venues or []
            if str(venue or "").strip()
        }
        if not venues or "*" in venues:
            raise StrategySpecValidationError(
                f"active futures spec {spec.id!r} needs explicit venues"
            )
        for symbol in spec.symbols:
            base = _base_symbol(symbol)
            routes.setdefault(base, set()).update(venues)
    return {base: frozenset(venues) for base, venues in routes.items()}


def approved_symbols(specs: list[StrategySpec] | None) -> set[str]:
    """Union of base symbols across specs whose promotion_status is active-paper.

    An empty set means there is no approved universe. Execution callers must
    never reinterpret it as an unrestricted universe.
    """
    out: set[str] = set()
    for s in specs or []:
        if str(s.promotion_status).lower() in _ACTIVE_PAPER_STATUSES:
            for sym in s.symbols:
                out.add(str(sym).split("/")[0].upper())
    return out
