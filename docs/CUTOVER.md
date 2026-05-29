# Phase C — Live Cutover Playbook

**Prerequisites:**
- Phase A shadow build complete + running for ≥ 7 days.
- Phase B promotion gate evaluating to `PROMOTE` consistently for ≥ 7 days.
- User has reviewed `reports/shadow_compare_*.md` daily reports.
- Operator email confirming intent to cut over.

**Default state: cutover is OFF.** `data/ab_split.json` either does not
exist or has `pct_to_shadow: 0.0`. The bot routes 100% of candidates to
the live MCPBrain pipeline. Shadow continues to run for evaluation.

---

## Step 1 — Verify promotion gate has been GREEN for 7 days

```bash
# View the last 14 daily promotion-gate evaluations
tail -14 data/promotion_log.jsonl | python -c "
import json, sys
for line in sys.stdin:
    j = json.loads(line)
    print(f\"  {j.get('ts')} {j['verdict']:<8} n_shadow={j['shadow_n']:>4} \"
          f\"WR={j['shadow_wr']:.2f} live_WR={j['live_wr']:.2f}  ({j['reason']})\")
"
```

**Required:** at least 7 consecutive `PROMOTE` rows in the last 7 days.

If any `HOLD` or `REJECT`: STOP. Investigate `reason` field; do not advance.

---

## Step 2 — Sanity check the live trade tape

Live should not be on a fresh losing streak that the shadow happens to
have avoided by random chance:

```bash
sqlite3 data/warehouse.sqlite "
  SELECT
    DATE(ts_exit, 'unixepoch') AS day,
    COUNT(*) AS n,
    ROUND(SUM(realized_pnl), 2) AS sum_pnl,
    ROUND(AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END), 3) AS wr
  FROM trades
  WHERE status='CLOSED' AND ts_exit >= strftime('%s','now','-14 days')
  GROUP BY day ORDER BY day;
"
```

**Required:** live WR not collapsing in the last 3 days. If it is,
the shadow's relative outperformance may be temporary noise.

---

## Step 3 — Initialize at 10%

```bash
python -c "
from core.agents.ab_controller import save_split, SplitState
import time
save_split(SplitState(pct_to_shadow=0.10, set_at=int(time.time())))
"
```

This sets `data/ab_split.json` with `pct_to_shadow: 0.10`. The bot will
now (on next cycle) route 10% of candidates to the shadow pipeline.

**Wait 24 hours.** Watch:
- `tail -f logs/bot_*.log | grep -E "shadow|ab_route"`
- `python scripts/shadow_vs_live_report.py --window-hours 24`

**Stop and roll back immediately if:**
- Any halt fires
- Shadow places an order it shouldn't (verify with warehouse)
- Bot crashes more than once

Roll back: `python -c "from core.agents.ab_controller import save_split, SplitState; save_split(SplitState(pct_to_shadow=0.0, set_at=0))"`

---

## Step 4 — Daily ramp

If 24h at 10% looks healthy AND promotion gate still PROMOTEs, the
auto-ramp will advance to 25%. Same for 50% and 100%.

The auto-ramp is wired in `core/agents/ab_controller.py:auto_ramp()` and
should be invoked from `_daily_self_check` at 00:00 UTC. Verify:

```bash
grep "auto_ramp" data/promotion_log.jsonl
```

If you want to advance manually (e.g., your live data after 10% looks
strong and you're impatient), do not. The dwell time exists to protect
against single-day flukes. Wait 24h.

---

## Step 5 — Auto-rollback safety net

`rollback_if_unhealthy()` runs daily and drops `pct_to_shadow` to 0 if
rolling 48h WR < 35%. This is automatic. If it fires:

1. Read `data/ab_split.json` — `rollback_active: true`
2. Open `data/promotion_log.jsonl` and the `reports/shadow_compare_*.md`
   reports for the day rollback fired.
3. **Do not auto-resume.** Investigate root cause first.
4. To resume: edit `ab_split.json` to `rollback_active: false` and start
   over from Step 3 at 10%.

---

## Disabling cutover entirely

```bash
# Most robust — kill_state takes precedence over split flag
echo '{"killed": true, "reason": "manual_cutover_off"}' > data/shadow_kill_state.json

# Soft — split goes back to 0
echo '{"pct_to_shadow": 0.0, "set_at": 0}' > data/ab_split.json
```

---

## Reverting a deployed cutover

If the bot has been running with shadow at any pct > 0 and you decide to
fully revert:

1. Set `pct_to_shadow: 0.0` in `data/ab_split.json` (or `--rollback`)
2. Wait one full bot cycle (5 min)
3. Confirm via `grep ab_route logs/bot_*.log` — all routes should now be `live`
4. Optionally disable shadow entirely: `SHADOW_MODE_ENABLED=false python main.py`

The shadow_decisions table is preserved (read-only data). The bot
continues with its pre-Phase-A live behavior — no positions are forced
closed by reverting the split.

---

## What "100% cutover" means

When `pct_to_shadow == 1.0`:
- 100% of candidates are routed to the multi-agent shadow pipeline
- The shadow's `ExecutionAgent` is the one writing decisions
- **The original `MCPBrain` live decision path is not called for those candidates**

At this point, the multi-agent system is effectively the bot. The Phase
0 live MCPBrain remains compiled but is no longer the entry authority.
You can choose to leave it as a fallback (e.g., for `pct_to_shadow=1.0`
fail-cases) or remove it in a follow-up cleanup commit — that's a
post-cutover engineering decision.
