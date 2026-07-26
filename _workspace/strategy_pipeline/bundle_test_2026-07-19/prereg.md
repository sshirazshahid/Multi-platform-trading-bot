# Pre-registration — futures_strategies_bundle test (2026-07-19)

Registered BEFORE any out-of-sample evaluation, per Trading_Bot strategy-evidence-pipeline
process rules (prereg before run; all sweeps reported; no post-hoc gate edits).

## Hypotheses
- H1: The bundle's three strategies as shipped (Trend SMA20/50 flip, MeanRev RSI-14 30/70 exit-50,
  Breakout Donchian-20 flip) do NOT sustain 63–67% win rate with net profit over 2023-01→2026-07,
  5 symbols, realistic per-exchange costs. (Trend & breakout are already-refuted families in the
  repo ledger; this is a confirmation test on the bundle's exact specs, not a reopen attempt.)
- H2 (Else-branch): A bracketed, regime-filtered mean-reversion family (the playbook's own
  "sensible add-ons" made concrete) can land OOS win rate in [63,67] with positive after-cost
  expectancy. Only configs passing ALL gates below are promoted to paper trading.

## Data
- Binance USDT-M klines (data.binance.vision, official): 1h/4h/1d, 2023-01-01 → 2026-07-18.
- Symbols (fixed a-priori): BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT.
- Binance real funding-rate history (signed cash flows).
- Bitget native candles + funding via api.bitget.com (as far back as available).
- Bybit: Binance price series + Bybit fee model (taker 0.055%/maker 0.02%) + Binance funding as
  proxy. Approximation to be validated against Bybit's own 2024 public archive (price RMSE check).
  DISCLOSED LIMITATION: live Bybit/Binance APIs are geo-blocked from this workspace.

## Splits (fixed before any run)
- In-sample (tuning allowed): 2023-01-01 → 2025-06-30.
- Out-of-sample (ONE evaluation of the pre-selected shortlist, no re-tuning): 2025-07-01 → 2026-07-18.

## Cost model
- Entry: limit at signal-bar close, maker 0.02%, filled only if the NEXT bar trades at/through the
  limit price; unfilled orders cancel after 1 bar.
- TP exit: resting limit (maker 0.02%).
- SL exit: stop-market, taker (0.05/0.055/0.06%) + 0.03% slippage.
- Time-stop exit: market at bar close, taker + 0.03% slippage.
- Same-bar TP&SL ambiguity: counted as SL (conservative; biases WR DOWN).
- Funding: real signed Binance series for Binance/Bybit models; real Bitget series for Bitget where
  fetched, else 0.01%/8h drag while in position.
- Leverage 1x. Per-repo risk rules honored in paper phase (risk ≤1%/trade, exposure ≤12%, lev ≤2.5x).

## Else-branch search space (all of it will be reported)
Family MR-B (bracketed RSI/Bollinger mean-reversion), timeframes {1h, 4h}, both sides:
- Entry trigger: RSI(p) < t_long / > 100-t_long, p ∈ {2,3,14}, t_long ∈ {10,15,20,25,30};
  optional Bollinger z(20) beyond {none, 1.5, 2.0}.
- Regime filter: {none, EMA200 trend-side only, ADX14 < 25}.
- Brackets: TP ∈ {0.6,0.8,1.0,1.2}×ATR14, SL ∈ {1.2,1.6,2.0,2.4}×ATR14, time-stop {12,24,48} bars.
- Grid is sampled (documented in sweep log), max ~400 configs; every evaluated config logged.
Family MR-C (close-to-mean fade): z-score(20) < -z_e enter long (mirror short), exit at mean touch
or bracket; z_e ∈ {1.5, 2.0, 2.5}. Same filters/brackets.

## Promotion gates (ALL required, evaluated on pooled 5-symbol OOS, per exchange fee model)
- G1: OOS pooled trades ≥ 30 (per repo promotion-gate convention).
- G2: OOS win rate ∈ [63%, 67%].
- G3: OOS net return > 0 after all costs; profit factor ≥ 1.05.
- G4: Bootstrap (2000 resamples) P(net>0) ≥ 0.90; WR 90% CI half-width reported.
- G5: Passes G2–G4 under at least 2 of the 3 exchange fee models.
- Multiplicity honesty: total configs tried is reported next to survivors; expected false-survivor
  count under the null is stated. IS selection band widened to [60,70] to absorb OOS shrinkage.

## What we will NOT do
- No widening of gates after seeing OOS results; failures are reported as failures.
- No live orders of any kind. Paper/sim only (repo restriction: no execution without dry-run).
- No leverage-escalation tricks to juice returns; no martingale/grid sizing to fake win rate.
