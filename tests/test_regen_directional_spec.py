"""Tests for scripts/regen_directional_spec.py — spec-universe regeneration.

The regenerated MCP_DIRECTIONAL_PAPER spec must derive its symbols from the
bot's own pair-discovery output, excluding everything pair_discovery rejects
(by CALLING its rejection logic, never copying lists) plus fresh
INCIDENT_QUARANTINE_BASES from env, while preserving every non-symbol field.
"""

import json

import pytest

from scripts.regen_directional_spec import (
    build_updated_spec,
    derive_futures_bases,
    main,
)


def _fake_pairs(futures_by_venue: dict) -> dict:
    """Shape-compatible with pair_discovery.discover_all_mode output."""
    return {
        venue: {"spot": [], "futures": list(symbols)} for venue, symbols in futures_by_venue.items()
    }


def _mini_spec(symbols=("BTC/USDT:USDT", "ETH/USDT:USDT")) -> dict:
    return {
        "id": "MCP_DIRECTIONAL_PAPER",
        "strategy_version": "directional-research-v1",
        "family": "algorithmic_scorer_directional",
        "market_type": "futures",
        "promotion_status": "active-paper",
        "validation_status": "edge_unproven",
        "data_required": ["ohlcv_multi_tf"],
        "entry_rules": {"type": "mcp_deterministic_scorer", "authority": "owner-approved"},
        "exit_rules": {"deterministic": True},
        "sizing": {"risk_based": True},
        "risk_limits": {"paper_only": True, "daily_loss_breaker_pct": 0.20},
        "venues": ["binance", "bybit", "bitget"],
        "symbols": list(symbols),
    }


# ── derive_futures_bases ────────────────────────────────────────────────


def test_commodity_and_stock_bases_excluded_via_real_rejection_logic():
    """CL/BZ (crude oil) and NVDA (stock) must be dropped by the REAL
    pair_discovery._asset_rejection_reason, not a copied list."""
    pairs = _fake_pairs(
        {"bybit": ["CL/USDT:USDT", "BZ/USDT:USDT", "NVDA/USDT:USDT", "BTC/USDT:USDT"]}
    )
    bases, stats = derive_futures_bases(pairs)
    assert "CL" not in bases
    assert "BZ" not in bases
    assert "NVDA" not in bases
    assert "BTC" in bases
    assert stats["asset_rejected"] == 3


def test_tokenized_commodity_bases_statically_disabled():
    """PAXG/XAUT are tokenized gold — commodity exposure, crypto-only charter."""
    from core.pair_discovery import _asset_rejection_reason

    assert _asset_rejection_reason("PAXG", {}) == "disabled_asset:PAXG"
    assert _asset_rejection_reason("XAUT", {}) == "disabled_asset:XAUT"
    assert _asset_rejection_reason("BZ", {}) == "disabled_asset:BZ"


def _tradfi_market(base="GC"):
    """Binance-style TradFi RWA perp metadata (real BZ/USDT:USDT shape)."""
    return {
        "base": base,
        "quote": "USDT",
        "settle": "USDT",
        "active": True,
        "type": "swap",
        "swap": True,
        "symbol": f"{base}/USDT:USDT",
        "info": {
            "contractType": "TRADIFI_PERPETUAL",
            "underlyingType": "COMMODITY",
            "underlyingSubType": ["TradFi"],
            "status": "TRADING",
        },
    }


def _crypto_perp(base, volume=5_000_000):
    return {
        "base": base,
        "quote": "USDT",
        "settle": "USDT",
        "active": True,
        "type": "swap",
        "swap": True,
        "symbol": f"{base}/USDT:USDT",
        "quoteVolume": volume,
        "info": {"underlyingType": "COIN", "status": "TRADING"},
    }


def test_tradfi_flagged_market_rejected_by_metadata_not_list():
    """A TradFi RWA perp with a ticker on NO static list (GC = gold) must be
    rejected from venue metadata alone — new RWA listings stay out without
    list maintenance."""
    from core.pair_discovery import _market_rejection_reason

    assert _market_rejection_reason(_tradfi_market("GC"), "futures") == "tradfi_asset:GC"
    # a normal crypto perp with COIN metadata still passes
    assert _market_rejection_reason(_crypto_perp("ETH"), "futures") is None


