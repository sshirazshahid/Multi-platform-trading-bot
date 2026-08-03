"""Offline tests for core/research_brief.py + scripts/research_brief.py.

Fixture-driven and network-free. These tests are the mechanical discharge of the
brief's two binding constraints — DESCRIPTIVE ONLY (no directional vocabulary
anywhere in the rendered markdown *or* the JSON keys) and ZERO LLM COST (no
model client is importable from the layer) — plus the only regression harness
for `core/pair_dossier.classify_verdict`, which was promoted verbatim from the
untracked 27_ provenance run and otherwise ships untested.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "research_brief_pairs.json"
CORE_SRC = ROOT / "core" / "research_brief.py"
SHELL_SRC = ROOT / "scripts" / "research_brief.py"

import core.research_brief as rb  # noqa: E402

FIX = json.loads(FIXTURE.read_text(encoding="utf-8"))
PAIRS = {p["base"]: p for p in FIX["pairs"]}
NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


# ── helpers ──────────────────────────────────────────────────────────────────
def _asset_inputs(base: str, **over):
    """Minimal AssetInputs for a fixture pair: local artifacts only, nothing set."""
    p = PAIRS[base]
    kw = dict(
        symbol=p["symbol"],
        base=p["base"],
        coverage=p["coverage"],
        indicators=p["indicators_4h"],
        bars_computable=p["indicators_4h"] is not None,
        live_1h_ok=True,
        n_bars_4h=p["live_fetch"]["bars_4h"],
    )
    kw.update(over)
    return rb.AssetInputs(**kw)


def _doc(bases, **docover):
    assets = [rb.build_asset_brief(_asset_inputs(b), now=NOW) for b in bases]
    kw = dict(
        assets=assets,
        now=NOW,
        mode="test",
        universe_source="tests/fixtures/research_brief_pairs.json",
        skipped_bases=[],
    )
    kw.update(docover)
    return rb.build_doc(**kw)


def _all_findings(doc):
    out = list(doc["market_context"]["findings"])
    for a in doc["assets"]:
        for sec in a["sections"].values():
            out += sec["findings"]
    return out


# ── 1. no directional language, in markdown AND in the JSON keys ─────────────
DENY = [
    "buy", "sell", "recommend", "recommends", "recommended", "recommendation",
    "edge", "alpha", "signal", "signals", "entry", "exit", "target", "targets",
    "bullish", "bearish", "overbought", "oversold", "forecast", "predict",
    "prediction", "veto", "long", "short",
    # Forward-looking modals: a descriptive record states what IS measured,
    # never what a price will/should do (plan denylist, restored 2026-07-25).
    "will", "should",
]
# Literal exemptions. Kept deliberately tiny: the projection renames rather than
# exempts (psar long/short -> price_vs_psar above/below, supertrend up/down ->
# above/below, macd `signal` -> macd_signal_line). Only source-path nouns remain.
EXEMPT = ("aggtrades_qh", "shortfall")
_DENY_RE = re.compile(r"\b(" + "|".join(sorted(DENY, key=len, reverse=True)) + r")\b", re.I)


def _scan(text: str) -> list[str]:
    cleaned = text
    for lit in EXEMPT:
        cleaned = cleaned.replace(lit, "")
    return sorted({m.group(0).lower() for m in _DENY_RE.finditer(cleaned)})


def test_no_directional_language_in_markdown_and_json():
    doc = _doc(["1INCH", "ADA", "APT", "BTC", "DASH", "TAO"])
    md = rb.render_md(doc)
    assert _scan(md) == [], f"directional vocabulary leaked into markdown: {_scan(md)}"
    blob = json.dumps(doc, sort_keys=True)
    assert _scan(blob) == [], f"directional vocabulary leaked into JSON: {_scan(blob)}"


def test_no_directional_language_with_every_optional_feed_populated():
    """The fixture path leaves most feeds empty; exercise the populated one too."""
    unlock_src = ROOT / "data" / "unlock_calendar" / "APT.json"
    unlock = json.loads(unlock_src.read_text(encoding="utf-8")) if unlock_src.exists() else {
        "max_supply": 1e9, "circ_calibration": {"documented_now": 5e8},
        "events": [{"ts": 1790000000, "tokens": 1.0e7, "ratio": 0.02}],
        "circ_series": [[1784000000, 5e8]],
    }
    assets = [
        rb.build_asset_brief(
            _asset_inputs(
                "BTC",  # BTC's fixture coverage carries both liquidity artifacts
                unlock=unlock,
                oi={"oi_usd": 1.2e9, "oi_chg_24h_pct": -3.4, "oi_conviction": 2},
                oi_as_of="2026-07-25T10:00Z",
                news_mentions=4, news_as_of="2026-07-25T11:00Z",
                sentiment_map={"BTC": -0.2}, sentiment_as_of="2026-07-25T11:00Z",
                l2_as_of="2026-07-25T09:00Z", aggtrades_as_of="2026-07-25T09:00Z",
                atr_4h=0.42,
                indicator_pctls={"n": 120, "rsi14_pctl": 0.81, "kdj_k_pctl": 0.77,
                                 "williams_r14_pctl": 0.9},
                warehouse_features={"atr_pct_1h": 0.31, "fr_side_signal": "x"},
                warehouse_as_of="2026-07-25T11:00Z",
                warehouse_vol_sample=[0.1, 0.2, 0.3, 0.4] * 10,
                funding_settlements=[(1784900000.0 + i * 28800, 0.0001) for i in range(21)],
            ),
            now=NOW,
        )
    ]
    doc = rb.build_doc(
        assets=assets, now=NOW, mode="test", universe_source="fixture", skipped_bases=[],
        market_intel={"ts_utc": "2026-07-25 08:00 UTC", "fng_value": 55, "fng_class": "Greed",
                      "btc_dom": 54.3, "eth_dom": 12.1, "breadth_green": 120,
                      "breadth_total": 300, "recommendation": "x"},
        market_intel_stale=True,
        diagnostics={"failed": ["news cache"]},
    )
    assert _scan(rb.render_md(doc)) == []
    assert _scan(json.dumps(doc, sort_keys=True)) == []
    sec = doc["assets"][0]["sections"]
    assert sec["supply_unlocks"]["status"] in ("OK", "DEGRADED")
    assert sec["funding"]["status"] == "OK"
    assert sec["open_interest"]["status"] == "OK"
    assert sec["liquidity"]["status"] == "OK"


def test_denylist_scanner_actually_catches_the_known_leaks():
    """Guard the guard: the raw 27_ indicator output MUST trip the scanner."""
    raw = json.dumps(PAIRS["ADA"]["indicators_4h"], sort_keys=True)
    hits = _scan(raw)
    assert "long" in hits or "short" in hits, "scanner is vacuous: raw psar_side not caught"
    assert "signal" in hits, "scanner is vacuous: raw macd signal key not caught"


# ── 2. staleness disclosure ──────────────────────────────────────────────────
def test_stale_asset_is_disclosed_and_confidence_capped():
    doc = _doc(["1INCH"])
    md = rb.render_md(doc)
    head = "\n".join(md.splitlines()[:15])
    assert "BACKFILL_FIRST" in head, f"readiness banner not in the first 15 lines:\n{head}"

    asset = doc["assets"][0]
    dq = asset["data_quality"]
    assert dq["verdict"] == "BACKFILL_FIRST"
    assert dq["confidence_ceiling"] == "LOW"
    assert dq["oldest_input_as_of_utc"] == "2026-05-31T21:00Z"
    # staleness must describe the stamp printed beside it, recomputed against the
    # injected now (the fixture's recorded 52.8 was measured at the 27_ run time)
    assert dq["max_staleness_days"] == rb.staleness_days(dq["oldest_input_as_of_utc"], NOW)
    assert dq["max_staleness_days"] > 52

    # findings carry the DATA's minute-resolution stamp; compare in that format,
    # not against generated_utc (seconds resolution), which could never collide
    run_stamp = NOW.strftime("%Y-%m-%dT%H:%MZ")
    assert doc["generated_utc"].startswith(run_stamp[:-1])
    dated = 0
    for f in _all_findings(doc):
        assert f["confidence"] in ("LOW", "NONE"), f
        assert f["as_of_utc"] != run_stamp, f"finding re-stamped with the run time: {f}"
        # Stronger than "not now": a dated finding must carry the DATA's own old
        # stamp. Anything newer than the oldest input would mean a fresher source
        # silently re-dated the stale asset (2026-07-25 hardening).
        if f["as_of_utc"] is not None:
            assert f["as_of_utc"] <= run_stamp, f
            assert f["as_of_utc"] >= dq["oldest_input_as_of_utc"], f
            dated += 1
    assert dated, "stale asset emitted no dated findings to check"


def test_data_quality_is_first_key_and_first_section():
    doc = _doc(["1INCH"])
    asset = doc["assets"][0]
    keys = list(asset.keys())
    assert keys[:3] == ["symbol", "base", "data_quality"], keys
    md = rb.render_md(doc)
    body = md.split("## 1INCH", 1)[1]
    assert body.index("data_quality") < body.index("price_action")


# ── 3. classify_verdict replay (the pair_dossier regression harness) ─────────
def _replay(pair):
    from core.pair_dossier import classify_verdict

    return classify_verdict(
        pair["coverage"],
        bars_computable=pair["indicators_4h"] is not None,
        live_1h_ok=pair["live_fetch"]["err_1h"] is None,
        n_bars_4h=pair["live_fetch"]["bars_4h"],
    )


def test_classify_verdict_replays_fixture_verdicts_and_reasons():
    for base, pair in PAIRS.items():
        verdict, reasons = _replay(pair)
        assert verdict == pair["verdict"], base
        assert reasons == pair["verdict_reasons"], base


def test_classify_verdict_replays_full_universe_counts():
    prov = ROOT / "_workspace" / "strategy_pipeline" / "27_pair_indicator_dossier.json"
    if not prov.exists():
        pytest.skip("27_ provenance artifact absent (untracked)")
    doc = json.loads(prov.read_text(encoding="utf-8"))
    counts = {"DATA_OK": 0, "PARTIAL": 0, "BACKFILL_FIRST": 0}
    for pair in doc["pairs"]:
        verdict, reasons = _replay(pair)
        assert verdict == pair["verdict"], pair["base"]
        assert reasons == pair["verdict_reasons"], pair["base"]
        counts[verdict] += 1
    assert counts == {"DATA_OK": 18, "PARTIAL": 0, "BACKFILL_FIRST": 25}
    assert counts == doc["verdict_counts"]


def test_classify_verdict_branch_thresholds():
    """Each BACKFILL_FIRST trigger in isolation, plus the PARTIAL band."""
    from core.pair_dossier import classify_verdict

    fresh = {"present": True, "staleness_days": 1.0}
    cov = {
        "ohlcv_cache_1h": dict(fresh),
        "ohlcv_cache_4h": dict(fresh),
        "funding_history": {"bybit": {"present": True}},
    }
    kw = dict(bars_computable=True, live_1h_ok=True, n_bars_4h=300)
    assert classify_verdict(cov, **kw)[0] == "DATA_OK"

    no_fund = json.loads(json.dumps(cov))
    no_fund["funding_history"]["bybit"]["present"] = False
    assert classify_verdict(no_fund, **kw)[0] == "BACKFILL_FIRST"

    stale_1h = json.loads(json.dumps(cov))
    stale_1h["ohlcv_cache_1h"]["staleness_days"] = 7.5
    assert classify_verdict(stale_1h, **kw)[0] == "BACKFILL_FIRST"
    assert classify_verdict({**cov, **{}}, **{**kw, "bars_computable": False})[0] == "BACKFILL_FIRST"

    stale_4h = json.loads(json.dumps(cov))
    stale_4h["ohlcv_cache_4h"]["staleness_days"] = 7.5
    assert classify_verdict(stale_4h, **kw)[0] == "PARTIAL"
    assert classify_verdict(cov, **{**kw, "live_1h_ok": False})[0] == "PARTIAL"
    assert classify_verdict(cov, **{**kw, "n_bars_4h": 299})[0] == "PARTIAL"


# ── 4. allowlist projection + never-invoked callables ────────────────────────
def test_feed_allowlist_drops_every_banned_key():
    poisoned = {
        "fr_side_signal": "x", "fr_extreme": 1, "crowd_signal": "x",
        "has_veto_news": True, "veto_reason": "x", "smart_money_inflow": 1.0,
        "social_hype_extreme": True, "oi_price_divergence": "x", "oi_conviction": 2,
        "recommendation": "x",
    }
    for feed in rb.FEED_ALLOWLIST:
        out = rb.project_feed(feed, dict(poisoned))
        assert out == {}, f"{feed} let banned keys through: {out}"
        assert set(rb.FEED_ALLOWLIST[feed]).isdisjoint(rb.BANNED_FEED_KEYS)


def test_feed_allowlist_keeps_only_declared_keys():
    raw = {"atr_pct_1h": 0.5, "oi_conviction": 3, "not_declared": 1}
    assert rb.project_feed("warehouse_features", raw) == {"atr_pct_1h": 0.5}
    with pytest.raises(KeyError):
        rb.project_feed("no_such_feed", {})


def test_banned_callables_are_never_invoked():
    banned_attrs = {n.rsplit(".", 1)[-1] for n in rb.BANNED_CALLABLES}
    assert banned_attrs, "BANNED_CALLABLES must not be empty"
    for src in (CORE_SRC, SHELL_SRC):
        tree = ast.parse(src.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            assert name not in banned_attrs, f"{src.name} invokes banned callable {name}"


def test_banned_callables_constant_is_never_serialized():
    doc = _doc(["ADA"])
    blob = json.dumps(doc, sort_keys=True)
    for name in rb.BANNED_CALLABLES:
        assert name not in blob
    for key in rb.BANNED_FEED_KEYS:
        assert key not in blob


# ── 5. finding invariants ────────────────────────────────────────────────────
def test_inference_lineage_resolves_to_facts_and_all_records_are_sourced():
    doc = _doc(["1INCH", "ADA", "APT", "BTC", "DASH", "TAO"])
    for asset in doc["assets"]:
        facts = {
            f["id"]
            for sec in asset["sections"].values()
            for f in sec["findings"]
            if f["kind"] == "FACT"
        }
        for sec in asset["sections"].values():
            for f in sec["findings"]:
                assert f["kind"] in ("FACT", "INFERENCE", "ABSENT")
                if f["kind"] == "FACT":
                    assert f["derived_from"] == []
                if f["kind"] == "INFERENCE":
                    assert f["derived_from"], f
                    for pid in f["derived_from"]:
                        assert pid in facts, f"{f['id']} lineage {pid} is not a FACT of this asset"
                if f["kind"] in ("FACT", "INFERENCE"):
                    assert f["as_of_utc"], f
                    assert f["source"], f
                    assert f["source_fn"], f
                    assert f["confidence_basis"], f
                else:
                    assert f["expected_source"], f
                    assert f["remediation"], f


def test_confidence_is_computed_not_authored():
    lvl, basis = rb.confidence_for(staleness_days=0.5, n=299, min_n=210, n_sources=2)
    assert lvl == "HIGH" and "0.5" in basis
    assert rb.confidence_for(staleness_days=52.8, n=299, min_n=210, n_sources=1)[0] == "NONE"
    assert rb.confidence_for(staleness_days=0.1, n=0, min_n=210, n_sources=1)[0] == "NONE"
    assert rb.confidence_for(staleness_days=None, n=None, min_n=None, n_sources=1)[0] == "LOW"
    assert rb.cap_confidence("HIGH", "LOW") == "LOW"
    assert rb.cap_confidence("NONE", "HIGH") == "NONE"


def test_absent_is_a_record_never_a_missing_key():
    doc = _doc(["TAO"], )
    asset = doc["assets"][0]
    for name in ("funding", "open_interest", "supply_unlocks", "news",
                 "sentiment", "liquidity", "macro_context"):
        sec = asset["sections"][name]
        assert sec["findings"], f"{name} emitted nothing at all"
        assert sec["status"] == "ABSENT", (name, sec["status"])
        assert all(f["kind"] == "ABSENT" for f in sec["findings"])


# ── 6. absence != neutral ────────────────────────────────────────────────────
def test_missing_coin_sentiment_is_absent_not_a_zero_fact():
    """news_scanner.symbol_sentiment() returns 0 for coins it never saw."""
    sent = {"BTC": 0.0}  # BTC seen with a genuine 0.0; ADA never seen at all
    stamp = "2026-07-25T11:00Z"
    btc = rb.build_asset_brief(
        _asset_inputs("BTC", sentiment_map=sent, sentiment_as_of=stamp), now=NOW
    )
    ada = rb.build_asset_brief(
        _asset_inputs("ADA", sentiment_map=sent, sentiment_as_of=stamp), now=NOW
    )
    btc_f = btc["sections"]["sentiment"]["findings"]
    assert any(f["kind"] == "FACT" and f["numeric"] == 0.0 for f in btc_f), btc_f
    ada_f = ada["sections"]["sentiment"]["findings"]
    assert all(f["kind"] == "ABSENT" for f in ada_f), ada_f
    assert all(f["numeric"] is None for f in ada_f)


# ── 7. scope separation + determinism ────────────────────────────────────────
def test_market_scope_findings_are_never_nested_in_an_asset():
    doc = _doc(["ADA", "BTC"], market_intel={
        "ts_utc": "2026-07-25 08:00 UTC", "fng_value": 55, "fng_class": "Greed",
        "btc_dom": 54.3, "eth_dom": 12.1, "breadth_green": 120, "breadth_total": 300,
    })
    mkt = doc["market_context"]["findings"]
    assert mkt and all(f["scope"] == "market" for f in mkt)
    for asset in doc["assets"]:
        for sec in asset["sections"].values():
            for f in sec["findings"]:
                assert f["scope"] == "asset", f


def test_determinism_byte_identical_with_frozen_now():
    bases = ["1INCH", "ADA", "APT", "BTC", "DASH", "TAO"]
    a = json.dumps(_doc(bases), sort_keys=True)
    b = json.dumps(_doc(bases), sort_keys=True)
    assert a == b
    assert rb.render_md(_doc(bases)) == rb.render_md(_doc(bases))


def test_generated_utc_comes_from_the_injected_now():
    doc = _doc(["ADA"])
    assert doc["generated_utc"] == "2026-07-25T12:00:00Z"
    assert doc["llm_used"] is False
    assert doc["schema_version"] == "research_brief/1"


# ── 8. quarantine: no order-path imports, no LLM imports ─────────────────────
def test_no_order_path_or_llm_imports():
    forbidden = (
        "order_manager", "risk_manager", "live_gate", "bot_engine",
        "entry_policy", "promotion_gate",
        "anthropic", "openai", "claude_client", "claude_advisor",
    )
    for src in (CORE_SRC, SHELL_SRC):
        lines = [
            ln for ln in src.read_text(encoding="utf-8").splitlines()
            if ln.strip().startswith(("import ", "from "))
        ]
        joined = "\n".join(lines)
        for bad in forbidden:
            assert bad not in joined, f"{src.name} must not import {bad}"


def test_core_module_is_network_and_clock_free():
    src = CORE_SRC.read_text(encoding="utf-8")
    for bad in ("requests", "urllib", "ccxt", "socket", "websocket"):
        assert f"import {bad}" not in src, f"core/research_brief.py must not import {bad}"
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            assert name not in ("time", "now", "utcnow", "today"), (
                "core/research_brief.py must take `now` as an argument, never read the clock"
            )


def test_shell_imports_and_exposes_the_documented_modes():
    spec = importlib.util.spec_from_file_location("_research_brief_shell", SHELL_SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    parser = mod.build_parser()
    for flag in ("--symbol", "--refresh"):
        assert any(flag in a.option_strings for a in parser._actions), flag


# ── 8. funding annualization must follow the OBSERVED settlement interval ────
# The perp funding interval is time-varying per symbol (bybit TAO/ENA/ONDO
# switched 8h -> 4h mid-history; BTC is still 8h). A hardcoded settlements/year
# constant understates any 4h base by exactly 2x. Derive it from the median
# consecutive-settlement delta of the very rows being averaged.
def _funding_labels(settlements):
    a = rb.build_asset_brief(
        _asset_inputs("BTC", funding_settlements=settlements), now=NOW
    )
    return {f["label"]: f for f in a["sections"]["funding"]["findings"]}


def test_funding_annualized_uses_8h_interval_when_history_is_8h():
    rows = [(1784900000.0 + i * 28800.0, 0.0001) for i in range(21)]
    ann = _funding_labels(rows)["funding_rate_annualized_pct"]
    assert ann["numeric"] == pytest.approx(0.0001 * 1095.0 * 100.0, rel=1e-9)


def test_funding_annualized_uses_4h_interval_when_history_is_4h():
    """The defect: this base settles 4h, so the 8h constant halved its carry."""
    rows = [(1784900000.0 + i * 14400.0, 0.0001) for i in range(21)]
    ann = _funding_labels(rows)["funding_rate_annualized_pct"]
    assert ann["numeric"] == pytest.approx(0.0001 * 2190.0 * 100.0, rel=1e-9)


def test_funding_annualized_takes_the_median_across_a_regime_switch():
    """TAO-like: an 8h prefix then a 4h tail. Median tracks the live regime."""
    ts = [1784900000.0 + i * 28800.0 for i in range(5)]
    for i in range(16):
        ts.append(ts[-1] + 14400.0)
    rows = [(t, 0.0001) for t in ts]
    ann = _funding_labels(rows)["funding_rate_annualized_pct"]
    assert ann["numeric"] == pytest.approx(0.0001 * 2190.0 * 100.0, rel=1e-9)


def test_funding_annualized_is_omitted_when_no_interval_is_observable():
    """One settlement = zero deltas. Emit nothing rather than assume a constant."""
    labels = _funding_labels([(1784900000.0, 0.0001)])
    assert "funding_rate_mean_recent" in labels
    assert "funding_rate_annualized_pct" not in labels


def test_funding_annualized_is_omitted_when_timestamps_are_degenerate():
    rows = [(1784900000.0, 0.0001)] * 4
    labels = _funding_labels(rows)
    assert "funding_rate_annualized_pct" not in labels
