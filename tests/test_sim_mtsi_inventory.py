"""TDD tests for MTSI inventory sim + prereg hash gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research import sim_mtsi_inventory as mtsi  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PREREG_JSON = ROOT / "_workspace/strategy_pipeline/55_prereg_mtsi_inventory.json"
PREREG_MD = ROOT / "_workspace/strategy_pipeline/55_prereg_mtsi_inventory.md"


def test_prereg_hash_matches_frozen_json() -> None:
    meta = mtsi.verify_prereg(PREREG_JSON, PREREG_MD)
    assert meta["prereg_id"] == "mtsi_inventory_v1_2026-08-01"
    assert meta["max_gross_inventory_usd"] == 1.0
    assert len(meta["cells"]) == 6


def test_prereg_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    md = tmp_path / "p.md"
    js = tmp_path / "p.json"
    md.write_text("frozen body\n", encoding="utf-8")
    js.write_text(
        json.dumps({"sha256_md": "deadbeef", "bytes_md": 12}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="hash mismatch"):
        mtsi.verify_prereg(js, md)


def test_inventory_never_exceeds_one_dollar() -> None:
    bars = mtsi.generate_synthetic_path(n=1500, seed=3)
    cell = {
        "id": "F2",
        "market": "futures",
        "gamma": 0.3,
        "half_spread_floor_bps": 3.0,
        "tilt_max_bps": 1.0,
    }
    result = mtsi.run_cell(
        cell,
        bars,
        fees={"futures_maker": 0.0001, "spot_maker": 0.001},
        clip_usd=0.4,
    )
    assert result.max_abs_inventory_usd <= 1.0 + 1e-9
    assert result.max_single_clip_usd <= 1.0 + 1e-9
    for c in result.clips:
        assert abs(c.inventory_after) <= 1.0 + 1e-9
        assert c.notional_usd <= 1.0 + 1e-9


def test_spot_has_no_funding_term_vs_futures() -> None:
    bars = mtsi.generate_synthetic_path(n=800, seed=11)
    # Force non-zero funding on every bar.
    for b in bars:
        b["funding_rate_8h"] = 0.01
    fees = {"futures_maker": 0.0001, "spot_maker": 0.001}
    base = {
        "gamma": 0.1,
        "half_spread_floor_bps": 5.0,
        "tilt_max_bps": 0.5,
    }
    fut = mtsi.run_cell({**base, "id": "F", "market": "futures"}, bars, fees=fees)
    spot = mtsi.run_cell({**base, "id": "S", "market": "spot"}, bars, fees=fees)
    # With huge funding, futures total should differ from spot when both trade.
    if fut.n_clips and spot.n_clips:
        assert fut.total_pnl_usd != pytest.approx(spot.total_pnl_usd, abs=1e-15)


def test_fee_floor_rejects_too_tight_quotes_as_unprofitable_mean() -> None:
    """Tight floor still pays maker fee + AS haircut → mean typically ≤ 0."""
    bars = mtsi.generate_synthetic_path(n=2000, seed=99)
    cell = {
        "id": "F1",
        "market": "futures",
        "gamma": 0.1,
        "half_spread_floor_bps": 2.0,
        "tilt_max_bps": 0.5,
    }
    result = mtsi.run_cell(
        cell,
        bars,
        fees={"futures_maker": 0.0001, "spot_maker": 0.001},
        adverse_frac=0.5,
    )
    # Not a hard mathematical guarantee on every seed, but with AS haircut
    # the synthetic path is designed to land NO_GO / non-positive mean.
    assert result.mean_clip_pnl_usd <= 0.0 or result.verdict in {
        "NO_GO",
        "INSUFFICIENT_DATA",
    }


def test_screen_all_writes_no_go_expectation() -> None:
    payload = mtsi.screen_all(write_artifacts=False)
    assert payload["expectation"] == "NO_GO"
    # Synthetic CEX path + AS haircut is expected NO_GO (owner doctrine falsification).
    assert payload["final_verdict"] == "NO_GO"
    for c in payload["cells"]:
        assert c["max_abs_inventory_usd"] <= 1.0 + 1e-9
        assert c["max_single_clip_usd"] <= 1.0 + 1e-9


def test_one_big_trade_invariant_on_clip_size() -> None:
    bars = mtsi.generate_synthetic_path(n=500, seed=1)
    cell = {
        "id": "F3",
        "market": "futures",
        "gamma": 0.5,
        "half_spread_floor_bps": 4.0,
        "tilt_max_bps": 1.5,
    }
    # Even if caller asks for huge clips, room + MAX clamp.
    result = mtsi.run_cell(
        cell,
        bars,
        fees={"futures_maker": 0.0001, "spot_maker": 0.001},
        clip_usd=50.0,
    )
    assert result.max_single_clip_usd <= 1.0 + 1e-9
