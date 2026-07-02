"""Phase B — StrategySpec round-trip + EvidenceRegistry registration (PAPER-only)."""
from __future__ import annotations

import json

from core.decision.promotion_loop import init_evidence_registry, load_registry
from core.strategy_spec import SPEC_DIR, StrategySpec, load_spec, save_spec


def _sample_spec() -> StrategySpec:
    return StrategySpec(
        id="S2",
        family="basket",
        market_type="futures",
        venues=["binance", "bybit"],
        symbols=["BTC/USDT:USDT", "ETH/USDT:USDT"],
        data_required=["ohlcv_1h", "funding"],
        entry_rules={"score_gte": 66, "layers_gte": 6},
        exit_rules={"policy": "atr_stop"},
        sizing={"risk_pct": 0.01, "notional_cap_usd": 4},
        risk_limits={"max_positions": 2, "max_leverage": 2.0},
        validation_status="untested",
        promotion_status="shadow",
    )


def test_strategy_spec_dict_roundtrip():
    spec = _sample_spec()
    restored = StrategySpec.from_dict(spec.to_dict())
    assert restored == spec


def test_strategy_spec_file_roundtrip(tmp_path):
    spec = _sample_spec()
    d = tmp_path / "specs"
    save_spec(spec, directory=d)
    p = d / "S2.json"
    assert p.exists()
    # file is valid JSON with the id key
    assert json.loads(p.read_text(encoding="utf-8"))["id"] == "S2"
    assert load_spec("S2", directory=d) == spec


def test_strategy_spec_registers_in_evidence_registry(tmp_path):
    reg_path = tmp_path / "active_strategies.json"
    init_evidence_registry(reg_path)
    spec = _sample_spec()
    spec.register(registry_path=reg_path)
    reg = load_registry(reg_path)
    rec = reg["strategies"]["S2"]
    assert rec["promotion_status"] == "shadow"
    assert rec["fingerprint"]  # deterministic fingerprint recorded
    assert "ohlcv_1h" in rec["data_sources"]


def test_default_spec_dir_constant():
    assert str(SPEC_DIR).replace("\\", "/").endswith("data/strategy_specs")
