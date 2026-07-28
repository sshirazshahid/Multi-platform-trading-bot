# 39 — Reconciled dual-model verdict: clamp-print zero-information screen

**Date:** 2026-07-28 · **Prereg:** `39_prereg_clamp_print_information_v2.md`
(sha256 `dda32c8cf71d…`, committed 4be03ad BEFORE any outcome computation)
**Models:** Codex gpt-5.6-sol (`39_verdict_codex.md`, `39_verdict_codex_final.md`) +
main-loop (Opus 5; sequential fallback per pipeline error protocol). **BOTH-AGREE: reached.**

## Process record

1. First screen run: 8/9 cells falsified. Codex round 1: **INVALID_RUN** — outcome
   linkage grouped by `symbol` only, leaking "next print" across venues for
   multi-venue symbols (66.9% of eligible rows cross-venue, 32.3% same-timestamp).
   The finding was verified, reproduced as a failing regression test
   (`test_next_print_never_crosses_venues`), and fixed (`groupby(venue, symbol)`).
2. Rerun under the SAME frozen prereg (no re-hash — the prereg's "that symbol's own
   NEXT settlement" was always the spec; the screen had the bug):
   **9/9 testable cells FALSIFIED** at α=0.05/12; OR_MH 1.55–7.83; clamp-arm
   next-sign success 0.88–0.96 vs control 0.58–0.90; 3 cells INSUFFICIENT_DATA
   (bitget-1h/2h structurally absent; binance-2h zero informative strata).
3. Codex round 2: CONFIRMS the corrected construction; commits to the
   persistence interpretation; endorses the ledger row and disposition below.
   Main-loop interpretation, formed independently before Codex round 1 was read,
   is identical.

## Agreed interpretation

The formal null ("clamp prints predict next-settlement sign no better than
contemporaneous non-clamp positive prints") is statistically falsified — but the
mechanism is **sticky venue default-state persistence**, not positioning
information. A symbol printing the venue baseline is in the no-premium default
state; its next print is overwhelmingly the same default (positive by
construction). Genuine market rates fluctuate and flip sign. Sign-of-next-print
cannot distinguish "the floor predicting itself" from information — and the
uniform OR>1 direction with clamp WR up to 0.96 is exactly the floor predicting
itself. The practical M2 claim is therefore SUPPORTED: clamp prints carry no
incremental market information.

## Agreed disposition

- Ledger row added (Refuted table, measurement-closure class) with the mechanism
  stated so the statistical falsification is never re-litigated as an edge.
- A clamp-aware filter is justified **only as log-only telemetry diagnostic**;
  any decision use (filtering F1 entries, conditioning any screen on clamp state)
  requires its OWN new pre-registered screen. No F1 parameter change.
- **Pre-refuted trade implications** (named so they cannot be drawn later):
  "positive clamp → take/keep F1", "front-run the next funding print", any
  directional long/short use. This screen measured neither price returns nor
  net carry after costs.

## Cost/scope record

Screen: 1,292,601 rows, 510 files, 72.6s local compute, $0 data cost. All
artifacts under `_workspace/strategy_pipeline/39_*`. No probe, no order-path
change, no promotion. The screen script's cell verdicts remain subordinate to
this reconciled verdict.
