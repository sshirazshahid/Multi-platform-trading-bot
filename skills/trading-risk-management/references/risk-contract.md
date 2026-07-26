# Risk Contract Reference

Use this reference to prepare the JSON input for `scripts/validate_risk_contract.py`. The manifest records implementation evidence; it never grants permission to trade.

## Required shape

```json
{
  "profile": "CONTROLLED_LIVE",
  "max_effective_leverage": 1.0,
  "max_concurrent_positions": 1,
  "risk_per_trade_pct": 0.1,
  "gross_exposure_pct": 2.0,
  "default_fail_closed": true,
  "double_latch_enforced": true,
  "owner_signoff_required": true,
  "risk_gate_on_all_entries": true,
  "persistent_kill_switch": true,
  "startup_reconciliation": true,
  "protection_verified_before_entries": true,
  "protection_pending_persisted": true,
  "reduce_only_exits": true,
  "clock_drift_pauses_entries": true
}
```

Values may be stricter than the numeric ceilings, but never looser. `risk_per_trade_pct` and `gross_exposure_pct` are percentages, so `0.1` means 0.1%, not a fraction of one.

## Invariants

- Missing, malformed, stale, or conflicting inputs reject new entries.
- All venues share one aggregate leverage, notional, risk, and position budget.
- A fill is unsafe until exchange-side reduce-only protection is read back and verified.
- Restart reconciliation occurs only after live gates and read-only preflight succeed, and before strategy entries start.
- Breaker state and protection intent survive a crash through atomic persistence.
- Manual owner sign-off and the environment latch remain independent; automation cannot create either authorization.
- Historical profit, win rate, or a small live sample cannot relax a hard cap.

## Review evidence

Attach code locations, test names, timestamps, and immutable report hashes outside this manifest. Never embed secrets or account balances.
