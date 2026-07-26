# 05b — Accuracy-Band Geometry Sim · Adversarial Audit

_Auditor pass 2026-07-10. Target: `research/sim_accuracy_band.py` + `_workspace/strategy_pipeline/05_accuracy_band_sim.md`._

## VERDICT: **CONFIRMED** (raw data reproduces; machinery honest; no leakage) — recommendation **CORRECTED 0.50 → 0.45** on forward-robustness grounds.

The measurement is sound: it reproduces, has no look-ahead, censors honestly, preserves SL-first, and charges both cost legs at the modified TP. It is correctly labelled a **geometry-tuning artifact on a no-edge signal** (exp ≈ −0.24R at every frac — I confirm this). The only thing I would change is the knob value: **0.45**, not 0.50, because the recent/forward-representative window puts 0.50 only 1.6pp above the 60% floor.

---

## Attack results

### 1. REPRODUCE — PASS
Re-ran the script live (24s, 8750/8882 rows with forward bars). Output vs artifact (frac | my WR | artifact WR):
```
0.35  65.7%  65.7% | 0.40  64.9%  64.9% | 0.45  63.8%  63.8%
0.50  62.7%  62.7% | 0.55  61.3%  61.3% | 0.60  59.8%  59.8% | 0.70  56.9%  56.9%
```
frac 0.50 CI **61.6–63.7%** reproduces to the decimal. Row count 8882 (artifact 8878; +4 accrued — bot still writing). Fully reproducible; seedless; only live-candle drift, which is immaterial.

### 2. Selection / censoring bias — PASS (no flattering)
- Censored rows are **excluded, never counted as wins**: `resolve_one` returns `None` when no barrier is hit and `len(scan) < horizon` (shadow_resolver.py:181), and `simulate` increments `censored` and `continue`s (sim_accuracy_band.py:171). Verified.
- Compressing TP does lower the censored count (137→182 across fracs) exactly as expected (nearer TP resolves within available bars). But the differential is **~45 rows out of ~8730 = <0.5pp**, dwarfed by the ~9pp geometric spread. Not a material lever on WR.
- Censoring is confined to the trailing horizon window only: 1h rows (8067) carry `horizon_bars=0` → mapped to the documented `DEFAULT_HORIZON_BARS=24` (24h); 15m rows (815) carry 32 (8h). So only the newest ~24h / ~8h of entries can be censored — consistent with the observed 1.6%. The fetch cap and resolver censoring guard use the **same** default, so they agree.

### 3. Replay honesty — PASS
- **SL-first tie-break preserved** (shadow_resolver.py:168-175): same-bar [low,high] envelope containing both barriers books `stop_loss`. AFML-conservative; not overridden by the TP swap.
- **Wick triggers, consistent**: buy uses `low<=sl` / `high>=tp`, sell mirror (161-167). Same envelope logic the primary resolver ships in production.
- **Costs on BOTH legs at the modified TP**: `fees=(entry_filled+exit_filled)*size*6bps` (197) — exit_filled is the swapped TP on a take_profit; `slippage=(open_slip+exit_slip)*size` (198); stop exits pay the worse 10bps (191). The min-TP floor (0.5%) exists precisely so a TP win clears the ~0.22% round-trip — verified honest (avg-win at frac 0.35 = +0.275R, i.e. wins are genuinely small after cost, not inflated).
- **No look-ahead**: `build_fetch_candles` keeps only bars with `open > entry_ms` (entry bar itself EXCLUDED — conservative, no same-bar fill) AND `open+tf <= now` (forming bar dropped, closed bars only) (resolve_shadow_outcomes.py:114). No repaint.

### 4. Sample validity — PARTIAL / CAVEAT (the real weakness)
- Data is **100% `shadow_v1`, 100% Binance, 9.69-day span** — a **single regime** (net BTC/ETH up-drift). There is no cross-venue and no multi-regime coverage. WR here is path-dependent, not a stationary property.
- Because the signal is **no-edge** (exp ≈ 0 before geometry), WR is a *pure geometry × price-path* function, so the frac→WR shape transfers across lanes even if the post-2026-07-09 `SIGNAL_SOURCE=mcp` entry mix differs. The **level**, however, does not: it rides the regime.
- **Side asymmetry is regime, not geometry**: longs 67.0% vs shorts 56.2% at frac 0.50. A single global frac cannot band both sides; shorts sit below the 60% floor at every frac ≥ 0.45.
- **Recent-vs-older confirms drift toward the floor, NOT stability**: older(≥5d ago) 66.3% vs recent(≤5d) 61.6% at 0.50. The recent window is also **long-heavier (63% vs 50% buy)**, which *props up* recent WR in this up-tape — so a regime flip hits WR twice (side WR ↓ and mix ↓). The 5d-recent number is the forward-representative one, and it is only **1.6pp above the 60% floor**.

### 5. Recommendation robustness — CORRECT the pick
- frac 0.50 overall CI 61.6–63.7% **is** inside 60–67% ✓ (claim verified). The script picks 0.50 because 62.7% is nearest the 63% mid-band center *on the full window*.
- But on the **recent/forward-representative** window the mid-band pick shifts down: recent WR is 0.45→62.9% (dead-center 63%) vs 0.50→61.6% (near floor). Overall 0.45 = 63.8% (CI 62.8–64.8%), also dead-center, at **negligible expectancy cost** (−0.244R either way).
- **I would set `ACCURACY_TP_FRAC_OF_SL = 0.45`**: it is mid-band on BOTH the full and the recent window, and buys ~2–3pp of cushion above the 60% floor to absorb the observed recent drift and a regime flip — whereas 0.50 leaves the recent window one bad week from breaching the floor.

---

## Bottom line for the owner
- **Numbers CONFIRMED, method honest, zero look-ahead.** The artifact's own headline caveat is accurate: this is **geometry tuning on a no-edge signal — expectancy is negative (−0.24R) at every frac, it clears no promotion gate, and changing this knob does not change profitability.** It only re-shapes win/loss ratio.
- **Set 0.45, not 0.50**, if the knob is touched at all — for forward floor-cushion given the recent-window drift and the fact shorts already sit below band.
- Do **not** read the 60–67% "accuracy band" as an edge or a live-promotion signal. It is a cosmetic WR target measured on a single 9.7-day up-drift regime.

_Repro + probes in tmpdir; no commits; no live-code edits._
