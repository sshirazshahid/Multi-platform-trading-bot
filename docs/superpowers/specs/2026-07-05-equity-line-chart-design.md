# The Money Line — Equity Snapshot + Self-Contained Chart Page — Design

Date: 2026-07-05 · Status: approved design, pre-implementation
Supersedes as active thread: the 2026-07-04 owner-plan spec (owner chose to
restart; that spec remains on disk but is not being implemented).
Owner's stated success criterion: **"a number growing — the money line, not the
machinery."** Honesty constraint carried into every element: the system shows a
REAL line and never promises its direction.

## Goals
1. One glanceable chart with two real lines: total actual balance across
   Binance/Bybit/Bitget (USD) and the carry engine's cumulative paper PnL.
2. Self-updating (hourly), self-contained (offline HTML), zero effort to view.
3. Zero trading-path changes; balances never leave the machine.

## Non-goals
- No performance promises, projections, or simulated growth of any kind.
- No new trading behavior; this is instrumentation only.
- No web hosting/cloud — local file only (public repo, private balances).

## Deliverable 1 — scripts/snapshot_equity.py (single-shot, hourly)
- Repo harvester pattern: one pass per invocation, scheduled via
  `schtasks /Create /TN TradingBot-EquitySnapshot ... /SC HOURLY`.
- Per venue (binance/bybit/bitget): total account equity in USD via the
  existing exchange clients' read-only balance calls (`fetch_balance`-family;
  NO order modules imported — pinned by the grep-proof test pattern).
- Carry line input: sum of `net_pnl` over RESOLVED cycles in
  `data/carry_positions.json` (0.0 when none).
- Appends to `data/equity_history.csv`:
  `ts,binance_usd,bybit_usd,bitget_usd,total_usd,carry_paper_pnl,partial`
  (epoch-second float ts; a failed venue writes an empty cell and that row's
  `partial` flag is true; synthetic example row:
  `1783300000.0,150.12,140.33,130.25,420.70,0.0,false`).
- Fail-honest: one venue erroring never kills the pass and never interpolates
  a number; a fully-failed pass writes nothing.
- After appending, regenerates Deliverable 2 from the full CSV.

## Deliverable 2 — data/equity_chart.html (regenerated every snapshot)
- Fully self-contained: data embedded as a JS array, hand-rolled SVG line chart
  (~100 lines, two polylines + axes + hover value), no CDN/network so it opens
  offline and instantly.
- Series: total_usd (primary, market-driven) and carry_paper_pnl (secondary
  axis, the evidence staircase). Partial-snapshot points rendered hollow.
- Last-updated stamp; if the embedded snapshot age exceeds 3h at view time the
  page shows a visible "snapshots stopped — check TradingBot-EquitySnapshot
  task" banner (client-side clock comparison).
- Fixed caption (verbatim): "The big line's direction belongs to the market.
  The small line is the bot earning — or not earning — the right to touch the
  big one."
- `TradingBot.bat` gains a menu entry that opens the file in the default
  browser (`start "" data\equity_chart.html`).

## Safety, privacy, error handling
- Read-only exchange access; the script imports clients + json/csv only.
- `data/` is gitignored — the CSV and HTML (real balances) never reach the
  public repo; the spec/commits contain no personal dollar values.
- All exceptions per-venue contained; chart regeneration failure leaves the
  previous HTML intact (write tmp + atomic replace).

## Testing
- Unit: CSV append with all venues OK / one venue failed (gap + partial flag) /
  all failed (no row); carry sum with 0, some, and malformed cycles; HTML
  contains both series, the stamp, the caption, and N data points; atomic
  replace leaves prior file on injected write failure.
- Grep-proof: no order-path symbols in the new script (existing test pattern).
- Manual E2E: run once live, open the page, verify both lines and stamp; full
  suite green before merge.

## Rollout
Branch `feat/equity-line`; commit 1 = snapshotter + tests; commit 2 = chart
generator + bat entry + tests; register the hourly task; one live snapshot as
verification; merge to main.

## Success criteria
Owner double-clicks one thing and sees, within seconds, today's real total,
its recent trend, the bot's honest contribution, and whether the data is
fresh — with zero promised direction anywhere on the page.
