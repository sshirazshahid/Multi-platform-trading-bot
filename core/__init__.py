"""Core package — LAZY re-exports for the trading bot.

The convenience names below (``from core import BotEngine`` etc.) are exposed via
PEP 562 ``__getattr__`` rather than eager top-level imports. Eager imports made
``core/__init__.py`` pull in the heavy ``BotEngine`` chain (ccxt / schedule /
loguru) on ANY ``core.*`` import — which broke the import-light decision-core CI
job (it installs only numpy/scipy/pandas/python-dotenv, so importing
``core.promotion_gate`` failed at ``import schedule``). Lazy access keeps the
convenience API working while letting lightweight submodules import cleanly.
"""

__all__ = [
    "BotEngine",
    "KnowledgeModel",
    "LearningEngine",
    "NewsScanner",
    "OrderManager",
    "Position",
    "PositionTracker",
    "RiskManager",
]

# name -> (submodule, attribute)
_LAZY_EXPORTS = {
    "BotEngine": ("core.bot_engine", "BotEngine"),
    "KnowledgeModel": ("core.knowledge_model", "KnowledgeModel"),
    "LearningEngine": ("core.learning_engine", "LearningEngine"),
    "NewsScanner": ("core.news_scanner", "NewsScanner"),
    "OrderManager": ("core.order_manager", "OrderManager"),
    "Position": ("core.position_tracker", "Position"),
    "PositionTracker": ("core.position_tracker", "PositionTracker"),
    "RiskManager": ("core.risk_manager", "RiskManager"),
}


def __getattr__(name):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'core' has no attribute {name!r}")
    import importlib

    module = importlib.import_module(target[0])
    return getattr(module, target[1])


def __dir__():
    return sorted(__all__)
