# Lessons — Self-Improvement Log

Per CLAUDE.md §3: after any correction from the user or advisor, record the
pattern here so it doesn't repeat. Short, imperative, "why" > "what".

## 2026-04-19 — WR restoration sweep

### L1: Verify advisor/agent claims before acting on them
Agent audit claimed "trailing + breakeven never reach exchange." Half true:
MCP BREAKEVEN in `bot_engine.py:2497` *does* call `_replace_exchange_sl`.
Only the `order_manager.py` trailing + partial TP branches were ghosts.
Had I trusted the agent's blanket claim, I'd have added redundant code
paths (double sync, double cancel, race conditions).

**Rule**: when an agent/audit says "X NEVER happens", grep for X first.
Verify scope before accepting scope.

### L2: Don't loosen protection without a concrete failure case
My initial plan included B2 (BREAKEVEN 2% → 3.5%) and B3 (trend-reversed
loss threshold 1% → 2.5%). Advisor pushed back: no evidence cuts were
firing, and both changes would LET LOSERS RUN. Only the STALE rule had
a single concrete bad case (BTC +0.2% cut on 2026-04-18).

**Rule**: symmetrical judgment — tightening protection needs a bad-cut
case; loosening protection needs a bad-hold case. Don't apply one without
the other.

### L3: `pnl_pct` is leverage-multiplied — mentally divide by leverage
At 3x, `pnl_pct = +0.5%` is only +0.17% raw price. Thresholds like
`-0.5 <= pnl_pct <= 0.5` read as "noise range" but cover ~0.33% of raw
price either side of entry — that's meaningful profit when SL is at
~3%. When reading any `pnl_pct` threshold in the codebase, divide by
leverage to get the price-move equivalent.

**Rule**: when evaluating pnl_pct thresholds, always compute the raw-%
equivalent at the active leverage before judging whether the threshold
is sensible.

### L4: `_exchange_sl=True` + `_exchange_tp=True` short-circuits everything
`check_sl_tp:1423` has a skip gate: `if _exchange_sl and _exchange_tp:
continue`. In the fully-protected live case, trailing/partial branches
never execute — the exchange handles both sides. A2/A3 fires ONLY when
exactly one of the two flags is True (partial exchange coverage). The
frequent real case: Binance rejects TP with -2021 "would immediately
trigger" when TP is too close to entry → position runs with
`_exchange_sl=True, _exchange_tp=False` → trailing + partial_close
paths DO execute → the ghost-update bug bites → A2/A3 fix matters.

Also: the `_place_exchange_sl_tp` helper sets `_exchange_sl=True`
*before* attempting TP; a TP failure leaves the partial state. Don't
mistake "SL flag set" for "full exchange protection."

**Rule**: when reasoning about exit paths, always consider the
partial-placement state (one flag True, other False). Most subtle bugs
in this subsystem live there, not in the symmetric "both placed" or
"both failed" states. `pos.stop_loss = X` + `_exchange_sl=True` + no
`_replace_exchange_sl` call = silent bug.

### L5: Don't re-introduce a local circuit breaker that duplicates
exchange SL logic
Initial plan included re-enabling the 3% hard stop and AGE_LIMIT even
when `_exchange_sl=True`. This creates a double-fire race: local path
sends close order, exchange SL also fires → one gets rejected or
leaves a naked reduce-only order. Trust the exchange SL when it's
active; the local path is a *fallback for placement failures*, not a
redundant monitor.

**Rule**: one authoritative close path per position at a time. Either
the exchange handles SL/TP or the local loop does — never both
racing on the same position.

## 2026-04-19 — WR restoration v2 (halt + reconcile)

### L6: Re-verify causal chains after a scope-reduction round
Initial plan (v1) claimed phantom reconcile losses were upstream of false
Spec §12 halts. Advisor pushed back on scope but DIDN'T challenge that
causal claim. When I then grepped for who calls `risk.record_trade_pnl` /
`record_trade_result`, only `order_manager.close_position` does. Reconcile
appends directly to `tracker._closed` — it never touches `_global_streak`.
The "phantom losses trigger halt" story was wrong; the 5-loss streak that
wrote `review_required.json` came from real trades.

**Rule**: a scope-reduction call doesn't validate the remaining claims,
just the absence of the cut ones. Re-grep each causal chain after the
reduction; advisors focus on what to cut, not what to reaffirm.

