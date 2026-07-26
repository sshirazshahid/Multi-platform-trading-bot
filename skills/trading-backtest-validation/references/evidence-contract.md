# Validation Evidence Contract

The manifest is a compact, machine-checkable summary. Keep full reports, hashes, trial registries, and datasets in the repository's normal evidence locations. Passing this contract is not live authorization.

## Required shape

```json
{
  "strategy_hash": "sha256:<64 hexadecimal characters>",
  "config_hash": "sha256:<64 hexadecimal characters>",
  "data_hash": "sha256:<64 hexadecimal characters>",
  "point_in_time_data": true,
  "lookahead_tests_passed": true,
  "realistic_costs_and_fills": true,
  "chronological_holdout_untouched": true,
  "walk_forward_segments": 4,
  "deterministic_replay": true,
  "replay_hash_match": true,
  "selection_bias_reported": true,
  "net_expectancy_after_costs": 0.01,
  "profit_factor_after_costs": 1.05,
  "pbo": 0.40,
  "deflated_sharpe": 0.12,
  "shadow_days": 30,
  "independent_mature_outcomes": 100,
  "shadow_outcomes_deduplicated": true,
  "unexplained_forward_divergence": false,
  "naked_position_incidents": 0,
  "uses_model": true,
  "model_manifest_valid": true,
  "model_artifact_checksum_valid": true,
  "model_pointer_atomic": true,
  "model_gate_fail_closed": true
}
```

## Gate interpretation

- Require at least 30 continuous shadow days and 100 independent matured outcomes. Extend to 60 days when signal frequency or regime coverage is insufficient.
- Require positive net expectancy and profit factor greater than 1 after all costs.
- The project baseline rejects `pbo > 0.5` or `deflated_sharpe < 0.10`; stricter preregistered thresholds take precedence.
- Require zero naked-position incidents and zero unexplained material forward divergence.
- If `uses_model` is true, all model integrity fields must be true. If false, omit those three model-artifact fields; the fail-closed model-gate field remains mandatory.
- Hashes must identify immutable artifacts; placeholders, mutable paths, or absolute workstation-only pointers do not count.

Raw win rate, in-sample AUC, paper profit without independent outcomes, and testnet plumbing success cannot substitute for any failed requirement.
