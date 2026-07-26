"""One-shot: run historically failing tests and write a summary."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "failing_tests_now.txt"

NODES = [
    "tests/test_arbitrage_dry_gate.py::test_controlled_live_global_allows_live_arb_path",
    "tests/test_audit_fixes_2026_06_06.py::test_daily_loss_breaker_reads_pnl_under_lock",
    "tests/test_bybit_transfer_and_circuit_breaker.py::TestCapitalAllocatorCircuitBreaker::test_execute_accumulate_calls_bybit_transfer",
    "tests/test_bybit_transfer_and_circuit_breaker.py::TestCapitalAllocatorCircuitBreaker::test_transfer_failure_recorded_in_breaker",
    "tests/test_capital_allocator_baseline.py::test_baseline_resets_after_successful_transfer",
    "tests/test_mfe_mae_persist.py::test_monitor_hook_wired_in_check_sl_tp",
    "tests/test_risk_unified_breaker.py::test_kill_switch_is_visible_in_unified_report_without_touching_exits",
    "tests/test_s2_basket_slice.py::test_s2_slice_end_to_end_records_verdict_and_infeasible",
    "tests/test_s3_vertical_slice.py::test_s3_vertical_slice_end_to_end",
    "tests/test_spot_rotation_slice.py::test_spot_rotation_slice_end_to_end",
    "tests/test_dashboard_mode_scoping.py::test_save_refuses_production_wallet_under_pytest",
    "tests/test_decision_provenance.py::test_every_return_false_in_execute_open_preceded_by_reason_stash",
    "tests/test_decision_provenance.py::test_spot_fallback_forwards_decision_and_provenance_kwargs",
    "tests/test_entry_policy.py::test_runtime_config_uses_shadow_policy_empty_allowlist_and_typed_profile",
]

cmd = [sys.executable, "-m", "pytest", *NODES, "-v", "--tb=short"]
proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
OUT.write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
print(f"exit={proc.returncode} wrote={OUT}")
print((proc.stdout + proc.stderr)[-4000:])
