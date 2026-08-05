#!/usr/bin/env python3
"""One-shot Phase D5 mechanical split of core/bot_engine.py into core/engine/ mixins."""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "core" / "bot_engine.py"
OUT = ROOT / "core" / "engine"

# Methods assigned to mixins (order within mixin = source order).
MIXIN_METHODS: dict[str, list[str]] = {
    "helpers": [],  # module-level only
    "probes": [
        "_init_shadow_runner",
        "_venue_perp_available",
        "_bundle_probe_symbols",
        "_build_probe",
        "_build_listing_probe",
        "_run_deep_breakout_lane",
        "_get_btc_vol_pause",
        "_deep_breakout_entry_paused",
        "_unlock_venue_order",
        "_unlock_perp_resolver",
        "_unlock_market_data",
        "_unlock_ohlcv",
        "_listing_venue_client",
        "_listing_markets",
        "_listing_market_data",
        "_listing_ohlcv",
        "_futures_ohlcv_params",
        "_shadow_enabled_flag",
        "_shadow_free_balance",
        "_shadow_symbols",
        "_shadow_symbols_legacy",
        "_shadow_ctx_for_symbol",
        "_shadow_loop",
    ],
    "gate_health": [
        "_band_regime_veto",
        "_band_regime_log_once",
        "_gate_health_check",
    ],
    "portfolio_state": [
        "_build_strategy_pool",
        "_resolve_pairs",
        "_resolve_all_mode_pairs",
        "_rescan_portfolio",
        "_log_balances",
        "_extract_usdt",
        "_extract_usdt_equity",
        "_collect_all_coins",
        "_build_position_snapshot",
        "_build_risk_envelope",
        "_get_recent_trades",
        "_current_utc_hour",
        "_load_hour_gate_evidence",
        "_load_dynamic_blocked_hours",
        "_classify_hour",
        "_get_btc_trend",
    ],
    "sizing_gates": [
        "_consec_loss_state",
        "_select_leverage_tier",
        "_within_loss_clamp",
        "_min_notional_floor",
        "_recent_side_wr",
        "_tsmom_signal",
        "_s3_signal",
        "_machine_signal",
        "_none_signal",
        "_mcp_det_signal",
        "_log_rejection",
        "_log_terminal_decision",
        "_btc_cross_regime_multiplier",
        "_hour_of_day_multiplier",
        "_ev_per_symbol_multiplier",
        "_validated_promoted_futures_model_version",
        "_apply_mcp_directional_economic_gate",
    ],
    "cycle": ["_portfolio_cycle"],
    "entry_exec": ["_execute_open"],
    "close_exec": ["_execute_close"],
    "monitors": [
        "_check_all_sl_tp",
        "_sltp_monitor_loop",
        "_heartbeat_portfolio_es",
        "_write_heartbeat",
        "_try_reconnect",
        "_retry_inactive_exchanges",
        "_check_exchange_health",
        "is_exchange_halted",
        "_sync_positions",
        "_replace_exchange_sl",
        "_replace_exchange_sl_impl",
        "_run_dca",
        "_run_rebalance",
        "_run_spot_evaluation",
        "_execute_spot_action",
        "_run_capital_allocation",
        "_execute_fund_ops",
        "_fetch_all_exchange_positions",
        "_unrealized_pnl_frac",
        "_maybe_capture_small_tp",
        "_maybe_tighten_aged_position",
        "_run_mcp_position_monitor",
        "_ext_position_still_open",
        "_close_external_position",
    ],
    "imported_protect": ["_protect_imported_positions"],
    "jobs": [
        "_run_learning",
        "_run_promotion_funnel",
        "_run_optimizer",
        "_run_self_healing",
        "_run_self_improve",
        "_daily_self_check",
    ],
    "lifecycle": [
        "_reconcile_realtime_stream",
        "_complete_authorized_live_startup_reconciliation",
        "run",
        "_shutdown",
        "_daily_summary",
        "_build_daily_summary_extras",
        "_print_live_status",
        "_print_full_summary",
    ],
    "engine": ["__init__"],
}