### L7: Authoritative source beats cached state for safety-critical flags
The Spec §12 halt bug had three suspect code paths (new-day reset clears
`_halted`, silent `unlink()` failure on Windows AV, `_halt_time=0` edge).
All would have needed separate fixes. The simpler invariant: `review_required.json`
is a filesystem object the operator can inspect; make it the source of
truth. Load-time check overrides `is_halted` from state; auto-resume
deletes the flag FIRST, only clears `_halted` if delete succeeds.

**Rule**: safety halts need a single authoritative fact, not derived
state across N code paths. If the ground truth is "is halt flag on
disk?", read it that way and stop trusting derived booleans.

### L8: pnl_pct is already leverage-multiplied — don't mistake it for phantom
Saw `-7.86%, -6.93%, -5.98%` in `risk_state.json` `trade_history` and
interpreted them as impossible given the 1.5-3.5% SL clamp. Wrong: those
are `pnl_pct` values which are `pnl / margin`, already leverage-multiplied.
-7.86% at 3x leverage = -2.62% raw price move, well within SL clamp. The
lesson L3 ("divide by leverage") was exactly the check I skipped.

**Rule**: when a percentage looks impossible, apply the leverage divisor
BEFORE assuming it's phantom/corrupt data. L3 applies at the reading
step, not just during design.

### L9: In PAPER/research mode, don't caveat findings with real-capital deployability
I kept appending "even a positive result isn't capturable at ~$1,300
(min-notional × many legs)" to every research finding. The user corrected
it: we're in PAPER mode, so capital is NOT the research constraint. Two
separate questions were being conflated:
  - **Does an edge exist, after costs?** — a research question, capital-
    independent. This is what PAPER mode is for. KEEP after-cost gating
    (an edge must beat fees/slippage to be real).
  - **Is it deployable at current real capital?** — a deployment question.
    Irrelevant while researching in PAPER; raise it only at a genuine
    live-sizing decision, and then once, not as a refrain.

**Rule**: evaluate PAPER-mode research purely on after-cost edge-existence.
Do NOT reflexively tack on the $1,300/min-notional caveat — it's a deploy-
time concern, not a research finding. (Does not contradict the standing
honesty that the bot won't cover living expenses — that's a big-picture
truth stated when relevant, not a per-result disclaimer.)

### L10: Tests must NEVER touch production state files (cwd-relative writes)
A regression test I added (test_partial_tp_accounting, 2026-06-03) constructed
a real VirtualWallet() with no isolation. Its _save() wrote the PRODUCTION
data/virtual_wallet.json (cwd-relative path) with start=1000 on every repo-root
pytest run. The bot then "re-seeded" the paper wallet on each restart (4x on
Jun 10-11), erasing paper losses and booking +913.52 of phantom margin credits
for positions open across the re-seed. The user saw an impossible dashboard
(+477.98 wallet vs -173.86 trade PnL) and lost trust in the data.

**Rule**: any test that instantiates a class with cwd-relative persistence MUST
isolate it (monkeypatch.chdir(tmp_path) or patch the path constant) BEFORE
construction. When writing a persistence class, add a save-guard that refuses
to write its production path while PYTEST_CURRENT_TEST is set. After any full
suite run, spot-check that no files under data/ were modified by tests
(git status data/ + key state files).

## 2026-07-07 — evidence-pipeline session
- **Relative data paths are a repo-wide TEST-ISOLATION contract.** Tests chdir to tmp and expect
  `Warehouse()` etc. to follow. Making WAREHOUSE_PATH absolute "for robustness" sent a test's fake
  trade INTO THE REAL WAREHOUSE (caught same session, row deleted). Fix cwd problems in the
  standalone ENTRYPOINT (`os.chdir(ROOT)`), never in core path constants.
- **Windows scheduled tasks need `cmd /c cd /d <repo> && ...`** — schtasks /TR alone launches with
  cwd=System32; a task can silently exit 1 on every run for days (check `Last Result` != 0).
  Add a staleness alert when MAX(resolved_ts) lags with PENDING rows present.
- **"RESOLVED" is forever: validate sizing/horizon BEFORE writing outcomes.** 2,175 outcomes were
  baked at ~$0 notional and premature horizons; rows marked RESOLVED are never re-selected, so
  garbage evidence is permanent. Floor/validate inputs at the writer, not the reader.
- **Stale calibration outlives its referent.** The 65-min trailing tier served a 75-min AGE_LIMIT
  removed months earlier and became the single biggest mechanical value-destroyer (-$52/30d).
  When changing a limit, grep for everything calibrated AGAINST it.
- **A quiet `except: pass` around evidence writes hid 5 days of no-op.** Evidence/bookkeeping
  failures must log loudly even when they must not crash the caller.
