# PREREG 77 — Align the scorer's ADX window with the band-regime veto

**Status:** PRE-REGISTERED. Hashed and committed BEFORE any outcome is
computed. Any edit after the hash is a NEW pre-registration.

**Date:** 2026-08-15. Owner directive: option 3 from `76_adx_gate_conflict`.

## 1. The problem this addresses (measured, not assumed)

The MCP scorer requires trend (`adx_4h >= 20`, EMA alignment; rejects `< 15`
as chop) while the band-regime veto rejects `adx_4h > 30`. Result over 24h:
2,531 candidates PASSED scoring, **65.4% of them had ADX > 30** and were
vetoed at execution; 1,743 of 1,786 post-approval blocks were
`band_regime_filter`; **0 opens in 38h**.

The scorer concentrates its output into the vetoed band (65.4% vs a 35.7%
universe base rate). That is wasted work, not a plumbing fault.

## 2. Change under test (H1)

Add an UPPER bound to the scorer's regime gate so it stops proposing entries
the veto will reject:

```
current:  reject if adx_4h < 15                     (chop)
proposed: reject if adx_4h < 15  OR  adx_4h > 30    (chop OR veto-doomed)
```

Scope: `core/scoring/entry_score.py` regime gate, MCP directional lane only.
The veto itself is NOT touched. No threshold is invented — 30 is the veto's
own pre-registered bucket edge from screen `13_band_conditional`.

## 3. What is expected, stated in advance

**This restores FLOW, not EDGE.** Historical outcomes by ADX-at-entry
(resolved trades joined to their candidate features, n=564):

| ADX band | n | WR | mean PnL | total |
|---|---|---|---|---|
| <20 | 137 | 38.0% | -0.1187 | -16.26 |
| **20-30** | **252** | **49.2%** | **-0.1551** | **-39.09** |
| >30 | 175 | 35.4% | -0.3098 | -54.21 |

Two facts, both binding:
- The bands **do** separate: 20-30 vs >30 differs by +0.155/trade,
  SE 0.063, **t = 2.44** (|t| > 1.96).
- The 20-30 band is **itself significantly negative**: mean -0.1551,
  95% CI **[-0.2232, -0.0871]** — excludes zero on the NEGATIVE side.

So the change should reduce bleed per trade and end the idle state, but
**it cannot make the lane profitable.** Anyone reading a later flow increase
as "the fix worked" is misreading it. Expected outcome: MORE trades, still
negative expectancy.

## 4. Success criteria (frozen)

Measured on trades entered AFTER this change ships, tagged by a new cohort
epoch, with the historical >30 population as control:

- **PRIMARY:** open rate recovers to > 4 opens/UTC-day (currently 0).
- **SECONDARY (bleed):** mean PnL/trade in the new cohort is no worse than
  the -0.1551 historical 20-30 figure, at n >= 100.
- **FAILURE:** if the new cohort's mean is worse than -0.31 (the >30 band),
  the alignment made things worse and is reverted.
- **NOT a success criterion:** profitability. It is not expected, and its
  absence does not falsify this change.

## 5. What this change may NOT be used to justify

- It does not license disabling `band_regime_filter`.
- It does not license a live-mode switch (the 39-finding audit stands).
- It does not license leverage changes.
- A flow recovery is NOT evidence of edge; expectancy stays the only
  profitability measure.

## 6. Rollback

Single-condition change behind the existing regime gate. Revert = delete the
upper bound. The cohort epoch stamp keeps pre/post separable forever.
