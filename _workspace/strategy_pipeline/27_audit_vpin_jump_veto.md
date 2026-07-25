# 27 — Audit: VPIN jump-risk veto screen

**Date:** 2026-07-25  
**Auditor:** honesty-auditor (main-loop; subagent resume unavailable)  
**Screen:** `27_screen_vpin_jump_veto.{md,json}`  
**Owner override:** `36_owner_override_vpin_full_screen.md` (Stage-0 skipped)

## Verdict

**CONFIRM NO_GO** — screen outcome matches frozen expectation; no demotion needed.

## Attack checklist

| # | Attack | Result |
|---|--------|--------|
| 1 | Prereg integrity | **PASS** — sha256_md `2b880d1beaefd5f9…` verified pre-outcome; θ/N/gates unchanged |
| 2 | Lookahead | **PASS** — `merge_asof(..., allow_exact_matches=False)` uses buckets closed before decision ts |
| 3 | Cost model | **PASS** — AccBand `r_multiple` substrate already after-cost; veto adds no fees |
| 4 | Multiplicity | **PASS** — n_trials=4; Holm applied; all θ fail ΔEV>0 before Holm matters |
| 5 | Bleed-mask | **N/A** — fire rate 0; no WR↑/EV↓ path |
| 6 | Sample | **PASS (binding caveat)** — n_kept+skip=3050≥30, but **n_skipped=0 for all θ** |
| 7 | Substrate | **PASS** — 13_band binance BTC/ETH perps = prereg “replay of same geometry” |
| 8 | Charter | **N/A** — veto overlay, no new sizing |

## Binding failure mode (bear R4 confirmed)

Mean joined VPIN ≈ **0.127** on the AccBand decision set. Frozen θ grid `{0.55,0.60,0.65,0.70}` **never fires** (fire%=0). Treatment is a no-op; ΔR=0; MC on kept arm = baseline bleed (P(>0)=0, maxDD p95 ≫ 0.25).

This is exactly the Stage-0 risk the committee flagged: raw VPIN on liquid Binance BTC/ETH perps does not reach flash-crash literature thresholds under this bucket construction. Owner override accepted that risk; screen measured it.

## What this is NOT

- Not a license to retune θ post-hoc (would invalidate prereg).
- Not evidence for directional VPIN (still STOP).
- Not a live/MCP install candidate.

## Binding next action

1. Add ledger row: **VPIN jump-risk veto (raw θ grid AccBand overlay)** = CONFIRMED_NO_GO (2026-07-25).  
2. Integration: **no-op** (no shadow probe).  
3. Free queue slot → **C2 gamma-expiry** harvest accrual / next edge-queue item.  
4. Reopen only with a **new** hashed prereg (e.g. VPIN CDF / empirical percentile θ) — not a silent θ edit.
