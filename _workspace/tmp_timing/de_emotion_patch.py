"""One-shot De-Emotion config + bot_engine patches."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def patch_config() -> None:
    p = ROOT / "config.py"
    text = p.read_text(encoding="utf-8")

    # Remove CLAUDE_PORTFOLIO_MODE block through end of old signal-source comments
    text2, n = re.subn(
        r"# ── Claude-dependence control.*?spot manager keep managing anything already open\.\n"
        r"(?=SIGNAL_SOURCE = )",
        (
            "# ── Signal source (De-Emotion 2026-08-04: no LLM on the decision path) ──────\n"
            "#   \"tsmom\"   - long-only TSMOM on validated majors (DEFAULT).\n"
            "#   \"machine\" - deterministic detector ensemble.\n"
            "#   \"s3\"      - multi-horizon momentum ensemble.\n"
            "#   \"mcp\" / \"mcp_det\" - deterministic MCPStrategyScorer (LLM-free).\n"
            "#   \"none\"    - directional entries OFF; carry runner only.\n"
            "# ⚠ Do NOT flip the default to mcp_det without an owner decision.\n"
        ),
        text,
        count=1,
        flags=re.S,
    )
    if n != 1:
        raise SystemExit(f"CLAUDE_PORTFOLIO_MODE block replace failed n={n}")
    text = text2

    text = text.replace('"news_enabled": True,', '"news_enabled": False,  # De-Emotion: feed deleted')
    text = re.sub(
        r"\n    # V2: News veto[^\n]*\n    \"v2_news_veto\": True,\n",
        "\n",
        text,
        count=1,
    )
    text = re.sub(
        r"\n    # V4: Social FOMO veto.*?\"v4_fomo_size_multiplier\": 0\.5,[^\n]*\n",
        "\n",
        text,
        count=1,
        flags=re.S,
    )

    # Soft-disable NEWS twitter section by setting twitter_enabled default false in dict
    text = text.replace(
        '"twitter_enabled": os.getenv("TWITTER_NEWS_ENABLED", "true").lower() == "true",',
        '"twitter_enabled": os.getenv("TWITTER_NEWS_ENABLED", "false").lower() == "true",  # De-Emotion',
    )

    p.write_text(text, encoding="utf-8")
    print("config.py patched")


def patch_bot_engine() -> None:
    p = ROOT / "core" / "bot_engine.py"
    text = p.read_text(encoding="utf-8")

    text = text.replace(
        "from config import CLAUDE_PORTFOLIO",
        "from config import PORTFOLIO_CYCLE as CLAUDE_PORTFOLIO",
    )
    # Prefer PORTFOLIO_CYCLE name going forward but keep CLAUDE_PORTFOLIO local alias
    text = text.replace(
        "from core.news_scanner import NewsScanner\n",
        "",
    )
    text = re.sub(
        r"\nNEWS_INTERVAL\s*=\s*CLAUDE_PORTFOLIO\.get\(\"news_interval_min\", 30\) \* 60\n",
        "\n",
        text,
        count=1,
    )
    text = text.replace("self.news      = NewsScanner()\n", "self.news = None  # De-Emotion: NewsScanner removed\n")

    # Remove news warmup block
    text = re.sub(
        r"\n\s*# Warm[^\n]*news[^\n]*\n\s*try:\n\s*self\.news\.scan\(\)\n\s*except Exception:\n\s*pass\n",
        "\n",
        text,
        count=1,
        flags=re.I,
    )

    # Rename method
    text = text.replace("def _claude_portfolio_cycle(self):", "def _portfolio_cycle(self):")
    text = text.replace("self._claude_portfolio_cycle", "self._portfolio_cycle")

    # Remove else mcp_brain fallthrough — raise on unknown
    old_else = '''        elif SIGNAL_SOURCE == "none":
            _signal = self._none_signal()
        else:
            _signal = self.mcp_brain
        actions = _signal.analyze_portfolio('''
    new_else = '''        elif SIGNAL_SOURCE == "none":
            _signal = self._none_signal()
        else:
            raise ValueError(
                f"SIGNAL_SOURCE={SIGNAL_SOURCE!r} is not a supported deterministic "
                "source (mcp/mcp_det/tsmom/machine/s3/none). LLM path removed."
            )
        actions = _signal.analyze_portfolio('''
    if old_else not in text:
        raise SystemExit("signal else block not found")
    text = text.replace(old_else, new_else, 1)

    # Neutralize news_context block — set empty
    text = re.sub(
        r"news_context\s*=\s*\{.*?\}\n.*?news_context\s*=\s*\{[\s\S]*?\n(?=\s*# Signal source)",
        "news_context = {}\n\n        # Signal source",
        text,
        count=1,
    )
    # Simpler: if regex fails, leave and fix manually

    # Drop news_context kwarg from analyze_portfolio call if present — keep for compat
    # Short-side sentiment: force None
    text = re.sub(
        r"symbol_news_sentiment\s*=\s*[^,\n]+",
        "symbol_news_sentiment=None",
        text,
    )

    # Remove _fetch_news method body usage in schedule
    text = re.sub(
        r"\n\s*schedule\.every\(NEWS_INTERVAL\)\.seconds\.do\(self\._fetch_news\)\n",
        "\n",
        text,
        count=1,
    )

    # Remove _fetch_news method
    text = re.sub(
        r"\n    def _fetch_news\(self\):[\s\S]*?(?=\n    def )",
        "\n",
        text,
        count=1,
    )

    p.write_text(text, encoding="utf-8")
    print("bot_engine.py patched")


if __name__ == "__main__":
    patch_config()
    patch_bot_engine()
