# trading_bot_mcp — read-only warehouse MCP server

A local [MCP](https://modelcontextprotocol.io) server that lets Claude (and you)
interrogate the bot's own data and reasoning **without touching the trading
path**. Every tool is read-only and the SQLite warehouse is opened in
`mode=ro`.

## Why

The bot records every candidate setup, trade, rejection, shadow-agent decision,
and model prediction into `data/warehouse.sqlite`. This server surfaces that as
queryable tools so you can ask, in plain language, things like *"what's the win
rate on BTC over the last 200 trades?"* or *"why were the last 20 setups
skipped?"* — and compare the shadow agent ensemble against the live path.

## Tools

| Tool | Purpose |
|------|---------|
| `trading_bot_list_tables` | List tables with row counts + columns (discover schema). |
| `trading_bot_recent_trades` | Most recent CLOSED trades (optional symbol filter). |
| `trading_bot_performance_summary` | Win rate, profit factor, total/avg PnL (optional symbol/strategy filter). |
| `trading_bot_recent_candidates` | Recent evaluated setups + skip reasons (filter ALLOW/SKIP/REVIEW/TAKEN). |
| `trading_bot_shadow_vs_live` | Shadow ensemble sim-PnL vs live realized PnL (promotion criterion). |
| `trading_bot_query` | Single guarded read-only `SELECT` against the warehouse. |

## Run

```bash
pip install -r mcp_server/requirements.txt
python mcp_server/trading_bot_mcp.py        # stdio transport
```

To register it for Claude Code, copy the example config (the live `.mcp.json` is
git-ignored so local setups don't get committed):

```bash
cp .mcp.json.example .mcp.json
```

A Claude Code session started in this repo then has the tools available.

## Safety

- Opens the warehouse read-only (`file:...?mode=ro`); cannot mutate trading state.
- The freeform `trading_bot_query` tool rejects anything but a single
  `SELECT`/`WITH … SELECT` (no DDL/DML, no multiple statements).
- No bot/config/ccxt imports — the server cannot place orders or change config.
- If `data/warehouse.sqlite` doesn't exist yet (fresh install), tools return a
  clear "warehouse not found" message rather than failing.

## Promotion note (shadow → live)

The multi-agent shadow ensemble (`core/shadow_runner.py`) is **log-only**. This
server is how you watch it: it may be promoted to the live decision path only
after its shadow decisions beat the live path on the honest gate
(`core/promotion_gate.py`). It is never promoted automatically.
