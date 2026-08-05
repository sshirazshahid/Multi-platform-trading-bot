"""Read BotEngine implementation source after Phase D5 mixin split."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT / "core" / "engine"
FACADE = ROOT / "core" / "bot_engine.py"

_METHOD_FILES: dict[str, str] = {
    "_execute_open": "entry_exec.py",
    "_execute_close": "close_exec.py",
    "_apply_mcp_directional_economic_gate": "sizing_gates.py",
    "_portfolio_cycle": "cycle.py",
    "_run_promotion_funnel": "jobs.py",
    "_run_deep_breakout_lane": "probes.py",
    "_gate_health_check": "gate_health.py",
    "_ev_per_symbol_multiplier": "sizing_gates.py",
    "_protect_imported_positions": "imported_protect.py",
    "run": "lifecycle.py",
}


def bot_engine_implementation_source(*, method: str | None = None) -> str:
    """Concatenated engine mixin sources, or a single module when ``method`` is set."""
    if method is not None:
        rel = _METHOD_FILES.get(method)
        if rel:
            return (ENGINE_DIR / rel).read_text(encoding="utf-8")
    order = (
        "helpers.py",
        "engine.py",
        "probes.py",
        "gate_health.py",
        "portfolio_state.py",
        "sizing_gates.py",
        "cycle.py",
        "entry_exec.py",
        "close_exec.py",
        "monitors.py",
        "imported_protect.py",
        "jobs.py",
        "lifecycle.py",
    )
    parts = [FACADE.read_text(encoding="utf-8")]
    for name in order:
        path = ENGINE_DIR / name
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def bot_engine_source_for_grep() -> str:
    """Full implementation text for legacy structural substring tests."""
    return bot_engine_implementation_source()