def test_eligible_futures_symbols_uncapped_candidacy_based():
    """Authorization derivation is UNCAPPED (the 25/venue ALL-mode cap is a
    scan budget, not an authorization rule) over the candidate bases, with
    metadata TradFi rejection applied even to candidates. Non-candidates are
    not authorized (load_markets metadata has no volume, so liquidity is
    enforced at runtime by universe_filter, not here)."""
    from scripts.regen_directional_spec import _eligible_futures_symbols_from_markets

    candidates = {f"QQ{i}A" for i in range(40)} | {"GC", "BTC"}
    markets = {f"QQ{i}A/USDT:USDT": _crypto_perp(f"QQ{i}A") for i in range(40)}
    markets["GC/USDT:USDT"] = _tradfi_market("GC")  # candidate but TradFi
    markets["RANDO/USDT:USDT"] = _crypto_perp("RANDO")  # eligible, NOT candidate
    markets["BTC/USDT:USDT"] = _crypto_perp("BTC")

    symbols = _eligible_futures_symbols_from_markets(markets, candidates)
    bases = {s.split("/", 1)[0] for s in symbols}

    assert sum(1 for b in bases if b.startswith("QQ")) == 40  # no 25-cap
    assert "GC" not in bases
    assert "RANDO" not in bases
    assert "BTC" in bases


def test_discovery_exception_fails_closed_without_writing(tmp_path):
    """A venue outage must never regen from a partial universe (wrong
    incumbent removals)."""
    spec_path = _write_spec(tmp_path, _mini_spec())
    before = spec_path.read_text(encoding="utf-8")

    def _boom():
        raise RuntimeError("venue down")

    rc = main(["--spec", str(spec_path)], discover_fn=_boom)

    assert rc != 0
    assert spec_path.read_text(encoding="utf-8") == before


def test_meme_and_stable_bases_excluded():
    pairs = _fake_pairs({"binance": ["PEPE/USDT:USDT", "USDC/USDT:USDT", "ETH/USDT:USDT"]})
    bases, stats = derive_futures_bases(pairs)
    assert "PEPE" not in bases
    assert "USDC" not in bases
    assert bases == ["ETH"]
    assert stats["asset_rejected"] == 2


def test_crypto_native_base_outside_current_spec_is_included():
    """BZ-class case: a liquid crypto-native base discovered on any venue
    joins the universe (union across venues)."""
    pairs = _fake_pairs(
        {"bybit": ["HBAR/USDT:USDT"], "binance": ["TAO/USDT:USDT", "ETH/USDT:USDT"]}
    )
    bases, _stats = derive_futures_bases(pairs)
    assert "HBAR" in bases
    assert "TAO" in bases
    assert "ETH" in bases


def test_incident_quarantine_env_excluded_fresh(monkeypatch):
    """Quarantine env is re-read at call time, not the import-time bake."""
    monkeypatch.setenv("INCIDENT_QUARANTINE_BASES", "FOOCOIN")
    pairs = _fake_pairs({"bybit": ["FOOCOIN/USDT:USDT", "BTC/USDT:USDT"]})
    bases, stats = derive_futures_bases(pairs)
    assert "FOOCOIN" not in bases
    assert "BTC" in bases
    assert stats["quarantined"] == 1


def test_empty_discovery_yields_no_bases():
    bases, _stats = derive_futures_bases({})
    assert bases == []


# ── build_updated_spec ──────────────────────────────────────────────────