MIXIN_CLASS = {
    "probes": "_ProbesMixin",
    "gate_health": "_GateHealthMixin",
    "portfolio_state": "_PortfolioStateMixin",
    "sizing_gates": "_SizingGatesMixin",
    "cycle": "_CycleMixin",
    "entry_exec": "_EntryExecMixin",
    "close_exec": "_CloseExecMixin",
    "monitors": "_MonitorsMixin",
    "imported_protect": "_ImportedProtectMixin",
    "jobs": "_JobsMixin",
    "lifecycle": "_LifecycleMixin",
    "engine": "_EngineInitMixin",
}


def _read_source() -> str:
    return SRC.read_text(encoding="utf-8")


def _extract_class_body(source: str) -> tuple[str, int, int]:
    """Return (class_body, start_line, end_line) 1-indexed."""
    m = re.search(r"^class BotEngine:\n", source, re.MULTILINE)
    if not m:
        raise RuntimeError("BotEngine class not found")
    start = m.end()
    # End before module-level sample_clock_drift_ms comment block
    end_m = re.search(
        r"\n# ── Clock-drift sampling",
        source[start:],
    )
    if end_m:
        end = start + end_m.start()
    else:
        end = len(source)
    return source[start:end], start, end


def _extract_probe_specs(source: str, class_body: str) -> str | None:
    m = re.search(
        r"(\n    # Log-only shadow probe specs.*?^\    \)\n)",
        class_body,
        re.MULTILINE | re.DOTALL,
    )
    return m.group(1) if m else None


def _split_methods(class_body: str) -> dict[str, str]:
    """Map method name -> full method source (including decorators, 4-space indent)."""
    lines = class_body.splitlines(keepends=True)
    methods: dict[str, str] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        # class attribute _PROBE_SPECS — skip (handled separately)
        if re.match(r"^\s+_PROBE_SPECS\s*=", line):
            while i < len(lines) and not (
                i + 1 < len(lines)
                and re.match(r"^\s{4}def ", lines[i + 1])
            ):
                i += 1
            continue
        m = re.match(r"^(\s{4})(async def|def|@)\s", line)
        if not m and re.match(r"^\s{4}@", line):
            # decorator start
            pass
        if re.match(r"^\s{4}(async def|def)\s+(\w+)", line):
            name_m = re.match(r"^\s{4}(async def|def)\s+(\w+)", line)
            assert name_m
            name = name_m.group(2)
            # include preceding decorators
            start = i
            while start > 0 and re.match(r"^\s{4}@", lines[start - 1]):
                start -= 1
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if re.match(r"^\s{4}(async def|def)\s+", nxt):
                    break
                if re.match(r"^\s{4}@", nxt) and not re.match(r"^\s{4}@\w", nxt):
                    break
                if re.match(r"^\s{4}# ──", nxt):
                    break
                if re.match(r"^\s{4}_PROBE_SPECS\s*=", nxt):
                    break
                i += 1
            methods[name] = "".join(lines[start:i])
        else:
            i += 1
    return methods


def _module_level_before_class(source: str) -> str:
    m = re.search(r"^class BotEngine:\n", source, re.MULTILINE)
    assert m
    return source[: m.start()]


def _module_level_after_class(source: str) -> str:
    m = re.search(r"\n# ── Clock-drift sampling", source)
    if m:
        return source[m.start() + 1 :]
    return ""


def _helpers_content(before_class: str, after_class: str) -> str:
    header = textwrap.dedent(
        '''\
        """
        core/engine/helpers.py — BotEngine module helpers and constants (Phase D5).
        """
        '''
    )
    # Strip original module docstring from before_class — keep imports through console
    body_start = before_class.find("\n\n", before_class.find('"""') + 3)
    if body_start == -1:
        body_start = 0
    imports_and_funcs = before_class[body_start:].lstrip("\n")
    return header + imports_and_funcs + "\n" + after_class.lstrip("\n")


