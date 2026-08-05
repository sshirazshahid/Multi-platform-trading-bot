## Q1

**NO.** The frozen gate fails 5/7: AUC, net P&L, expectancy, profit factor, and DSR. The WR pass cannot override those failures.

## Q2

**RETIRE — option (a), ADJUDICATION-CLOSE.**

The tracker’s registered measurement purpose is fulfilled at 102 resolved events: WR is 64.7%, but it remains below the 73.8% breakeven WR and produced −79.46 USDT net (−55.07 USDT even without all costs). The positive upper expectancy-CI bound is not a frozen-gate exception.

Close this lane now; later outcomes do not reopen it. Do not add a per-arm flag solely for this closure. De-register the shared module when `zfade_4h_cfg365` receives its own adjudication, then disable it through the existing shared flag.

## Q3

Endorse this ledger row text:

```markdown
| RSI(2) extreme mean-reversion tracker — `rsi2_4h_cfg226` (MR-B; 4h Bybit linear perps; with-trend RSI(2)<10 long / RSI(2)>90 short; 0.8×ATR14 TP, 2.0×ATR14 SL, 12-bar time-stop) | **[2026-07-31 CLOSED — S1 adjudicated NO-PROMOTE; retirement approved.]** Owner-directed log-only tracker measuring the registered band-vs-profit tension. Frozen snapshot: 102 resolved / 66 wins, OOS WR 0.6471, but AUC 0.50, net −79.46 USDT, expectancy −0.779/trade, PF 0.652, and DSR 0.0403 — 5/7 gates fail. The 0.8×ATR-versus-2.0×ATR geometry requires 73.8% breakeven WR; realised WR is 9.1pp short. Friction was 48.05 USDT, but gross P&L was still −55.07 USDT: costs do not explain away the loss. Both long and short sides were negative; excluding ZEC, net remained −42.12 across 98 trades. Final adjudication-close: later resolutions do not reopen this verdict. No per-arm flag will be added; physically de-register with the shared bundle-MR module after `zfade_4h_cfg365` is adjudicated. | 2026-07-19; closed 2026-07-31 |
```

## Flags

- “The band is real” is overstated: 64.7% is the observed WR, but its stated 95% interval is 54.6%–73.9%; it supports an observed in-band result, not a precisely established true 63–67% band.
- “High WR is manufactured” is too strong causally. The bracket geometry demonstrably makes 73.8% necessary for breakeven and is consistent with the high win count; it does not alone prove the source of every win.
- The confidence interval and side/symbol checks should not be treated as independence-grade evidence: 101/102 events followed the 5→43-symbol universe change, and events may be correlated across symbols. This only weakens any case for continued accrual; it does not soften the arithmetic gate failure.
