"""De-Emotion Phase 4 audit hardening — purity, gates, census, probes, checksums."""
from __future__ import annotations

import ast
import hashlib
import importlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Frozen production call-site count (authorize_runtime_entry) as of De-Emotion
# Wave-1+ — do not "fix" by deleting enforcement; update only with intentional census.
_EXPECTED_AUTHORIZE_SITES = 10

_DELETED_MODULES = (
    "news_scanner",
    "twitter_feed",
    "news_sentiment_feed",
    "claude_trader",
    "claude_ai_runner",
    "claude_daily",
    "claude_analyst",
    "prediction_engine",
)

_DECISION_PATH_FILES = (
    "core/bot_engine.py",
    "core/mcp_brain.py",
    "core/order_manager.py",
    "core/entry_policy.py",
    "core/carry_runner.py",
)


def _production_py_files():
    for base in ("core", "exchanges", "strategies"):
        for p in (ROOT / base).rglob("*.py"):
            if "test" in p.parts:
                continue
            yield p


def test_deleted_sentiment_modules_absent():
    for name in (
        "core/news_scanner.py",
        "core/data_feeds/news_sentiment_feed.py",
        "core/data_feeds/twitter_feed.py",
        "claude_ai_runner.py",
        "claude_daily.py",
        "claude_analysis_runner.py",
    ):
        assert not (ROOT / name).exists(), f"deleted module still present: {name}"


def test_decision_path_does_not_import_deleted_modules():
    banned = set(_DELETED_MODULES)
    for rel in _DECISION_PATH_FILES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    leaf = alias.name.split(".")[-1]
                    assert leaf not in banned, f"{rel} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for part in mod.split("."):
                    assert part not in banned, f"{rel} from-imports {mod}"
                for alias in node.names:
                    assert alias.name not in banned, f"{rel} imports name {alias.name}"


def test_decision_path_no_sentiment_hosts():
    host_re = re.compile(
        r"alternative\.me|api\.alternative|fear.?greed|/fng\b",
        re.I,
    )
    for rel in _DECISION_PATH_FILES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert not host_re.search(src), f"sentiment host string in {rel}"


def test_gate_coherence_floors():
    from core.mcp_brain import _entry_score_floor

    assert _entry_score_floor(False) in (66.0, float(_entry_score_floor(False)))
    # Without MCP_ENTRY_MIN_SCORE override → 66 / 65
    import config as cfg

    override = getattr(cfg, "MCP_ENTRY_MIN_SCORE", None)
    if override is None:
        assert _entry_score_floor(False) == 66.0
        assert _entry_score_floor(True, {"entry_threshold": 65}) == 65.0
    else:
        assert _entry_score_floor(False) == float(override)
        assert _entry_score_floor(True, {"entry_threshold": 65}) == float(override)

    src = (ROOT / "core/scoring/portfolio.py").read_text(encoding="utf-8")
    assert "layers_ok\"] >= 6" in src or "layers_ok'] >= 6" in src
    assert "layers_ok\"] >= 4" in src or "layers_ok'] >= 4" in src


def test_authorize_runtime_entry_census():
    """Freeze production enforcement sites (calls + carry_runner binding)."""
    call_pat = re.compile(r"\bauthorize_runtime_entry\s*\(")
    bind_pat = re.compile(
        r"\bauthorize_runtime_entry\s+if\s+entry_authorizer"
    )
    hits = []
    for p in _production_py_files():
        text = p.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if "def authorize_runtime_entry" in line:
                continue
            if "from core.entry_policy import" in line and "authorize_runtime_entry" in line:
                continue
            if "import authorize_runtime_entry" in line:
                continue
            if call_pat.search(line) or bind_pat.search(line):
                hits.append(f"{p.relative_to(ROOT).as_posix()}:{i}")
    assert len(hits) == _EXPECTED_AUTHORIZE_SITES, (
        f"expected {_EXPECTED_AUTHORIZE_SITES} authorize sites, found {len(hits)}:\n"
        + "\n".join(hits)
    )


def test_probe_specs_registered():
    from core.bot_engine import BotEngine

    specs = BotEngine._PROBE_SPECS
    assert len(specs) >= 7
    labels = {s.get("warn_label") for s in specs}
    for required in (
        "listing-short",
        "unlock-short",
        "tsmom",
        "breakout",
        "zfade-bundle",
        "rsi2-bundle",
    ):
        assert required in labels, f"missing probe {required}"


def test_promotion_gate_frozen_constants():
    import core.promotion_gate as pg

    assert pg.MIN_DSR == 0.10
    assert pg.MAX_PBO == 0.5
    assert pg.MIN_OOS_WR == 0.55
    assert pg.MIN_AUC == 0.60
    assert pg.PromotionGate().wr_floor == 0.65
    # Checksum over the constant block — catches silent edits.
    blob = f"{pg.MIN_DSR}|{pg.MAX_PBO}|{pg.MIN_OOS_WR}|{pg.MIN_AUC}|{pg.PromotionGate().wr_floor}"
    digest = hashlib.sha256(blob.encode()).hexdigest()
    assert digest == hashlib.sha256(
        b"0.1|0.5|0.55|0.6|0.65"
    ).hexdigest()


def test_promotion_funnel_imports_without_warehouse(tmp_path, monkeypatch):
    import scripts.promotion_funnel as pf

    monkeypatch.setattr(pf, "FUNNEL_JSON", tmp_path / "promotion_funnel.json")
    # Missing warehouse should not raise uncaught — main returns int.
    rc = pf.main()
    assert isinstance(rc, int)


def test_bot_engine_schedules_promotion_funnel():
    from tests.bot_engine_source import bot_engine_implementation_source

    src = bot_engine_implementation_source(method="run")
    assert "_run_promotion_funnel" in src
    assert "schedule.every(6).hours.do(self._run_promotion_funnel)" in src


def test_signal_source_default_unchanged():
    src = (ROOT / "config" / "signal.py").read_text(encoding="utf-8")
    m = re.search(
        r'SIGNAL_SOURCE\s*=\s*os\.getenv\(\s*"SIGNAL_SOURCE"\s*,\s*"([^"]+)"',
        src,
    )
    assert m is not None
    assert m.group(1) == "tsmom"
