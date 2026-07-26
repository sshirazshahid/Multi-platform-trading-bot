# 21 — Loss autopsy (PAPER)

**Generated:** 2026-07-23T10:07:49.346073+00:00

## Plain language

Losses are dominated by **after-cost directional PAPER outcomes**, not by a missing candlestick AI.
Win-rate swings (funnel cohort recently ~46%) are expected under band geometry; expectancy stays the binding metric.

### Why it feels like “still losing”

1. **7d:** 41 closed, WR ~49%, sum PnL **−$3.68** (fees ~$1.62). Nearly coin-flip WR but **stop-losses lose more than take-profits win**: TP n=20 pnl=+$2.85 vs SL n=20 pnl=−$6.41.
2. **30d:** 850 closed, WR ~32%, sum PnL **−$250.58** — longer window includes older cohorts / colder regimes; not a new bug.
3. **Fill quality:** `taker_fallback` (n=21, pnl=−$3.24) is worse than `maker` (n=20, pnl=−$0.45). Maker-first helps; chase timeouts still bleed.
4. **Symbols:** JUP/INJ/ADA dominate 7d losses — concentration risk, not “missing pattern AI.”
5. Funnel `directional_paper_cohort` WR ~0.46 matches the 7d picture; earlier high WR was a short lucky stretch, not edge.

**No new defect found** that would flip expectancy positive by coding alone. Do not loosen gates to fake profits.

## Heartbeat

- Mode/profile/signal: `{'operating_mode': 'PAPER', 'paper_trading_profile': 'MAX_FLOW_BAND', 'signal_source': 'mcp', 'entry_policy': 'APPROVED_PAPER', 'is_halted': False, 'daily_pnl': -2.4120579355949014, 'open_positions': 1}`

## 7-day closed summary

- n=41 wins=20 losses=21 WR=0.4878048780487805
- sum realized_pnl=-3.684420298789892 avg=-0.08986390972658273 fees=1.61695546879

## 30-day closed summary

- n=850 wins=270 losses=580 WR=0.3176470588235294
- sum realized_pnl=-250.5815364434725 avg=-0.29480180758055585 fees=63.949167715184416

## Top exit reasons (7d)

- `take_profit`: n=20 wins=20 pnl=2.84851486328504 fees=0.651649956715
- `stop_loss`: n=20 wins=0 pnl=-6.409251162074935 fees=0.903463512075
- `data_feed_failure`: n=1 wins=0 pnl=-0.12368399999999774 fees=0.06184200000000001

## Strategy families (7d)

- `algo_det`: n=41 wins=20 pnl=-3.684420298789892

## Fill types (7d)

- `taker_fallback`: n=21 wins=8 pnl=-3.237542623544914
- `maker`: n=20 wins=12 pnl=-0.44687767524497835

## Worst symbols (7d by pnl)

- `JUP/USDT:USDT`: n=13 wins=2 pnl=-2.2266844770749135
- `INJ/USDT:USDT`: n=10 wins=5 pnl=-1.4217039088499976
- `ADA/USDT:USDT`: n=8 wins=4 pnl=-1.2594775566249792
- `AXS/USDT:USDT`: n=1 wins=0 pnl=-0.12368399999999774
- `ETH/USDT:USDT`: n=2 wins=2 pnl=0.28454779552500453
- `LTC/USDT:USDT`: n=2 wins=2 pnl=0.5038779253349932
- `HBAR/USDT:USDT`: n=5 wins=5 pnl=0.5587039228999982

## Funnel snapshot

- tsmom_20d_1h: ACCRUING 25/30 wr=0.36
- tsmom_20d_4h: ACCRUING 9/30 wr=0.2222222222222222
- breakout_60d: IDLE 0/30 wr=None
- unlock_short: IDLE 0/30 wr=None
- zfade_4h_cfg365: ACCRUING 19/30 wr=0.7368421052631579
- rsi2_4h_cfg226: ACCRUING 25/30 wr=0.64
- pullback_ma20_4h: ACCRUING 5/30 wr=0.0
- listing_short: STARVED 0/30 wr=None
- f1_carry: IDLE 0/30 wr=None
- directional_paper_cohort: ACCRUING 24/30 wr=0.458333
