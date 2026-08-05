# Contributing to Trading Bot

Thank you for considering contributing! This document explains how to get started.

---

## Getting Started

1. **Fork** the repository on GitHub
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/yourusername/trading-bot.git
   cd trading-bot
   ```
3. **Create a branch** for your change:
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Set up the environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # or venv\Scripts\activate on Windows
   pip install -r requirements.txt
   cp .env.example .env
   # Edit .env — for development, DRY_RUN=true is sufficient
   ```

---

## What We Welcome

- **New exchange connectors** — `exchanges/` — implement `BaseExchange`
- **New strategies** — `strategies/` — implement `BaseStrategy`
- **Bug fixes** — with a clear description of the issue and fix
- **Documentation improvements** — README, docstrings, inline comments
- **Performance improvements** — especially around OHLCV caching and scanning
- **Tests** — unit tests for any module in `core/` or `strategies/`

---

## Strategy evidence artifacts

When adding a row to `.claude/skills/refuted-families-ledger/SKILL.md`, commit the
cited `_workspace/strategy_pipeline/<N>_*` screen/prereg/audit/verdict files **the
same UTC day**. Untracked citations become evidence-loss. `reports/` outputs are
generated at runtime and gitignored — do not rely on them as the sole record.

`DRY_RUN` is derived from `OPERATING_MODE` in `config.py`; prefer setting
`OPERATING_MODE=PAPER` (or OBSERVATION) rather than treating `DRY_RUN` as an
independent env knob.

---

## Code Standards

- Python 3.10+
- Follow existing code style (no formatter enforced, but match the surrounding code)
- All strategies must extend `BaseStrategy` and implement `generate_signal()` and `run()`
- All exchange clients must extend `BaseExchange`
- No hardcoded credentials anywhere — use `config.py` / `.env`
- New features should work correctly with `DRY_RUN=true` before any live integration

---

## Adding a New Exchange

1. Create `exchanges/yourexchange_client.py`
2. Extend `BaseExchange` and implement all abstract methods
3. Handle exchange-specific quirks (clock sync, unified account, etc.) in `_init_exchange()`
4. Add the client to `exchanges/__init__.py`
5. Add API key config variables to `config.py` and `.env.example`
6. Add the exchange to the `TRADING_PAIRS` dict in `config.py`
7. Test with `DRY_RUN=true` — confirm balance reads correctly

---

## Adding a New Strategy

1. Create `strategies/your_strategy.py`
2. Extend `BaseStrategy`:
   ```python
   from strategies.base_strategy import BaseStrategy

   class MyStrategy(BaseStrategy):
       def __init__(self, order_manager, risk_manager, market_type="spot"):
           super().__init__(order_manager, risk_manager,
                            name="MyStrategy", market_type=market_type)

       def generate_signal(self, df):
           # Return "buy", "sell", or None
           ...

       def run(self, exchange, symbol):
           df = self.get_dataframe(exchange, symbol, "1h", 100)
           if df is None:
               return
           signal = self.generate_signal(df)
           ...
   ```
3. Export from `strategies/__init__.py`
4. Register in the strategy pool in `core/bot_engine.py`

---

## Submitting a Pull Request

1. Make sure `DRY_RUN=true` tests pass (no crashes, sensible log output)
2. Keep PRs focused — one feature or fix per PR
3. Write a clear PR description:
   - What problem does this solve?
   - How was it tested?
   - Any breaking changes?
4. Reference any related issues: `Fixes #123`

---

## Reporting Bugs

Open a GitHub issue with:
- Python version (`python --version`)
- Exchange(s) affected
- Relevant log lines (redact any API keys!)
- Steps to reproduce
- Expected vs actual behaviour

**Never include real API keys, secrets, or position data in issues.**

---

## Security

If you discover a security vulnerability, please do **not** open a public issue. Email the maintainers privately instead.

---

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