def test_non_symbol_fields_preserved_and_provenance_appended():
    current = _mini_spec(symbols=["BTC/USDT:USDT", "SOL/USDT:USDT"])
    frozen = json.loads(json.dumps(current))
    updated = build_updated_spec(current, ["BTC", "HBAR"], {"asset_rejected": 3})

    # symbols replaced, deterministic order
    assert updated["symbols"] == ["BTC/USDT:USDT", "HBAR/USDT:USDT"]
    # every non-symbol field preserved byte-for-byte
    for key, value in frozen.items():
        if key != "symbols":
            assert updated[key] == value, f"field {key} was not preserved"
    # input dict not mutated
    assert current == frozen
    # provenance appended with the required facts
    prov = updated["regen_provenance"][-1]
    assert prov["base_count"] == 2
    assert prov["added"] == ["HBAR"]
    assert prov["removed"] == ["SOL"]
    assert prov["exclusions"] == {"asset_rejected": 3}
    assert "regenerated_utc" in prov
    assert prov["source"] == "scripts/regen_directional_spec.py"


def test_provenance_history_appends_not_replaces():
    current = _mini_spec()
    current["regen_provenance"] = [{"regenerated_utc": "2026-01-01T00:00:00Z"}]
    updated = build_updated_spec(current, ["BTC"], {})
    assert len(updated["regen_provenance"]) == 2


def test_regenerated_spec_still_yields_routes():
    """The updated payload must still parse as an active PAPER futures spec."""
    from core.strategy_spec import StrategySpec, approved_paper_futures_routes

    updated = build_updated_spec(_mini_spec(), ["BTC", "HBAR"], {})
    routes = approved_paper_futures_routes([StrategySpec.from_dict(updated)])
    assert set(routes) == {"BTC", "HBAR"}
    assert routes["HBAR"] == frozenset({"binance", "bybit", "bitget"})


# ── main / CLI ──────────────────────────────────────────────────────────


def _write_spec(tmp_path, spec: dict):
    p = tmp_path / "MCP_DIRECTIONAL_PAPER.json"
    p.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return p


def test_dry_run_prints_diff_and_writes_nothing(tmp_path, capsys):
    spec_path = _write_spec(tmp_path, _mini_spec(symbols=["BTC/USDT:USDT", "SOL/USDT:USDT"]))
    before = spec_path.read_text(encoding="utf-8")

    rc = main(
        ["--dry-run", "--spec", str(spec_path)],
        discover_fn=lambda: _fake_pairs({"bybit": ["BTC/USDT:USDT", "HBAR/USDT:USDT"]}),
    )

    assert rc == 0
    assert spec_path.read_text(encoding="utf-8") == before, "dry-run must not write"
    out = capsys.readouterr().out
    assert "HBAR" in out  # added
    assert "SOL" in out  # removed
    assert "DRY-RUN" in out


def test_real_run_writes_updated_spec_atomically(tmp_path):
    spec_path = _write_spec(tmp_path, _mini_spec(symbols=["BTC/USDT:USDT"]))

    rc = main(
        ["--spec", str(spec_path)],
        discover_fn=lambda: _fake_pairs({"bybit": ["BTC/USDT:USDT", "HBAR/USDT:USDT"]}),
    )

    assert rc == 0
    written = json.loads(spec_path.read_text(encoding="utf-8"))
    assert written["symbols"] == ["BTC/USDT:USDT", "HBAR/USDT:USDT"]
    assert written["promotion_status"] == "active-paper"
    assert written["regen_provenance"][-1]["base_count"] == 2
    leftovers = [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == [], "atomic temp file must not survive"


def test_empty_discovery_fails_closed_without_writing(tmp_path):
    spec_path = _write_spec(tmp_path, _mini_spec())
    before = spec_path.read_text(encoding="utf-8")

    rc = main(["--spec", str(spec_path)], discover_fn=lambda: {})

    assert rc != 0
    assert spec_path.read_text(encoding="utf-8") == before


def test_all_bases_rejected_fails_closed(tmp_path):
    """Discovery returning only rejected assets must never write an empty universe."""
    spec_path = _write_spec(tmp_path, _mini_spec())
    before = spec_path.read_text(encoding="utf-8")

    rc = main(
        ["--spec", str(spec_path)],
        discover_fn=lambda: _fake_pairs({"bybit": ["CL/USDT:USDT", "NVDA/USDT:USDT"]}),
    )

    assert rc != 0
    assert spec_path.read_text(encoding="utf-8") == before


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
