# 82 — Should the vol gate also use ETH? Measured: no — and the BTC gate itself doesn't separate expectancy

**Type:** MEASUREMENT. Read-only. **No config changed.**

**Trigger:** owner — "When BTC and ETH increases/decreases in crypto world
everything (except STOCKS/GOLD/OIL/SILVER) moves accordingly. So also
identify using ETH."

## 1. The premise is correct

BTC and ETH volatility move together almost perfectly:

| | |
|---|---|
| corr(BTC vol ratio, ETH vol ratio) | **+0.942** over 746 hourly obs |
| current BTC ratio | 0.266 |
| current ETH ratio | 0.269 |
| gates agree (block/allow) | **93.3%** of hours |

Right now **both** sit far below the 0.70 threshold — the quiet tape is
market-wide, exactly as the owner described.

## 2. But that correlation is the argument AGAINST adding ETH

Block rates over the sample:

| rule | hours blocked |
|---|---|
| BTC alone (current) | 27.9% |
| ETH alone | 26.8% |
| **BOTH must be quiet (AND)** | 24.0% |
| **EITHER quiet blocks (OR)** | 30.7% |

Disagreement is rare: BTC-only 3.9%, ETH-only 2.8%. Adding ETH as an OR
tightens blocking ~2.8pp; as an AND it loosens ~3.9pp. Either way it is a
**small perturbation of the same signal**, not new information — which is
what corr +0.942 implies.

## 3. The finding that matters more (and was not the question)

Splitting **trades already taken** by the vol_ratio the gate uses (n=564,
balanced halves):

| bucket | n | WR | mean |
|---|---|---|---|
| vol < 0.7 (gate would block) | 282 | 39.7% | **-25.1 bps** |
| vol >= 0.7 (gate allows) | 282 | 44.7% | **-24.9 bps** |

**The BTC vol gate separates win rate by 5pp but expectancy by 0.2 bps —
i.e. not at all.** Both sides lose the same amount.

Same test against ETH vol (n=530 matched; ETH ATR history spans ~41d so
2,017 older trades are unmatched — a real coverage limit, stated):

| bucket | n | WR | mean |
|---|---|---|---|
| ETH vol < 0.7 | 47 | 53.2% | **-28.1 bps** |
| ETH vol >= 0.7 | 483 | 41.2% | **-26.6 bps** |

ETH's split runs the *opposite* way on WR (higher WR inside the "toxic"
bucket) and is likewise flat on expectancy. At n=47 the low bucket is thin,
so this is directional, not conclusive — but it is certainly not evidence
for adding ETH.

## 4. Why this does not contradict screen 13

Screen 13 (14,555 outcomes) measured **win rate** in the band lane — 55.6%
vs 65.7% baseline — and shipped the veto as *WR-band protection, explicitly
NOT edge*, with every bucket after-cost negative. That is consistent with
what is measured here: the gate protects the WR band and does not, and never
claimed to, create expectancy.

Artifact 73's replay (blocked entries at -19.9/-25.3 bps) also stands: those
trades lose. So does the trades-taken population. **Everything loses roughly
-25 bps regardless of which side of the gate it falls on** — the artifact-81
conclusion restated from a new angle.

## 5. Verdict

**Do not add ETH to the vol gate.** It is 94% the same signal, would change
~3% of hours, and the underlying filter does not separate expectancy on
either asset. Adding it would be a 72nd rejection path (artifact 81 counted
71) buying no measurable selection value.

**What the owner's insight IS good for:** the premise — crypto moves as one
beta block — is correct and useful, just not as another *veto*. Its honest
uses are (a) a **regime label** stored on features so future screens can
condition on it, and (b) a reminder that per-symbol universe widening cannot
escape a market-wide quiet tape, which is exactly what artifact 79 found when
widening 44 -> 150 changed nothing.

**Not authorized by this measurement:** removing or loosening the BTC vol
gate. Its WR-band protection is real and pre-registered; a removal case needs
its own hashed prereg and must reckon with artifact 73's -19.9/-25.3 bps on
blocked entries.