def _mixin_file(name: str, methods_src: list[str], probe_specs: str | None = None) -> str:
    cls = MIXIN_CLASS[name]
    doc = f'"""\ncore/engine/{name}.py — BotEngine {cls} mixin (Phase D5).\n"""\n'
    parts = [doc]
    if name == "probes" and probe_specs:
        parts.append(f"\nclass {cls}:\n")
        parts.append(probe_specs)
        parts.append("\n")
        for m in methods_src:
            parts.append(m)
            if not m.endswith("\n"):
                parts.append("\n")
    else:
        parts.append(f"\nclass {cls}:\n")
        for m in methods_src:
            parts.append(m)
            if not m.endswith("\n"):
                parts.append("\n")
    return "".join(parts)


def _engine_assembly() -> str:
    mixins = [
        "_LifecycleMixin",
        "_JobsMixin",
        "_MonitorsMixin",
        "_ImportedProtectMixin",
        "_CloseExecMixin",
        "_EntryExecMixin",
        "_CycleMixin",
        "_SizingGatesMixin",
        "_PortfolioStateMixin",
        "_GateHealthMixin",
        "_ProbesMixin",
        "_EngineInitMixin",
    ]
    imports = "\n".join(
        f"from core.engine.{m[1:].lower().replace('mixin', '').replace('engineinit', 'engine').replace('entryexec', 'entry_exec').replace('closeexec', 'close_exec').replace('gatehealth', 'gate_health').replace('portfoliostate', 'portfolio_state').replace('sizinggates', 'sizing_gates').replace('importedprotect', 'imported_protect')} import {m}"
        for m in mixins
    )
    # Fix import paths manually — the above is too fragile
    imports = textwrap.dedent(
        """\
        from core.engine.close_exec import _CloseExecMixin
        from core.engine.cycle import _CycleMixin
        from core.engine.engine import _EngineInitMixin
        from core.engine.entry_exec import _EntryExecMixin
        from core.engine.gate_health import _GateHealthMixin
        from core.engine.imported_protect import _ImportedProtectMixin
        from core.engine.jobs import _JobsMixin
        from core.engine.lifecycle import _LifecycleMixin
        from core.engine.monitors import _MonitorsMixin
        from core.engine.portfolio_state import _PortfolioStateMixin
        from core.engine.probes import _ProbesMixin
        from core.engine.sizing_gates import _SizingGatesMixin
        """
    )
    bases = ",\n    ".join(reversed(mixins.split("\n")))
    return textwrap.dedent(
        f'''\
        """
        core/engine/engine.py — BotEngine assembly (Phase D5).
        """
        {imports}

        class BotEngine(
            {bases},
        ):
            pass
        '''
    )


def _facade_content() -> str:
    return textwrap.dedent(
        '''\
        """
        core/bot_engine.py — Permanent facade for the engine package.

        All implementation lives under core/engine/; this module re-exports the
        public API so existing imports (main, tests, strategies) keep working.
        """
        from core.engine.engine import BotEngine
        from core.engine.helpers import (
            CLAUDE_PORTFOLIO,
            LEARN_INTERVAL,
            MAX_ACTIONS_PER_CYCLE,
            MAX_PER_EXCHANGE,
            MAX_TOTAL_POSITIONS,
            PORTFOLIO_CYCLE_SEC,
            _STRUCTURAL_ERRORS,
            _UNIFIED_EXCHANGES,
            _boot_profile_log_lines,
            _canonical_exit_reason,
            _deployable_total,
            _effective_tp_threshold,
            _is_mcp_directional_paper_futures,
            _live_entry_clock_drift_rejection,
            _tier_blocked_by_cap,
            console,
            sample_clock_drift_ms,
            smart_money_entry_rejection,
        )
        from config import DRY_RUN

        __all__ = [
            "BotEngine",
            "CLAUDE_PORTFOLIO",
            "DRY_RUN",
            "LEARN_INTERVAL",
            "MAX_ACTIONS_PER_CYCLE",
            "MAX_PER_EXCHANGE",
            "MAX_TOTAL_POSITIONS",
            "PORTFOLIO_CYCLE_SEC",
            "_STRUCTURAL_ERRORS",
            "_UNIFIED_EXCHANGES",
            "_boot_profile_log_lines",
            "_canonical_exit_reason",
            "_deployable_total",
            "_effective_tp_threshold",
            "_is_mcp_directional_paper_futures",
            "_live_entry_clock_drift_rejection",
            "_tier_blocked_by_cap",
            "console",
            "sample_clock_drift_ms",
            "smart_money_entry_rejection",
        ]
        '''
    )


