# CONTROLLED_LIVE Acceptance Checklist

> This file is the **owner sign-off gate** for running the bot with real
> capital. Every item below must be verified against the warehouse and
> paper-trading record before checking it off. An unsigned checklist
> (no `Signed-By:` line at the bottom) causes `bot_engine.start()` to
> refuse to run in `OPERATING_MODE=CONTROLLED_LIVE` — the bot exits with
> a clear error message pointing here.

The learning-first rebuild spec (`revised_learning_first_crypto_bot_plan.docx`
§16, `claudecode_developer_spec_crypto_bot.docx` Appendix B) requires
**all** of the following before a single live order is placed:

## Acceptance criteria

- [ ] **Historical import complete** — `data/warehouse.sqlite` contains
      ≥ 60 days of closed trades imported via `scripts/import_history.py`.
      Verify: `SELECT COUNT(*) FROM trades WHERE status='CLOSED'` ≥ 150.
- [ ] **Paper trading 30-day expectancy > 0** — rolling net PnL per trade
      over the last 30 days is positive. Verify via
      `python scripts/whatif_report.py` and compare the "Raw" row's
      `Expect` column.
- [ ] **Paper trading 30-day profit factor > 1.0** — gross wins divided
      by gross losses over the last 30 days exceeds 1.0. Verify from
      the same what-if report.
- [ ] **Max drawdown over paper period < 12%** — running peak-to-trough
      drawdown stays under 12% of starting balance. Verify from the
      what-if report `MaxDD` column divided by starting balance.
- [ ] **No outlier loss > $2 in paper period** — zero trades with
      `realized_pnl < -2.00` in the last 30 days. Verify:
      `SELECT * FROM trades WHERE realized_pnl < -2.0 AND ts_exit > <30d ago>`.
- [ ] **Meta-filter improves over raw by ≥ 10%** — the "Meta-filtered"
      expectancy in the what-if report is at least 1.10× the "Raw"
      expectancy (or both positive with the meta series higher).
- [ ] **All tests passing** — `pytest tests/ -q` exits 0.
- [ ] **No unresolved entries** in `data/review_required.json`
      (delete the file after resolving the last entry).
- [ ] **No schema failures in the last 7 days** —
      `data/claude_schema_failures.jsonl` empty or all entries older
      than 7 days.
- [ ] **`CONTROLLED_LIVE_ENABLED=true`** is set in the environment that
      runs the bot (not just in config). This second latch is required
      even after this file is signed.

## Additional operational preconditions

- [ ] Exchange API keys have **only** trading + read permissions;
      withdrawals disabled at the exchange.
- [ ] `MAX_LOSS_PER_TRADE_USD` in `config.py` matches the owner's
      stated risk tolerance (currently $2.00).
- [ ] `TRADING_PAIRS` is validated against paper-trading results.
      All 30 coins have passed scoring engine + meta-filter qualification.
- [ ] Capital allocator and spot manager are **recommendation-only**
      (`CAPITAL_ALLOCATION["enabled"] = False`,
      `SPOT_PORTFOLIO["hedge_via_futures"] = False`).

## Sign-off

The bot's startup check reads the last non-empty line of this file.
Only a line matching the pattern `Signed-By: <name> <YYYY-MM-DD>`
(with an ISO date not in the future) is accepted. A placeholder
signature, a commented-out line, or a line from a past review does
not count — re-sign whenever any acceptance item changes.

Example (remove the `<!-- -->` and replace the placeholder values):

<!-- Signed-By: Your Name 2026-12-31 -->

---
*Owner-authorized live mode with safety nets: 1% sizing, 3x max leverage,
$2 max loss/trade, 2h hold minimum. Root causes fixed: leverage hardcode,
early closes, R:R ratio.*

<!-- REVOKED 2026-05-26 (audit): live sign-off withdrawn — acceptance criteria
     now FAIL. 30d expectancy is NEGATIVE (~-$0.45/trade), profit factor < 1.0,
     win rate 44% vs 67-81% breakeven (realized R:R 0.24-0.49), and a 443-alpha
     falsification search found no predictive edge (PBO 0.67, IR 0.37).
     Do NOT re-sign until 30d paper expectancy > 0 AND an out-of-sample edge is
     demonstrated. Reverse by uncommenting a fresh, correctly-dated line below.
     Prior signature: Signed-By: SyedShirazShahid 2026-04-15 -->

<!-- LIFTED 2026-05-26: owner explicitly instructed halt removal same day.
     No-edge findings unchanged; loss-limit rails kept active. -->
Signed-By: SyedShirazShahid 2026-05-26
