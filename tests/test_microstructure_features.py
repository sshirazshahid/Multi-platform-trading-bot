# tests/test_microstructure_features.py
"""Microstructure feature extraction (2026-05-25). 0.0 must be the neutral
missing-value for every feature (absent source -> key omitted -> _coerce 0.0)."""
from __future__ import annotations

import math

from core.mcp_brain import _microstructure_features


def _data(**coin_blocks):
    d = {"funding": {}, "orderbook": {}, "oi": {}}
    for k, v in coin_blocks.items():
        d[k]["BTC"] = v
    return d


def test_extracts_all_five_when_present():
    data = _data(
        funding={"funding_rate": 0.0001, "mark_price": 101.0, "index_price": 100.0},
        orderbook={"imbalance": 0.20, "bid_depth_usd": 8000.0, "ask_depth_usd": 4000.0},
        oi={"oi_delta_pct": 0.03},
    )
    f = _microstructure_features("BTC", data)
    assert f["oi_delta_6h"] == 0.03
    assert f["ob_imbalance"] == 0.20
    assert f["funding_rate"] == 0.0001
    assert abs(f["basis_bps"] - 100.0) < 1e-6
    assert abs(f["depth_ratio"] - math.log(2.0)) < 1e-6


def test_balanced_book_gives_zero_depth_ratio():
    data = _data(orderbook={"imbalance": 0.0, "bid_depth_usd": 5000.0,
                            "ask_depth_usd": 5000.0})
    f = _microstructure_features("BTC", data)
    assert f["depth_ratio"] == 0.0


def test_missing_sources_omit_keys_never_raises():
    f = _microstructure_features("BTC", {"funding": {}, "orderbook": {}, "oi": {}})
    assert f == {}
    assert _microstructure_features("BTC", {}) == {}


def test_zero_depth_is_safe():
    data = _data(orderbook={"imbalance": 0.1, "bid_depth_usd": 0.0,
                            "ask_depth_usd": 5000.0})
    f = _microstructure_features("BTC", data)
    assert "depth_ratio" not in f
    assert f["ob_imbalance"] == 0.1


def test_feat_dict_merges_microstructure():
    model_input = {"score": 70, "rsi_1h": 55}
    data = _data(
        funding={"funding_rate": 0.0002, "mark_price": 200.0, "index_price": 199.0},
        orderbook={"imbalance": -0.1, "bid_depth_usd": 3000.0, "ask_depth_usd": 6000.0},
        oi={"oi_delta_pct": -0.02},
    )
    feat = dict(model_input)
    feat.update(_microstructure_features("BTC", data))
    assert feat["score"] == 70
    assert feat["oi_delta_6h"] == -0.02
    assert feat["ob_imbalance"] == -0.1
    assert "basis_bps" in feat and "depth_ratio" in feat


def test_microstructure_keys_deferred_until_retrain():
    """The 3 new keys are CAPTURED into features_json now but are
    DELIBERATELY NOT in FEATURE_KEYS yet — the live model was trained on
    the current vector, and ShadowPredictor/model-gate build inference
    vectors from FEATURE_KEYS, so adding keys mid-stream breaks inference
    (13 shadow tests). They get appended at retrain time, together with the
    model that trains on them. funding_rate/ob_imbalance are already in
    FEATURE_KEYS (legacy) and stay."""
    from scripts.train_models import FEATURE_KEYS, _MICROSTRUCTURE_FEATURES_PENDING
    for k in ("oi_delta_6h", "depth_ratio", "basis_bps"):
        assert k not in FEATURE_KEYS, (
            f"{k} added to FEATURE_KEYS prematurely — breaks inference on the "
            f"current model. Add it only with a retrain.")
        assert k in _MICROSTRUCTURE_FEATURES_PENDING
    assert "funding_rate" in FEATURE_KEYS and "ob_imbalance" in FEATURE_KEYS


def test_load_dataset_coerces_missing_keys_to_zero():
    """Any FEATURE_KEYS entry absent from a candidate's features_json
    coerces to 0.0 (no crash). Proves forward back-compat: when the
    microstructure keys ARE eventually added, old candidates won't break."""
    from scripts.train_models import FEATURE_KEYS, _coerce
    old_feats = {"score": 66, "rsi_1h": 50}  # lacks funding_rate/ob_imbalance
    vec = [_coerce(old_feats.get(k)) for k in FEATURE_KEYS]
    assert len(vec) == len(FEATURE_KEYS)
    assert vec[FEATURE_KEYS.index("funding_rate")] == 0.0
    assert vec[FEATURE_KEYS.index("ob_imbalance")] == 0.0
