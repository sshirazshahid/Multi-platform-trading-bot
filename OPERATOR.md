# Trading Bot — operator notes

## LIVE dashboard (`dashboard.py`)

- **Launch:** TradingBot.bat option **[2]**, or from the project root:  
  `venv\Scripts\python.exe dashboard.py`
- **Defaults:** refresh every **60** seconds (matches the batch menu). Same **`.env`** as the main bot (API keys, `DRY_RUN`).
- **Overrides:**
  - `--refresh SEC` — seconds between refresh and redraw (clamped 3–3600).
  - `--width COLS` — layout width (60–200). If omitted, width follows the terminal (capped at 120 columns).
- **Exit:** **Ctrl+C** in the console.
- **Fetch errors:** If a background exchange fetch fails, a **Fetch warning** line appears at the bottom; details are also logged at WARNING level (throttled to once per minute per distinct message).

## General

- Copy **`.env.example`** to **`.env`** and fill keys before live use.
- Prefer **`DRY_RUN=true`** until you validate behavior.
