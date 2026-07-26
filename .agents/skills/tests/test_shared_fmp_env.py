#!/usr/bin/env python3
"""Tests for the shared FMP API-key resolver (`_shared_fmp_yahoo_patch`).

The equity skills read ``FMP_API_KEY`` from the process environment, but the
key actually lives in the repo-root ``.env`` which nothing exports.  These
tests pin the resolution contract:

    explicit argument  ->  os.environ  ->  nearest .env walking up from *start*

Every test passes an explicit ``start=tmp_path`` so the real repo ``.env`` is
never read and the real key never enters a test process.  The fake key below
is not a credential.
"""

import importlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _shared_fmp_yahoo_patch as shared  # noqa: E402

FAKE_KEY = "TEST_KEY_NOT_REAL"  # pragma: allowlist secret
OTHER_FAKE_KEY = "TEST_KEY_NOT_REAL_ENV"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Guarantee no ambient FMP_API_KEY leaks between tests."""
    monkeypatch.delenv("FMP_API_KEY", raising=False)


def _write_env(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".env"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------


class TestPrecedence:
    def test_explicit_argument_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FMP_API_KEY", OTHER_FAKE_KEY)
        _write_env(tmp_path, "FMP_API_KEY=from_dotenv\n")
        assert shared.resolve_fmp_key(FAKE_KEY, start=tmp_path) == FAKE_KEY

    def test_environment_used_when_no_argument(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FMP_API_KEY", OTHER_FAKE_KEY)
        assert shared.resolve_fmp_key(start=tmp_path) == OTHER_FAKE_KEY

    def test_dotenv_used_when_argument_and_environment_absent(self, tmp_path):
        _write_env(tmp_path, f"FMP_API_KEY={FAKE_KEY}\n")
        assert shared.resolve_fmp_key(start=tmp_path) == FAKE_KEY

    def test_environment_not_overridden_by_dotenv(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FMP_API_KEY", OTHER_FAKE_KEY)
        _write_env(tmp_path, f"FMP_API_KEY={FAKE_KEY}\n")
        assert shared.resolve_fmp_key(start=tmp_path) == OTHER_FAKE_KEY
        assert os.environ["FMP_API_KEY"] == OTHER_FAKE_KEY

    def test_empty_environment_value_falls_through_to_dotenv(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FMP_API_KEY", "")
        _write_env(tmp_path, f"FMP_API_KEY={FAKE_KEY}\n")
        assert shared.resolve_fmp_key(start=tmp_path) == FAKE_KEY


# ---------------------------------------------------------------------------
# Upward walk
# ---------------------------------------------------------------------------


class TestUpwardWalk:
    def test_finds_dotenv_in_ancestor_directory(self, tmp_path):
        _write_env(tmp_path, f"FMP_API_KEY={FAKE_KEY}\n")
        deep = tmp_path / "skills" / "ftd-detector" / "scripts"
        deep.mkdir(parents=True)
        assert shared.resolve_fmp_key(start=deep) == FAKE_KEY

    def test_nearest_dotenv_wins(self, tmp_path):
        _write_env(tmp_path, "FMP_API_KEY=outer_value\n")
        inner = tmp_path / "inner"
        _write_env(inner, f"FMP_API_KEY={FAKE_KEY}\n")
        assert shared.resolve_fmp_key(start=inner) == FAKE_KEY

    def test_start_accepts_a_file_path(self, tmp_path):
        _write_env(tmp_path, f"FMP_API_KEY={FAKE_KEY}\n")
        script = tmp_path / "scripts" / "fmp_client.py"
        script.parent.mkdir(parents=True)
        script.write_text("# not executed\n", encoding="utf-8")
        assert shared.resolve_fmp_key(start=script) == FAKE_KEY


# ---------------------------------------------------------------------------
# Missing / malformed inputs — must degrade quietly, never raise
# ---------------------------------------------------------------------------


class TestMissingKey:
    def test_returns_none_when_nothing_available(self, tmp_path):
        assert shared.resolve_fmp_key(start=tmp_path) is None

    def test_returns_none_when_dotenv_lacks_the_variable(self, tmp_path):
        _write_env(tmp_path, "OTHER_KEY=abc\n# comment\n\n")
        assert shared.resolve_fmp_key(start=tmp_path) is None

    def test_unreadable_dotenv_does_not_raise(self, tmp_path, monkeypatch):
        _write_env(tmp_path, f"FMP_API_KEY={FAKE_KEY}\n")

        def _boom(*args, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "read_text", _boom)
        monkeypatch.setattr(shared, "_dotenv_values", None, raising=False)
        assert shared.resolve_fmp_key(start=tmp_path) is None

    def test_blank_argument_is_ignored(self, tmp_path):
        _write_env(tmp_path, f"FMP_API_KEY={FAKE_KEY}\n")
        assert shared.resolve_fmp_key("", start=tmp_path) == FAKE_KEY


# ---------------------------------------------------------------------------
# .env parsing (manual fallback must match python-dotenv on the basics)
# ---------------------------------------------------------------------------


class TestManualParse:
    @pytest.mark.parametrize(
        "body",
        [
            "FMP_API_KEY=TEST_KEY_NOT_REAL\n",
            'FMP_API_KEY="TEST_KEY_NOT_REAL"\n',
            "FMP_API_KEY='TEST_KEY_NOT_REAL'\n",
            "export FMP_API_KEY=TEST_KEY_NOT_REAL\n",
            "  FMP_API_KEY = TEST_KEY_NOT_REAL  \n",
            "# comment\n\nOTHER=1\nFMP_API_KEY=TEST_KEY_NOT_REAL\nTRAILING=2\n",
        ],
    )
    def test_manual_parser_handles_common_forms(self, tmp_path, monkeypatch, body):
        monkeypatch.setattr(shared, "_dotenv_values", None, raising=False)
        _write_env(tmp_path, body)
        assert shared.resolve_fmp_key(start=tmp_path) == FAKE_KEY

    def test_manual_parser_keeps_equals_inside_value(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shared, "_dotenv_values", None, raising=False)
        _write_env(tmp_path, "FMP_API_KEY=abc=def\n")
        assert shared.resolve_fmp_key(start=tmp_path) == "abc=def"


# ---------------------------------------------------------------------------
# Side effects: populate os.environ, never override, never leak
# ---------------------------------------------------------------------------


class TestSideEffects:
    def test_populates_os_environ_when_unset(self, tmp_path):
        _write_env(tmp_path, f"FMP_API_KEY={FAKE_KEY}\n")
        shared.resolve_fmp_key(start=tmp_path)
        assert os.environ.get("FMP_API_KEY") == FAKE_KEY

    def test_import_has_no_side_effects(self, monkeypatch):
        """Re-importing the module must not read .env or touch os.environ."""
        monkeypatch.delenv("FMP_API_KEY", raising=False)
        importlib.reload(shared)
        assert "FMP_API_KEY" not in os.environ

    def test_key_value_is_never_printed(self, tmp_path, capsys):
        _write_env(tmp_path, f"FMP_API_KEY={FAKE_KEY}\n")
        shared.resolve_fmp_key(start=tmp_path)
        captured = capsys.readouterr()
        assert FAKE_KEY not in captured.out
        assert FAKE_KEY not in captured.err

    def test_wired_client_still_raises_the_same_error_when_nothing_resolves(self, monkeypatch):
        """A wired client keeps its own 'key required' ValueError, unchanged.

        ``find_dotenv`` is stubbed out because the real one walks up from this
        module and would legitimately find the repo ``.env``.
        """
        monkeypatch.delenv("FMP_API_KEY", raising=False)
        monkeypatch.setattr(shared, "find_dotenv", lambda start=None: None)
        scripts = Path(__file__).resolve().parents[1] / "vcp-screener" / "scripts"
        monkeypatch.syspath_prepend(str(scripts))
        for name in [n for n in sys.modules if n.split(".")[0] == "fmp_client"]:
            del sys.modules[name]
        try:
            import fmp_client

            with pytest.raises(ValueError) as exc:
                fmp_client.FMPClient()
            assert "FMP API key required" in str(exc.value)
            assert FAKE_KEY not in str(exc.value)
        finally:
            sys.modules.pop("fmp_client", None)


# ---------------------------------------------------------------------------
# Consumer contract: every wired script resolves the key the same way
# ---------------------------------------------------------------------------


SKILLS_DIR = Path(__file__).resolve().parents[1]

# Runtime FMP consumers that read the key directly (no fmp_client.py of their own).
EXTRA_CONSUMERS = [
    "canslim-screener/scripts/check_institutional_endpoint.py",
    "dividend-growth-pullback-screener/scripts/screen_dividend_growth_rsi.py",
    "downtrend-duration-analyzer/scripts/analyze_downtrends.py",
    "earnings-calendar/scripts/fetch_earnings_fmp.py",
    "economic-calendar-fetcher/scripts/get_economic_calendar.py",
    "institutional-flow-tracker/scripts/analyze_single_stock.py",
    "institutional-flow-tracker/scripts/track_institution_portfolio.py",
    "institutional-flow-tracker/scripts/track_institutional_flow.py",
    "kanchi-dividend-sop/scripts/build_entry_signals.py",
    "pair-trade-screener/scripts/analyze_spread.py",
    "pair-trade-screener/scripts/find_pairs.py",
    "signal-postmortem/scripts/postmortem_recorder.py",
    "theme-detector/scripts/theme_detector.py",
    "trader-memory-core/scripts/fmp_price_adapter.py",
    "value-dividend-screener/scripts/screen_dividend_stocks.py",
]

# Deliberately NOT wired — these match on the string, they do not consume the key:
#   dual-axis-skill-reviewer/scripts/run_dual_axis_review.py  (SKILL.md linter)
#   skill-integration-tester/scripts/validate_workflows.py    (required-key manifest)
#   */scripts/tests/*                                          (test fixtures)
NON_CONSUMERS = [
    "dual-axis-skill-reviewer/scripts/run_dual_axis_review.py",
    "skill-integration-tester/scripts/validate_workflows.py",
]

ALL_CLIENTS = sorted(
    str(p.relative_to(SKILLS_DIR)).replace("\\", "/")
    for p in SKILLS_DIR.glob("*/scripts/fmp_client.py")
)


class TestConsumersWired:
    """Static check — every runtime FMP consumer must call the shared resolver."""

    @pytest.mark.parametrize("relpath", ALL_CLIENTS)
    def test_every_fmp_client_calls_resolver(self, relpath):
        """Derived by glob, so a newly added fmp_client.py cannot slip through."""
        source = (SKILLS_DIR / relpath).read_text(encoding="utf-8")
        assert "resolve_fmp_key" in source, f"{relpath} does not use the shared resolver"

    @pytest.mark.parametrize("relpath", EXTRA_CONSUMERS)
    def test_direct_reader_calls_resolver(self, relpath):
        source = (SKILLS_DIR / relpath).read_text(encoding="utf-8")
        assert "resolve_fmp_key" in source, f"{relpath} does not use the shared resolver"

    @pytest.mark.parametrize("relpath", EXTRA_CONSUMERS + ALL_CLIENTS)
    def test_consumer_has_no_bare_env_read_left(self, relpath):
        """The bare os.getenv/os.environ read must survive only as the fallback stub."""
        source = (SKILLS_DIR / relpath).read_text(encoding="utf-8")
        bare = source.count('os.environ.get("FMP_API_KEY")') + source.count(
            'os.getenv("FMP_API_KEY")'
        )
        assert bare <= 1, f"{relpath} still reads FMP_API_KEY directly ({bare} sites)"

    @pytest.mark.parametrize("relpath", NON_CONSUMERS)
    def test_non_consumers_left_alone(self, relpath):
        """Linters/manifests must NOT be rewired — they only match on the name."""
        source = (SKILLS_DIR / relpath).read_text(encoding="utf-8")
        assert "resolve_fmp_key" not in source, f"{relpath} should not have been wired"