def main() -> None:
    source = _read_source()
    class_body, _, _ = _extract_class_body(source)
    methods = _split_methods(class_body)
    probe_specs = _extract_probe_specs(source, class_body)

    assigned: set[str] = set()
    for names in MIXIN_METHODS.values():
        assigned.update(names)

    missing = assigned - set(methods)
    extra = set(methods) - assigned
    if missing:
        raise RuntimeError(f"Missing methods in source: {sorted(missing)}")
    if extra:
        raise RuntimeError(f"Unassigned methods: {sorted(extra)}")

    OUT.mkdir(parents=True, exist_ok=True)

    before = _module_level_before_class(source)
    after = _module_level_after_class(source)
    (OUT / "helpers.py").write_text(_helpers_content(before, after), encoding="utf-8")

    for mix_name, method_names in MIXIN_METHODS.items():
        if mix_name in ("helpers", "engine"):
            continue
        srcs = [methods[n] for n in method_names]
        ps = probe_specs if mix_name == "probes" else None
        (OUT / f"{mix_name}.py").write_text(
            _mixin_file(mix_name, srcs, ps), encoding="utf-8"
        )

    init_src = methods["__init__"]
    engine_body = textwrap.dedent(
        '''\
        """
        core/engine/engine.py — BotEngine assembly (Phase D5).
        """
        from core.engine.close_exec import _CloseExecMixin
        from core.engine.cycle import _CycleMixin
        from core.engine.entry_exec import _EntryExecMixin
        from core.engine.gate_health import _GateHealthMixin
        from core.engine.imported_protect import _ImportedProtectMixin
        from core.engine.jobs import _JobsMixin
        from core.engine.lifecycle import _LifecycleMixin
        from core.engine.monitors import _MonitorsMixin
        from core.engine.portfolio_state import _PortfolioStateMixin
        from core.engine.probes import _ProbesMixin
        from core.engine.sizing_gates import _SizingGatesMixin


        class _EngineInitMixin:
        '''
    ) + init_src + "\n\n\nclass BotEngine(\n    _LifecycleMixin,\n    _JobsMixin,\n    _MonitorsMixin,\n    _ImportedProtectMixin,\n    _CloseExecMixin,\n    _EntryExecMixin,\n    _CycleMixin,\n    _SizingGatesMixin,\n    _PortfolioStateMixin,\n    _GateHealthMixin,\n    _ProbesMixin,\n    _EngineInitMixin,\n):\n    pass\n"
    (OUT / "engine.py").write_text(engine_body, encoding="utf-8")

    (OUT / "__init__.py").write_text(
        '"""core/engine — BotEngine mixin package (Phase D5)."""\n'
        "from core.engine.engine import BotEngine\n\n"
        "__all__ = ['BotEngine']\n",
        encoding="utf-8",
    )

    SRC.write_text(_facade_content(), encoding="utf-8")
    print(f"Split complete: {len(methods)} methods -> {OUT}")


if __name__ == "__main__":
    main()
