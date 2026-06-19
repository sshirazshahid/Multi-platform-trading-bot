# NautilusTrader Rebuild — Phase 0 + Phase 1

Isolated migration of the live signal onto [NautilusTrader](https://github.com/nautechsystems/nautilus_trader)
1.228.0. **Fully self-contained in `ntrader/` with its own venv (`ntrader/venv_nt`).** It never
touches the live bot's venv, `data/` state, or PM2 — the running PAPER bot is unaffected.

## Honest framing
NautilusTrader does **not** create edge. Every signal this bot has screened is NO_EDGE after costs
at retail-taker scale (see `reports/why_no_winning_trades_2026-06-19.md`). What this buys is
**backtest↔live parity**, a battle-tested risk/execution engine, and realistic fills — i.e. a
*trustworthy* backtest, not a profit lever.

## What it is
- **Strategy A** (`strategies/tsmom_strategy.py`) — faithful NT port of the LIVE discrete signal
  `core/tsmom_signal.py` (long-only, 28d momentum, vol-scaled size, **8% disaster stop**, leverage 1).
  Verified **bar-for-bar** against a pure-python reference on synthetic *and* real data.
- **Strategy B** (in `backtests/phase1_tsmom_backtest.py`) — the research validation model
  (`scripts/tsmom_validation_backtest.py`: continuous vol-weight, 5-day rebalance), recomputed on the
  same bars for aggregate context. A full NT port of B is the next increment.
- **Risk guard** (`strategies/pretrade_guard.py`) — authoritative CLAUDE.md §2 limits: 3% risk/trade,
  12% portfolio-wide gross exposure, mandatory ≤8% stop, ≤2.5x leverage.
- **Warehouse shim** (`adapters/warehouse_shim.py`) — mirrors `core/warehouse.py` to a *separate*
  `ntrader/data/nt_warehouse.sqlite`.

## Run it (Windows, Python 3.12)
```bash
py -3.12 -m venv ntrader/venv_nt
ntrader/venv_nt/Scripts/pip install -r ntrader/requirements-nt.txt

# Tests (parity gate + risk guard + disaster stop)
ntrader/venv_nt/Scripts/python -m pytest ntrader/tests/ --rootdir ntrader \
    -p no:cacheprovider --import-mode=importlib -q

# Phase 0 install smoke
ntrader/venv_nt/Scripts/python ntrader/backtests/phase0_smoke.py

# Phase 1 harness: A (NT) + B (reference) side by side
ntrader/venv_nt/Scripts/python ntrader/backtests/phase1_tsmom_backtest.py --source synth   --parity
ntrader/venv_nt/Scripts/python ntrader/backtests/phase1_tsmom_backtest.py --source parquet --parity
```

## Verified (this build)
- NT installs + backtests run on Windows / Python 3.12.9 (smoke).
- Strategy A bar-for-bar parity = **100% on all 5 majors**, synthetic and real `data/ohlcv_cache` parquet.
- Disaster-stop placement works (MUST-FIX: `make_qty` on the stop quantity); guard rejects all §2 breaches.
- Real-data run (after-cost): TSMOM is **capital-preserving** (drawdown roughly halved vs buy-and-hold);
  it is not a profit engine — consistent with the validation thesis.

## Next increments (owner-gated, NOT built)
- Full NT port of Strategy B (continuous rebalance).
- Phase 2: NT Binance/Bybit **testnet/demo** adapters for live↔backtest parity (PAPER only).
- Phase 3: any live cutover stays behind the existing double-latch
  (`CONTROLLED_LIVE` + `CONTROLLED_LIVE_ENABLED=true` + signed checklist).

## Gotchas (learned building this)
- The subtree is named `ntrader`, **not** `nt` — `nt` is Python's Windows built-in OS module.
- NT's Rust logger initializes **once per process**; multi-engine runs use `LoggingConfig(bypass_logging=True)`.
- The generic price container is a **high-precision (8dp) perp** so low-priced coins (XRP) aren't rounded.
