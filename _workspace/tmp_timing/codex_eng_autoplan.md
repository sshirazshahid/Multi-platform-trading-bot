Reading additional input from stdin...
OpenAI Codex v0.144.5
--------
workdir: D:\Downloads\Trading_Bot
model: gpt-5.6-terra
provider: openai
approval: never
sandbox: read-only
reasoning effort: xhigh
reasoning summaries: none
session id: 019fb0c6-982c-7b81-b006-dac1ffa83d99
--------
user
IMPORTANT: Do NOT read or execute any SKILL.md files or files in skill definition directories (paths containing skills/gstack). These are AI assistant skill definitions meant for a different system. Stay focused on repository code only.

Review this plan for architectural issues, missing edge cases, and hidden complexity. Be adversarial.

Also consider these findings from prior review phases:
CEO: Both Claude and Codex say this is ops-honesty not profitability; F1 idle is binding; tradfi shortlist pollution; missing time-box/stop-pivot; competitive risks absent. Consensus DISAGREE on problem framing vs owner premises (owner confirmed refuse -EV = success).
Design: skipped, no UI scope.

Score YES/NO/PARTIAL: Architecture sound? Test coverage sufficient? Performance risks addressed? Security threats covered? Error paths handled? Deployment risk manageable?

File: _workspace/plans/2026-07-30-profitability-improvement-loop.md
deprecated: `[features].web_search_cached` is deprecated because web search is enabled by default.
Set `web_search` to `"live"`, `"indexed"`, `"cached"`, or `"disabled"` at the top level (or under a profile) in config.toml if you want to override it.
warning: Exceeded skills context budget of 2%. All skill descriptions were removed and 338 additional skills were not included in the model-visible skills list.
codex
I’ll inspect the plan and assess it against the prior findings, focusing on failure modes and unstated operational assumptions.
exec
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command "Get-Content -Raw -LiteralPath '_workspace\\plans\\2026-07-30-profitability-improvement-loop.md'" in D:\Downloads\Trading_Bot
 succeeded in 765ms:
<!-- /autoplan restore point: /c/Users/SyedShirazShahid/.gstack/projects/sshirazshahid-Multi-platform-trading-bot/probe-bundle-mr-shadow-2026-07-19-autoplan-restore-20260730-065941.md -->
# Profitability Improvement Loop (PAPER, evidence-gated)

**Branch:** `probe/bundle-mr-shadow-2026-07-19`  
**Date:** 2026-07-30  
**Owner intent:** Restart bot â†’ monitor â†’ keep improving/optimizing â†’ `/autoplan` the next improvement track.  
**Honesty bound:** AccBand WR â‰  edge. No live AccBand/TA reopen without frozen promotion. F1 is the only validated live family (currently idle on compressed funding).

## Problem

Directional MCP paper trading under AccBand geometry has measured after-cost negative expectancy. Owner asked for "profitable trades ONLY." Strict economic gate + EntryFloor 66 now refuse âˆ’EV opens. That correctly idles directional flow; it does not invent profit. The bot needs a continuous improvement loop that:

1. Keeps the process healthy (restart, banners, heartbeat, shortlist).
2. Accrues only evidence-gated research (movers band, HL funding, C2/gamma queue, screens).
3. Never confuses activity with edge.

## Premises (require human confirm â€” Phase 1 gate)

1. **Refuse âˆ’EV opens is success, not failure.** Zero AccBand opens under `EconGate=strict` is the intended state until a promoted model or a CONFIRMED_GO family exists.
2. **F1 carry remains the only live-path family** that may enter when `net_edge` clears; do not weaken F1 gates for activity.
3. **Abs-USDT mover band [$5,$200] is research/telemetry only** â€” shortlist + MCP tools; it does not authorize OPENs.
4. **Screens stay dual-agreed + prereg-hashed**; liq-cascade (41_) is CLOSED NO_GO; next work is queue-driven (44_ Stage-0 when dense, C2 accrual, HL harvest cadence).
5. **Optimize = reduce silent failures + accelerate honest evidence**, not raise WR by loosening costs.

## Current runtime (post-restart target)

| Knob | Target |
|------|--------|
| `OPERATING_MODE` | PAPER |
| `PAPER_TRADING_PROFILE` | MAX_FLOW_BAND |
| `MCP_ENTRY_MIN_SCORE` | 66 |
| `MCP_DIRECTIONAL_ECONOMIC_GATE_MODE` | strict |
| Abs band | min=5 max=200 prefer=true (launcher-pinned) |
| Shadow probes | log-only fleet unchanged |

## In scope (this plan)

### Track A â€” Ops health (ship continuously)
- Clean supervisor restart; verify boot banner (Profile / EntryFloor / EconGate).
- Heartbeat freshness watchdog; alert if stale > N minutes after boot.
- Persist `data/mover_shortlist_latest.json` each shadow scan; MCP `trading_bot_recent_movers` / `trading_bot_f1_edge_status`.
- Log econ-gate block reasons with counts (no silent starve without reason).

### Track B â€” Evidence accrual (research)
- Cadence `scripts/harvest_hl_funding.py` â†’ `data/hl_funding_history.jsonl`.
- When 44_ Stage-0 data density met: run hashed screen under frozen prereg (no outcomes before hash).
- Keep C2 gamma-expiry / new-data edge program on queue (no TA reopen).

### Track C â€” Small DX/ops polish (blast-radius)
- Ensure `.env` UTF-8 + launcher pins cannot be silently overridden.
- Dashboard/MCP visibility of: econ-gate mode, entry floor, F1 last-ok count, mover band hits.

## NOT in scope

- Reopening AccBand via `paper_fallback` for trade activity.
- Promoting any shadow probe without frozen gate â‰¥30 resolved + owner sign-off.
- Live CONTROLLED_LIVE.
- New directional TA/MCP strategies without pipeline GO.
- Mission Control UI redesign (unless a one-line ops fix).

## What already exists

| Need | Existing |
|------|----------|
| Econ gate | `core/economic_entry_gate.py`, `MCP_DIRECTIONAL_ECONOMIC_GATE_MODE` |
| Abs band | `core/universe_monitor.py`, `BROAD_UNIVERSE_*`, bot_engine snapshot |
| Launcher pin | `scripts/launcher_supervisor._safe_worker_env` |
| F1 | `core/carry_runner.py`, gate log |
| HL harvest | `scripts/harvest_hl_funding.py` |
| Pipeline | strategy-evidence-pipeline + refuted ledger |
| Prereg 44_ | `_workspace/strategy_pipeline/44_prereg_abs_usdt_mover_band.md` |

## Success criteria

1. Bot stays up; boot banner matches `.env` targets every restart.
2. `mover_shortlist_latest.json` refreshes each shadow cycle with `abs_band_usdt=[5,200]`.
3. Zero AccBand/MCP directional OPENs while model missing + strict gate (unless F1 clears).
4. At least one evidence artifact advanced per UTC day when queue has work (harvest or screen Stage-0).
5. `/autoplan` review complete â†’ approved task list executed in priority order.

## Implementation alternatives

| Approach | Effort | Risk | Pros | Cons |
|----------|--------|------|------|------|
| A. Ops+evidence loop (this plan) | 0.5â€“2d CC | Low | Honest; compounds data | Few/no fills |
| B. Loosen econ gate for activity | Hours | High | Looks "alive" | Reopens âˆ’EV bleed |
| C. Big new strategy invent | Days | High | Narrative hope | Ledger says NO without screen |

**Selected:** A (P1 completeness of honest loop, P5 explicit).

## Dream state delta

```
CURRENT: strict idle directional + F1 idle + movers telemetry shipping
THIS PLAN: durable monitor loop + evidence cadence + ops silence fixes
12-MONTH: â‰¥1 CONFIRMED_GO family beyond F1 OR F1 regime returns; promotion owner-signed
```

## Risks

| Risk | Mitigation |
|------|------------|
| Starvation misread as bug | Banner + econ-gate reason counters |
| Stale supervisor env | Launcher pin + kill full tree on restart |
| Outcome leak on screens | Hash prereg before outcomes; dual-model audit |
| Heartbeat lag after boot | Explicit freshness check in monitor |

## First executable tasks (pre-approval draft)

1. [x] Confirm post-restart: EconGate strict, EntryFloor 66, shortlist band [5,200] â€” verified 2026-07-30 ~06:56â€“06:58Z
2. [ ] Filter tradfi/tokenized-equity noise from crypto mover shortlist (or tag `asset_class`) â€” live shortlist is SKHYNIX/XAU/META/SOXS-heavy
3. [ ] Heartbeat-stale check after boot if missing
4. [ ] Schedule / document HL funding harvest cadence
5. [ ] Econ-gate block counter in status/MCP (if not already)
6. [ ] Queue next screen only when 44_ density gate passes

## Autoplan intake notes

- UI scope: **no** (ops + research; no component/screen/layout work)
- DX scope: **yes** (MCP tools, harvest CLI, launcher pins)
- Design doc: none for this branch â†’ standard review (office-hours optional)
- Codex: binary present; dual voices enabled in config


codex
The plan has the right honesty boundary, but its operational contract is still underspecified. I’m checking the named implementation surfaces for gaps between the stated controls and what can actually be verified.
exec
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command 'rg -n -S "MCP_DIRECTIONAL_ECONOMIC_GATE_MODE|MCP_ENTRY_MIN_SCORE|BROAD_UNIVERSE_|mover_shortlist_latest|trading_bot_recent_movers|trading_bot_f1_edge_status|heartbeat|harvest_hl_funding|_safe_worker_env" core scripts *.py 2>$null' in D:\Downloads\Trading_Bot
2026-07-30T02:07:30.403422Z ERROR codex_core::tools::router: error=Exit code: 1
Wall time: 0.7 seconds
Output:
scripts\backfill_positions_json.py:36:If the bot is currently running (heartbeat fresh < 120s), a clear
scripts\backfill_positions_json.py:60:HB_PATH = ROOT / "data" / "heartbeat.json"
scripts\backfill_positions_json.py:139:def heartbeat_age_sec() -> float | None:
scripts\backfill_positions_json.py:166:    age = heartbeat_age_sec()
scripts\backfill_positions_json.py:168:        print(f"WARNING: heartbeat is fresh ({age:.0f}s old) — bot is running.")
core\carry_runner.py:63:DEFAULT_HEARTBEAT_PATH = Path("data/carry_heartbeat.json")
core\carry_runner.py:152:        heartbeat_path: Path | str | None = DEFAULT_HEARTBEAT_PATH,
core\carry_runner.py:171:        self.heartbeat_path = Path(heartbeat_path) if heartbeat_path is not None else None
core\carry_runner.py:304:        # into the heartbeat below, which stores this summary).
core\carry_runner.py:307:        self._write_heartbeat(now, summary)
core\carry_runner.py:310:    def _write_heartbeat(self, now: float, summary: dict) -> None:
core\carry_runner.py:312:        if self.heartbeat_path is None:
core\carry_runner.py:315:            self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
core\carry_runner.py:316:            tmp = self.heartbeat_path.with_suffix(".json.tmp")
core\carry_runner.py:320:            os.replace(tmp, self.heartbeat_path)
core\bot_engine.py:249:            MCP_ENTRY_MIN_SCORE as _floor,
core\bot_engine.py:281:        f"  EntryFloor: MCP_ENTRY_MIN_SCORE={floor_txt}",
core\bot_engine.py:398:        # Health watchdog — observes heartbeat, halts, SL failures, loss streaks.
core\bot_engine.py:549:        self._last_heartbeat    = 0  # Time-based heartbeat writer (60s interval)
core\bot_engine.py:1159:            from config import BROAD_UNIVERSE_MONITOR, SHADOW_MODE
core\bot_engine.py:1170:            if BROAD_UNIVERSE_MONITOR.get("enabled", False):
core\bot_engine.py:1181:                        max(0, int(BROAD_UNIVERSE_MONITOR.get("shortlist_cap", 18))),
core\bot_engine.py:1187:                            db_path=Path(BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1192:                                    BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1196:                                max_ticker_age_s=float(BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1200:                                    BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1204:                                retention_days=float(BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1208:                                    BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1214:                                    BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1219:                                    BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1224:                                    BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1229:                                    BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1254:                                float(BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1257:                                float(BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1278:                        _Path("data/mover_shortlist_latest.json").write_text(
core\bot_engine.py:5014:    def _heartbeat_portfolio_es(self):
core\bot_engine.py:5032:    def _write_heartbeat(self):
core\bot_engine.py:5033:        """Write heartbeat file for external monitoring."""
core\bot_engine.py:5081:        heartbeat = {
core\bot_engine.py:5112:            "portfolio_es": self._heartbeat_portfolio_es(),
core\bot_engine.py:5136:            atomic_write_json(Path("data/heartbeat.json"), heartbeat, indent=2)
core\bot_engine.py:5138:            logger.warning(f"[Health] heartbeat write failed: {exc}")
core\bot_engine.py:7138:                if time.time() - self._last_heartbeat >= 60:
core\bot_engine.py:7139:                    self._last_heartbeat = time.time()
core\bot_engine.py:7140:                    self._write_heartbeat()
scripts\consolidate_spot_dust.py:21:1. Refuses to run when `heartbeat.json` is < 120s old (live bot may
scripts\consolidate_spot_dust.py:48:HB_PATH = ROOT / "data" / "heartbeat.json"
scripts\consolidate_spot_dust.py:55:def heartbeat_age_seconds() -> float | None:
scripts\consolidate_spot_dust.py:138:    ap.add_argument("--force", action="store_true", help="Bypass heartbeat freshness guard.")
scripts\consolidate_spot_dust.py:149:    age = heartbeat_age_seconds()
scripts\consolidate_spot_dust.py:152:            f"REFUSED: heartbeat is {age:.0f}s old — bot is running and may "
core\agents\probe_common.py:79:    """Epoch seconds -> compact UTC stamp for heartbeat lines; 'none' if absent."""
core\agents\probe_common.py:251:            f"[{log_tag}] heartbeat: units={len(units)} "
core\agents\probe_common.py:257:        logger.debug(f"[{log_tag}] heartbeat error: {e}")
core\agents\unlock_short_probe_agent.py:317:                f"[UnlockProbe] heartbeat: docs={len(docs)} "
core\agents\unlock_short_probe_agent.py:325:            logger.debug(f"[UnlockProbe] heartbeat error: {e}")
core\health_watchdog.py:17:  1. heartbeat_stale
core\health_watchdog.py:18:     `data/heartbeat.json` has not been touched for HEARTBEAT_STALE_SEC.
core\health_watchdog.py:85:HEARTBEAT_PATH        = Path("data/heartbeat.json")
core\health_watchdog.py:86:CARRY_HEARTBEAT_PATH  = Path("data/carry_heartbeat.json")
core\health_watchdog.py:130:    "heartbeat_stale":       30 * 60,
core\health_watchdog.py:131:    "carry_heartbeat_stale": 60 * 60,
core\health_watchdog.py:212:            self._check_heartbeat,
core\health_watchdog.py:213:            self._check_carry_heartbeat,
core\health_watchdog.py:368:    def _check_heartbeat(self) -> None:
core\health_watchdog.py:374:                "heartbeat_stale", "WARN",
core\health_watchdog.py:375:                f"heartbeat.json is {int(age)}s old (> {HEARTBEAT_STALE_SEC}s threshold)",
core\health_watchdog.py:379:    def _check_carry_heartbeat(self) -> None:
core\health_watchdog.py:380:        # NOT gated on SIGNAL_SOURCE — the heartbeat file's existence is the
core\health_watchdog.py:383:            self._edge_alert("carry_heartbeat_stale", False, "WARN", "")
core\health_watchdog.py:387:            "carry_heartbeat_stale", age > CARRY_HEARTBEAT_STALE_SEC, "WARN",
core\health_watchdog.py:388:            f"carry heartbeat is {int(age)}s old "
core\health_watchdog.py:394:        # Rev 5.2: the carry runner's heartbeat stores its pass summary;
core\health_watchdog.py:659:                "heartbeat is fresh but cycle_count has not advanced for "
scripts\harvest_hl_funding.py:8:  ./venv/Scripts/python.exe scripts/harvest_hl_funding.py
scripts\harvest_hl_funding.py:9:  ./venv/Scripts/python.exe scripts/harvest_hl_funding.py --coins BTC ETH SOL
core\mcp_brain.py:33:  4+ layers). MCP_ENTRY_MIN_SCORE env overrides BOTH score floors when set (2026-07-19
core\mcp_brain.py:223:    path. When the owner sets config.MCP_ENTRY_MIN_SCORE (PAPER research
core\mcp_brain.py:227:        from config import MCP_ENTRY_MIN_SCORE as _override
core\news_scanner.py:145:    """One-line source health for the heartbeat log, e.g. 'coindesk:5ok/0dead'."""
scripts\launcher_supervisor.py:106:def _safe_worker_env(
scripts\launcher_supervisor.py:139:        "MCP_DIRECTIONAL_ECONOMIC_GATE_MODE",
scripts\launcher_supervisor.py:140:        "MCP_ENTRY_MIN_SCORE",
scripts\launcher_supervisor.py:143:        "BROAD_UNIVERSE_ABS_MOVE_USDT_MIN",
scripts\launcher_supervisor.py:144:        "BROAD_UNIVERSE_ABS_MOVE_USDT_MAX",
scripts\launcher_supervisor.py:145:        "BROAD_UNIVERSE_PREFER_ABS_USDT_RANK",
scripts\launcher_supervisor.py:252:def _heartbeat_is_stale(
scripts\launcher_supervisor.py:260:    """Return true only when the owned worker has missed its heartbeat budget.
scripts\launcher_supervisor.py:262:    An old heartbeat from a prior process is deliberately ignored during the
scripts\launcher_supervisor.py:263:    startup grace period.  After that deadline, a missing heartbeat or one not
scripts\launcher_supervisor.py:271:    path = Path(root) / "data" / "heartbeat.json"
scripts\launcher_supervisor.py:304:    its heartbeat is stale, the launcher stops its own child and returns a
scripts\launcher_supervisor.py:311:        env=_safe_worker_env(root),
scripts\launcher_supervisor.py:321:        if _heartbeat_is_stale(
scripts\launcher_supervisor.py:329:                    "[launcher] main.py heartbeat stale strike "
scripts\launcher_supervisor.py:335:                "[launcher] main.py heartbeat remained stale; stopping the owned "
core\portfolio_risk.py:235:        """ES of the open book only — heartbeat/dashboard refresh."""
scripts\report_goal_progress.py:9:Read-only inputs: ``data/warehouse.sqlite``, ``data/heartbeat.json``, and the
scripts\report_goal_progress.py:361:    """Resolve the active profile and stable cohort start from the heartbeat."""
scripts\report_goal_progress.py:372:        heartbeat = json.loads((root / "data" / "heartbeat.json").read_text(
scripts\report_goal_progress.py:377:                "profile": heartbeat.get("paper_trading_profile") or "STANDARD",
scripts\report_goal_progress.py:378:                "operating_mode": heartbeat.get("operating_mode") or "UNKNOWN",
scripts\report_goal_progress.py:379:                "dry_run": heartbeat.get("dry_run"),
scripts\report_goal_progress.py:380:                "entry_policy": heartbeat.get("entry_policy") or "UNKNOWN",
scripts\report_goal_progress.py:383:        raw_start = heartbeat.get("paper_profile_started_at")
scripts\report_goal_progress.py:386:            context["cohort_start_source"] = "heartbeat_profile_start"
scripts\report_goal_progress.py:389:            str(heartbeat["timestamp"]).replace("Z", "+00:00")
scripts\report_goal_progress.py:391:        context["cohort_started_at"] = stamp - float(heartbeat["uptime_seconds"])
scripts\report_goal_progress.py:392:        context["cohort_start_source"] = "heartbeat_uptime"
core\report_emailer.py:813:        # Halted exchanges — check heartbeat
core\report_emailer.py:814:        hb = self._load_json(Path("data/heartbeat.json")) or {}
scripts\run_f1_carry_paper.py:55:HEARTBEAT_PATH = ROOT / "data" / "carry_heartbeat.json"
scripts\run_f1_carry_paper.py:325:                   heartbeat_path: Path | str = HEARTBEAT_PATH) -> bool:
scripts\run_f1_carry_paper.py:335:            heartbeat_path=heartbeat_path,
scripts\run_f1_carry_paper.py:342:                           heartbeat_path: Path | str) -> bool:
scripts\run_f1_carry_paper.py:368:    # Best-effort: also drop the heartbeat's recovery flag so the watchdog
scripts\run_f1_carry_paper.py:370:    # next scheduled pass to rewrite the heartbeat.
scripts\run_f1_carry_paper.py:372:        hb_p = Path(heartbeat_path)
scripts\run_f1_carry_paper.py:379:            print("[f1_carry_paper] heartbeat recovery flag cleared (watchdog re-arms)")
scripts\run_f1_carry_paper.py:380:    except Exception:  # noqa: BLE001 - heartbeat is advisory; never block the clear
scripts\run_f1_carry_paper.py:415:    for venue in VENUES:  # sequential; ONE shared state + heartbeat file
scripts\run_f1_carry_paper.py:427:            heartbeat_path=HEARTBEAT_PATH,
scripts\trading_system_audit.py:350:def audit_heartbeat(checks: list[dict[str, Any]]) -> None:
scripts\trading_system_audit.py:351:    path = ROOT / "data" / "heartbeat.json"
scripts\trading_system_audit.py:353:        add_check(checks, "runtime.heartbeat", "WARN", "heartbeat.json is missing")
scripts\trading_system_audit.py:360:        add_check(checks, "runtime.heartbeat", "FAIL", f"cannot parse heartbeat: {exc}")
scripts\trading_system_audit.py:364:        "runtime.heartbeat",
scripts\trading_system_audit.py:366:        f"heartbeat age is {age_min:.1f} minutes",
scripts\trading_system_audit.py:895:    audit_heartbeat(checks)
scripts\trim_ghost_positions.py:33:1. Refuses to run if heartbeat.json is < 120s old (bot is live and
scripts\trim_ghost_positions.py:44:                                                       # heartbeat
scripts\trim_ghost_positions.py:58:HB_PATH = ROOT / "data" / "heartbeat.json"
scripts\trim_ghost_positions.py:71:def heartbeat_age_seconds() -> float | None:
scripts\trim_ghost_positions.py:86:    ap.add_argument("--force", action="store_true", help="Bypass the live-bot heartbeat guard.")
scripts\trim_ghost_positions.py:93:    age = heartbeat_age_seconds()
scripts\trim_ghost_positions.py:96:            f"REFUSED: heartbeat is {age:.0f}s old — bot appears to be "
scripts\tv_client.py:12:Protocol: frames are "~m~<len>~m~<json>"; heartbeats "~m~<len>~m~~h~<n>" must
scripts\tv_client.py:94:def is_heartbeat(part: str) -> bool:
scripts\tv_client.py:197:                if is_heartbeat(part):

 exited 1 in 716ms:
scripts\backfill_positions_json.py:36:If the bot is currently running (heartbeat fresh < 120s), a clear
scripts\backfill_positions_json.py:60:HB_PATH = ROOT / "data" / "heartbeat.json"
scripts\backfill_positions_json.py:139:def heartbeat_age_sec() -> float | None:
scripts\backfill_positions_json.py:166:    age = heartbeat_age_sec()
scripts\backfill_positions_json.py:168:        print(f"WARNING: heartbeat is fresh ({age:.0f}s old) — bot is running.")
core\carry_runner.py:63:DEFAULT_HEARTBEAT_PATH = Path("data/carry_heartbeat.json")
core\carry_runner.py:152:        heartbeat_path: Path | str | None = DEFAULT_HEARTBEAT_PATH,
core\carry_runner.py:171:        self.heartbeat_path = Path(heartbeat_path) if heartbeat_path is not None else None
core\carry_runner.py:304:        # into the heartbeat below, which stores this summary).
core\carry_runner.py:307:        self._write_heartbeat(now, summary)
core\carry_runner.py:310:    def _write_heartbeat(self, now: float, summary: dict) -> None:
core\carry_runner.py:312:        if self.heartbeat_path is None:
core\carry_runner.py:315:            self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
core\carry_runner.py:316:            tmp = self.heartbeat_path.with_suffix(".json.tmp")
core\carry_runner.py:320:            os.replace(tmp, self.heartbeat_path)
core\bot_engine.py:249:            MCP_ENTRY_MIN_SCORE as _floor,
core\bot_engine.py:281:        f"  EntryFloor: MCP_ENTRY_MIN_SCORE={floor_txt}",
core\bot_engine.py:398:        # Health watchdog — observes heartbeat, halts, SL failures, loss streaks.
core\bot_engine.py:549:        self._last_heartbeat    = 0  # Time-based heartbeat writer (60s interval)
core\bot_engine.py:1159:            from config import BROAD_UNIVERSE_MONITOR, SHADOW_MODE
core\bot_engine.py:1170:            if BROAD_UNIVERSE_MONITOR.get("enabled", False):
core\bot_engine.py:1181:                        max(0, int(BROAD_UNIVERSE_MONITOR.get("shortlist_cap", 18))),
core\bot_engine.py:1187:                            db_path=Path(BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1192:                                    BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1196:                                max_ticker_age_s=float(BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1200:                                    BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1204:                                retention_days=float(BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1208:                                    BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1214:                                    BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1219:                                    BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1224:                                    BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1229:                                    BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1254:                                float(BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1257:                                float(BROAD_UNIVERSE_MONITOR.get(
core\bot_engine.py:1278:                        _Path("data/mover_shortlist_latest.json").write_text(
core\bot_engine.py:5014:    def _heartbeat_portfolio_es(self):
core\bot_engine.py:5032:    def _write_heartbeat(self):
core\bot_engine.py:5033:        """Write heartbeat file for external monitoring."""
core\bot_engine.py:5081:        heartbeat = {
core\bot_engine.py:5112:            "portfolio_es": self._heartbeat_portfolio_es(),
core\bot_engine.py:5136:            atomic_write_json(Path("data/heartbeat.json"), heartbeat, indent=2)
core\bot_engine.py:5138:            logger.warning(f"[Health] heartbeat write failed: {exc}")
core\bot_engine.py:7138:                if time.time() - self._last_heartbeat >= 60:
core\bot_engine.py:7139:                    self._last_heartbeat = time.time()
core\bot_engine.py:7140:                    self._write_heartbeat()
scripts\consolidate_spot_dust.py:21:1. Refuses to run when `heartbeat.json` is < 120s old (live bot may
scripts\consolidate_spot_dust.py:48:HB_PATH = ROOT / "data" / "heartbeat.json"
scripts\consolidate_spot_dust.py:55:def heartbeat_age_seconds() -> float | None:
scripts\consolidate_spot_dust.py:138:    ap.add_argument("--force", action="store_true", help="Bypass heartbeat freshness guard.")
scripts\consolidate_spot_dust.py:149:    age = heartbeat_age_seconds()
scripts\consolidate_spot_dust.py:152:            f"REFUSED: heartbeat is {age:.0f}s old — bot is running and may "
core\agents\probe_common.py:79:    """Epoch seconds -> compact UTC stamp for heartbeat lines; 'none' if absent."""
core\agents\probe_common.py:251:            f"[{log_tag}] heartbeat: units={len(units)} "
core\agents\probe_common.py:257:        logger.debug(f"[{log_tag}] heartbeat error: {e}")
core\agents\unlock_short_probe_agent.py:317:                f"[UnlockProbe] heartbeat: docs={len(docs)} "
core\agents\unlock_short_probe_agent.py:325:            logger.debug(f"[UnlockProbe] heartbeat error: {e}")
core\health_watchdog.py:17:  1. heartbeat_stale
core\health_watchdog.py:18:     `data/heartbeat.json` has not been touched for HEARTBEAT_STALE_SEC.
core\health_watchdog.py:85:HEARTBEAT_PATH        = Path("data/heartbeat.json")
core\health_watchdog.py:86:CARRY_HEARTBEAT_PATH  = Path("data/carry_heartbeat.json")
core\health_watchdog.py:130:    "heartbeat_stale":       30 * 60,
core\health_watchdog.py:131:    "carry_heartbeat_stale": 60 * 60,
core\health_watchdog.py:212:            self._check_heartbeat,
core\health_watchdog.py:213:            self._check_carry_heartbeat,
core\health_watchdog.py:368:    def _check_heartbeat(self) -> None:
core\health_watchdog.py:374:                "heartbeat_stale", "WARN",
core\health_watchdog.py:375:                f"heartbeat.json is {int(age)}s old (> {HEARTBEAT_STALE_SEC}s threshold)",
core\health_watchdog.py:379:    def _check_carry_heartbeat(self) -> None:
core\health_watchdog.py:380:        # NOT gated on SIGNAL_SOURCE — the heartbeat file's existence is the
core\health_watchdog.py:383:            self._edge_alert("carry_heartbeat_stale", False, "WARN", "")
core\health_watchdog.py:387:            "carry_heartbeat_stale", age > CARRY_HEARTBEAT_STALE_SEC, "WARN",
core\health_watchdog.py:388:            f"carry heartbeat is {int(age)}s old "
core\health_watchdog.py:394:        # Rev 5.2: the carry runner's heartbeat stores its pass summary;
core\health_watchdog.py:659:                "heartbeat is fresh but cycle_count has not advanced for "
scripts\harvest_hl_funding.py:8:  ./venv/Scripts/python.exe scripts/harvest_hl_funding.py
scripts\harvest_hl_funding.py:9:  ./venv/Scripts/python.exe scripts/harvest_hl_funding.py --coins BTC ETH SOL
core\mcp_brain.py:33:  4+ layers). MCP_ENTRY_MIN_SCORE env overrides BOTH score floors when set (2026-07-19
core\mcp_brain.py:223:    path. When the owner sets config.MCP_ENTRY_MIN_SCORE (PAPER research
core\mcp_brain.py:227:        from config import MCP_ENTRY_MIN_SCORE as _override
core\news_scanner.py:145:    """One-line source health for the heartbeat log, e.g. 'coindesk:5ok/0dead'."""
scripts\launcher_supervisor.py:106:def _safe_worker_env(
scripts\launcher_supervisor.py:139:        "MCP_DIRECTIONAL_ECONOMIC_GATE_MODE",
scripts\launcher_supervisor.py:140:        "MCP_ENTRY_MIN_SCORE",
scripts\launcher_supervisor.py:143:        "BROAD_UNIVERSE_ABS_MOVE_USDT_MIN",
scripts\launcher_supervisor.py:144:        "BROAD_UNIVERSE_ABS_MOVE_USDT_MAX",
scripts\launcher_supervisor.py:145:        "BROAD_UNIVERSE_PREFER_ABS_USDT_RANK",
scripts\launcher_supervisor.py:252:def _heartbeat_is_stale(
scripts\launcher_supervisor.py:260:    """Return true only when the owned worker has missed its heartbeat budget.
scripts\launcher_supervisor.py:262:    An old heartbeat from a prior process is deliberately ignored during the
scripts\launcher_supervisor.py:263:    startup grace period.  After that deadline, a missing heartbeat or one not
scripts\launcher_supervisor.py:271:    path = Path(root) / "data" / "heartbeat.json"
scripts\launcher_supervisor.py:304:    its heartbeat is stale, the launcher stops its own child and returns a
scripts\launcher_supervisor.py:311:        env=_safe_worker_env(root),
scripts\launcher_supervisor.py:321:        if _heartbeat_is_stale(
scripts\launcher_supervisor.py:329:                    "[launcher] main.py heartbeat stale strike "
scripts\launcher_supervisor.py:335:                "[launcher] main.py heartbeat remained stale; stopping the owned "
core\portfolio_risk.py:235:        """ES of the open book only — heartbeat/dashboard refresh."""
scripts\report_goal_progress.py:9:Read-only inputs: ``data/warehouse.sqlite``, ``data/heartbeat.json``, and the
scripts\report_goal_progress.py:361:    """Resolve the active profile and stable cohort start from the heartbeat."""
scripts\report_goal_progress.py:372:        heartbeat = json.loads((root / "data" / "heartbeat.json").read_text(
scripts\report_goal_progress.py:377:                "profile": heartbeat.get("paper_trading_profile") or "STANDARD",
scripts\report_goal_progress.py:378:                "operating_mode": heartbeat.get("operating_mode") or "UNKNOWN",
scripts\report_goal_progress.py:379:                "dry_run": heartbeat.get("dry_run"),
scripts\report_goal_progress.py:380:                "entry_policy": heartbeat.get("entry_policy") or "UNKNOWN",
scripts\report_goal_progress.py:383:        raw_start = heartbeat.get("paper_profile_started_at")
scripts\report_goal_progress.py:386:            context["cohort_start_source"] = "heartbeat_profile_start"
scripts\report_goal_progress.py:389:            str(heartbeat["timestamp"]).replace("Z", "+00:00")
scripts\report_goal_progress.py:391:        context["cohort_started_at"] = stamp - float(heartbeat["uptime_seconds"])
scripts\report_goal_progress.py:392:        context["cohort_start_source"] = "heartbeat_uptime"
core\report_emailer.py:813:        # Halted exchanges — check heartbeat
core\report_emailer.py:814:        hb = self._load_json(Path("data/heartbeat.json")) or {}
scripts\run_f1_carry_paper.py:55:HEARTBEAT_PATH = ROOT / "data" / "carry_heartbeat.json"
scripts\run_f1_carry_paper.py:325:                   heartbeat_path: Path | str = HEARTBEAT_PATH) -> bool:
scripts\run_f1_carry_paper.py:335:            heartbeat_path=heartbeat_path,
scripts\run_f1_carry_paper.py:342:                           heartbeat_path: Path | str) -> bool:
scripts\run_f1_carry_paper.py:368:    # Best-effort: also drop the heartbeat's recovery flag so the watchdog
scripts\run_f1_carry_paper.py:370:    # next scheduled pass to rewrite the heartbeat.
scripts\run_f1_carry_paper.py:372:        hb_p = Path(heartbeat_path)
scripts\run_f1_carry_paper.py:379:            print("[f1_carry_paper] heartbeat recovery flag cleared (watchdog re-arms)")
scripts\run_f1_carry_paper.py:380:    except Exception:  # noqa: BLE001 - heartbeat is advisory; never block the clear
scripts\run_f1_carry_paper.py:415:    for venue in VENUES:  # sequential; ONE shared state + heartbeat file
scripts\run_f1_carry_paper.py:427:            heartbeat_path=HEARTBEAT_PATH,
scripts\trading_system_audit.py:350:def audit_heartbeat(checks: list[dict[str, Any]]) -> None:
scripts\trading_system_audit.py:351:    path = ROOT / "data" / "heartbeat.json"
scripts\trading_system_audit.py:353:        add_check(checks, "runtime.heartbeat", "WARN", "heartbeat.json is missing")
scripts\trading_system_audit.py:360:        add_check(checks, "runtime.heartbeat", "FAIL", f"cannot parse heartbeat: {exc}")
scripts\trading_system_audit.py:364:        "runtime.heartbeat",
scripts\trading_system_audit.py:366:        f"heartbeat age is {age_min:.1f} minutes",
scripts\trading_system_audit.py:895:    audit_heartbeat(checks)
scripts\trim_ghost_positions.py:33:1. Refuses to run if heartbeat.json is < 120s old (bot is live and
scripts\trim_ghost_positions.py:44:                                                       # heartbeat
scripts\trim_ghost_positions.py:58:HB_PATH = ROOT / "data" / "heartbeat.json"
scripts\trim_ghost_positions.py:71:def heartbeat_age_seconds() -> float | None:
scripts\trim_ghost_positions.py:86:    ap.add_argument("--force", action="store_true", help="Bypass the live-bot heartbeat guard.")
scripts\trim_ghost_positions.py:93:    age = heartbeat_age_seconds()
scripts\trim_ghost_positions.py:96:            f"REFUSED: heartbeat is {age:.0f}s old — bot appears to be "
scripts\tv_client.py:12:Protocol: frames are "~m~<len>~m~<json>"; heartbeats "~m~<len>~m~~h~<n>" must
scripts\tv_client.py:94:def is_heartbeat(part: str) -> bool:
scripts\tv_client.py:197:                if is_heartbeat(part):

exec
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command '$paths = @('"'scripts/launcher_supervisor.py','core/health_watchdog.py','core/bot_engine.py','core/universe_monitor.py','scripts/harvest_hl_funding.py'); foreach ("'$p in $paths) { Write-Output "`n### $p"; rg -n -C 5 -S "def _safe_worker_env|def _heartbeat_is_stale|def _check_heartbeat|def _check_carry_heartbeat|mover_shortlist_latest|abs_move_usdt|BROAD_UNIVERSE_ABS_MOVE_USDT|def main|argparse|jsonl|append|write_text" -- $p }' in D:\Downloads\Trading_Bot
 succeeded in 973ms:

### scripts/launcher_supervisor.py
5-``bot_helper`` functions, so they never appear in a child process command line.
6-"""
7-
8-from __future__ import annotations
9-
10:import argparse
11-import getpass
12-import os
13-import re
14-import subprocess
15-import sys
--
101-    return _read_env(root / ".env").get(
102-        "PAPER_TRADING_PROFILE", STANDARD_PAPER_PROFILE
103-    )
104-
105-
106:def _safe_worker_env(
107-    root: Path,
108-    *,
109-    environ: Optional[dict[str, str]] = None,
110-) -> dict[str, str]:
111-    """Return a child environment pinned to the launcher's safe mode.
--
138-    _PIN_KEYS = (
139-        "MCP_DIRECTIONAL_ECONOMIC_GATE_MODE",
140-        "MCP_ENTRY_MIN_SCORE",
141-        "ACCURACY_TARGET_MODE",
142-        "BAND_REGIME_FILTER_ENABLED",
143:        "BROAD_UNIVERSE_ABS_MOVE_USDT_MIN",
144:        "BROAD_UNIVERSE_ABS_MOVE_USDT_MAX",
145-        "BROAD_UNIVERSE_PREFER_ABS_USDT_RANK",
146-    )
147-    for key in _PIN_KEYS:
148-        val = file_env.get(key)
149-        if val is not None:
--
247-        check=False,
248-    )
249-    return int(completed.returncode)
250-
251-
252:def _heartbeat_is_stale(
253-    root: Path,
254-    *,
255-    now: float,
256-    worker_started_at: float,
257-    max_age_seconds: float = HEARTBEAT_MAX_AGE_SECONDS,
--
481-                os.environ["PAPER_PROFILE_STARTED_AT"] = old_started_at
482-    finally:
483-        lock.close()
484-
485-
486:def _build_parser() -> argparse.ArgumentParser:
487:    parser = argparse.ArgumentParser(description="TradingBot launcher supervisor")
488-    commands = parser.add_subparsers(dest="command", required=True)
489-
490-    run_parser = commands.add_parser("run", help="run the supervised paper/read-only bot")
491-    run_parser.add_argument("--restart", action="store_true", help="restart after non-zero exits")
492-    run_parser.add_argument(
--
507-    mode_parser = commands.add_parser("set-mode", help="select a launcher-safe operating mode")
508-    mode_parser.add_argument("mode")
509-    return parser
510-
511-
512:def main(argv: Optional[list[str]] = None) -> int:
513-    args = _build_parser().parse_args(argv)
514-    os.chdir(ROOT)
515-    if args.command == "run":
516-        return supervise(
517-            restart=args.restart,

### core/health_watchdog.py
34-  5. loss_streak
35-     Warehouse `trades` table closed >= LOSS_STREAK_N losers in the
36-     last LOSS_STREAK_WINDOW_MIN minutes. WARN.
37-
38-  6. model_gate_starving
39:     mcp_decisions.jsonl tail shows zero OPENs in the last
40-     MODEL_STARVE_HOURS hours while RiskManager.daily_pnl > -2%.
41-     INFO — the model gate has been blocking everything; not an
42-     emergency, but operator should know.
43-"""
44-
--
54-
55-from loguru import logger
56-
57-
58-def _decision_ts_epoch(raw) -> Optional[float]:
59:    """Parse an mcp_decisions.jsonl ``ts`` value to epoch seconds.
60-
61-    Accepts legacy float/int epochs AND the current ISO-8601 strings
62-    (e.g. ``2026-07-20T01:13:35.644077+00:00``); returns None when
63-    unparseable so one bad record cannot silently kill a whole check
64-    (F6, 2026-07-20 audit).
--
88-# Live-sample accuracy milestones: announce measured per-cycle WR when the
89-# resolved-cycle count first reaches each level (owner goal bar: 80%).
90-CARRY_SAMPLE_MILESTONES = (1, 10, 30, 60)
91-REVIEW_FLAG_PATH      = Path("data/review_required.json")
92-POST_MORTEM_PATH      = Path("data/post_mortem.json")
93:DECISIONS_PATH        = Path("data/mcp_decisions.jsonl")
94-# Authoritative record of "a position actually opened" — every entry carries
95:# open_time. Used by the starvation check instead of mcp_decisions.jsonl, whose
96-# record shapes cannot answer the question (see _check_model_gate_starving).
97-POSITIONS_PATH        = Path("data/positions.json")
98-# Anchored to the repo root (parents[1] == repo root from core/) so a standalone
99-# entrypoint that constructs HealthWatchdog from another cwd reads the canonical
100-# warehouse — cwd-relative broke ShadowResolver from System32 on 2026-07-05. (The
--
197-
198-    def _persist_cooldowns(self) -> None:
199-        """Write last-alert timestamps to disk so cooldowns survive a restart."""
200-        try:
201-            COOLDOWN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
202:            COOLDOWN_STATE_PATH.write_text(
203-                json.dumps(self._state.last_alert), encoding="utf-8")
204-        except Exception as e:  # persistence is best-effort, never fatal
205-            logger.debug(f"[Watchdog] cooldown state persist skipped: {e}")
206-
207-    # ── Public API ──────────────────────────────────────────────────────
--
348-                state = json.loads(state_path.read_text(encoding="utf-8"))
349-                now = time.time()
350-                for key, intent in (state.get("pending") or {}).items():
351-                    age = now - float(intent.get("created_ts") or now)
352-                    if age > STALE_MAKER_INTENT_SEC:
353:                        stale_syms.append(f"{key}@{age / 60:.0f}min")
354-                    oldest_age = max(oldest_age, age)
355-        except (OSError, ValueError, TypeError):
356-            return  # unreadable state — not this check's business
357-        is_bad = bool(stale_syms)
358-        self._edge_alert(
--
363-             f"2026-07-11 fix)" if is_bad else ""),
364-            {"stale": stale_syms[:8],
365-             "oldest_age_min": round(oldest_age / 60, 1)} if is_bad else None,
366-        )
367-
368:    def _check_heartbeat(self) -> None:
369-        if not HEARTBEAT_PATH.exists():
370-            return  # bot may not have written one yet
371-        age = time.time() - HEARTBEAT_PATH.stat().st_mtime
372-        if age > HEARTBEAT_STALE_SEC:
373-            self._alert(
374-                "heartbeat_stale", "WARN",
375-                f"heartbeat.json is {int(age)}s old (> {HEARTBEAT_STALE_SEC}s threshold)",
376-                {"age_sec": int(age), "path": str(HEARTBEAT_PATH)},
377-            )
378-
379:    def _check_carry_heartbeat(self) -> None:
380-        # NOT gated on SIGNAL_SOURCE — the heartbeat file's existence is the
381-        # opt-in ("carry never ran" is not an alert). Edge-triggered.
382-        if not CARRY_HEARTBEAT_PATH.exists():
383-            self._edge_alert("carry_heartbeat_stale", False, "WARN", "")
384-            return
--
548-            try:
549-                if float(self._risk.daily_pnl) <= MODEL_STARVE_DAILY_PNL_FLOOR_PCT:
550-                    return
551-            except Exception:
552-                pass
553:        # 2026-07-28: this counted mcp_decisions.jsonl records whose TOP-LEVEL
554-        # "type"/"action" == "OPEN". Measured against the live file, top-level
555-        # type is only ever portfolio / rejection / position_monitor — the real
556-        # OPEN actions sit two levels down at decisions.actions[].type. So the
557-        # count was structurally always 0 and this INFO alert emailed hourly
558-        # forever while the bot traded normally (16 entries that day). The F6

### core/bot_engine.py
92-    heavy sell pressure") as the learning substrate's exit-type key. A genuine
93-    machine label is a short, space-free snake_case token; anything containing a
94-    space (or absurdly long) is prose and maps to a source-specific canonical
95-    close label.
96-    The full rationale is preserved in the [Claude] log line and, when threaded,
97:    via `exit_decision_id` -> mcp_decisions.jsonl. Idempotent on clean labels.
98-    """
99-    source_key = str(source or "claude").strip().lower()
100-    fallback = {
101-        "machine": "machine_close",
102-        "tsmom": "tsmom_close",
--
292-    ]
293-    try:
294-        from config import UNIVERSE_FLOW_LOOSEN as _ufl
295-
296-        if bool((_ufl or {}).get("enabled")):
297:            lines.append(
298-                "  UniverseLoosen: ON (V1 mild spread/depth/chop — 7d review)"
299-            )
300-        else:
301:            lines.append("  UniverseLoosen: OFF")
302-    except Exception:  # pragma: no cover
303:        lines.append("  UniverseLoosen: UNAVAILABLE")
304-    # Statistical contract (2026-07-24 harden): AccBand shapes WR by geometry;
305-    # dual-goal (band WR + profit) is CONFIRMED_NO_GO on the measured no-edge path.
306-    if acc_on:
307:        lines.append(
308-            "  AccBandNote: WR geometry research only; dual-goal profit "
309-            "CONFIRMED_NO_GO (screen 30_*); expectancy ~-0.24R class - not edge"
310-        )
311-    return lines
312-
--
439-                    )
440-                    if not _cpath.is_absolute():
441-                        _cpath = _P(__file__).resolve().parents[1] / _cpath
442-                    if not _cpath.exists():
443-                        _cpath.parent.mkdir(parents=True, exist_ok=True)
444:                        _cpath.write_text(
445-                            _json.dumps(
446-                                {
447-                                    "schema_version": 1,
448-                                    "enabled": True,
449-                                    "started_at_utc": _dt.now(_tz.utc).isoformat(),
--
594-                for ename, pairs in discovered.items():
595-                    existing = self.current_pairs.get(ename, {"spot": [], "futures": []})
596-                    for mtype in ("spot", "futures"):
597-                        for sym in pairs.get(mtype, []):
598-                            if sym not in existing.get(mtype, []):
599:                                existing.setdefault(mtype, []).append(sym)
600-                    self.current_pairs[ename] = existing
601-                total = sum(len(p.get("spot",[]))+len(p.get("futures",[]))
602-                            for p in self.current_pairs.values())
603-                logger.info(f"[Engine] Total pairs after discovery: {total}")
604-            except Exception as e:
--
1074-            markets = getattr(client, "markets", None) or {}
1075-            out = []
1076-            for sym, m in markets.items():
1077-                if (m.get("swap") and m.get("active", True)
1078-                        and m.get("quote") == "USDT" and m.get("settle") == "USDT"):
1079:                    out.append(sym)
1080-            return out
1081-        except Exception as e:
1082-            logger.debug(f"[ListingProbe] markets fetch failed: {e}")
1083-            return []
1084-
--
1213-                                max_contracts_per_venue=int(
1214-                                    BROAD_UNIVERSE_MONITOR.get(
1215-                                        "max_contracts_per_venue", 5000
1216-                                    )
1217-                                ),
1218:                                abs_move_usdt_min=float(
1219-                                    BROAD_UNIVERSE_MONITOR.get(
1220:                                        "abs_move_usdt_min", 0.0
1221-                                    )
1222-                                ),
1223:                                abs_move_usdt_max=float(
1224-                                    BROAD_UNIVERSE_MONITOR.get(
1225:                                        "abs_move_usdt_max", float("inf")
1226-                                    )
1227-                                ),
1228-                                prefer_abs_usdt_rank=bool(
1229-                                    BROAD_UNIVERSE_MONITOR.get(
1230-                                        "prefer_abs_usdt_rank", False
--
1250-                            "ts": now,
1251-                            "accepted_tickers": scan.accepted_tickers,
1252-                            "raw_tickers": scan.raw_tickers,
1253-                            "abs_band_usdt": [
1254-                                float(BROAD_UNIVERSE_MONITOR.get(
1255:                                    "abs_move_usdt_min", 0
1256-                                )),
1257-                                float(BROAD_UNIVERSE_MONITOR.get(
1258:                                    "abs_move_usdt_max", 0
1259-                                )),
1260-                            ],
1261-                            "shortlist": [
1262-                                {
1263-                                    "venue": r.venue,
--
1273-                                    "quote_volume_usdt": r.quote_volume_usdt,
1274-                                }
1275-                                for r in scan.shortlist
1276-                            ],
1277-                        }
1278:                        _Path("data/mover_shortlist_latest.json").write_text(
1279-                            _json.dumps(snap, indent=2), encoding="utf-8"
1280-                        )
1281-                    except Exception as _snap_err:
1282-                        logger.debug(f"[Shadow] shortlist snapshot skip: {_snap_err}")
1283-                    if out:
--
1308-                    return list(out)
1309-            screened: list[str] = []
1310-            for _ename, pairs in (self.current_pairs or {}).items():
1311-                for sym in (pairs.get("futures") or []):
1312-                    if sym not in screened:
1313:                        screened.append(sym)
1314-            if not screened or not self.active_exchanges:
1315-                return self._shadow_symbols_legacy(cap)
1316-            ex = next(iter(self.active_exchanges.values()))
1317-            # Route through the wrapper's fetch_tickers(market_type='futures'),
1318-            # which passes _futures_params() so the call hits the PERP endpoint
--
1329-                if not t:
1330-                    continue
1331-                pct, qv = t.get("percentage"), t.get("quoteVolume")
1332-                if pct is None or qv is None or float(qv) < min_qv:
1333-                    continue
1334:                moves.append((abs(float(pct)), sym))
1335-            moves.sort(reverse=True)
1336-            out = [s for _, s in moves[:cap]]
1337-            if not out:
1338-                # Screened symbols but zero usable tickers = key-format or
1339-                # venue trouble. Be LOUD (silent fallback is undiagnosable)
--
1359-        """Pre-2026-07-06 selection: first futures pairs per exchange."""
1360-        out: list[str] = []
1361-        for _ename, pairs in (self.current_pairs or {}).items():
1362-            for sym in (pairs.get("futures") or [])[:2]:
1363-                if sym not in out:
1364:                    out.append(sym)
1365-            if len(out) >= cap:
1366-                break
1367-        return out[:cap]
1368-
1369-    def _shadow_ctx_for_symbol(self, symbol: str) -> dict | None:
--
1443-        )
1444-        findings: list[str] = []
1445-
1446-        # 1) Active gates summary
1447-        gate_lines = []
1448:        gate_lines.append(
1449-            f"cell-filter={'on' if CELL_FILTER.get('enabled') else 'OFF'} "
1450-            f"(stars={len(STAR_SYMBOLS)}, "
1451-            f"band=[{CELL_FILTER.get('score_band_min')},"
1452-            f"{CELL_FILTER.get('score_band_max')}])")
1453:        gate_lines.append(
1454-            f"expectancy={'on' if EXPECTANCY_FILTER.get('enabled') else 'OFF'} "
1455-            f"(floor=${EXPECTANCY_FILTER.get('min_expected_dollar')}, "
1456-            f"star_floor=${EXPECTANCY_FILTER.get('min_expected_star')})")
1457:        gate_lines.append(
1458-            f"staleness={'on' if ENTRY_STALENESS_EXIT.get('enabled') else 'OFF'} "
1459-            f"(gap={ENTRY_STALENESS_EXIT.get('invalidation_gap_pct')}%, "
1460-            f"min_hold={ENTRY_STALENESS_EXIT.get('min_hold_minutes')}min)")
1461-        model_active = (MODEL_GATE.get('enabled')
1462-                        and not MODEL_GATE.get('shadow_only'))
1463:        gate_lines.append(
1464-            f"model-gate={'live' if model_active else ('shadow' if MODEL_GATE.get('enabled') else 'OFF')} "
1465-            f"(p_win>={MODEL_GATE.get('threshold_futures')})")
1466-        spot_active = (SPOT_STRATEGY.get('enabled')
1467-                       and not SPOT_PORTFOLIO.get('recommendation_only', True))
1468:        gate_lines.append(
1469-            f"spot-protect={'live' if spot_active else 'recommend-only'} "
1470-            f"(half=-{SPOT_STRATEGY.get('drawdown_half_pct')*100:.0f}%, "
1471-            f"full=-{SPOT_STRATEGY.get('drawdown_full_pct')*100:.0f}%)")
1472-        logger.info(f"[GateHealth] {' | '.join(gate_lines)}")
1473-
1474-        # 2) Silent-mode flags — wired but doesn't act
1475-        if SPOT_PORTFOLIO.get("recommendation_only", True) and SPOT_STRATEGY.get("enabled"):
1476:            findings.append(
1477-                "SPOT-PROTECT-V1 is enabled but SPOT_PORTFOLIO.recommendation_only=True — "
1478-                "drawdown triggers are computed but never sell. Set "
1479-                "recommendation_only=False to enable autonomous defense.")
1480-
1481-        # 3) Calibration sanity — expectancy floor must be reachable
1482-        floor = EXPECTANCY_FILTER.get("min_expected_dollar", 0.05)
1483-        # Heuristic: at $4-5 notional and 0.1% fees, round-trip is ~$0.01.
1484-        # A floor > $0.50 is almost certainly mis-calibrated.
1485-        if EXPECTANCY_FILTER.get("enabled") and floor > 0.50:
1486:            findings.append(
1487-                f"EXPECTANCY_FILTER floor=${floor:.2f} is suspiciously high — at "
1488-                f"current notional this is >50× round-trip fee and may block "
1489-                f"all symbols. Sanity-check vs warehouse data.")
1490-
1491-        # 4) Open positions without exchange-side SL — flag count, not error
--
1493-            naked = [p for p in self.tracker.get_open()
1494-                     if not getattr(p, "_exchange_sl", False)
1495-                     and p.market_type == "futures"
1496-                     and not p.paper_trade]
1497-            if naked:
1498:                findings.append(
1499-                    f"{len(naked)} live futures position(s) have no exchange-side "
1500-                    f"SL — relying on bot's monitor cycle for soft-SL only. "
1501-                    f"If bot crashes, these are unprotected.")
1502-        except Exception:
1503-            pass
--
1514-            from pathlib import Path as _P_p35
1515-            ensemble = _P_p35("data/models/ensemble_futures_latest.json")
1516-            if ensemble.exists():
1517-                age_h = (_t_p35.time() - ensemble.stat().st_mtime) / 3600
1518-                if age_h > 168:   # > 7 days = stale
1519:                    findings.append(
1520-                        f"ML ensemble model is {age_h/24:.1f} days old "
1521-                        f"(>{168/24:.0f}d threshold) — refit via "
1522-                        f"`python scripts/train_models.py` to keep MODEL_GATE "
1523-                        f"and LR sizing predictions current.")
1524-                elif age_h > 48:  # 2-7 days = warning
1525:                    findings.append(
1526-                        f"ML ensemble model is {age_h/24:.1f} days old "
1527-                        f"(>2d) — consider refitting via "
1528-                        f"`python scripts/train_models.py` for fresher predictions.")
1529-            else:
1530:                findings.append(
1531-                    "ML ensemble model file not found "
1532-                    "(data/models/ensemble_futures_latest.json) — "
1533-                    "MODEL_GATE may be operating without learned weights.")
1534-        except Exception as _e:
1535-            logger.debug(f"[GateHealth] model-age check skipped: {_e}")
--
1543-            n_recent = con.execute(
1544-                "SELECT COUNT(*) FROM trades WHERE status='CLOSED' AND ts_entry >= ?",
1545-                (since_7d,)).fetchone()[0]
1546-            con.close()
1547-            if n_recent < 10:
1548:                findings.append(
1549-                    f"only {n_recent} closed trade(s) in last 7d — gate-stack "
1550-                    f"calibration is statistically unverified.")
1551-        except Exception:
1552-            pass
1553-
--
1568-            n_stuck = con.execute(
1569-                "SELECT COUNT(*) FROM trades WHERE status='OPEN' AND ts_entry < ?",
1570-                (cutoff,)).fetchone()[0]
1571-            con.close()
1572-            if n_stuck > 0:
1573:                findings.append(
1574-                    f"{n_stuck} warehouse trade row(s) stuck at status='OPEN' "
1575-                    f"and older than 24h — learning analytics under-counted "
1576-                    f"by {n_stuck} closes. Run "
1577-                    f"`python scripts/backfill_warehouse_closes.py --commit` "
1578-                    f"to repair.")
--
1646-            for ex_name, exchange in self.active_exchanges.items():
1647-                if ex_name not in pairs:
1648-                    pairs[ex_name] = {"spot": [], "futures": []}
1649-                for pair in ps.scan_spot_holdings(exchange):
1650-                    if pair not in pairs[ex_name]["spot"]:
1651:                        pairs[ex_name]["spot"].append(pair)
1652-                for pair in ps.scan_futures_holdings(exchange):
1653-                    if pair not in pairs[ex_name]["futures"]:
1654:                        pairs[ex_name]["futures"].append(pair)
1655-        except Exception as e:
1656-            logger.debug(f"[Engine] ALL mode wallet scan: {e}")
1657-
1658-        # Layer 2: Aggressive pair discovery (expanded limits)
1659-        try:
--
1663-                if ex_name not in pairs:
1664-                    pairs[ex_name] = {"spot": [], "futures": []}
1665-                for mtype in ("spot", "futures"):
1666-                    for sym in disc_pairs.get(mtype, []):
1667-                        if sym not in pairs[ex_name][mtype]:
1668:                            pairs[ex_name][mtype].append(sym)
1669-        except Exception as e:
1670-            logger.debug(f"[Engine] ALL mode discovery: {e}")
1671-
1672-        total = sum(
1673-            len(p.get("spot", [])) + len(p.get("futures", []))
--
1695-        for ex_name, type_dict in new_pairs.items():
1696-            old = self.current_pairs.get(ex_name, {"spot": [], "futures": []})
1697-            for mtype in ("spot", "futures"):
1698-                for sym in type_dict.get(mtype, []):
1699-                    if sym not in old.get(mtype, []):
1700:                        added.append((ex_name, mtype, sym))
1701-        if added:
1702-            logger.info(f"[Engine] New holdings: {added}")
1703-            self.current_pairs = new_pairs
1704-        else:
1705-            logger.info("[Engine] No new holdings.")
--
2042-        # warehoused for the eventual edge screen. They are hard-blocked from
2043-        # live entry in _execute_open, so analyzing them never risks a trade.
2044-        from config import ANALYSIS_ONLY_BASES
2045-        for _b in sorted(ANALYSIS_ONLY_BASES):
2046-            if _b not in result:
2047:                result.append(_b)
2048-        return result
2049-
2050-    def _build_position_snapshot(self) -> list:
2051-        """Build snapshot of all open positions for Claude."""
2052-        result = []
--
2066-            lev = max(1, getattr(p, "leverage", 1) or 1)
2067-            if p.side == "buy":
2068-                pnl_pct = (current_price - p.entry_price) / p.entry_price * 100 * lev
2069-            else:
2070-                pnl_pct = (p.entry_price - current_price) / p.entry_price * 100 * lev
2071:            result.append({
2072-                "id": p.id,
2073-                "symbol": p.symbol,
2074-                "side": p.side,
2075-                "entry_price": p.entry_price,
2076-                "current_price": current_price,
--
2172-    def _get_recent_trades(self, n: int = 20) -> list:
2173-        """Get last N closed trades with P&L for accuracy feedback."""
2174-        closed = getattr(self.tracker, '_closed', [])[-n:]
2175-        result = []
2176-        for t in closed:
2177:            result.append({
2178-                "symbol": t.symbol,
2179-                "side": t.side,
2180-                "pnl": round(getattr(t, "pnl", 0) or 0, 4),
2181-                "strategy": getattr(t, "strategy", ""),
2182-                "exchange": t.exchange,
--
2328-            # the raw slope sign.
2329-            try:
2330-                _btc_p = Path("data/btc_trend.json")
2331-                _btc_p.parent.mkdir(parents=True, exist_ok=True)
2332-                _btc_tmp = _btc_p.with_name(_btc_p.name + ".tmp")
2333:                _btc_tmp.write_text(json.dumps({
2334-                    "trend": trend,
2335-                    "ema200_slope": slope_pct,
2336-                    "close_above_ema": above_ema,
2337-                    "ts": now,
2338-                }), encoding="utf-8")
--
3631-                            try:
3632-                                _sf = _j.loads(_sr.get("features_json") or "{}")
3633-                            except Exception:
3634-                                continue
3635-                            if isinstance(_sf.get("spread_pct"), (int, float)):
3636:                                _sp_samp.append(float(_sf["spread_pct"]))
3637-                            if isinstance(_sf.get("atr_pct_1h"), (int, float)):
3638:                                _at_samp.append(float(_sf["atr_pct_1h"]))
3639-                        _spread_pctl = _pctl(_feat.get("spread_pct"), _sp_samp)
3640-                        _vol_pctl = _pctl(_feat.get("atr_pct_1h"), _at_samp)
3641-                    except Exception:
3642-                        pass
3643-
--
4656-            return False
4657-
4658-        # A target venue's own book is the only valid execution snapshot.
4659-        # Generic market features may rank a candidate, but they cannot justify
4660-        # a fill on another venue. Walk the final size and retain expected costs
4661:        # for the append-only decision record.
4662-        try:
4663-            from config import (
4664-                EXECUTION_BOOK_DEPTH_LEVELS as _BOOK_LEVELS,
4665-            )
4666-            from config import (
--
4990-
4991-        with ThreadPoolExecutor(max_workers=8) as pool:
4992-            futs = []
4993-            for ex_name, exchange in self.active_exchanges.items():
4994-                for mtype in ("spot", "futures"):
4995:                    futs.append(pool.submit(_check_one, ex_name, exchange, mtype))
4996-            for f in as_completed(futs):
4997-                f.result()  # Propagate any unhandled exception
4998-
4999-    def _sltp_monitor_loop(self, stop_event: threading.Event):
5000-        """Dedicated thread: monitors SL/TP every 10 seconds, never blocked by scans."""
--
5609-                        f"({', '.join(a.get('action','?') for a in actions)})")
5610-
5611-            from config import SPOT_PORTFOLIO as _SP
5612-            if _SP.get("recommendation_only", True):
5613-                # Recommendation-only mode: spot_manager already wrote
5614:                # to data/spot_recommendations.jsonl; nothing to execute.
5615-                return
5616-
5617-            for a in actions:
5618-                # 2026-05-03: Multi-strategy coordination gate.
5619-                # If DCA bought this asset within the last 24h, skip
--
5767-            km = KnowledgeModel()
5768-            caution = []
5769-            fee_heavy = km.get_fee_heavy_strategies()
5770-            for strat in km.model.get("strategies", {}):
5771-                if km.is_caution_strategy(strat):
5772:                    caution.append(strat)
5773-            report["strategies"] = {
5774-                "caution_strategies": caution,
5775-                "fee_heavy_strategies": list(fee_heavy),
5776-            }
5777-        except Exception:
5778-            pass
5779-        # Save report
5780-        try:
5781-            Path("data").mkdir(parents=True, exist_ok=True)
5782:            Path("data/daily_check.json").write_text(
5783-                json.dumps(report, indent=2, default=str), encoding="utf-8")
5784-        except Exception:
5785-            pass
5786-        # Notify
5787-        ex_status = ", ".join(
--
6029-                                pnl_pct = (mark - entry) / entry * 100
6030-                            else:
6031-                                pnl_pct = (entry - mark) / entry * 100
6032-                        base = symbol.split("/")[0].split(":")[0]
6033-                        asset_class = "commodity" if base in self._COMMODITY_BASES else "crypto_futures"
6034:                        positions.append({
6035-                            "id": f"EX-{ex_name}-{symbol}-{side}",
6036-                            "symbol": symbol,
6037-                            "side": side,
6038-                            "entry_price": entry,
6039-                            "current_price": mark,
--
6073-                        continue
6074-                    usdt_val = amt * px
6075-                    if usdt_val < 5.0:
6076-                        continue
6077-                    asset_class = "commodity" if asset in self._COMMODITY_BASES else "crypto_spot"
6078:                    positions.append({
6079-                        "id": f"SPOT-{ex_name}-{asset}",
6080-                        "symbol": sym,
6081-                        "side": "buy",
6082-                        "entry_price": 0,
6083-                        "current_price": px,
--
6119-        try:
6120-            import json as _json
6121-            from pathlib import Path as _Path
6122-            _path = _Path("data/exchange_positions.json")
6123-            _path.parent.mkdir(parents=True, exist_ok=True)
6124:            _path.write_text(_json.dumps({
6125-                "ts": self._exchange_positions_time,
6126-                "positions": results,
6127-            }, indent=2), encoding="utf-8")
6128-        except Exception as _e:
6129-            logger.debug(f"[ExScan] cache write failed: {_e}")
--
6364-                    "source": "tracker",
6365-                    "size": p.size,
6366-                    "usdt_value": round(current_price * p.size, 2),
6367-                    "asset_class": asset_class,
6368-                }
6369:                pos_data.append(pd)
6370-                tracker_map[p.id] = p
6371-                sym_norm = p.symbol.split(":")[0]
6372-                tracked_keys.add((p.exchange.lower(), sym_norm, p.side))
6373-
6374-            # ── Phase 2: Exchange-discovered positions (futures + spot) ──
--
6378-            for ep in exchange_positions:
6379-                sym_norm = ep["symbol"].split(":")[0]
6380-                key = (ep["exchange"].lower(), sym_norm, ep["side"])
6381-                if key in tracked_keys:
6382-                    continue  # already tracked by bot — skip exchange duplicate
6383:                pos_data.append(ep)
6384-
6385-            if not pos_data:
6386-                return
6387-
6388-            # ── Priority cap: max 20 positions for MCP Brain ──

### core/universe_monitor.py
52-    max_abs_return_pct: float = 5_000.0
53-    # Owner 2026-07-30: prefer mid-priced movers whose absolute USDT move
54-    # (price * |pct|/100) falls in [$5, $200] — filters out BTC-scale swings
55-    # and sub-dollar dust while keeping the 1h/24h/7d shadow research set.
56-    # Disabled (min=0, max=inf) restores pure %-ranking behavior.
57:    abs_move_usdt_min: float = 0.0
58:    abs_move_usdt_max: float = float("inf")
59-    prefer_abs_usdt_rank: bool = False
60-    per_direction_per_horizon: int = 3
61-    max_shortlist: int = 18
62-    max_contracts_per_venue: int = 5_000
63-    horizons_ms: Tuple[Tuple[str, int], ...] = field(
--
73-            raise ValueError("max_future_skew_s must not be negative")
74-        if self.reference_tolerance_s < 0:
75-            raise ValueError("reference_tolerance_s must not be negative")
76-        if self.max_abs_return_pct <= 0:
77-            raise ValueError("max_abs_return_pct must be positive")
78:        if self.abs_move_usdt_min < 0:
79:            raise ValueError("abs_move_usdt_min must not be negative")
80:        if self.abs_move_usdt_max < self.abs_move_usdt_min:
81:            raise ValueError("abs_move_usdt_max must be >= abs_move_usdt_min")
82-        if self.per_direction_per_horizon < 0:
83-            raise ValueError("per_direction_per_horizon must not be negative")
84-        if self.max_shortlist < 0:
85-            raise ValueError("max_shortlist must not be negative")
86-        if self.max_contracts_per_venue <= 0:
--
349-
350-            values_sql = ", ".join("(?, ?)" for _ in horizons_ms)
351-            params: list[Any] = []
352-            for label, duration_ms in horizons_ms:
353-                params.extend((label, now_ms - int(duration_ms)))
354:            params.append(int(tolerance_ms))
355-            query = f"""
356-                WITH targets(label, target_ms) AS (VALUES {values_sql})
357-                SELECT c.venue, c.symbol, targets.label,
358-                       snap.observed_at_ms, snap.price
359-                FROM current_universe_contracts AS c
--
1080-                    market=market,
1081-                )
1082-                if normalized is None:
1083-                    rejections[str(reason or "invalid_ticker")] += 1
1084-                else:
1085:                    records.append(normalized)
1086-
1087-        contract_counts, contract_coverage = self._store.persist_contract_master(
1088-            contract_records,
1089-            now_ms=observed_at_ms,
1090-        )
--
1181-
1182-        groups: Dict[Tuple[str, str], list[Tuple[NormalizedTicker, ReturnPoint]]] = {}
1183-        for (_base, horizon), candidate in by_base_horizon.items():
1184-            row, point = candidate
1185-            abs_usdt = abs(point.value_pct) / 100.0 * float(row.price)
1186:            if abs_usdt < self.config.abs_move_usdt_min:
1187-                continue
1188:            if abs_usdt > self.config.abs_move_usdt_max:
1189-                continue
1190-            direction = "gainer" if point.value_pct > 0 else "loser"
1191:            groups.setdefault((horizon, direction), []).append(candidate)
1192-        for (horizon, direction), rows in groups.items():
1193-            if self.config.prefer_abs_usdt_rank:
1194-                # Rank by absolute USDT move magnitude (owner $5–$200 band).
1195-                rows.sort(
1196-                    key=lambda item: (
--
1245-                        break
1246-                if chosen is None:
1247-                    continue
1248-                row, selected_point = chosen
1249-                returns = observations.get((row.venue, row.symbol), {})
1250:                selected.append(
1251-                    ShortlistEntry(
1252-                        venue=row.venue,
1253-                        symbol=row.symbol,
1254-                        base=row.base,
1255-                        price=row.price,

### scripts/harvest_hl_funding.py
1-"""Harvest Hyperliquid hourly funding (F1 timing conditioner — data only).
2-
3-Queue item (30_edge_queue #4): free HL API as a *signal* vs local
4:``data/carry_gate_log.jsonl``. This script NEVER places orders and is not
5-wired into MCP directional opens.
6-
7-Usage:
8-  ./venv/Scripts/python.exe scripts/harvest_hl_funding.py
9-  ./venv/Scripts/python.exe scripts/harvest_hl_funding.py --coins BTC ETH SOL
10-"""
11-from __future__ import annotations
12-
13:import argparse
14-import json
15-import time
16-import urllib.request
17-from pathlib import Path
18-
19-ROOT = Path(__file__).resolve().parents[1]
20:OUT = ROOT / "data" / "hl_funding_history.jsonl"
21-API = "https://api.hyperliquid.xyz/info"
22-DEFAULT_COINS = ("BTC", "ETH", "SOL", "ARB", "AVAX", "LINK")
23-
24-
25-def fetch_meta_and_ctxs() -> dict:
--
31-    )
32-    with urllib.request.urlopen(req, timeout=30) as resp:
33-        return json.loads(resp.read().decode())
34-
35-
36:def main() -> int:
37:    ap = argparse.ArgumentParser()
38-    ap.add_argument("--coins", nargs="*", default=list(DEFAULT_COINS))
39-    args = ap.parse_args()
40-    want = {c.upper() for c in args.coins}
41-
42-    payload = fetch_meta_and_ctxs()
--
51-        ctx = ctxs[i] if i < len(ctxs) else {}
52-        try:
53-            funding = float(ctx.get("funding"))
54-        except (TypeError, ValueError):
55-            continue
56:        rows.append(
57-            {
58-                "ts": now,
59-                "venue": "hyperliquid",
60-                "coin": name,
61-                "funding": funding,
--
66-        )
67-    OUT.parent.mkdir(parents=True, exist_ok=True)
68-    with OUT.open("a", encoding="utf-8") as fh:
69-        for r in rows:
70-            fh.write(json.dumps(r) + "\n")
71:    print(f"appended {len(rows)} HL funding rows -> {OUT}")
72-    for r in rows:
73-        print(f"  {r['coin']}: funding={r['funding']}")
74-    return 0
75-
76-

exec
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command 'rg -n -C 4 -S "trading_bot_recent_movers|trading_bot_f1_edge_status|economic.*gate|econ.*gate|gate.*reason|block.*reason|f1.*last|carry.*last" core scripts tests 2>$null' in D:\Downloads\Trading_Bot
 succeeded in 803ms:
tests\test_accuracy_target_mode.py-81-    # SL 0.8% x 0.45 = 0.36 >= 0.35 clearance → keep 0.36 (not 0.5)
tests\test_accuracy_target_mode.py-82-    assert mb._apply_accuracy_target(0.8, 1.6, side="buy") == pytest.approx(0.36)
tests\test_accuracy_target_mode.py-83-
tests\test_accuracy_target_mode.py-84-
tests\test_accuracy_target_mode.py:85:def test_sub_clearance_tp_lifted_for_economic_gate(acc_on, monkeypatch):
tests\test_accuracy_target_mode.py-86-    """Sell frac 0.35 x 0.8% SL = 0.28 < 0.35 clearance → lift to 0.35.
tests\test_accuracy_target_mode.py-87-
tests\test_accuracy_target_mode.py:88:    Without this lift, paper_fallback economic_gate_stressed_breakeven
tests\test_accuracy_target_mode.py-89-    systematically starves AccBand shorts (RT ~31.5bps).
tests\test_accuracy_target_mode.py-90-    """
tests\test_accuracy_target_mode.py-91-    import config
tests\test_accuracy_target_mode.py-92-    monkeypatch.setattr(
--
core\arbitrage_engine.py-373-            "cross_venue_spread", strategy_version="f2-shadow-v1"
core\arbitrage_engine.py-374-        )
core\arbitrage_engine.py-375-        if not authorization.allowed:
core\arbitrage_engine.py-376-            logger.info(
core\arbitrage_engine.py:377:                f"[EntryPolicy] cross-venue entry blocked: {authorization.reason}"
core\arbitrage_engine.py-378-            )
core\arbitrage_engine.py-379-            return False
core\arbitrage_engine.py-380-        buy_ex  = self.exchanges.get(opp.buy_exchange)
core\arbitrage_engine.py-381-        sell_ex = self.exchanges.get(opp.sell_exchange)
--
tests\test_band_regime_filter.py-222-# ── wire-in pins (source-scan, matching repo scan-test style) ────────────────
tests\test_band_regime_filter.py-223-
tests\test_band_regime_filter.py-224-def test_veto_lives_only_inside_the_accuracy_band_carveout():
tests\test_band_regime_filter.py-225-    """The veto must run inside _execute_open's _acc_mode_on block — after the
tests\test_band_regime_filter.py:226:    band geometry is applied, before the R:R gate — and set reject_reason so
tests\test_band_regime_filter.py-227-    the candidates funnel stays auditable. Exactly one call site."""
tests\test_band_regime_filter.py-228-    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
tests\test_band_regime_filter.py-229-    assert src.count("self._band_regime_veto(action)") == 1
tests\test_band_regime_filter.py-230-    # Band lane is now inverted-geometry after apply (not merely TP-changed).
--
tests\test_cell_filter_entry_gate.py-105-    assert allow
tests\test_cell_filter_entry_gate.py-106-    assert reason == ""
tests\test_cell_filter_entry_gate.py-107-
tests\test_cell_filter_entry_gate.py-108-
tests\test_cell_filter_entry_gate.py:109:def test_non_star_below_band_blocks_with_correct_reason(stars, cf_default):
tests\test_cell_filter_entry_gate.py-110-    """score=69.99 must block with 'score_below_band'. The existing
tests\test_cell_filter_entry_gate.py-111-    score-gate at the scorer level requires score>=66 to even reach
tests\test_cell_filter_entry_gate.py-112-    this code, so 'below band' is rare but real for edge cases like
tests\test_cell_filter_entry_gate.py-113-    Claude-AI-proposed entries with arbitrary scores."""
--
tests\test_cell_filter_entry_gate.py-118-    assert not allow
tests\test_cell_filter_entry_gate.py-119-    assert reason == "cell_filter:score_below_band"
tests\test_cell_filter_entry_gate.py-120-
tests\test_cell_filter_entry_gate.py-121-
tests\test_cell_filter_entry_gate.py:122:def test_non_star_above_band_blocks_with_correct_reason(stars, cf_default):
tests\test_cell_filter_entry_gate.py-123-    """score=85.0 (the anti-EV threshold) on a non-STAR symbol must
tests\test_cell_filter_entry_gate.py-124-    block with 'score_above_band'. This is THE load-bearing rejection.
tests\test_cell_filter_entry_gate.py-125-    claude_portfolio data: score 85-100 is -$2.98/16 trades/31% WR."""
tests\test_cell_filter_entry_gate.py-126-    allow, reason = _gate_decision(
--
tests\test_carry_runner.py-1012-    assert r.run_once()["closed"] == 1
tests\test_carry_runner.py-1013-    state = json.loads((tmp_path / "carry_positions.json").read_text())
tests\test_carry_runner.py-1014-    assert state["recovery"]["reason"] == orig["reason"]
tests\test_carry_runner.py-1015-    assert state["recovery"]["ts"] == orig["ts"]
tests\test_carry_runner.py:1016:    # the second trigger is still gate-logged
tests\test_carry_runner.py-1017-    latch_logs = [x for x in _gate_recs(tmp_path) if x.get("recovery_latched")]
tests\test_carry_runner.py-1018-    assert len(latch_logs) == 2
tests\test_carry_runner.py-1019-
tests\test_carry_runner.py-1020-
--
core\bot_engine.py-140-    operating_mode: str,
core\bot_engine.py-141-    *,
core\bot_engine.py-142-    is_tsmom: bool = False,
core\bot_engine.py-143-) -> bool:
core\bot_engine.py:144:    """Return whether the P0 economic gate owns this entry.
core\bot_engine.py-145-
core\bot_engine.py-146-    Catalog aliases (``mcp_registry``/``algo_det``) resolve to the canonical
core\bot_engine.py-147-    ``MCP_DIRECTIONAL_PAPER`` ID.  Explicitly excluding tsmom keeps its
core\bot_engine.py-148-    momentum-flip/no-TP contract out of a bracket-expectancy calculation.
--
core\bot_engine.py-242-        from config import (
core\bot_engine.py-243-            ACCURACY_TARGET_MODE as _acc,
core\bot_engine.py-244-        )
core\bot_engine.py-245-        from config import (
core\bot_engine.py:246:            MCP_DIRECTIONAL_ECONOMIC_GATE as _egate,
core\bot_engine.py-247-        )
core\bot_engine.py-248-        from config import (
core\bot_engine.py-249-            MCP_ENTRY_MIN_SCORE as _floor,
core\bot_engine.py-250-        )
--
core\bot_engine.py-287-        f"  BandRegime: {'ON (ADX>30 / BTC vol<0.7 veto)' if _brf else 'OFF'}",
core\bot_engine.py-288-        (
core\bot_engine.py-289-            f"  SmartMoney: {'ON (hard entry gate)' if _smg else 'OFF'}"
core\bot_engine.py-290-        ),
core\bot_engine.py:291:        f"  EconGate  : mode={_egate.get('mode', 'strict')}",
core\bot_engine.py-292-    ]
core\bot_engine.py-293-    try:
core\bot_engine.py-294-        from config import UNIVERSE_FLOW_LOOSEN as _ufl
core\bot_engine.py-295-
--
core\bot_engine.py-3091-                payload, market_type="futures"
core\bot_engine.py-3092-            )
core\bot_engine.py-3093-            if not allowed:
core\bot_engine.py-3094-                logger.warning(
core\bot_engine.py:3095:                    f"[EconomicGate] promoted futures pointer unavailable: {reason}"
core\bot_engine.py-3096-                )
core\bot_engine.py-3097-                return None
core\bot_engine.py-3098-            version = str(payload.get("model_version") or "").strip()
core\bot_engine.py-3099-            return version or None
core\bot_engine.py-3100-        except Exception as exc:
core\bot_engine.py-3101-            logger.warning(
core\bot_engine.py:3102:                f"[EconomicGate] promoted futures pointer validation failed: {exc}"
core\bot_engine.py-3103-            )
core\bot_engine.py-3104-            return None
core\bot_engine.py-3105-
core\bot_engine.py:3106:    def _apply_mcp_directional_economic_gate(
core\bot_engine.py-3107-        self,
core\bot_engine.py-3108-        action: dict,
core\bot_engine.py-3109-        *,
core\bot_engine.py-3110-        strategy_id: str,
--
core\bot_engine.py-3146-            entry_slippage_frac = None
core\bot_engine.py-3147-
core\bot_engine.py-3148-        try:
core\bot_engine.py-3149-            from config import (
core\bot_engine.py:3150:                MCP_DIRECTIONAL_ECONOMIC_GATE,
core\bot_engine.py-3151-                SLIPPAGE,
core\bot_engine.py-3152-                STRESSED_EXIT_COST_FRAC,
core\bot_engine.py-3153-            )
core\bot_engine.py-3154-            from core.cost_model import fee_rate
core\bot_engine.py-3155-
core\bot_engine.py-3156-            exit_fee_frac = fee_rate(exchange_name, "futures", "taker")
core\bot_engine.py-3157-            exit_slippage_frac = float(SLIPPAGE["pct_close"])
core\bot_engine.py:3158:            gate_cfg = MCP_DIRECTIONAL_ECONOMIC_GATE
core\bot_engine.py-3159-            exit_cost_floor = float(STRESSED_EXIT_COST_FRAC)
core\bot_engine.py-3160-        except Exception:
core\bot_engine.py-3161-            exit_fee_frac = None
core\bot_engine.py-3162-            exit_slippage_frac = None
core\bot_engine.py-3163-            exit_cost_floor = None
core\bot_engine.py-3164-            gate_cfg = {}
core\bot_engine.py-3165-
core\bot_engine.py:3166:        from core.economic_entry_gate import evaluate_directional_entry
core\bot_engine.py-3167-
core\bot_engine.py-3168-        # 2026-07-21 paper_fallback: config resolves the mode to "strict"
core\bot_engine.py-3169-        # unless PAPER + MAX_FLOW_BAND (F3 gate); the operating_mode belt
core\bot_engine.py-3170-        # below is defense-in-depth for this per-call parameter.
--
core\bot_engine.py-3201-            "entry_quote_usdt": quote if math.isfinite(quote) else None,
core\bot_engine.py-3202-            "stressed_exit_cost_floor_frac": exit_cost_floor,
core\bot_engine.py-3203-            "promotion_authority": "ensemble_futures_latest.json",
core\bot_engine.py-3204-        })
core\bot_engine.py:3205:        action["economic_entry_gate"] = audit
core\bot_engine.py:3206:        action["economic_gate_reason"] = decision.reason
core\bot_engine.py:3207:        action["economic_gate_required_p_win"] = decision.required_p_win
core\bot_engine.py:3208:        action["economic_gate_stressed_expectancy_frac"] = (
core\bot_engine.py-3209-            decision.stressed_expectancy_frac
core\bot_engine.py-3210-        )
core\bot_engine.py-3211-
core\bot_engine.py-3212-        # Persist the audit alongside the exact venue book snapshot used for
--
core\bot_engine.py-3214-        try:
core\bot_engine.py-3215-            execution = action.get("execution_snapshot")
core\bot_engine.py-3216-            snapshot = dict(execution.get("snapshot") or {})
core\bot_engine.py-3217-            context = dict(snapshot.get("context") or {})
core\bot_engine.py:3218:            context["economic_entry_gate"] = audit
core\bot_engine.py-3219-            snapshot["context"] = context
core\bot_engine.py-3220-            execution["snapshot"] = snapshot
core\bot_engine.py-3221-        except (AttributeError, TypeError, ValueError):
core\bot_engine.py-3222-            # The execution-book guard already requires a valid snapshot in
core\bot_engine.py-3223-            # production; audit attachment failure must not hide the decision.
core\bot_engine.py-3224-            pass
core\bot_engine.py-3225-
core\bot_engine.py-3226-        if decision.allowed:
core\bot_engine.py:3227:            if decision.reason == "economic_gate_paper_fallback_pass":
core\bot_engine.py-3228-                logger.info(
core\bot_engine.py:3229:                    "[EconomicGate] paper_fallback admission (no promoted "
core\bot_engine.py-3230-                    f"model): {action.get('symbol')} "
core\bot_engine.py-3231-                    f"breakeven_wr={decision.breakeven_p_win:.3f} stressed"
core\bot_engine.py-3232-                )
core\bot_engine.py-3233-                return True
core\bot_engine.py-3234-            logger.info(
core\bot_engine.py:3235:                f"[EconomicGate] PASS {exchange_name}:{action.get('symbol')} "
core\bot_engine.py-3236-                f"p={decision.p_win:.3f} required={decision.required_p_win:.3f} "
core\bot_engine.py-3237-                f"EV={decision.stressed_expectancy_frac * 10_000:+.2f}bps "
core\bot_engine.py-3238-                f"cost={decision.stressed_round_trip_cost_frac * 10_000:.2f}bps "
core\bot_engine.py-3239-                f"model={decision.model_version}"
core\bot_engine.py-3240-            )
core\bot_engine.py-3241-            return True
core\bot_engine.py-3242-
core\bot_engine.py-3243-        logger.info(
core\bot_engine.py:3244:            f"[EconomicGate] BLOCKED {exchange_name}:{action.get('symbol')} "
core\bot_engine.py-3245-            f"reason={decision.reason} p={decision.p_win} "
core\bot_engine.py-3246-            f"required={decision.required_p_win} "
core\bot_engine.py-3247-            f"EV={decision.stressed_expectancy_frac} "
core\bot_engine.py-3248-            f"model={decision.model_version} promoted={decision.promoted_model_version}"
--
core\bot_engine.py-3334-        try:
core\bot_engine.py-3335-            _sl_active, _sl_reason = self.risk.is_sl_cooldown_active(symbol, side)
core\bot_engine.py-3336-            if _sl_active:
core\bot_engine.py-3337-                logger.info(
core\bot_engine.py:3338:                    f"[Risk29] BLOCKED open {symbol} {side} — {_sl_reason}")
core\bot_engine.py-3339-                action["reject_reason"] = "sl_cooldown_active"
core\bot_engine.py-3340-                return False
core\bot_engine.py-3341-        except Exception as _e:
core\bot_engine.py-3342-            logger.debug(f"[Risk29] sl-cooldown check skipped: {_e}")
--
core\bot_engine.py-3406-                    fail_open_stale=bool(_sm_gate.get("fail_open_stale", True)),
core\bot_engine.py-3407-                )
core\bot_engine.py-3408-                if _sm_reason:
core\bot_engine.py-3409-                    logger.info(
core\bot_engine.py:3410:                        f"[SmartMoney] BLOCKED {ex_name}:{symbol} {side} — {_sm_reason}"
core\bot_engine.py-3411-                    )
core\bot_engine.py-3412-                    action["reject_reason"] = _sm_reason
core\bot_engine.py-3413-                    return False
core\bot_engine.py-3414-        except Exception as _sm_exc:
--
core\bot_engine.py-3505-                    symbol_news_sentiment=_sym_sent,
core\bot_engine.py-3506-                )
core\bot_engine.py-3507-                if _ssf_d.block:
core\bot_engine.py-3508-                    logger.info(
core\bot_engine.py:3509:                        f"[ShortFilter] BLOCKED {symbol} sell -- {_ssf_d.reason}"
core\bot_engine.py-3510-                    )
core\bot_engine.py-3511-                    action["reject_reason"] = "short_filter_blocked"
core\bot_engine.py-3512-                    return False
core\bot_engine.py-3513-            except Exception as _ssfe:
--
core\bot_engine.py-3688-                            (f"meta_advisory:{_decision.reason}", _cid),
core\bot_engine.py-3689-                        )
core\bot_engine.py-3690-                    except Exception:
core\bot_engine.py-3691-                        pass
core\bot_engine.py:3692:                    logger.info(f"[MetaFilter] SKIP advisory (not blocking): {_decision.reason}")
core\bot_engine.py-3693-                if _decision.decision == "REVIEW":
core\bot_engine.py:3694:                    logger.info(f"[MetaFilter] REVIEW advisory (not blocking): {_decision.reason}")
core\bot_engine.py-3695-                _meta_size_multiplier = float(_decision.size_multiplier or 1.0)
core\bot_engine.py-3696-        except Exception as _mfe:
core\bot_engine.py-3697-            logger.debug(f"[MetaFilter] skipped ({_mfe}) -- defaulting to ALLOW")
core\bot_engine.py-3698-
--
core\bot_engine.py-3824-        except Exception as _ee:
core\bot_engine.py-3825-            logger.debug(f"[EV] check skipped ({_ee}) — defaulting to ALLOW")
core\bot_engine.py-3826-        if _ev_symbol_mult <= 0.0:
core\bot_engine.py-3827-            logger.warning(
core\bot_engine.py:3828:                f"[EV] BLOCKED {symbol} {side} ({_ev_symbol_reason}). "
core\bot_engine.py-3829-                f"Phase 27: catastrophic historical EV — refuse to trade.")
core\bot_engine.py-3830-            try:
core\bot_engine.py-3831-                from core.warehouse import get_warehouse as _gw_block
core\bot_engine.py-3832-                if (_cid := int(action.get("candidate_id") or 0)) > 0:
--
core\bot_engine.py-4105-                # NOT edge (all screen buckets stay after-cost negative).
core\bot_engine.py-4106-                _brf_reason = self._band_regime_veto(action)
core\bot_engine.py-4107-                if _brf_reason:
core\bot_engine.py-4108-                    logger.info(
core\bot_engine.py:4109:                        f"[BandRegime] BLOCKED {symbol} — {_brf_reason} "
core\bot_engine.py-4110-                        f"(band-lane toxic regime; screen 13_band_conditional)")
core\bot_engine.py-4111-                    action["reject_reason"] = _brf_reason
core\bot_engine.py-4112-                    return False
core\bot_engine.py-4113-
--
core\bot_engine.py-4177-                return False
core\bot_engine.py-4178-
core\bot_engine.py-4179-        # Risk manager circuit breakers
core\bot_engine.py-4180-        if not self.risk.can_trade(self.tracker.count_open()):
core\bot_engine.py:4181:            logger.warning(f"[Claude] BLOCKED by risk manager: {self.risk.halt_reason}")
core\bot_engine.py-4182-            action["reject_reason"] = "risk_halted"
core\bot_engine.py-4183-            return False
core\bot_engine.py-4184-
core\bot_engine.py-4185-        # Per-exchange position limit
--
core\bot_engine.py-4710-            )
core\bot_engine.py-4711-            action["reject_reason"] = "execution_book_guard_error"
core\bot_engine.py-4712-            return False
core\bot_engine.py-4713-
core\bot_engine.py:4714:        # P0 economic execution gate. The scorer/candidate/shadow pipeline has
core\bot_engine.py-4715-        # already run; this check affects only the order-producing boundary.
core\bot_engine.py-4716-        # It deliberately executes after the FINAL SL/TP geometry, quantity
core\bot_engine.py-4717-        # quantization, and venue-book walk so probability and friction use the
core\bot_engine.py-4718-        # exact bracket and entry snapshot that would be submitted. Catalog
core\bot_engine.py-4719-        # scoping leaves tsmom, carry, deep-breakout, spot, and live lanes
core\bot_engine.py-4720-        # unchanged.
core\bot_engine.py:4721:        if not self._apply_mcp_directional_economic_gate(
core\bot_engine.py-4722-            action,
core\bot_engine.py-4723-            strategy_id=_strategy_id,
core\bot_engine.py-4724-            operating_mode=OPERATING_MODE,
core\bot_engine.py-4725-            market_type=market_type,
--
core\bot_engine.py-4729-            entry_quote_usdt=float(_book_decision.quote_cost),
core\bot_engine.py-4730-            is_tsmom=_is_tsmom_entry,
core\bot_engine.py-4731-        ):
core\bot_engine.py-4732-            action["reject_reason"] = str(
core\bot_engine.py:4733:                action.get("economic_gate_reason") or "economic_gate_rejected"
core\bot_engine.py-4734-            )
core\bot_engine.py-4735-            return False
core\bot_engine.py-4736-
core\bot_engine.py-4737-        # Set leverage (LIVE only — 2026-05-31: was an ungated live-account
--
scripts\gate_effectiveness_report.py-124-    return out
scripts\gate_effectiveness_report.py-125-
scripts\gate_effectiveness_report.py-126-
scripts\gate_effectiveness_report.py-127-def report_cell_performance(c, since_ts: int) -> str:
scripts\gate_effectiveness_report.py:128:    """STAR vs mid-band vs blocked-cells (visible via skip_reason)."""
scripts\gate_effectiveness_report.py-129-    out = _section_header(
scripts\gate_effectiveness_report.py-130-        "2. Cell-filter performance",
scripts\gate_effectiveness_report.py-131-        "Closed-trade attribution by cell. STAR symbols, score-band "
scripts\gate_effectiveness_report.py-132-        "non-STAR, and blocks (visible only via SKIP rows in the "
--
scripts\gate_effectiveness_report.py-403-    md += "stale, retrain or revert MODEL_GATE_SHADOW=true.\n"
scripts\gate_effectiveness_report.py-404-    md += "- **Score 85+ with positive sum** → score-85 cap may be over-"
scripts\gate_effectiveness_report.py-405-    md += "tuned, consider relaxing.\n"
scripts\gate_effectiveness_report.py-406-    md += "- **OTHER cell with rows present** → cell-filter has a leak "
scripts\gate_effectiveness_report.py:407:    md += "(should be 0 — investigate skip_reason distribution).\n"
scripts\gate_effectiveness_report.py-408-    md += "- **Per-symbol with n>=5 and avg<-$0.30** → expectancy filter "
scripts\gate_effectiveness_report.py-409-    md += "should now be auto-blocking it on next entry attempt.\n"
scripts\gate_effectiveness_report.py-410-    md += "- **`entry_invalidated` in exit reasons** → the entry-"
scripts\gate_effectiveness_report.py-411-    md += "staleness exit is firing; verify avg PnL is small-negative or "
--
tests\test_decision_provenance.py-270-    assert action["reject_reason"] == "symbol_cooldown_active"
tests\test_decision_provenance.py-271-
tests\test_decision_provenance.py-272-
tests\test_decision_provenance.py-273-def test_every_return_false_in_execute_open_preceded_by_reason_stash():
tests\test_decision_provenance.py:274:    """TD-3 (gate-approved): ALL exits in _execute_open stash a reason."""
tests\test_decision_provenance.py-275-    src = inspect.getsource(BotEngine._execute_open)
tests\test_decision_provenance.py-276-    lines = src.splitlines()
tests\test_decision_provenance.py-277-    misses = []
tests\test_decision_provenance.py-278-    for i, ln in enumerate(lines):
--
scripts\launcher_supervisor.py-135-    child_env["PAPER_TRADING_PROFILE"] = profile
scripts\launcher_supervisor.py-136-    # Pin critical research knobs from .env so stale inherited values cannot
scripts\launcher_supervisor.py-137-    # silently override the owner's intent (dotenv never overrides inherited).
scripts\launcher_supervisor.py-138-    _PIN_KEYS = (
scripts\launcher_supervisor.py:139:        "MCP_DIRECTIONAL_ECONOMIC_GATE_MODE",
scripts\launcher_supervisor.py-140-        "MCP_ENTRY_MIN_SCORE",
scripts\launcher_supervisor.py-141-        "ACCURACY_TARGET_MODE",
scripts\launcher_supervisor.py-142-        "BAND_REGIME_FILTER_ENABLED",
scripts\launcher_supervisor.py-143-        "BROAD_UNIVERSE_ABS_MOVE_USDT_MIN",
--
tests\test_economic_gate_paper_fallback.py:1:"""MCP_DIRECTIONAL_ECONOMIC_GATE_MODE=paper_fallback (2026-07-21).
tests\test_economic_gate_paper_fallback.py-2-
tests\test_economic_gate_paper_fallback.py-3-No futures model has ever legitimately passed promotion, so the strict gate's
tests\test_economic_gate_paper_fallback.py-4-demand for a promoted model probability blocks 100% of MCP_DIRECTIONAL_PAPER
tests\test_economic_gate_paper_fallback.py:5:entries (reason=economic_gate_model_missing). The profile-gated fallback lets
tests\test_economic_gate_paper_fallback.py-6-the PAPER MAX_FLOW_BAND cohort trade on the stressed GEOMETRIC breakeven check
tests\test_economic_gate_paper_fallback.py-7-alone: the bracket's TP must still clear stressed round-trip costs
tests\test_economic_gate_paper_fallback.py-8-(breakeven WR < 1.0). Strict remains the default everywhere; the model path
tests\test_economic_gate_paper_fallback.py-9-returns automatically the moment a model is legitimately promoted.
--
tests\test_economic_gate_paper_fallback.py-16-import pytest
tests\test_economic_gate_paper_fallback.py-17-
tests\test_economic_gate_paper_fallback.py-18-import config as config_module
tests\test_economic_gate_paper_fallback.py-19-from core.bot_engine import BotEngine
tests\test_economic_gate_paper_fallback.py:20:from core.economic_entry_gate import evaluate_directional_entry
tests\test_economic_gate_paper_fallback.py-21-
tests\test_economic_gate_paper_fallback.py-22-ROOT = Path(__file__).resolve().parents[1]
tests\test_economic_gate_paper_fallback.py-23-
tests\test_economic_gate_paper_fallback.py-24-
--
tests\test_economic_gate_paper_fallback.py-61-    return action
tests\test_economic_gate_paper_fallback.py-62-
tests\test_economic_gate_paper_fallback.py-63-
tests\test_economic_gate_paper_fallback.py-64-def _gate(engine, action, *, operating_mode="PAPER", sl_pct=2.0, tp_pct=1.0):
tests\test_economic_gate_paper_fallback.py:65:    return engine._apply_mcp_directional_economic_gate(
tests\test_economic_gate_paper_fallback.py-66-        action,
tests\test_economic_gate_paper_fallback.py-67-        strategy_id="MCP_DIRECTIONAL_PAPER",
tests\test_economic_gate_paper_fallback.py-68-        operating_mode=operating_mode,
tests\test_economic_gate_paper_fallback.py-69-        market_type="futures",
--
tests\test_economic_gate_paper_fallback.py-77-# ── config-level mode resolution (profile gate) ──────────────────────────
tests\test_economic_gate_paper_fallback.py-78-
tests\test_economic_gate_paper_fallback.py-79-
tests\test_economic_gate_paper_fallback.py-80-def test_mode_defaults_to_strict():
tests\test_economic_gate_paper_fallback.py:81:    fn = config_module._profile_gated_economic_gate_mode
tests\test_economic_gate_paper_fallback.py-82-    assert fn("", "PAPER", "MAX_FLOW_BAND") == "strict"
tests\test_economic_gate_paper_fallback.py-83-    assert fn("strict", "PAPER", "MAX_FLOW_BAND") == "strict"
tests\test_economic_gate_paper_fallback.py-84-
tests\test_economic_gate_paper_fallback.py-85-
tests\test_economic_gate_paper_fallback.py-86-def test_mode_paper_fallback_requires_paper_plus_max_flow_band():
tests\test_economic_gate_paper_fallback.py:87:    fn = config_module._profile_gated_economic_gate_mode
tests\test_economic_gate_paper_fallback.py-88-    assert fn("paper_fallback", "PAPER", "MAX_FLOW_BAND") == "paper_fallback"
tests\test_economic_gate_paper_fallback.py-89-    assert fn("paper_fallback", "PAPER", "STANDARD") == "strict"
tests\test_economic_gate_paper_fallback.py-90-    assert fn("paper_fallback", "CONTROLLED_LIVE", "STANDARD") == "strict"
tests\test_economic_gate_paper_fallback.py-91-    assert fn("paper_fallback", "OBSERVATION", "STANDARD") == "strict"
tests\test_economic_gate_paper_fallback.py-92-
tests\test_economic_gate_paper_fallback.py-93-
tests\test_economic_gate_paper_fallback.py-94-def test_mode_invalid_value_raises():
tests\test_economic_gate_paper_fallback.py-95-    with pytest.raises(ValueError):
tests\test_economic_gate_paper_fallback.py:96:        config_module._profile_gated_economic_gate_mode(
tests\test_economic_gate_paper_fallback.py-97-            "yolo", "PAPER", "MAX_FLOW_BAND"
tests\test_economic_gate_paper_fallback.py-98-        )
tests\test_economic_gate_paper_fallback.py-99-
tests\test_economic_gate_paper_fallback.py-100-
tests\test_economic_gate_paper_fallback.py-101-def test_env_template_documents_the_mode_knob():
tests\test_economic_gate_paper_fallback.py-102-    template = (ROOT / ".env.example").read_text(encoding="utf-8")
tests\test_economic_gate_paper_fallback.py:103:    assert "MCP_DIRECTIONAL_ECONOMIC_GATE_MODE=strict" in template
tests\test_economic_gate_paper_fallback.py-104-
tests\test_economic_gate_paper_fallback.py-105-
tests\test_economic_gate_paper_fallback.py-106-# ── pure-function semantics ──────────────────────────────────────────────
tests\test_economic_gate_paper_fallback.py-107-
tests\test_economic_gate_paper_fallback.py-108-
tests\test_economic_gate_paper_fallback.py-109-def test_strict_default_preserved_model_missing_blocks():
tests\test_economic_gate_paper_fallback.py-110-    decision = _evaluate()  # no paper_fallback flag -> today's behavior
tests\test_economic_gate_paper_fallback.py-111-    assert decision.allowed is False
tests\test_economic_gate_paper_fallback.py:112:    assert decision.reason == "economic_gate_model_missing"
tests\test_economic_gate_paper_fallback.py-113-
tests\test_economic_gate_paper_fallback.py-114-
tests\test_economic_gate_paper_fallback.py-115-def test_fallback_admits_bracket_that_clears_stressed_costs():
tests\test_economic_gate_paper_fallback.py-116-    decision = _evaluate(paper_fallback=True)
tests\test_economic_gate_paper_fallback.py-117-    assert decision.allowed is True
tests\test_economic_gate_paper_fallback.py:118:    assert decision.reason == "economic_gate_paper_fallback_pass"
tests\test_economic_gate_paper_fallback.py-119-    assert decision.breakeven_p_win is not None
tests\test_economic_gate_paper_fallback.py-120-    assert decision.breakeven_p_win < 1.0
tests\test_economic_gate_paper_fallback.py-121-    # No model probability term was consulted.
tests\test_economic_gate_paper_fallback.py-122-    assert decision.model_version is None
--
tests\test_economic_gate_paper_fallback.py-130-def test_fallback_blocks_bracket_that_cannot_clear_stressed_costs():
tests\test_economic_gate_paper_fallback.py-131-    # TP 2bps < stressed round-trip 31.5bps: even a 100% hit rate loses.
tests\test_economic_gate_paper_fallback.py-132-    decision = _evaluate(paper_fallback=True, target_frac=0.0002)
tests\test_economic_gate_paper_fallback.py-133-    assert decision.allowed is False
tests\test_economic_gate_paper_fallback.py:134:    assert decision.reason == "economic_gate_stressed_breakeven"
tests\test_economic_gate_paper_fallback.py-135-    assert decision.breakeven_p_win >= 1.0
tests\test_economic_gate_paper_fallback.py-136-
tests\test_economic_gate_paper_fallback.py-137-
tests\test_economic_gate_paper_fallback.py-138-def test_fallback_still_fails_closed_on_invalid_inputs():
tests\test_economic_gate_paper_fallback.py-139-    assert (
tests\test_economic_gate_paper_fallback.py-140-        _evaluate(paper_fallback=True, target_frac=0).reason
tests\test_economic_gate_paper_fallback.py:141:        == "economic_gate_geometry_invalid"
tests\test_economic_gate_paper_fallback.py-142-    )
tests\test_economic_gate_paper_fallback.py-143-    assert (
tests\test_economic_gate_paper_fallback.py-144-        _evaluate(paper_fallback=True, exit_fee_frac=None).reason
tests\test_economic_gate_paper_fallback.py:145:        == "economic_gate_cost_invalid"
tests\test_economic_gate_paper_fallback.py-146-    )
tests\test_economic_gate_paper_fallback.py-147-    assert (
tests\test_economic_gate_paper_fallback.py-148-        _evaluate(paper_fallback=True, fee_stress_multiplier=0.99).reason
tests\test_economic_gate_paper_fallback.py:149:        == "economic_gate_config_invalid"
tests\test_economic_gate_paper_fallback.py-150-    )
tests\test_economic_gate_paper_fallback.py-151-
tests\test_economic_gate_paper_fallback.py-152-
tests\test_economic_gate_paper_fallback.py-153-def test_model_present_uses_original_model_path_in_both_modes():
--
tests\test_economic_gate_paper_fallback.py-157-            model_version="promoted-v1",
tests\test_economic_gate_paper_fallback.py-158-            promoted_model_version="promoted-v1",
tests\test_economic_gate_paper_fallback.py-159-            p_win=0.90,
tests\test_economic_gate_paper_fallback.py-160-        )
tests\test_economic_gate_paper_fallback.py:161:        assert decision.reason == "economic_gate_pass"
tests\test_economic_gate_paper_fallback.py-162-        weak = _evaluate(
tests\test_economic_gate_paper_fallback.py-163-            paper_fallback=paper_fallback,
tests\test_economic_gate_paper_fallback.py-164-            model_version="promoted-v1",
tests\test_economic_gate_paper_fallback.py-165-            promoted_model_version="promoted-v1",
tests\test_economic_gate_paper_fallback.py-166-            p_win=0.45,
tests\test_economic_gate_paper_fallback.py-167-        )
tests\test_economic_gate_paper_fallback.py-168-        assert weak.allowed is False
tests\test_economic_gate_paper_fallback.py-169-        assert weak.reason in {
tests\test_economic_gate_paper_fallback.py:170:            "economic_gate_negative_expectancy",
tests\test_economic_gate_paper_fallback.py:171:            "economic_gate_probability_margin_not_met",
tests\test_economic_gate_paper_fallback.py-172-        }
tests\test_economic_gate_paper_fallback.py-173-
tests\test_economic_gate_paper_fallback.py-174-
tests\test_economic_gate_paper_fallback.py-175-def test_fallback_does_not_bypass_promotion_mismatch():
--
tests\test_economic_gate_paper_fallback.py-179-        promoted_model_version="promoted-v1",
tests\test_economic_gate_paper_fallback.py-180-        p_win=0.90,
tests\test_economic_gate_paper_fallback.py-181-    )
tests\test_economic_gate_paper_fallback.py-182-    assert decision.allowed is False
tests\test_economic_gate_paper_fallback.py:183:    assert decision.reason == "economic_gate_model_not_promoted"
tests\test_economic_gate_paper_fallback.py-184-
tests\test_economic_gate_paper_fallback.py-185-
tests\test_economic_gate_paper_fallback.py-186-# ── bot_engine wiring ────────────────────────────────────────────────────
tests\test_economic_gate_paper_fallback.py-187-
--
tests\test_economic_gate_paper_fallback.py-192-        "_validated_promoted_futures_model_version",
tests\test_economic_gate_paper_fallback.py-193-        staticmethod(lambda: None),
tests\test_economic_gate_paper_fallback.py-194-    )
tests\test_economic_gate_paper_fallback.py-195-    monkeypatch.setitem(
tests\test_economic_gate_paper_fallback.py:196:        config_module.MCP_DIRECTIONAL_ECONOMIC_GATE, "mode", "strict"
tests\test_economic_gate_paper_fallback.py-197-    )
tests\test_economic_gate_paper_fallback.py-198-    engine = object.__new__(BotEngine)
tests\test_economic_gate_paper_fallback.py-199-    action = _action()
tests\test_economic_gate_paper_fallback.py-200-
tests\test_economic_gate_paper_fallback.py-201-    assert _gate(engine, action) is False
tests\test_economic_gate_paper_fallback.py:202:    assert action["economic_gate_reason"] == "economic_gate_model_missing"
tests\test_economic_gate_paper_fallback.py-203-
tests\test_economic_gate_paper_fallback.py-204-
tests\test_economic_gate_paper_fallback.py-205-def test_bot_gate_paper_fallback_admits_clearing_geometry(monkeypatch):
tests\test_economic_gate_paper_fallback.py-206-    monkeypatch.setattr(
--
tests\test_economic_gate_paper_fallback.py-208-        "_validated_promoted_futures_model_version",
tests\test_economic_gate_paper_fallback.py-209-        staticmethod(lambda: None),
tests\test_economic_gate_paper_fallback.py-210-    )
tests\test_economic_gate_paper_fallback.py-211-    monkeypatch.setitem(
tests\test_economic_gate_paper_fallback.py:212:        config_module.MCP_DIRECTIONAL_ECONOMIC_GATE, "mode", "paper_fallback"
tests\test_economic_gate_paper_fallback.py-213-    )
tests\test_economic_gate_paper_fallback.py-214-    engine = object.__new__(BotEngine)
tests\test_economic_gate_paper_fallback.py-215-    action = _action()
tests\test_economic_gate_paper_fallback.py-216-
tests\test_economic_gate_paper_fallback.py-217-    assert _gate(engine, action, sl_pct=2.0, tp_pct=1.0) is True
tests\test_economic_gate_paper_fallback.py:218:    audit = action["economic_entry_gate"]
tests\test_economic_gate_paper_fallback.py:219:    assert action["economic_gate_reason"] == "economic_gate_paper_fallback_pass"
tests\test_economic_gate_paper_fallback.py-220-    assert audit["breakeven_p_win"] < 1.0
tests\test_economic_gate_paper_fallback.py-221-    assert (
tests\test_economic_gate_paper_fallback.py-222-        action["execution_snapshot"]["snapshot"]["context"][
tests\test_economic_gate_paper_fallback.py:223:            "economic_entry_gate"
tests\test_economic_gate_paper_fallback.py-224-        ]
tests\test_economic_gate_paper_fallback.py-225-        == audit
tests\test_economic_gate_paper_fallback.py-226-    )
tests\test_economic_gate_paper_fallback.py-227-
--
tests\test_economic_gate_paper_fallback.py-232-        "_validated_promoted_futures_model_version",
tests\test_economic_gate_paper_fallback.py-233-        staticmethod(lambda: None),
tests\test_economic_gate_paper_fallback.py-234-    )
tests\test_economic_gate_paper_fallback.py-235-    monkeypatch.setitem(
tests\test_economic_gate_paper_fallback.py:236:        config_module.MCP_DIRECTIONAL_ECONOMIC_GATE, "mode", "paper_fallback"
tests\test_economic_gate_paper_fallback.py-237-    )
tests\test_economic_gate_paper_fallback.py-238-    engine = object.__new__(BotEngine)
tests\test_economic_gate_paper_fallback.py-239-    action = _action()
tests\test_economic_gate_paper_fallback.py-240-
tests\test_economic_gate_paper_fallback.py-241-    # TP 0.02% cannot clear the stressed round-trip cost.
tests\test_economic_gate_paper_fallback.py-242-    assert _gate(engine, action, sl_pct=2.0, tp_pct=0.02) is False
tests\test_economic_gate_paper_fallback.py:243:    assert action["economic_gate_reason"] == "economic_gate_stressed_breakeven"
tests\test_economic_gate_paper_fallback.py-244-
tests\test_economic_gate_paper_fallback.py-245-
tests\test_economic_gate_paper_fallback.py-246-def test_bot_gate_paper_fallback_inert_outside_paper_mode(monkeypatch):
tests\test_economic_gate_paper_fallback.py-247-    monkeypatch.setattr(
--
tests\test_economic_gate_paper_fallback.py-249-        "_validated_promoted_futures_model_version",
tests\test_economic_gate_paper_fallback.py-250-        staticmethod(lambda: None),
tests\test_economic_gate_paper_fallback.py-251-    )
tests\test_economic_gate_paper_fallback.py-252-    monkeypatch.setitem(
tests\test_economic_gate_paper_fallback.py:253:        config_module.MCP_DIRECTIONAL_ECONOMIC_GATE, "mode", "paper_fallback"
tests\test_economic_gate_paper_fallback.py-254-    )
tests\test_economic_gate_paper_fallback.py-255-    engine = object.__new__(BotEngine)
tests\test_economic_gate_paper_fallback.py-256-    action = _action()
tests\test_economic_gate_paper_fallback.py-257-
tests\test_economic_gate_paper_fallback.py-258-    # CONTROLLED_LIVE never reaches this PAPER-lane gate at all, and no
tests\test_economic_gate_paper_fallback.py-259-    # fallback admission audit may be produced for it.
tests\test_economic_gate_paper_fallback.py-260-    assert _gate(engine, action, operating_mode="CONTROLLED_LIVE") is True
tests\test_economic_gate_paper_fallback.py:261:    assert "economic_entry_gate" not in action
tests\test_economic_gate_paper_fallback.py-262-
tests\test_economic_gate_paper_fallback.py-263-
tests\test_economic_gate_paper_fallback.py-264-def test_bot_gate_model_present_uses_model_path_in_fallback_mode(monkeypatch):
tests\test_economic_gate_paper_fallback.py-265-    monkeypatch.setattr(
--
tests\test_economic_gate_paper_fallback.py-267-        "_validated_promoted_futures_model_version",
tests\test_economic_gate_paper_fallback.py-268-        staticmethod(lambda: "promoted-v1"),
tests\test_economic_gate_paper_fallback.py-269-    )
tests\test_economic_gate_paper_fallback.py-270-    monkeypatch.setitem(
tests\test_economic_gate_paper_fallback.py:271:        config_module.MCP_DIRECTIONAL_ECONOMIC_GATE, "mode", "paper_fallback"
tests\test_economic_gate_paper_fallback.py-272-    )
tests\test_economic_gate_paper_fallback.py-273-    engine = object.__new__(BotEngine)
tests\test_economic_gate_paper_fallback.py-274-    action = _action(model_version="promoted-v1", p_win_ensemble=0.90)
tests\test_economic_gate_paper_fallback.py-275-
tests\test_economic_gate_paper_fallback.py-276-    assert _gate(engine, action, sl_pct=1.0, tp_pct=2.0) is True
tests\test_economic_gate_paper_fallback.py:277:    assert action["economic_gate_reason"] == "economic_gate_pass"
--
scripts\machine_strategy_replay.py-692-        }
scripts\machine_strategy_replay.py-693-        for t in _chronological_trades(trades)
scripts\machine_strategy_replay.py-694-    ]
scripts\machine_strategy_replay.py-695-    report = evaluate_records(records, thresholds=thresholds, name=label)
scripts\machine_strategy_replay.py:696:    blockers = [str(reason) for reason in (evidence_blockers or []) if str(reason)]
scripts\machine_strategy_replay.py-697-    if blockers:
scripts\machine_strategy_replay.py-698-        report["ready"] = False
scripts\machine_strategy_replay.py-699-        report["verdict"] = "PAPER_ONLY"
scripts\machine_strategy_replay.py-700-        report["evidence_eligible"] = False
--
scripts\machine_strategy_replay.py-765-            selection_trades.extend(selected)
scripts\machine_strategy_replay.py-766-            validation_trades.extend(validated)
scripts\machine_strategy_replay.py-767-        selection_trades = _chronological_trades(selection_trades)
scripts\machine_strategy_replay.py-768-        validation_trades = _chronological_trades(validation_trades)
scripts\machine_strategy_replay.py:769:        evidence_blockers = list(dataset_audit.get("reasons") or [])
scripts\machine_strategy_replay.py-770-        evidence_blockers.extend(
scripts\machine_strategy_replay.py-771-            f"{protocol['symbol']}: {protocol.get('reason')}"
scripts\machine_strategy_replay.py-772-            for protocol in split_protocols
scripts\machine_strategy_replay.py-773-            if not protocol.get("eligible")
--
scripts\review_universe_flow_loosen.py-94-                    if reason.startswith("universe_filter"):
scripts\review_universe_flow_loosen.py-95-                        universe_rejects += 1
scripts\review_universe_flow_loosen.py-96-                    elif reason.startswith("band_regime_filter"):
scripts\review_universe_flow_loosen.py-97-                        band_rejects += 1
scripts\review_universe_flow_loosen.py:98:                    elif reason.startswith("economic_gate"):
scripts\review_universe_flow_loosen.py-99-                        econ_rejects += 1
scripts\review_universe_flow_loosen.py-100-                    else:
scripts\review_universe_flow_loosen.py-101-                        other_rejects += 1
scripts\review_universe_flow_loosen.py-102-                elif typ == "portfolio":
--
tests\test_economic_entry_gate.py-7-import pytest
tests\test_economic_entry_gate.py-8-
tests\test_economic_entry_gate.py-9-import config as config_module
tests\test_economic_entry_gate.py-10-from core.bot_engine import BotEngine, _is_mcp_directional_paper_futures
tests\test_economic_entry_gate.py:11:from core.economic_entry_gate import evaluate_directional_entry
tests\test_economic_entry_gate.py-12-
tests\test_economic_entry_gate.py-13-ROOT = Path(__file__).resolve().parents[1]
tests\test_economic_entry_gate.py-14-
tests\test_economic_entry_gate.py-15-
--
tests\test_economic_entry_gate.py-65-        decision.breakeven_p_win + 0.03
tests\test_economic_entry_gate.py-66-    )
tests\test_economic_entry_gate.py-67-    assert decision.stressed_expectancy_frac == pytest.approx(0.00485)
tests\test_economic_entry_gate.py-68-    assert decision.allowed is True
tests\test_economic_entry_gate.py:69:    assert decision.reason == "economic_gate_pass"
tests\test_economic_entry_gate.py-70-    assert decision.to_dict()["model_version"] == "promoted-v1"
tests\test_economic_entry_gate.py-71-
tests\test_economic_entry_gate.py-72-
tests\test_economic_entry_gate.py-73-@pytest.mark.parametrize("model_version", [None, "", "fallback", "rule_only"])
tests\test_economic_entry_gate.py-74-def test_missing_or_fallback_model_fails_closed(model_version):
tests\test_economic_entry_gate.py-75-    decision = _evaluate(model_version=model_version)
tests\test_economic_entry_gate.py-76-
tests\test_economic_entry_gate.py-77-    assert decision.allowed is False
tests\test_economic_entry_gate.py:78:    assert decision.reason == "economic_gate_model_missing"
tests\test_economic_entry_gate.py-79-
tests\test_economic_entry_gate.py-80-
tests\test_economic_entry_gate.py-81-def test_arbitrary_or_stale_model_version_cannot_impersonate_promotion():
tests\test_economic_entry_gate.py-82-    decision = _evaluate(model_version="stale-v0")
tests\test_economic_entry_gate.py-83-
tests\test_economic_entry_gate.py-84-    assert decision.allowed is False
tests\test_economic_entry_gate.py:85:    assert decision.reason == "economic_gate_model_not_promoted"
tests\test_economic_entry_gate.py-86-    assert decision.promoted_model_version == "promoted-v1"
tests\test_economic_entry_gate.py-87-
tests\test_economic_entry_gate.py-88-
tests\test_economic_entry_gate.py-89-@pytest.mark.parametrize("p_win", [None, float("nan"), float("inf"), -0.1, 1.1])
tests\test_economic_entry_gate.py-90-def test_nonfinite_or_out_of_range_probability_fails_closed(p_win):
tests\test_economic_entry_gate.py-91-    decision = _evaluate(p_win=p_win)
tests\test_economic_entry_gate.py-92-
tests\test_economic_entry_gate.py-93-    assert decision.allowed is False
tests\test_economic_entry_gate.py:94:    assert decision.reason == "economic_gate_p_win_invalid"
tests\test_economic_entry_gate.py-95-
tests\test_economic_entry_gate.py-96-
tests\test_economic_entry_gate.py-97-def test_nonpositive_after_cost_expectancy_has_explicit_reason():
tests\test_economic_entry_gate.py-98-    decision = _evaluate(p_win=0.30)
tests\test_economic_entry_gate.py-99-
tests\test_economic_entry_gate.py-100-    assert decision.stressed_expectancy_frac < 0.0
tests\test_economic_entry_gate.py-101-    assert decision.allowed is False
tests\test_economic_entry_gate.py:102:    assert decision.reason == "economic_gate_negative_expectancy"
tests\test_economic_entry_gate.py-103-
tests\test_economic_entry_gate.py-104-
tests\test_economic_entry_gate.py-105-def test_positive_ev_still_needs_predeclared_probability_margin():
tests\test_economic_entry_gate.py-106-    decision = _evaluate(p_win=0.45)
tests\test_economic_entry_gate.py-107-
tests\test_economic_entry_gate.py-108-    assert decision.stressed_expectancy_frac > 0.0
tests\test_economic_entry_gate.py-109-    assert decision.p_win < decision.required_p_win
tests\test_economic_entry_gate.py-110-    assert decision.allowed is False
tests\test_economic_entry_gate.py:111:    assert decision.reason == "economic_gate_probability_margin_not_met"
tests\test_economic_entry_gate.py-112-
tests\test_economic_entry_gate.py-113-
tests\test_economic_entry_gate.py-114-def test_invalid_geometry_or_nonconservative_stress_fails_closed():
tests\test_economic_entry_gate.py:115:    assert _evaluate(target_frac=0).reason == "economic_gate_geometry_invalid"
tests\test_economic_entry_gate.py-116-    assert (
tests\test_economic_entry_gate.py-117-        _evaluate(fee_stress_multiplier=0.99).reason
tests\test_economic_entry_gate.py:118:        == "economic_gate_config_invalid"
tests\test_economic_entry_gate.py-119-    )
tests\test_economic_entry_gate.py-120-
tests\test_economic_entry_gate.py-121-
tests\test_economic_entry_gate.py-122-@pytest.mark.parametrize("strategy_id", [
--
tests\test_economic_entry_gate.py-156-    )
tests\test_economic_entry_gate.py-157-    engine = object.__new__(BotEngine)
tests\test_economic_entry_gate.py-158-    action = _action()
tests\test_economic_entry_gate.py-159-
tests\test_economic_entry_gate.py:160:    allowed = engine._apply_mcp_directional_economic_gate(
tests\test_economic_entry_gate.py-161-        action,
tests\test_economic_entry_gate.py-162-        strategy_id="mcp_registry",
tests\test_economic_entry_gate.py-163-        operating_mode="PAPER",
tests\test_economic_entry_gate.py-164-        market_type="futures",
--
tests\test_economic_entry_gate.py-168-        entry_quote_usdt=10_000.0,
tests\test_economic_entry_gate.py-169-    )
tests\test_economic_entry_gate.py-170-
tests\test_economic_entry_gate.py-171-    assert allowed is True
tests\test_economic_entry_gate.py:172:    audit = action["economic_entry_gate"]
tests\test_economic_entry_gate.py-173-    assert audit["entry_fee_frac"] == pytest.approx(0.0005)
tests\test_economic_entry_gate.py-174-    assert audit["entry_slippage_frac"] == pytest.approx(0.0002)
tests\test_economic_entry_gate.py-175-    assert audit["stressed_expectancy_frac"] > 0.0
tests\test_economic_entry_gate.py:176:    assert action["economic_gate_reason"] == "economic_gate_pass"
tests\test_economic_entry_gate.py-177-    assert (
tests\test_economic_entry_gate.py-178-        action["execution_snapshot"]["snapshot"]["context"][
tests\test_economic_entry_gate.py:179:            "economic_entry_gate"
tests\test_economic_entry_gate.py-180-        ]
tests\test_economic_entry_gate.py-181-        == audit
tests\test_economic_entry_gate.py-182-    )
tests\test_economic_entry_gate.py-183-
--
tests\test_economic_entry_gate.py-190-    )
tests\test_economic_entry_gate.py-191-    # Strict-mode semantics under test; pin the mode so the live .env
tests\test_economic_entry_gate.py-192-    # (paper_fallback under MAX_FLOW_BAND) cannot invert the assertion.
tests\test_economic_entry_gate.py-193-    monkeypatch.setitem(
tests\test_economic_entry_gate.py:194:        config_module.MCP_DIRECTIONAL_ECONOMIC_GATE, "mode", "strict"
tests\test_economic_entry_gate.py-195-    )
tests\test_economic_entry_gate.py-196-    engine = object.__new__(BotEngine)
tests\test_economic_entry_gate.py-197-    action = _action()
tests\test_economic_entry_gate.py-198-
tests\test_economic_entry_gate.py:199:    allowed = engine._apply_mcp_directional_economic_gate(
tests\test_economic_entry_gate.py-200-        action,
tests\test_economic_entry_gate.py-201-        strategy_id="MCP_DIRECTIONAL_PAPER",
tests\test_economic_entry_gate.py-202-        operating_mode="PAPER",
tests\test_economic_entry_gate.py-203-        market_type="futures",
--
tests\test_economic_entry_gate.py-207-        entry_quote_usdt=10_000.0,
tests\test_economic_entry_gate.py-208-    )
tests\test_economic_entry_gate.py-209-
tests\test_economic_entry_gate.py-210-    assert allowed is False
tests\test_economic_entry_gate.py:211:    assert action["economic_gate_reason"] == (
tests\test_economic_entry_gate.py:212:        "economic_gate_promoted_model_unavailable"
tests\test_economic_entry_gate.py-213-    )
tests\test_economic_entry_gate.py-214-
tests\test_economic_entry_gate.py-215-
tests\test_economic_entry_gate.py-216-def test_bot_gate_is_a_noop_for_tsmom_and_carry(monkeypatch):
--
tests\test_economic_entry_gate.py-221-        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("must not load"))),
tests\test_economic_entry_gate.py-222-    )
tests\test_economic_entry_gate.py-223-    for strategy_id, is_tsmom in (("mcp_registry", True), ("F1", False)):
tests\test_economic_entry_gate.py-224-        action = _action(model_version=None, p_win_ensemble=math.nan)
tests\test_economic_entry_gate.py:225:        assert engine._apply_mcp_directional_economic_gate(
tests\test_economic_entry_gate.py-226-            action,
tests\test_economic_entry_gate.py-227-            strategy_id=strategy_id,
tests\test_economic_entry_gate.py-228-            operating_mode="PAPER",
tests\test_economic_entry_gate.py-229-            market_type="futures",
--
tests\test_economic_entry_gate.py-232-            tp_pct=2.0,
tests\test_economic_entry_gate.py-233-            entry_quote_usdt=10_000.0,
tests\test_economic_entry_gate.py-234-            is_tsmom=is_tsmom,
tests\test_economic_entry_gate.py-235-        )
tests\test_economic_entry_gate.py:236:        assert "economic_entry_gate" not in action
tests\test_economic_entry_gate.py-237-
tests\test_economic_entry_gate.py-238-
tests\test_economic_entry_gate.py-239-def test_execute_open_gate_is_after_final_geometry_and_book_before_order():
tests\test_economic_entry_gate.py-240-    source = inspect.getsource(BotEngine._execute_open)
tests\test_economic_entry_gate.py-241-
tests\test_economic_entry_gate.py:242:    gate = source.index("self._apply_mcp_directional_economic_gate")
tests\test_economic_entry_gate.py-243-    assert source.index("tp_pct = _apply_accuracy_target") < gate
tests\test_economic_entry_gate.py-244-    assert source.index("fetch_and_validate_execution_book") < gate
tests\test_economic_entry_gate.py-245-    assert gate < source.index("self.order_mgr.open_position")
tests\test_economic_entry_gate.py-246-    assert "entry_quote_usdt=float(_book_decision.quote_cost)" in source
tests\test_economic_entry_gate.py-247-
tests\test_economic_entry_gate.py-248-
tests\test_economic_entry_gate.py-249-def test_config_and_template_predeclare_non_geometry_stress_controls():
tests\test_economic_entry_gate.py:250:    from config import MCP_DIRECTIONAL_ECONOMIC_GATE
tests\test_economic_entry_gate.py-251-
tests\test_economic_entry_gate.py:252:    gate = dict(MCP_DIRECTIONAL_ECONOMIC_GATE)
tests\test_economic_entry_gate.py-253-    # 2026-07-21: env- and profile-resolved; semantics covered in
tests\test_economic_entry_gate.py:254:    # tests/test_economic_gate_paper_fallback.py.
tests\test_economic_entry_gate.py-255-    assert gate.pop("mode") in {"strict", "paper_fallback"}
tests\test_economic_entry_gate.py-256-    assert gate == {
tests\test_economic_entry_gate.py-257-        "probability_margin": 0.03,
tests\test_economic_entry_gate.py-258-        "fee_stress_multiplier": 1.5,
--
tests\test_economic_entry_gate.py-261-    template = (ROOT / ".env.example").read_text(encoding="utf-8")
tests\test_economic_entry_gate.py-262-    assert "MCP_DIRECTIONAL_ECONOMIC_PROBABILITY_MARGIN=0.03" in template
tests\test_economic_entry_gate.py-263-    assert "MCP_DIRECTIONAL_ECONOMIC_FEE_STRESS_MULT=1.5" in template
tests\test_economic_entry_gate.py-264-    assert "MCP_DIRECTIONAL_ECONOMIC_SLIPPAGE_STRESS_MULT=2.0" in template
tests\test_economic_entry_gate.py:265:    assert "MCP_DIRECTIONAL_ECONOMIC_GATE_MODE=strict" in template
--
tests\test_f7_funnel_reporting.py-1-"""F7 (2026-07-20 deep audit) — funnel reporting polish.
tests\test_f7_funnel_reporting.py-2-
tests\test_f7_funnel_reporting.py-3-a) render_journal printed ``eta=Noned`` when a funnel lane's eta_days was
tests\test_f7_funnel_reporting.py-4-   None (f-string interpolation of None + literal ``d``).
tests\test_f7_funnel_reporting.py:5:b) f1_lane_state silently capped entries_48h at the last 2000 log lines, so a
tests\test_f7_funnel_reporting.py-6-   busy 48h window under-reported gate evaluations with no indication.
tests\test_f7_funnel_reporting.py-7-"""
tests\test_f7_funnel_reporting.py-8-from __future__ import annotations
tests\test_f7_funnel_reporting.py-9-
--
core\kelly_sizer.py-143-
core\kelly_sizer.py-144-        return round(position_pct, 4)
core\kelly_sizer.py-145-
core\kelly_sizer.py-146-    def should_block_trade(self, strategy: str, mcp_approved: bool = False) -> tuple:
core\kelly_sizer.py:147:        """Return (block: bool, reason: str) if trade is too risky.
core\kelly_sizer.py-148-
core\kelly_sizer.py-149-        2026-04-29: SHORT-CIRCUITED to always return (False, "") per user
core\kelly_sizer.py-150-        directive "clear kelly_block". The bot's claude_portfolio kelly_stats
core\kelly_sizer.py-151-        currently show 82W/104L (44% WR) with avg_loss > avg_win → kelly
--
core\economic_entry_gate.py-103-    target = _finite_number(target_frac)
core\economic_entry_gate.py-104-    if stop is None or target is None or stop <= 0.0 or target <= 0.0:
core\economic_entry_gate.py-105-        return EconomicEntryDecision(
core\economic_entry_gate.py-106-            False,
core\economic_entry_gate.py:107:            "economic_gate_geometry_invalid",
core\economic_entry_gate.py-108-            stop_frac=stop,
core\economic_entry_gate.py-109-            target_frac=target,
core\economic_entry_gate.py-110-            **base,
core\economic_entry_gate.py-111-        )
--
core\economic_entry_gate.py-121-    costs = (entry_fee, entry_slip, exit_fee, exit_slip, exit_floor)
core\economic_entry_gate.py-122-    if any(value is None or value < 0.0 for value in costs):
core\economic_entry_gate.py-123-        return EconomicEntryDecision(
core\economic_entry_gate.py-124-            False,
core\economic_entry_gate.py:125:            "economic_gate_cost_invalid",
core\economic_entry_gate.py-126-            stop_frac=stop,
core\economic_entry_gate.py-127-            target_frac=target,
core\economic_entry_gate.py-128-            **base,
core\economic_entry_gate.py-129-        )
--
core\economic_entry_gate.py-136-        or not 0.0 <= margin <= 1.0
core\economic_entry_gate.py-137-    ):
core\economic_entry_gate.py-138-        return EconomicEntryDecision(
core\economic_entry_gate.py-139-            False,
core\economic_entry_gate.py:140:            "economic_gate_config_invalid",
core\economic_entry_gate.py-141-            stop_frac=stop,
core\economic_entry_gate.py-142-            target_frac=target,
core\economic_entry_gate.py-143-            fee_stress_multiplier=fee_stress,
core\economic_entry_gate.py-144-            slippage_stress_multiplier=slip_stress,
--
core\economic_entry_gate.py-167-        "probability_margin": margin,
core\economic_entry_gate.py-168-    }
core\economic_entry_gate.py-169-    if breakeven >= 1.0:
core\economic_entry_gate.py-170-        return EconomicEntryDecision(
core\economic_entry_gate.py:171:            False, "economic_gate_stressed_breakeven", **audit
core\economic_entry_gate.py-172-        )
core\economic_entry_gate.py-173-    return EconomicEntryDecision(
core\economic_entry_gate.py:174:        True, "economic_gate_paper_fallback_pass", **audit
core\economic_entry_gate.py-175-    )
core\economic_entry_gate.py-176-
core\economic_entry_gate.py-177-
core\economic_entry_gate.py-178-def evaluate_directional_entry(
--
core\economic_entry_gate.py-226-            slippage_stress_multiplier=slippage_stress_multiplier,
core\economic_entry_gate.py-227-            probability_margin=probability_margin,
core\economic_entry_gate.py-228-        )
core\economic_entry_gate.py-229-    if version is None:
core\economic_entry_gate.py:230:        return EconomicEntryDecision(False, "economic_gate_model_missing", **base)
core\economic_entry_gate.py-231-    if promoted is None:
core\economic_entry_gate.py-232-        return EconomicEntryDecision(
core\economic_entry_gate.py:233:            False, "economic_gate_promoted_model_unavailable", **base
core\economic_entry_gate.py-234-        )
core\economic_entry_gate.py-235-    if version != promoted:
core\economic_entry_gate.py:236:        return EconomicEntryDecision(False, "economic_gate_model_not_promoted", **base)
core\economic_entry_gate.py-237-
core\economic_entry_gate.py-238-    probability = _finite_number(p_win)
core\economic_entry_gate.py-239-    if probability is None or not 0.0 <= probability <= 1.0:
core\economic_entry_gate.py-240-        return EconomicEntryDecision(
core\economic_entry_gate.py:241:            False, "economic_gate_p_win_invalid", p_win=probability, **base
core\economic_entry_gate.py-242-        )
core\economic_entry_gate.py-243-
core\economic_entry_gate.py-244-    stop = _finite_number(stop_frac)
core\economic_entry_gate.py-245-    target = _finite_number(target_frac)
core\economic_entry_gate.py-246-    if stop is None or target is None or stop <= 0.0 or target <= 0.0:
core\economic_entry_gate.py-247-        return EconomicEntryDecision(
core\economic_entry_gate.py-248-            False,
core\economic_entry_gate.py:249:            "economic_gate_geometry_invalid",
core\economic_entry_gate.py-250-            p_win=probability,
core\economic_entry_gate.py-251-            stop_frac=stop,
core\economic_entry_gate.py-252-            target_frac=target,
core\economic_entry_gate.py-253-            **base,
--
core\economic_entry_gate.py-264-    costs = (entry_fee, entry_slip, exit_fee, exit_slip, exit_floor)
core\economic_entry_gate.py-265-    if any(value is None or value < 0.0 for value in costs):
core\economic_entry_gate.py-266-        return EconomicEntryDecision(
core\economic_entry_gate.py-267-            False,
core\economic_entry_gate.py:268:            "economic_gate_cost_invalid",
core\economic_entry_gate.py-269-            p_win=probability,
core\economic_entry_gate.py-270-            stop_frac=stop,
core\economic_entry_gate.py-271-            target_frac=target,
core\economic_entry_gate.py-272-            **base,
--
core\economic_entry_gate.py-280-        or not 0.0 <= margin <= 1.0
core\economic_entry_gate.py-281-    ):
core\economic_entry_gate.py-282-        return EconomicEntryDecision(
core\economic_entry_gate.py-283-            False,
core\economic_entry_gate.py:284:            "economic_gate_config_invalid",
core\economic_entry_gate.py-285-            p_win=probability,
core\economic_entry_gate.py-286-            stop_frac=stop,
core\economic_entry_gate.py-287-            target_frac=target,
core\economic_entry_gate.py-288-            fee_stress_multiplier=fee_stress,
--
core\economic_entry_gate.py-317-        "stressed_expectancy_frac": expectancy,
core\economic_entry_gate.py-318-    }
core\economic_entry_gate.py-319-    if expectancy <= 0.0:
core\economic_entry_gate.py-320-        return EconomicEntryDecision(
core\economic_entry_gate.py:321:            False, "economic_gate_negative_expectancy", **audit
core\economic_entry_gate.py-322-        )
core\economic_entry_gate.py-323-    if probability < required:
core\economic_entry_gate.py-324-        return EconomicEntryDecision(
core\economic_entry_gate.py:325:            False, "economic_gate_probability_margin_not_met", **audit
core\economic_entry_gate.py-326-        )
core\economic_entry_gate.py:327:    return EconomicEntryDecision(True, "economic_gate_pass", **audit)
core\economic_entry_gate.py-328-
core\economic_entry_gate.py-329-
core\economic_entry_gate.py-330-__all__ = ["EconomicEntryDecision", "evaluate_directional_entry"]
--
tests\test_gate_status.py-1-"""scripts/gate_status.py — promotion-readiness status reporter (Codex port).
tests\test_gate_status.py-2-
tests\test_gate_status.py-3-REPORTING ONLY. The script reads warehouse/shadow tables and existing gate
tests\test_gate_status.py:4:constants; it must never become a second gate authority:
tests\test_gate_status.py-5-  * frozen thresholds (DSR/PBO/OOS-WR/AUC) are IMPORTED from
tests\test_gate_status.py-6-    core/promotion_gate.py — nothing re-declared;
tests\test_gate_status.py-7-  * the >=30 resolved floor is IMPORTED from scripts/report_goal_progress.py;
tests\test_gate_status.py-8-  * owner sign-off is always PENDING — the reporter can never grant it;
--
tests\test_gate_status.py-120-            if c["scope"] == scope and (name is None or c["name"] == name)]
tests\test_gate_status.py-121-    return hits[0] if name is not None else hits
tests\test_gate_status.py-122-
tests\test_gate_status.py-123-
tests\test_gate_status.py:124:# ── no second gate authority ─────────────────────────────────────────────
tests\test_gate_status.py-125-def test_thresholds_imported_not_redeclared():
tests\test_gate_status.py-126-    import core.promotion_gate as pg
tests\test_gate_status.py-127-
tests\test_gate_status.py-128-    gs = _gs()
--
core\data_sources\cross_venue.py-786-) -> dict[str, Any]:
core\data_sources\cross_venue.py-787-    """Walk ``book`` levels to fill ``qty`` of ``side`` (buy=>take asks, sell=>take bids).
core\data_sources\cross_venue.py-788-
core\data_sources\cross_venue.py-789-    Rejects the fill if the book feed is older than ``max_age_sec`` (stale-feed
core\data_sources\cross_venue.py:790:    gate). Returns ``{filled_qty, avg_px, reason}`` — partial fills carry the
core\data_sources\cross_venue.py-791-    available quantity and a ``partial`` reason.
core\data_sources\cross_venue.py-792-    """
core\data_sources\cross_venue.py-793-    age = float(now) - float(book.ts)
core\data_sources\cross_venue.py-794-    if age > float(max_age_sec):
--
tests\test_kelly_block_cleared.py-39-        },
tests\test_kelly_block_cleared.py-40-    }))
tests\test_kelly_block_cleared.py-41-    from core.kelly_sizer import KellySizer
tests\test_kelly_block_cleared.py-42-    ks = KellySizer()
tests\test_kelly_block_cleared.py:43:    blocked, reason = ks.should_block_trade("claude_portfolio", mcp_approved=True)
tests\test_kelly_block_cleared.py-44-    assert blocked is False, (
tests\test_kelly_block_cleared.py-45-        "Kelly block must be cleared per user directive — even at "
tests\test_kelly_block_cleared.py-46-        "kelly=-56% the trade must proceed.")
tests\test_kelly_block_cleared.py-47-    assert reason == ""
--
tests\test_kelly_block_cleared.py-56-        },
tests\test_kelly_block_cleared.py-57-    }))
tests\test_kelly_block_cleared.py-58-    from core.kelly_sizer import KellySizer
tests\test_kelly_block_cleared.py-59-    ks = KellySizer()
tests\test_kelly_block_cleared.py:60:    blocked, reason = ks.should_block_trade("test_strat", mcp_approved=False)
tests\test_kelly_block_cleared.py-61-    assert blocked is False
tests\test_kelly_block_cleared.py-62-    assert reason == ""
tests\test_kelly_block_cleared.py-63-
tests\test_kelly_block_cleared.py-64-
--
tests\test_kelly_block_cleared.py-71-        },
tests\test_kelly_block_cleared.py-72-    }))
tests\test_kelly_block_cleared.py-73-    from core.kelly_sizer import KellySizer
tests\test_kelly_block_cleared.py-74-    ks = KellySizer()
tests\test_kelly_block_cleared.py:75:    blocked, reason = ks.should_block_trade("test_strat", mcp_approved=False)
tests\test_kelly_block_cleared.py-76-    assert blocked is False
tests\test_kelly_block_cleared.py-77-
tests\test_kelly_block_cleared.py-78-
tests\test_kelly_block_cleared.py-79-def test_kelly_block_disabled_with_no_stats():
tests\test_kelly_block_cleared.py-80-    """No kelly_stats.json: still no block (was already this way)."""
tests\test_kelly_block_cleared.py-81-    from core.kelly_sizer import KellySizer
tests\test_kelly_block_cleared.py-82-    ks = KellySizer()
tests\test_kelly_block_cleared.py:83:    blocked, reason = ks.should_block_trade("anything", mcp_approved=True)
tests\test_kelly_block_cleared.py-84-    assert blocked is False
tests\test_kelly_block_cleared.py-85-
tests\test_kelly_block_cleared.py-86-
tests\test_kelly_block_cleared.py-87-def test_kelly_optimal_position_pct_still_works():
--
core\readiness.py-373-    reasons = {
core\readiness.py-374-        "SYSTEM_HEALTHY": health_reason,
core\readiness.py-375-        "PAPER_READY": paper_reason,
core\readiness.py-376-        "EDGE_UNPROVEN": edge_reason,
core\readiness.py:377:        "LIVE_BLOCKED": live_reason,
core\readiness.py-378-    }
core\readiness.py-379-    return ReadinessReport(
core\readiness.py-380-        system_healthy=healthy,
core\readiness.py-381-        paper_ready=paper_ready,
--
core\live_gate.py-248-    reasons = "; ".join((report.get("reasons") or [])[:5]) or "not promotion-ready"
core\live_gate.py-249-    name = report.get("name", "strategy")
core\live_gate.py-250-    raise SystemExit(
core\live_gate.py-251-        "[LiveGate] REFUSING TO START in CONTROLLED_LIVE: "
core\live_gate.py:252:        f"{name} evidence gate failed: {reasons}"
core\live_gate.py-253-    )
core\live_gate.py-254-
core\live_gate.py-255-
core\live_gate.py-256-def enforce_model_gate_readiness(
--
tests\test_kill_switch.py-122-    src = (ROOT / "core" / "bot_engine.py").read_text(encoding="utf-8")
tests\test_kill_switch.py-123-    idx = src.index("def _execute_open")
tests\test_kill_switch.py-124-    block = src[idx : idx + 4000]
tests\test_kill_switch.py-125-    assert "entries_halted" in block, "_execute_open must consult the kill switch"
tests\test_kill_switch.py:126:    assert "kill_switch_active" in block, "reject_reason must be recorded"
tests\test_kill_switch.py-127-
tests\test_kill_switch.py-128-
tests\test_kill_switch.py-129-def test_kill_switch_check_precedes_order_placement():
tests\test_kill_switch.py-130-    """The switch must fire before any sizing/order work — near the top of
--
core\mcp_strategy_scorer.py-83-        return routes, ""
core\mcp_strategy_scorer.py-84-
core\mcp_strategy_scorer.py-85-    def _filter_executable_opens(self, actions: list) -> list:
core\mcp_strategy_scorer.py-86-        """Keep research proposals broad, but return only approved OPEN routes."""
core\mcp_strategy_scorer.py:87:        routes, gate_reason = self._execution_routes()
core\mcp_strategy_scorer.py-88-        executable: list = []
core\mcp_strategy_scorer.py-89-        blocked_routes: list = []
core\mcp_strategy_scorer.py-90-        futures_types = {"futures", "future", "perpetual", "perp", "swap"}
core\mcp_strategy_scorer.py-91-        for action in actions:
--
core\mcp_strategy_scorer.py-96-            base = symbol.split("/", 1)[0].split(":", 1)[0]
core\mcp_strategy_scorer.py-97-            venue = str(action.get("exchange") or "").strip().lower()
core\mcp_strategy_scorer.py-98-            market_type = str(action.get("market_type") or "").strip().lower()
core\mcp_strategy_scorer.py-99-            allowed_venues = routes.get(base, frozenset())
core\mcp_strategy_scorer.py:100:            if (not gate_reason and market_type in futures_types
core\mcp_strategy_scorer.py-101-                    and venue in allowed_venues):
core\mcp_strategy_scorer.py-102-                executable.append(action)
core\mcp_strategy_scorer.py-103-            else:
core\mcp_strategy_scorer.py-104-                blocked_routes.append(f"{base or '?'}@{venue or '?'}")
core\mcp_strategy_scorer.py-105-        self._last_blocked_open_routes = blocked_routes
core\mcp_strategy_scorer.py-106-        if blocked_routes:
core\mcp_strategy_scorer.py:107:            reason = gate_reason or "strategy_spec_route_not_approved"
core\mcp_strategy_scorer.py-108-            detail = ", ".join(blocked_routes[:8])
core\mcp_strategy_scorer.py-109-            if len(blocked_routes) > 8:
core\mcp_strategy_scorer.py-110-                detail += f" +{len(blocked_routes) - 8} more"
core\mcp_strategy_scorer.py-111-            logger.warning(
--
tests\test_launcher_safety.py-314-
tests\test_launcher_safety.py-315-    (tmp_path / ".env").write_text(
tests\test_launcher_safety.py-316-        "OPERATING_MODE=PAPER\n"
tests\test_launcher_safety.py-317-        "PAPER_TRADING_PROFILE=MAX_FLOW_BAND\n"
tests\test_launcher_safety.py:318:        "MCP_DIRECTIONAL_ECONOMIC_GATE_MODE=strict\n"
tests\test_launcher_safety.py-319-        "MCP_ENTRY_MIN_SCORE=66\n"
tests\test_launcher_safety.py-320-        "ACCURACY_TARGET_MODE=true\n"
tests\test_launcher_safety.py-321-        "BAND_REGIME_FILTER_ENABLED=true\n"
tests\test_launcher_safety.py-322-        "BROAD_UNIVERSE_ABS_MOVE_USDT_MIN=5\n"
--
tests\test_launcher_safety.py-328-        tmp_path,
tests\test_launcher_safety.py-329-        environ={
tests\test_launcher_safety.py-330-            "OPERATING_MODE": "PAPER",
tests\test_launcher_safety.py-331-            "PAPER_TRADING_PROFILE": "MAX_FLOW_BAND",
tests\test_launcher_safety.py:332:            "MCP_DIRECTIONAL_ECONOMIC_GATE_MODE": "paper_fallback",
tests\test_launcher_safety.py-333-            "MCP_ENTRY_MIN_SCORE": "50",
tests\test_launcher_safety.py-334-            "ACCURACY_TARGET_MODE": "false",
tests\test_launcher_safety.py-335-            "BAND_REGIME_FILTER_ENABLED": "false",
tests\test_launcher_safety.py-336-            "BROAD_UNIVERSE_ABS_MOVE_USDT_MIN": "0",
tests\test_launcher_safety.py-337-            "BROAD_UNIVERSE_ABS_MOVE_USDT_MAX": "999999",
tests\test_launcher_safety.py-338-            "BROAD_UNIVERSE_PREFER_ABS_USDT_RANK": "false",
tests\test_launcher_safety.py-339-        },
tests\test_launcher_safety.py-340-    )
tests\test_launcher_safety.py:341:    assert child["MCP_DIRECTIONAL_ECONOMIC_GATE_MODE"] == "strict"
tests\test_launcher_safety.py-342-    assert child["MCP_ENTRY_MIN_SCORE"] == "66"
tests\test_launcher_safety.py-343-    assert child["ACCURACY_TARGET_MODE"] == "true"
tests\test_launcher_safety.py-344-    assert child["BAND_REGIME_FILTER_ENABLED"] == "true"
tests\test_launcher_safety.py-345-    assert child["BROAD_UNIVERSE_ABS_MOVE_USDT_MIN"] == "5"
--
core\self_healing_supervisor.py-272-    if not decision.allowed or decision.path is None:
core\self_healing_supervisor.py-273-        return {
core\self_healing_supervisor.py-274-            "script": str(script),
core\self_healing_supervisor.py-275-            "started": False,
core\self_healing_supervisor.py:276:            "blocked": decision.reason,
core\self_healing_supervisor.py-277-            "dry_run": cfg.dry_run,
core\self_healing_supervisor.py-278-        }
core\self_healing_supervisor.py-279-    if cfg.dry_run:
core\self_healing_supervisor.py-280-        return {
--
core\pair_discovery.py-570-
core\pair_discovery.py-571-            self.MIN_TREND_EFFICIENCY = float(configured_min_efficiency)
core\pair_discovery.py-572-        except Exception:
core\pair_discovery.py-573-            pass
core\pair_discovery.py:574:        # Optional mild PAPER flow loosen (band/econ gates untouched).
core\pair_discovery.py-575-        try:
core\pair_discovery.py-576-            from config import UNIVERSE_FLOW_LOOSEN as _ufl
core\pair_discovery.py-577-
core\pair_discovery.py-578-            if bool((_ufl or {}).get("enabled")):
--
core\mcp_brain.py-194-        if frac <= 0 or frac >= 1.0:
core\mcp_brain.py-195-            return tp_pct  # cannot form inverted band shape
core\mcp_brain.py-196-        raw = sl_pct * frac
core\mcp_brain.py-197-        # Binding cost clearance (2026-07-29): must clear stressed round-trip
core\mcp_brain.py:198:        # (~31.5bps under paper_fallback defaults) or economic_gate_stressed_
core\mcp_brain.py-199-        # breakeven starves AccBand OPENs. Prefer geometry hit-rate over the
core\mcp_brain.py-200-        # legacy min_tp_pct=0.5 inflate — clearance is the only soft floor.
core\mcp_brain.py-201-        cost_clearance = float(_acc.get("min_tp_cost_pct", 0.35))
core\mcp_brain.py-202-        tp = max(raw, cost_clearance)
--
tests\test_live_startup_hardening.py-88-            pointer_path=pointer,
tests\test_live_startup_hardening.py-89-        )
tests\test_live_startup_hardening.py-90-
tests\test_live_startup_hardening.py-91-
tests\test_live_startup_hardening.py:92:def test_active_live_model_gate_propagates_validation_reason(
tests\test_live_startup_hardening.py-93-    tmp_path, monkeypatch
tests\test_live_startup_hardening.py-94-):
tests\test_live_startup_hardening.py-95-    pointer = tmp_path / "latest.json"
tests\test_live_startup_hardening.py-96-    pointer.write_text(
--
tests\test_live_preflight.py-251-    for mode in ("PAPER", "OBSERVATION", "", None):
tests\test_live_preflight.py-252-        enforce_live_preflight_gate(mode, exchanges={"bybit": Untouchable()}, symbols=SYMBOLS)
tests\test_live_preflight.py-253-
tests\test_live_preflight.py-254-
tests\test_live_preflight.py:255:def test_enforce_blocks_controlled_live_on_failure_with_reason():
tests\test_live_preflight.py-256-    exchanges = {"bybit": FakeClient(skew_ms=9000)}
tests\test_live_preflight.py-257-    notes = []
tests\test_live_preflight.py-258-    with pytest.raises(SystemExit) as exc:
tests\test_live_preflight.py-259-        enforce_live_preflight_gate(
--
tests\test_maker_resolution_provenance.py-1-"""Maker-first resolution must hand the INTENT to the terminal recorder.
tests\test_maker_resolution_provenance.py-2-
tests\test_maker_resolution_provenance.py:3:2026-07-21 incident (first fill after the economic-gate unblock):
tests\test_maker_resolution_provenance.py-4-``_finalize_maker_intent`` built ``_maker_first_ctx`` WITHOUT the
tests\test_maker_resolution_provenance.py-5-``maker_intent`` key, so ``open_position`` passed an EMPTY dict to
tests\test_maker_resolution_provenance.py-6-``_record_maker_resolution_decision``. With ``enforce_event_provenance`` on,
tests\test_maker_resolution_provenance.py-7-``record_terminal_decision`` raised "candidate symbol is missing" and
--
core\order_manager.py-1388-                if mcp_dec.get("action") in ("BUY", "SELL"):
core\order_manager.py-1389-                    mcp_approved = True
core\order_manager.py-1390-            except Exception:
core\order_manager.py-1391-                pass
core\order_manager.py:1392:        blocked, block_reason = self.kelly.should_block_trade(
core\order_manager.py-1393-            strategy, mcp_approved=mcp_approved)
core\order_manager.py-1394-        if blocked:
core\order_manager.py:1395:            logger.warning(f"[Orders] KELLY BLOCK: {block_reason}")
core\order_manager.py-1396-            self.last_open_reject = "kelly_block"
core\order_manager.py-1397-            return None
core\order_manager.py-1398-
core\order_manager.py-1399-        if size <= 0:
--
tests\test_mcp_server_smoke.py-16-        "trading_bot_performance_summary",
tests\test_mcp_server_smoke.py-17-        "trading_bot_recent_candidates",
tests\test_mcp_server_smoke.py-18-        "trading_bot_shadow_vs_live",
tests\test_mcp_server_smoke.py-19-        "trading_bot_query",
tests\test_mcp_server_smoke.py:20:        "trading_bot_recent_movers",
tests\test_mcp_server_smoke.py:21:        "trading_bot_f1_edge_status",
tests\test_mcp_server_smoke.py-22-    }
tests\test_mcp_server_smoke.py-23-    for tool in by_name.values():
tests\test_mcp_server_smoke.py-24-        annotations = tool.annotations
tests\test_mcp_server_smoke.py-25-        assert annotations is not None
--
tests\test_mission_control_state.py-437-        rows=[("SKIP", "scalp_veto:quiet(atr=0.6%)")] * 4
tests\test_mission_control_state.py-438-        + [("SKIP", "scalp_req_fail(2/4:adx=42,atr=0.9%)")] * 2
tests\test_mission_control_state.py-439-        + [("ALLOW", ""), ("ALLOW", ""), ("ALLOW", "")],
tests\test_mission_control_state.py-440-        events=[
tests\test_mission_control_state.py:441:            (7, "rejected", "economic_gate_stressed_breakeven", "execute_open"),
tests\test_mission_control_state.py-442-            (8, "filled", "maker_first_maker_fill", "maker_resolution"),
tests\test_mission_control_state.py-443-        ],
tests\test_mission_control_state.py-444-    )
tests\test_mission_control_state.py-445-    out = state.load_brain(tmp_path, window_minutes=60)
--
tests\test_mission_control_state.py-463-        + terminal["residual"]
tests\test_mission_control_state.py-464-        == scorer["allowed"]
tests\test_mission_control_state.py-465-    )
tests\test_mission_control_state.py-466-    assert scorer["skip_causes"][0]["family"] == "scalp_veto:quiet"
tests\test_mission_control_state.py:467:    assert terminal["blocks"][0]["reason"] == "economic_gate_stressed_breakeven"
tests\test_mission_control_state.py-468-    assert terminal["blocks"][0]["plain"] is not None
tests\test_mission_control_state.py-469-
tests\test_mission_control_state.py-470-
tests\test_mission_control_state.py-471-def test_load_brain_names_the_binding_constraint_at_each_stage(tmp_path: Path) -> None:
--
tests\test_mission_control_state.py-473-    _brain_warehouse(
tests\test_mission_control_state.py-474-        tmp_path,
tests\test_mission_control_state.py-475-        rows=[("SKIP", "scalp_veto:quiet(atr=0.6%)")] * 5 + [("ALLOW", "")] * 2,
tests\test_mission_control_state.py-476-        events=[
tests\test_mission_control_state.py:477:            (6, "rejected", "economic_gate_stressed_breakeven", "execute_open"),
tests\test_mission_control_state.py:478:            (7, "rejected", "economic_gate_stressed_breakeven", "execute_open"),
tests\test_mission_control_state.py-479-        ],
tests\test_mission_control_state.py-480-    )
tests\test_mission_control_state.py-481-    binding = state.load_brain(tmp_path)["binding"]
tests\test_mission_control_state.py-482-    assert binding["scorer"]["family"] == "scalp_veto:quiet"
tests\test_mission_control_state.py-483-    assert binding["scorer"]["count"] == 5
tests\test_mission_control_state.py-484-    assert binding["scorer"]["measurements"][0]["label"] == "ATR"
tests\test_mission_control_state.py:485:    assert binding["downstream"]["family"] == "economic_gate_stressed_breakeven"
tests\test_mission_control_state.py-486-    assert binding["downstream"]["count"] == 2
tests\test_mission_control_state.py-487-
tests\test_mission_control_state.py-488-
tests\test_mission_control_state.py-489-def test_required_split_names_the_condition_that_failed_not_the_ones_that_passed(
--
tests\test_mission_control_state.py-795-                    "ts": "2026-07-28T16:00:01+00:00",
tests\test_mission_control_state.py-796-                    "type": "rejection",
tests\test_mission_control_state.py-797-                    "decision_id": "abc",
tests\test_mission_control_state.py-798-                    "symbol": "LINK/USDT",
tests\test_mission_control_state.py:799:                    "reason": "economic_gate_stressed_breakeven",
tests\test_mission_control_state.py-800-                    "stage": "execute_open",
tests\test_mission_control_state.py-801-                }
tests\test_mission_control_state.py-802-            ),
tests\test_mission_control_state.py-803-            # position_monitor cycles are NOT entry intents, but they are not
--
tests\test_mission_control_state.py-817-    assert len(live["intents"]) == 1
tests\test_mission_control_state.py-818-    intent = live["intents"][0]
tests\test_mission_control_state.py-819-    assert intent["symbol"] == "LINK/USDT"
tests\test_mission_control_state.py-820-    assert intent["mcp_score"] == 58
tests\test_mission_control_state.py:821:    assert intent["outcome"]["reason"] == "economic_gate_stressed_breakeven"
tests\test_mission_control_state.py-822-    assert intent["outcome"]["plain"] is not None
tests\test_mission_control_state.py-823-    # The monitor cycle is measured, not assumed away.
tests\test_mission_control_state.py-824-    assert live["monitor"]["cycles"] == 1
tests\test_mission_control_state.py-825-    assert live["monitor"]["decisions"] == 1
--
tests\test_phase29_post_sl_cooldown.py-192-    Path("data").mkdir(exist_ok=True)
tests\test_phase29_post_sl_cooldown.py-193-    from core.risk_manager import RiskManager
tests\test_phase29_post_sl_cooldown.py-194-    r = RiskManager()
tests\test_phase29_post_sl_cooldown.py-195-    r._recent_sl_by_pair_side["ADA/USDT:USDT|sell"] = [_t.time() - 170 * 60]
tests\test_phase29_post_sl_cooldown.py:196:    blocked_same, reason = r.is_sl_cooldown_active("ADA/USDT:USDT", "sell")
tests\test_phase29_post_sl_cooldown.py-197-    blocked_opp, _ = r.is_sl_cooldown_active("ADA/USDT:USDT", "buy")
tests\test_phase29_post_sl_cooldown.py:198:    assert blocked_same is True and "post_sl_cooldown" in reason
tests\test_phase29_post_sl_cooldown.py-199-    assert blocked_opp is False
tests\test_phase29_post_sl_cooldown.py-200-
tests\test_phase29_post_sl_cooldown.py-201-
tests\test_phase29_post_sl_cooldown.py-202-def test_execute_open_blocks_on_active_cooldown():
--
tests\test_phase29_post_sl_cooldown.py-238-    assert "self.risk.note_sl_hit(" in fc_block
tests\test_phase29_post_sl_cooldown.py-239-
tests\test_phase29_post_sl_cooldown.py-240-
tests\test_phase29_post_sl_cooldown.py-241-def test_finalize_close_only_records_on_sl_not_other_reasons():
tests\test_phase29_post_sl_cooldown.py:242:    """Make sure note_sl_hit is gated by the stop_loss reason check —
tests\test_phase29_post_sl_cooldown.py-243-    we don't want trailing/AGE/ghost exits to start the cooldown."""
tests\test_phase29_post_sl_cooldown.py-244-    src = Path("core/order_manager.py").read_text(encoding="utf-8")
tests\test_phase29_post_sl_cooldown.py-245-    fc_idx = src.index("def _finalize_close(self, pos: Position")
tests\test_phase29_post_sl_cooldown.py-246-    fc_block = src[fc_idx:fc_idx + 2000]
tests\test_phase29_post_sl_cooldown.py-247-    # The note_sl_hit call must appear AFTER the if-stop_loss guard
tests\test_phase29_post_sl_cooldown.py:248:    guard_idx = fc_block.index('reason == "stop_loss"')
tests\test_phase29_post_sl_cooldown.py-249-    note_idx = fc_block.index("self.risk.note_sl_hit(")
tests\test_phase29_post_sl_cooldown.py-250-    assert note_idx > guard_idx
tests\test_phase29_post_sl_cooldown.py-251-
tests\test_phase29_post_sl_cooldown.py-252-
--
tests\test_readiness.py-124-        approved_by="operator",
tests\test_readiness.py-125-    )
tests\test_readiness.py-126-    blocked = evaluate_readiness(live_approval=short_sha, **base).to_dict()
tests\test_readiness.py-127-    assert blocked["LIVE_BLOCKED"] is True
tests\test_readiness.py:128:    assert blocked["state_reasons"]["LIVE_BLOCKED"] == "live_approval_git_sha_not_full"
tests\test_readiness.py-129-
tests\test_readiness.py-130-    expired = LiveApproval(
tests\test_readiness.py-131-        strategy_id="F1",
tests\test_readiness.py-132-        strategy_version="f1-v3",
--
tests\test_readiness.py-135-        approved_by="operator",
tests\test_readiness.py-136-    )
tests\test_readiness.py-137-    blocked = evaluate_readiness(live_approval=expired, **base).to_dict()
tests\test_readiness.py-138-    assert blocked["LIVE_BLOCKED"] is True
tests\test_readiness.py:139:    assert blocked["state_reasons"]["LIVE_BLOCKED"] == "live_approval_expired"
tests\test_readiness.py-140-
tests\test_readiness.py-141-
tests\test_readiness.py-142-def test_evidence_registry_requires_explicit_accepted_status(tmp_path):
tests\test_readiness.py-143-    registry = tmp_path / "active_strategies.json"
--
tests\test_regime_aware_gate.py-42-    # The except branch must log + fall through (return False NOT triggered)
tests\test_regime_aware_gate.py-43-    assert "[Regime] check skipped" in src
tests\test_regime_aware_gate.py-44-
tests\test_regime_aware_gate.py-45-
tests\test_regime_aware_gate.py:46:def test_regime_gate_writes_skip_reason_to_warehouse():
tests\test_regime_aware_gate.py:47:    """When blocked, the candidate row gets decision=SKIP + reason."""
tests\test_regime_aware_gate.py-48-    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
tests\test_regime_aware_gate.py-49-    assert "UPDATE candidates SET decision='SKIP'" in src
tests\test_regime_aware_gate.py-50-    assert "skip_reason=? WHERE id=?" in src
tests\test_regime_aware_gate.py-51-
--
tests\test_risk_agent.py-65-    p = _proposal("buy")
tests\test_risk_agent.py-66-    p.ts = ts
tests\test_risk_agent.py-67-    res = ra.review(p, blacklist=set(), allowed_hours={2, 3, 14, 15, 16})
tests\test_risk_agent.py-68-    assert not res.approved
tests\test_risk_agent.py:69:    assert "hour_blocked:17" in res.reason
tests\test_risk_agent.py-70-
tests\test_risk_agent.py-71-
tests\test_risk_agent.py-72-def test_risk_agent_vetoes_invalid_long_levels(risk_state):
tests\test_risk_agent.py-73-    """Long with SL above entry → invalid."""

exec
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command "rg -l -S \"trading_bot_recent_movers|trading_bot_f1_edge_status\" . -g '"'!data/**'"' -g '"'!*.pyc'"' | Select-Object -First 20" in D:\Downloads\Trading_Bot
 succeeded in 971ms:
.\mcp_server\trading_bot_mcp.py
.\_workspace\plans\2026-07-30-profitability-improvement-loop.md
.\_workspace\strategy_pipeline\44_deep_research_strategy_hunt_movers_2026-07-30.md
.\_workspace\tmp_timing\codex_ceo_autoplan.md
.\tests\test_mcp_server_smoke.py
.\_workspace\tmp_timing\codex_eng_autoplan.md

exec
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command 'rg -n -C 12 -S "def trading_bot_recent_movers|def trading_bot_f1_edge_status|@mcp.tool" mcp_server/trading_bot_mcp.py' in D:\Downloads\Trading_Bot
 succeeded in 700ms:
74-    limit: int = Field(default=20, ge=1, le=500, description="Max rows to return")
75-    decision: Optional[str] = Field(
76-        default=None, description="Filter by decision: ALLOW | SKIP | REVIEW | TAKEN"
77-    )
78-
79-
80-class QueryInput(BaseModel):
81-    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
82-    sql: str = Field(..., min_length=6, description="A single read-only SELECT statement")
83-    limit: int = Field(default=100, ge=1, le=1000, description="Row cap applied to results")
84-
85-
86:@mcp.tool(name="trading_bot_list_tables", annotations={"title": "List warehouse tables", **_RO})
87-async def trading_bot_list_tables() -> str:
88-    """List every warehouse table with its row count and column names.
89-
90-    Returns: JSON list of {"table": str, "rows": int, "columns": [str]}.
91-    Use this first to discover what data exists before querying.
92-    """
93-    return _guard(wr.list_tables)
94-
95-
96:@mcp.tool(name="trading_bot_recent_trades", annotations={"title": "Recent closed trades", **_RO})
97-async def trading_bot_recent_trades(params: LimitInput) -> str:
98-    """Return the most recent CLOSED trades (newest first), optionally by symbol.
99-
100-    Returns JSON rows with: symbol, exchange, side, strategy_family, entry_px,
101-    exit_px, realized_pnl, r_multiple, leverage, hold_sec, exit_reason,
102-    ts_entry, ts_exit.
103-    """
104-    return _guard(lambda: wr.recent_trades(limit=params.limit, symbol=params.symbol))
105-
106-
107:@mcp.tool(
108-    name="trading_bot_performance_summary", annotations={"title": "Performance summary", **_RO}
109-)
110-async def trading_bot_performance_summary(params: SummaryInput) -> str:
111-    """Aggregate after-cost performance over CLOSED trades (optionally filtered).
112-
113-    Returns JSON: trades, wins, losses, win_rate, total_realized_pnl,
114-    avg_pnl_per_trade, profit_factor, gross_win, gross_loss. Use to judge
115-    whether the live path (or a symbol/strategy slice) has edge.
116-    """
117-    return _guard(lambda: wr.performance_summary(symbol=params.symbol, strategy=params.strategy))
118-
119-
120:@mcp.tool(
121-    name="trading_bot_recent_candidates", annotations={"title": "Recent candidate setups", **_RO}
122-)
123-async def trading_bot_recent_candidates(params: CandidatesInput) -> str:
124-    """Return recent candidate setups the engine evaluated (newest first).
125-
126-    SKIP rows carry skip_reason so you can see why setups were rejected. Filter
127-    with decision = ALLOW | SKIP | REVIEW | TAKEN. Returns JSON rows with: ts,
128-    exchange, symbol, side, strategy_family, confidence, decision, skip_reason,
129-    entry_px, leverage.
130-    """
131-    return _guard(lambda: wr.recent_candidates(limit=params.limit, decision=params.decision))
132-
133-
134:@mcp.tool(
135-    name="trading_bot_shadow_vs_live", annotations={"title": "Shadow vs live comparison", **_RO}
136-)
137-async def trading_bot_shadow_vs_live() -> str:
138-    """Compare the shadow agent ensemble's simulated PnL against live trades.
139-
140-    Returns JSON {shadow: {...}, live: {...}, note}. The shadow ensemble is
141-    log-only and may be promoted to live only after beating live on the honest
142-    promotion gate.
143-    """
144-    return _guard(wr.shadow_vs_live)
145-
146-
147:@mcp.tool(name="trading_bot_query", annotations={"title": "Read-only SQL query", **_RO})
148-async def trading_bot_query(params: QueryInput) -> str:
149-    """Run a single read-only SELECT against the warehouse and return rows.
150-
151-    Only one SELECT (or WITH ... SELECT) statement is permitted; any DDL/DML or
152-    multiple statements is rejected. Results are capped by `limit`. Use
153-    trading_bot_list_tables first to learn the schema.
154-    """
155-    return _guard(lambda: wr.run_select(params.sql, limit=params.limit))
156-
157-
158-class MoversInput(BaseModel):
159-    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
160-    # Reserved for future filters; currently returns the full latest snapshot.
161-
162-
163-class F1StatusInput(BaseModel):
164-    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
165-    lookback_hours: float = Field(
166-        default=24.0, ge=1.0, le=168.0, description="Hours of carry_gate_log to summarize"
167-    )
168-
169-
170:@mcp.tool(
171-    name="trading_bot_recent_movers",
172-    annotations={"title": "Recent absolute-USDT movers (shadow)", **_RO},
173-)
174:async def trading_bot_recent_movers(params: MoversInput) -> str:
175-    """Return the latest 1h/24h/7d absolute-USDT-band mover shortlist.
176-
177-    Shadow research only — these symbols are NOT an executable universe.
178-    Band defaults to [$5, $200] absolute price move (owner 2026-07-30).
179-    """
180-    return _guard(wr.recent_movers)
181-
182-
183:@mcp.tool(
184-    name="trading_bot_f1_edge_status",
185-    annotations={"title": "F1 carry edge status", **_RO},
186-)
187:async def trading_bot_f1_edge_status(params: F1StatusInput) -> str:
188-    """Summarize F1 funding/basis carry gate checks over a lookback window.
189-
190-    ok_rate≈0 with negative best net_edge_bps means the validated family is
191-    correctly idle (compressed funding), not a bug.
192-    """
193-    return _guard(lambda: wr.f1_edge_status(lookback_hours=params.lookback_hours))
194-
195-
196-if __name__ == "__main__":
197-    mcp.run()

exec
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command "rg -n -C 10 -S \"def recent_movers|def f1_edge_status|mover_shortlist_latest|carry_gate_log\" mcp_server core scripts -g '*.py'" in D:\Downloads\Trading_Bot
 succeeded in 742ms:
mcp_server\warehouse_reader.py-258-    conn = _connect(db_path)
mcp_server\warehouse_reader.py-259-    try:
mcp_server\warehouse_reader.py-260-        capped = f"SELECT * FROM ({sql.strip().rstrip(';')}) LIMIT {max(1, min(limit, 1000))}"
mcp_server\warehouse_reader.py-261-        return _rows(conn, capped)
mcp_server\warehouse_reader.py-262-    except sqlite3.Error as e:
mcp_server\warehouse_reader.py-263-        raise WarehouseError(f"SQL error: {e}") from e
mcp_server\warehouse_reader.py-264-    finally:
mcp_server\warehouse_reader.py-265-        conn.close()
mcp_server\warehouse_reader.py-266-
mcp_server\warehouse_reader.py-267-
mcp_server\warehouse_reader.py:268:def recent_movers(path: str | Path | None = None) -> dict[str, Any]:
mcp_server\warehouse_reader.py-269-    """Latest absolute-USDT-band mover shortlist (shadow research only)."""
mcp_server\warehouse_reader.py-270-    import json
mcp_server\warehouse_reader.py-271-
mcp_server\warehouse_reader.py:272:    p = Path(path) if path else REPO_ROOT / "data" / "mover_shortlist_latest.json"
mcp_server\warehouse_reader.py-273-    if not p.exists():
mcp_server\warehouse_reader.py-274-        raise WarehouseError(
mcp_server\warehouse_reader.py-275-            f"mover shortlist not found at {p}. Bot must complete ≥1 shadow "
mcp_server\warehouse_reader.py-276-            "universe scan with BROAD_UNIVERSE_MONITOR enabled."
mcp_server\warehouse_reader.py-277-        )
mcp_server\warehouse_reader.py-278-    try:
mcp_server\warehouse_reader.py-279-        return json.loads(p.read_text(encoding="utf-8"))
mcp_server\warehouse_reader.py-280-    except (OSError, json.JSONDecodeError) as e:
mcp_server\warehouse_reader.py-281-        raise WarehouseError(f"mover shortlist unreadable: {e}") from e
mcp_server\warehouse_reader.py-282-
mcp_server\warehouse_reader.py-283-
mcp_server\warehouse_reader.py:284:def f1_edge_status(
mcp_server\warehouse_reader.py-285-    path: str | Path | None = None, *, lookback_hours: float = 24.0
mcp_server\warehouse_reader.py-286-) -> dict[str, Any]:
mcp_server\warehouse_reader.py-287-    """Summarize F1 carry gate-log over the last lookback window (read-only)."""
mcp_server\warehouse_reader.py-288-    import json
mcp_server\warehouse_reader.py-289-    import time
mcp_server\warehouse_reader.py-290-
mcp_server\warehouse_reader.py:291:    p = Path(path) if path else REPO_ROOT / "data" / "carry_gate_log.jsonl"
mcp_server\warehouse_reader.py-292-    if not p.exists():
mcp_server\warehouse_reader.py-293-        raise WarehouseError(
mcp_server\warehouse_reader.py:294:            f"carry_gate_log not found at {p}. F1 runner has not written yet."
mcp_server\warehouse_reader.py-295-        )
mcp_server\warehouse_reader.py-296-    cut = time.time() - float(lookback_hours) * 3600.0
mcp_server\warehouse_reader.py-297-    n = 0
mcp_server\warehouse_reader.py-298-    ok = 0
mcp_server\warehouse_reader.py-299-    best: dict[str, Any] | None = None
mcp_server\warehouse_reader.py-300-    reasons: dict[str, int] = {}
mcp_server\warehouse_reader.py-301-    try:
mcp_server\warehouse_reader.py-302-        with p.open(encoding="utf-8", errors="ignore") as fh:
mcp_server\warehouse_reader.py-303-            for line in fh:
mcp_server\warehouse_reader.py-304-                line = line.strip()
--
mcp_server\warehouse_reader.py-324-                if best is None or edge_f > float(best.get("net_edge_bps") or -1e18):
mcp_server\warehouse_reader.py-325-                    best = {
mcp_server\warehouse_reader.py-326-                        "symbol": row.get("symbol"),
mcp_server\warehouse_reader.py-327-                        "venue": row.get("venue"),
mcp_server\warehouse_reader.py-328-                        "net_edge_bps": edge_f,
mcp_server\warehouse_reader.py-329-                        "ok": is_ok,
mcp_server\warehouse_reader.py-330-                        "reason": reason,
mcp_server\warehouse_reader.py-331-                        "ts": ts,
mcp_server\warehouse_reader.py-332-                    }
mcp_server\warehouse_reader.py-333-    except OSError as e:
mcp_server\warehouse_reader.py:334:        raise WarehouseError(f"carry_gate_log unreadable: {e}") from e
mcp_server\warehouse_reader.py-335-    top_reasons = sorted(reasons.items(), key=lambda kv: -kv[1])[:8]
mcp_server\warehouse_reader.py-336-    return {
mcp_server\warehouse_reader.py-337-        "lookback_hours": lookback_hours,
mcp_server\warehouse_reader.py-338-        "checks": n,
mcp_server\warehouse_reader.py-339-        "ok": ok,
mcp_server\warehouse_reader.py-340-        "ok_rate": (ok / n) if n else 0.0,
mcp_server\warehouse_reader.py-341-        "best": best,
mcp_server\warehouse_reader.py-342-        "top_reject_reasons": [
mcp_server\warehouse_reader.py-343-            {"reason": r, "count": c} for r, c in top_reasons
mcp_server\warehouse_reader.py-344-        ],
--
mcp_server\trading_bot_mcp.py-156-
mcp_server\trading_bot_mcp.py-157-
mcp_server\trading_bot_mcp.py-158-class MoversInput(BaseModel):
mcp_server\trading_bot_mcp.py-159-    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
mcp_server\trading_bot_mcp.py-160-    # Reserved for future filters; currently returns the full latest snapshot.
mcp_server\trading_bot_mcp.py-161-
mcp_server\trading_bot_mcp.py-162-
mcp_server\trading_bot_mcp.py-163-class F1StatusInput(BaseModel):
mcp_server\trading_bot_mcp.py-164-    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
mcp_server\trading_bot_mcp.py-165-    lookback_hours: float = Field(
mcp_server\trading_bot_mcp.py:166:        default=24.0, ge=1.0, le=168.0, description="Hours of carry_gate_log to summarize"
mcp_server\trading_bot_mcp.py-167-    )
mcp_server\trading_bot_mcp.py-168-
mcp_server\trading_bot_mcp.py-169-
mcp_server\trading_bot_mcp.py-170-@mcp.tool(
mcp_server\trading_bot_mcp.py-171-    name="trading_bot_recent_movers",
mcp_server\trading_bot_mcp.py-172-    annotations={"title": "Recent absolute-USDT movers (shadow)", **_RO},
mcp_server\trading_bot_mcp.py-173-)
mcp_server\trading_bot_mcp.py-174-async def trading_bot_recent_movers(params: MoversInput) -> str:
mcp_server\trading_bot_mcp.py-175-    """Return the latest 1h/24h/7d absolute-USDT-band mover shortlist.
mcp_server\trading_bot_mcp.py-176-
--
core\carry_runner.py-52-    CarryPositionState,
core\carry_runner.py-53-    carry_exit_signal,
core\carry_runner.py-54-    f1_entry_gate,
core\carry_runner.py-55-    f1_net_expected_edge_bps,
core\carry_runner.py-56-    f1_sizing_gate,
core\carry_runner.py-57-    pnl_concentration_ok,
core\carry_runner.py-58-)
core\carry_runner.py-59-from utils.process_lock import acquire_process_lock
core\carry_runner.py-60-
core\carry_runner.py-61-DEFAULT_STATE_PATH = Path("data/carry_positions.json")
core\carry_runner.py:62:DEFAULT_GATE_LOG = Path("data/carry_gate_log.jsonl")
core\carry_runner.py-63-DEFAULT_HEARTBEAT_PATH = Path("data/carry_heartbeat.json")
core\carry_runner.py-64-DEFAULT_HOLD_SETTLEMENTS = 21  # planning horizon for the edge gate (half of max)
core\carry_runner.py-65-DEFAULT_SLIP_FRAC = 0.0005     # 5 bps pessimistic taker slippage per crossing
core\carry_runner.py-66-RECONCILE_TIMEOUT_SEC = 10.0
core\carry_runner.py-67-# Maintenance-margin rate for the short perp leg's per-position liquidation
core\carry_runner.py-68-# model. Matches fill_reality.liquidation_price and
core\carry_runner.py-69-# funding_carry_lab.per_leg_liquidation_price defaults; conservative vs
core\carry_runner.py-70-# Binance/Bybit tier-1 0.4-0.5% for BTC/ETH at these notionals.
core\carry_runner.py-71-F1_MMR_FRAC = 0.005
core\carry_runner.py-72-# This single-shot runner has no resting-order queue/trade-event evidence.
--
core\bot_engine.py-1268-                                    "direction": r.direction,
core\bot_engine.py-1269-                                    "return_pct": r.selected_return_pct,
core\bot_engine.py-1270-                                    "abs_usdt": abs(r.selected_return_pct)
core\bot_engine.py-1271-                                    / 100.0
core\bot_engine.py-1272-                                    * float(r.price),
core\bot_engine.py-1273-                                    "quote_volume_usdt": r.quote_volume_usdt,
core\bot_engine.py-1274-                                }
core\bot_engine.py-1275-                                for r in scan.shortlist
core\bot_engine.py-1276-                            ],
core\bot_engine.py-1277-                        }
core\bot_engine.py:1278:                        _Path("data/mover_shortlist_latest.json").write_text(
core\bot_engine.py-1279-                            _json.dumps(snap, indent=2), encoding="utf-8"
core\bot_engine.py-1280-                        )
core\bot_engine.py-1281-                    except Exception as _snap_err:
core\bot_engine.py-1282-                        logger.debug(f"[Shadow] shortlist snapshot skip: {_snap_err}")
core\bot_engine.py-1283-                    if out:
core\bot_engine.py-1284-                        logger.info(
core\bot_engine.py-1285-                            f"[Shadow] broad universe accepted "
core\bot_engine.py-1286-                            f"{scan.accepted_tickers}/{scan.raw_tickers} tickers; "
core\bot_engine.py-1287-                            f"deep-analysis shortlist={len(out)}"
core\bot_engine.py-1288-                        )
--
scripts\harvest_hl_funding.py-1-"""Harvest Hyperliquid hourly funding (F1 timing conditioner — data only).
scripts\harvest_hl_funding.py-2-
scripts\harvest_hl_funding.py-3-Queue item (30_edge_queue #4): free HL API as a *signal* vs local
scripts\harvest_hl_funding.py:4:``data/carry_gate_log.jsonl``. This script NEVER places orders and is not
scripts\harvest_hl_funding.py-5-wired into MCP directional opens.
scripts\harvest_hl_funding.py-6-
scripts\harvest_hl_funding.py-7-Usage:
scripts\harvest_hl_funding.py-8-  ./venv/Scripts/python.exe scripts/harvest_hl_funding.py
scripts\harvest_hl_funding.py-9-  ./venv/Scripts/python.exe scripts/harvest_hl_funding.py --coins BTC ETH SOL
scripts\harvest_hl_funding.py-10-"""
scripts\harvest_hl_funding.py-11-from __future__ import annotations
scripts\harvest_hl_funding.py-12-
scripts\harvest_hl_funding.py-13-import argparse
scripts\harvest_hl_funding.py-14-import json
--
scripts\promotion_funnel.py-479-    lines += [f"- {lane}: {old or 'NEW'} → **{new}**" for lane, old, new in changes]
scripts\promotion_funnel.py-480-    if alert:
scripts\promotion_funnel.py-481-        lines.append(f"- ⚠ F1 REGIME ALERT: positive net edge sustained — "
scripts\promotion_funnel.py-482-                     f"{f1['detail']['top_edges'][:3]}")
scripts\promotion_funnel.py-483-    with day.open("a", encoding="utf-8") as fh:
scripts\promotion_funnel.py-484-        fh.write("\n".join(lines) + "\n")
scripts\promotion_funnel.py-485-
scripts\promotion_funnel.py-486-
scripts\promotion_funnel.py-487-def main() -> int:
scripts\promotion_funnel.py-488-    paths = {"warehouse": ROOT / "data" / "warehouse.sqlite",
scripts\promotion_funnel.py:489:             "gate_log": ROOT / "data" / "carry_gate_log.jsonl",
scripts\promotion_funnel.py-490-             "goal_json": ROOT / "data" / "goal_progress.json",
scripts\promotion_funnel.py-491-             "cal_dir": ROOT / "data" / "unlock_calendar",
scripts\promotion_funnel.py-492-             "funnel_json": FUNNEL_JSON, "dossier_dir": DOSSIER_DIR,
scripts\promotion_funnel.py-493-             "journal_dir": ROOT / "journal"}
scripts\promotion_funnel.py-494-    # Keep the hourly directional cohort current before the funnel consumes it.
scripts\promotion_funnel.py-495-    # build_report is read-only against trading state; this writes monitoring
scripts\promotion_funnel.py-496-    # output only and never touches config, orders, or promotion authority.
scripts\promotion_funnel.py-497-    atomic_write_json(paths["goal_json"], build_report(ROOT))
scripts\promotion_funnel.py-498-    doc = compute_all(paths, time.time())
scripts\promotion_funnel.py-499-    persist(doc, paths)
--
scripts\run_f1_carry_paper.py-411-    # default universe).
scripts\run_f1_carry_paper.py-412-    build_f1_spec(symbols=SYMBOLS, venues=VENUES, allow_extended_universe=True)
scripts\run_f1_carry_paper.py-413-    warehouse = Warehouse()
scripts\run_f1_carry_paper.py-414-    summaries = []
scripts\run_f1_carry_paper.py-415-    for venue in VENUES:  # sequential; ONE shared state + heartbeat file
scripts\run_f1_carry_paper.py-416-        runner = CarryRunner(
scripts\run_f1_carry_paper.py-417-            state_path=STATE_PATH,
scripts\run_f1_carry_paper.py-418-            snapshot_provider=build_live_snapshot_provider(venue),
scripts\run_f1_carry_paper.py-419-            symbols=SYMBOLS,
scripts\run_f1_carry_paper.py-420-            venue=venue,
scripts\run_f1_carry_paper.py:421:            gate_log_path=ROOT / "data" / "carry_gate_log.jsonl",
scripts\run_f1_carry_paper.py-422-            warehouse=warehouse,
scripts\run_f1_carry_paper.py-423-            # 2026-07-07: must be the registry FILE — passing the
scripts\run_f1_carry_paper.py-424-            # data/strategy_specs DIRECTORY made every F1 evidence write a
scripts\run_f1_carry_paper.py-425-            # silent no-op since Jul 2 (promotion evidence never recorded).
scripts\run_f1_carry_paper.py-426-            registry_path=ROOT / "data" / "active_strategies.json",
scripts\run_f1_carry_paper.py-427-            heartbeat_path=HEARTBEAT_PATH,
scripts\run_f1_carry_paper.py-428-            execution_mode=os.getenv("F1_EXECUTION_MODE", "taker").lower(),
scripts\run_f1_carry_paper.py-429-        )
scripts\run_f1_carry_paper.py-430-        summaries.append(runner.run_once())
scripts\run_f1_carry_paper.py-431-    print(f"[f1_carry_paper] PAPER pass: {json.dumps(summaries)}")

exec
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command '$n=0; Get-Content -LiteralPath '"'_workspace\\plans\\2026-07-30-profitability-improvement-loop.md' | ForEach-Object { "'$n++; '"'{0,4}: {1}' -f "'$n, $_ }' in D:\Downloads\Trading_Bot
 succeeded in 812ms:
   1: <!-- /autoplan restore point: /c/Users/SyedShirazShahid/.gstack/projects/sshirazshahid-Multi-platform-trading-bot/probe-bundle-mr-shadow-2026-07-19-autoplan-restore-20260730-065941.md -->
   2: # Profitability Improvement Loop (PAPER, evidence-gated)
   3: 
   4: **Branch:** `probe/bundle-mr-shadow-2026-07-19`  
   5: **Date:** 2026-07-30  
   6: **Owner intent:** Restart bot â†’ monitor â†’ keep improving/optimizing â†’ `/autoplan` the next improvement track.  
   7: **Honesty bound:** AccBand WR â‰  edge. No live AccBand/TA reopen without frozen promotion. F1 is the only validated live family (currently idle on compressed funding).
   8: 
   9: ## Problem
  10: 
  11: Directional MCP paper trading under AccBand geometry has measured after-cost negative expectancy. Owner asked for "profitable trades ONLY." Strict economic gate + EntryFloor 66 now refuse âˆ’EV opens. That correctly idles directional flow; it does not invent profit. The bot needs a continuous improvement loop that:
  12: 
  13: 1. Keeps the process healthy (restart, banners, heartbeat, shortlist).
  14: 2. Accrues only evidence-gated research (movers band, HL funding, C2/gamma queue, screens).
  15: 3. Never confuses activity with edge.
  16: 
  17: ## Premises (require human confirm â€” Phase 1 gate)
  18: 
  19: 1. **Refuse âˆ’EV opens is success, not failure.** Zero AccBand opens under `EconGate=strict` is the intended state until a promoted model or a CONFIRMED_GO family exists.
  20: 2. **F1 carry remains the only live-path family** that may enter when `net_edge` clears; do not weaken F1 gates for activity.
  21: 3. **Abs-USDT mover band [$5,$200] is research/telemetry only** â€” shortlist + MCP tools; it does not authorize OPENs.
  22: 4. **Screens stay dual-agreed + prereg-hashed**; liq-cascade (41_) is CLOSED NO_GO; next work is queue-driven (44_ Stage-0 when dense, C2 accrual, HL harvest cadence).
  23: 5. **Optimize = reduce silent failures + accelerate honest evidence**, not raise WR by loosening costs.
  24: 
  25: ## Current runtime (post-restart target)
  26: 
  27: | Knob | Target |
  28: |------|--------|
  29: | `OPERATING_MODE` | PAPER |
  30: | `PAPER_TRADING_PROFILE` | MAX_FLOW_BAND |
  31: | `MCP_ENTRY_MIN_SCORE` | 66 |
  32: | `MCP_DIRECTIONAL_ECONOMIC_GATE_MODE` | strict |
  33: | Abs band | min=5 max=200 prefer=true (launcher-pinned) |
  34: | Shadow probes | log-only fleet unchanged |
  35: 
  36: ## In scope (this plan)
  37: 
  38: ### Track A â€” Ops health (ship continuously)
  39: - Clean supervisor restart; verify boot banner (Profile / EntryFloor / EconGate).
  40: - Heartbeat freshness watchdog; alert if stale > N minutes after boot.
  41: - Persist `data/mover_shortlist_latest.json` each shadow scan; MCP `trading_bot_recent_movers` / `trading_bot_f1_edge_status`.
  42: - Log econ-gate block reasons with counts (no silent starve without reason).
  43: 
  44: ### Track B â€” Evidence accrual (research)
  45: - Cadence `scripts/harvest_hl_funding.py` â†’ `data/hl_funding_history.jsonl`.
  46: - When 44_ Stage-0 data density met: run hashed screen under frozen prereg (no outcomes before hash).
  47: - Keep C2 gamma-expiry / new-data edge program on queue (no TA reopen).
  48: 
  49: ### Track C â€” Small DX/ops polish (blast-radius)
  50: - Ensure `.env` UTF-8 + launcher pins cannot be silently overridden.
  51: - Dashboard/MCP visibility of: econ-gate mode, entry floor, F1 last-ok count, mover band hits.
  52: 
  53: ## NOT in scope
  54: 
  55: - Reopening AccBand via `paper_fallback` for trade activity.
  56: - Promoting any shadow probe without frozen gate â‰¥30 resolved + owner sign-off.
  57: - Live CONTROLLED_LIVE.
  58: - New directional TA/MCP strategies without pipeline GO.
  59: - Mission Control UI redesign (unless a one-line ops fix).
  60: 
  61: ## What already exists
  62: 
  63: | Need | Existing |
  64: |------|----------|
  65: | Econ gate | `core/economic_entry_gate.py`, `MCP_DIRECTIONAL_ECONOMIC_GATE_MODE` |
  66: | Abs band | `core/universe_monitor.py`, `BROAD_UNIVERSE_*`, bot_engine snapshot |
  67: | Launcher pin | `scripts/launcher_supervisor._safe_worker_env` |
  68: | F1 | `core/carry_runner.py`, gate log |
  69: | HL harvest | `scripts/harvest_hl_funding.py` |
  70: | Pipeline | strategy-evidence-pipeline + refuted ledger |
  71: | Prereg 44_ | `_workspace/strategy_pipeline/44_prereg_abs_usdt_mover_band.md` |
  72: 
  73: ## Success criteria
  74: 
  75: 1. Bot stays up; boot banner matches `.env` targets every restart.
  76: 2. `mover_shortlist_latest.json` refreshes each shadow cycle with `abs_band_usdt=[5,200]`.
  77: 3. Zero AccBand/MCP directional OPENs while model missing + strict gate (unless F1 clears).
  78: 4. At least one evidence artifact advanced per UTC day when queue has work (harvest or screen Stage-0).
  79: 5. `/autoplan` review complete â†’ approved task list executed in priority order.
  80: 
  81: ## Implementation alternatives
  82: 
  83: | Approach | Effort | Risk | Pros | Cons |
  84: |----------|--------|------|------|------|
  85: | A. Ops+evidence loop (this plan) | 0.5â€“2d CC | Low | Honest; compounds data | Few/no fills |
  86: | B. Loosen econ gate for activity | Hours | High | Looks "alive" | Reopens âˆ’EV bleed |
  87: | C. Big new strategy invent | Days | High | Narrative hope | Ledger says NO without screen |
  88: 
  89: **Selected:** A (P1 completeness of honest loop, P5 explicit).
  90: 
  91: ## Dream state delta
  92: 
  93: ```
  94: CURRENT: strict idle directional + F1 idle + movers telemetry shipping
  95: THIS PLAN: durable monitor loop + evidence cadence + ops silence fixes
  96: 12-MONTH: â‰¥1 CONFIRMED_GO family beyond F1 OR F1 regime returns; promotion owner-signed
  97: ```
  98: 
  99: ## Risks
 100: 
 101: | Risk | Mitigation |
 102: |------|------------|
 103: | Starvation misread as bug | Banner + econ-gate reason counters |
 104: | Stale supervisor env | Launcher pin + kill full tree on restart |
 105: | Outcome leak on screens | Hash prereg before outcomes; dual-model audit |
 106: | Heartbeat lag after boot | Explicit freshness check in monitor |
 107: 
 108: ## First executable tasks (pre-approval draft)
 109: 
 110: 1. [x] Confirm post-restart: EconGate strict, EntryFloor 66, shortlist band [5,200] â€” verified 2026-07-30 ~06:56â€“06:58Z
 111: 2. [ ] Filter tradfi/tokenized-equity noise from crypto mover shortlist (or tag `asset_class`) â€” live shortlist is SKHYNIX/XAU/META/SOXS-heavy
 112: 3. [ ] Heartbeat-stale check after boot if missing
 113: 4. [ ] Schedule / document HL funding harvest cadence
 114: 5. [ ] Econ-gate block counter in status/MCP (if not already)
 115: 6. [ ] Queue next screen only when 44_ density gate passes
 116: 
 117: ## Autoplan intake notes
 118: 
 119: - UI scope: **no** (ops + research; no component/screen/layout work)
 120: - DX scope: **yes** (MCP tools, harvest CLI, launcher pins)
 121: - Design doc: none for this branch â†’ standard review (office-hours optional)
 122: - Codex: binary present; dual voices enabled in config

exec
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command "Get-Content -LiteralPath 'scripts\\launcher_supervisor.py' | Select-Object -Skip 250 -First 105; Get-Content -LiteralPath 'core\\health_watchdog.py' | Select-Object -Skip 360 -First 40; Get-Content -LiteralPath 'core\\bot_engine.py' | Select-Object -Skip 1240 -First 47" in D:\Downloads\Trading_Bot
 succeeded in 874ms:

def _heartbeat_is_stale(
    root: Path,
    *,
    now: float,
    worker_started_at: float,
    max_age_seconds: float = HEARTBEAT_MAX_AGE_SECONDS,
    startup_grace_seconds: float = HEARTBEAT_STARTUP_GRACE_SECONDS,
) -> bool:
    """Return true only when the owned worker has missed its heartbeat budget.

    An old heartbeat from a prior process is deliberately ignored during the
    startup grace period.  After that deadline, a missing heartbeat or one not
    written by the current worker is considered a hang.  This check uses file
    metadata so the supervisor remains independent of bot imports and a partly
    written JSON document cannot crash the monitor.
    """

    if now - worker_started_at < startup_grace_seconds:
        return False
    path = Path(root) / "data" / "heartbeat.json"
    try:
        modified_at = float(path.stat().st_mtime)
    except OSError:
        return True
    if modified_at < worker_started_at:
        return True
    return now - modified_at > max_age_seconds


def _stop_owned_worker(process) -> None:
    """Stop only the exact child created by this launcher."""

    process.terminate()
    try:
        process.wait(timeout=WORKER_STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=WORKER_STOP_GRACE_SECONDS)


def _run_worker(
    root: Path,
    *,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    ensure_helpers: Callable[[Path], int] = _start_helpers,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run and externally monitor one PAPER/OBSERVATION worker.

    Process supervision is a separate failure domain from ``BotEngine``.  A
    blocked scheduler therefore cannot keep claiming health indefinitely: once
    its heartbeat is stale, the launcher stops its own child and returns a
    non-zero code so the bounded restart loop can recover it.
    """

    process = popen(
        [sys.executable, str(root / "main.py")],
        cwd=root,
        env=_safe_worker_env(root),
    )
    worker_started_at = clock()
    last_helper_check_at = worker_started_at
    stale_strikes = 0
    while True:
        return_code = process.poll()
        if return_code is not None:
            return int(return_code)
        now = clock()
        if _heartbeat_is_stale(
            root,
            now=now,
            worker_started_at=worker_started_at,
        ):
            stale_strikes += 1
            if stale_strikes < HEARTBEAT_FAILURE_STRIKES:
                print(
                    "[launcher] main.py heartbeat stale strike "
                    f"{stale_strikes}/{HEARTBEAT_FAILURE_STRIKES}; confirming."
                )
                sleep(HEARTBEAT_POLL_SECONDS)
                continue
            print(
                "[launcher] main.py heartbeat remained stale; stopping the owned "
                "worker so supervision can restart it."
            )
            _stop_owned_worker(process)
            return WORKER_HUNG_EXIT_CODE
        stale_strikes = 0
        if now - last_helper_check_at >= HELPER_RECHECK_SECONDS:
            helper_status = ensure_helpers(root)
            if helper_status:
                print(
                    "[launcher] Auxiliary health pass returned "
                    f"{helper_status}; retrying on the next interval."
                )
            last_helper_check_at = now
        sleep(HEARTBEAT_POLL_SECONDS)


def _worker_is_running(root: Path) -> Optional[bool]:
    """Fail closed when a legacy ``main.py`` process is already present."""

    if os.name != "nt":
             f"45s timeout ({', '.join(stale_syms[:4])}) â€” the resolver is "
             f"not running; entries are being LOST (starvation class, see "
             f"2026-07-11 fix)" if is_bad else ""),
            {"stale": stale_syms[:8],
             "oldest_age_min": round(oldest_age / 60, 1)} if is_bad else None,
        )

    def _check_heartbeat(self) -> None:
        if not HEARTBEAT_PATH.exists():
            return  # bot may not have written one yet
        age = time.time() - HEARTBEAT_PATH.stat().st_mtime
        if age > HEARTBEAT_STALE_SEC:
            self._alert(
                "heartbeat_stale", "WARN",
                f"heartbeat.json is {int(age)}s old (> {HEARTBEAT_STALE_SEC}s threshold)",
                {"age_sec": int(age), "path": str(HEARTBEAT_PATH)},
            )

    def _check_carry_heartbeat(self) -> None:
        # NOT gated on SIGNAL_SOURCE â€” the heartbeat file's existence is the
        # opt-in ("carry never ran" is not an alert). Edge-triggered.
        if not CARRY_HEARTBEAT_PATH.exists():
            self._edge_alert("carry_heartbeat_stale", False, "WARN", "")
            return
        age = time.time() - CARRY_HEARTBEAT_PATH.stat().st_mtime
        self._edge_alert(
            "carry_heartbeat_stale", age > CARRY_HEARTBEAT_STALE_SEC, "WARN",
            f"carry heartbeat is {int(age)}s old "
            f"(> {CARRY_HEARTBEAT_STALE_SEC}s threshold)",
            {"age_sec": int(age), "path": str(CARRY_HEARTBEAT_PATH)},
        )

    def _check_carry_recovery(self) -> None:
        # Rev 5.2: the carry runner's heartbeat stores its pass summary;
        # summary.recovery_active true means the portfolio-wide reduce-only
        # latch is set. Missing file / missing key -> silent + re-arm.
        # Edge-triggered: one alert per episode, re-arms when the flag clears.
        active = False
        if CARRY_HEARTBEAT_PATH.exists():
            try:
                    self._shadow_symbol_venues = {
                        row.symbol: row.venue for row in scan.shortlist
                    }
                    # Persist latest shortlist for MCP / research (log-only).
                    try:
                        import json as _json
                        from pathlib import Path as _Path

                        snap = {
                            "ts": now,
                            "accepted_tickers": scan.accepted_tickers,
                            "raw_tickers": scan.raw_tickers,
                            "abs_band_usdt": [
                                float(BROAD_UNIVERSE_MONITOR.get(
                                    "abs_move_usdt_min", 0
                                )),
                                float(BROAD_UNIVERSE_MONITOR.get(
                                    "abs_move_usdt_max", 0
                                )),
                            ],
                            "shortlist": [
                                {
                                    "venue": r.venue,
                                    "symbol": r.symbol,
                                    "base": r.base,
                                    "price": r.price,
                                    "horizon": r.selected_horizon,
                                    "direction": r.direction,
                                    "return_pct": r.selected_return_pct,
                                    "abs_usdt": abs(r.selected_return_pct)
                                    / 100.0
                                    * float(r.price),
                                    "quote_volume_usdt": r.quote_volume_usdt,
                                }
                                for r in scan.shortlist
                            ],
                        }
                        _Path("data/mover_shortlist_latest.json").write_text(
                            _json.dumps(snap, indent=2), encoding="utf-8"
                        )
                    except Exception as _snap_err:
                        logger.debug(f"[Shadow] shortlist snapshot skip: {_snap_err}")
                    if out:
                        logger.info(
                            f"[Shadow] broad universe accepted "
                            f"{scan.accepted_tickers}/{scan.raw_tickers} tickers; "
                            f"deep-analysis shortlist={len(out)}"

codex
Verdict: **do not approve as-is.** The honesty bound is sound and the owner-confirmed “refuse −EV = success” premise resolves the framing dispute. But this is not yet an executable improvement loop—mostly a list of desirable observability and research activities without a reliable control plane.

| Area | Score |
|---|---|
| Architecture sound? | **PARTIAL** |
| Test coverage sufficient? | **NO** |
| Performance risks addressed? | **NO** |
| Security threats covered? | **NO** |
| Error paths handled? | **PARTIAL** |
| Deployment risk manageable? | **PARTIAL** |

Key findings, in priority order:

1. **No time-boxed stop/pivot mechanism.** The plan can harvest one artifact per day indefinitely and still make no research decision. “When density met” has no date, target count, owner, evaluation window, or fallback if it is never met. Add a per-track decision contract: prerequisites, deadline, exact GO/NO-GO/PIVOT test, and a mandatory next action. This directly addresses the CEO finding.

2. **F1 is asserted as the only viable path but is not operated as one.** The plan includes status visibility, not a guaranteed F1 execution/monitoring cadence. `trading_bot_f1_edge_status` only summarizes a log; it cannot distinguish “no edge” from “runner never ran.” Define the F1 scheduler/service, singleton lock, expected run frequency, stale/missing semantics, retry/backoff, and alert ownership.

3. **“Zero AccBand/MCP OPENs” is not an authoritative safety invariant.** It scopes only one path and contains an ambiguous exception (“unless F1 clears”). The plan needs an explicit allowlist at the order-producing boundary: only F1 may create an order; all other directional families must be rejected with durable evidence. Also verify pre-existing positions and any auxiliary executors at restart.

4. **The boot-banner check is weak evidence of effective configuration.** A banner can be stale, incomplete, or describe the wrong process. Capture effective config in the heartbeat with process ID, boot ID, config hash, source/version, and pinned values. Validate that exact record after every restart—not merely that `.env` and logs appear to agree.

5. **Mover telemetry is not reliable enough to be a research artifact.** The current plan requires a fresh file, but not atomic writes, schema/version, scan ID, source coverage, error state, or freshness SLO. A partial/stale JSON document can look valid. The current write path is best-effort and swallows failure; replace it with atomic publish plus explicit `scan_status`, `completed_at`, rejected counts, and an age alert.

6. **Tradfi/tokenized-equity pollution is more than cosmetic.** Filtering after shortlist construction can let non-crypto instruments consume capacity and distort apparent opportunity density. Classify and exclude/partition by asset class before ranking; report crypto coverage separately from excluded instruments. Require tests for ambiguous symbols and venues.

7. **Economic-gate counters can misdiagnose starvation.** Counts of final gate blocks lack a denominator and miss upstream starvation. Report a versioned funnel by time window: scanned → eligible → candidate → proposed OPEN → execution-book valid → economic-gate pass/block → actual order. Include deduplication and stable reason taxonomy.

8. **HL funding harvest has hidden data-quality and scheduling complexity.** “Cadence” is unspecified. The script needs scheduler ownership, UTC schedule, natural-key deduplication, overlap prevention, retry/backoff, API outage recording, zero-row failure handling, retention, and a definition of the funding timestamp. Otherwise repeated current snapshots become misleading “evidence.”

9. **Competitive/research risk is absent.** A reactive mover screen is highly vulnerable to selection bias, venue/instrument contamination, crowding, survivorship, and post-hoc parameter tuning. The frozen prereg should include a benchmark, a holdout period, capacity/slippage assumptions, multiple-testing budget, and a kill criterion—not only a hash.

10. **No implementation-level test or rollout plan.** Add acceptance tests for missing/corrupt/old heartbeat; duplicate supervisor; restart limits; effective-config attestation; atomic shortlist failure; asset-class filtering; no-candidate versus econ-gate-block distinction; F1 runner staleness; duplicate/failed HL harvests; and density boundary/prereg immutability. Add a paper canary, rollback procedure, and explicit alert destinations.

Security is effectively unaddressed: `.env` UTF-8 and launcher pins do not cover secrets exposure in banners/MCP, local MCP access control, artifact tampering, or untrusted external-data failure. Paper mode reduces trading impact, not confidentiality or integrity risk.

The plan should be revised around three concrete deliverables before execution: a single effective-runtime status contract, a scheduled/observable F1 + research job control plane, and a time-boxed evidence decision ledger with mandatory stop/pivot outcomes.
tokens used
52,985
Verdict: **do not approve as-is.** The honesty bound is sound and the owner-confirmed “refuse −EV = success” premise resolves the framing dispute. But this is not yet an executable improvement loop—mostly a list of desirable observability and research activities without a reliable control plane.

| Area | Score |
|---|---|
| Architecture sound? | **PARTIAL** |
| Test coverage sufficient? | **NO** |
| Performance risks addressed? | **NO** |
| Security threats covered? | **NO** |
| Error paths handled? | **PARTIAL** |
| Deployment risk manageable? | **PARTIAL** |

Key findings, in priority order:

1. **No time-boxed stop/pivot mechanism.** The plan can harvest one artifact per day indefinitely and still make no research decision. “When density met” has no date, target count, owner, evaluation window, or fallback if it is never met. Add a per-track decision contract: prerequisites, deadline, exact GO/NO-GO/PIVOT test, and a mandatory next action. This directly addresses the CEO finding.

2. **F1 is asserted as the only viable path but is not operated as one.** The plan includes status visibility, not a guaranteed F1 execution/monitoring cadence. `trading_bot_f1_edge_status` only summarizes a log; it cannot distinguish “no edge” from “runner never ran.” Define the F1 scheduler/service, singleton lock, expected run frequency, stale/missing semantics, retry/backoff, and alert ownership.

3. **“Zero AccBand/MCP OPENs” is not an authoritative safety invariant.** It scopes only one path and contains an ambiguous exception (“unless F1 clears”). The plan needs an explicit allowlist at the order-producing boundary: only F1 may create an order; all other directional families must be rejected with durable evidence. Also verify pre-existing positions and any auxiliary executors at restart.

4. **The boot-banner check is weak evidence of effective configuration.** A banner can be stale, incomplete, or describe the wrong process. Capture effective config in the heartbeat with process ID, boot ID, config hash, source/version, and pinned values. Validate that exact record after every restart—not merely that `.env` and logs appear to agree.

5. **Mover telemetry is not reliable enough to be a research artifact.** The current plan requires a fresh file, but not atomic writes, schema/version, scan ID, source coverage, error state, or freshness SLO. A partial/stale JSON document can look valid. The current write path is best-effort and swallows failure; replace it with atomic publish plus explicit `scan_status`, `completed_at`, rejected counts, and an age alert.

6. **Tradfi/tokenized-equity pollution is more than cosmetic.** Filtering after shortlist construction can let non-crypto instruments consume capacity and distort apparent opportunity density. Classify and exclude/partition by asset class before ranking; report crypto coverage separately from excluded instruments. Require tests for ambiguous symbols and venues.

7. **Economic-gate counters can misdiagnose starvation.** Counts of final gate blocks lack a denominator and miss upstream starvation. Report a versioned funnel by time window: scanned → eligible → candidate → proposed OPEN → execution-book valid → economic-gate pass/block → actual order. Include deduplication and stable reason taxonomy.

8. **HL funding harvest has hidden data-quality and scheduling complexity.** “Cadence” is unspecified. The script needs scheduler ownership, UTC schedule, natural-key deduplication, overlap prevention, retry/backoff, API outage recording, zero-row failure handling, retention, and a definition of the funding timestamp. Otherwise repeated current snapshots become misleading “evidence.”

9. **Competitive/research risk is absent.** A reactive mover screen is highly vulnerable to selection bias, venue/instrument contamination, crowding, survivorship, and post-hoc parameter tuning. The frozen prereg should include a benchmark, a holdout period, capacity/slippage assumptions, multiple-testing budget, and a kill criterion—not only a hash.

10. **No implementation-level test or rollout plan.** Add acceptance tests for missing/corrupt/old heartbeat; duplicate supervisor; restart limits; effective-config attestation; atomic shortlist failure; asset-class filtering; no-candidate versus econ-gate-block distinction; F1 runner staleness; duplicate/failed HL harvests; and density boundary/prereg immutability. Add a paper canary, rollback procedure, and explicit alert destinations.

Security is effectively unaddressed: `.env` UTF-8 and launcher pins do not cover secrets exposure in banners/MCP, local MCP access control, artifact tampering, or untrusted external-data failure. Paper mode reduces trading impact, not confidentiality or integrity risk.

The plan should be revised around three concrete deliverables before execution: a single effective-runtime status contract, a scheduled/observable F1 + research job control plane, and a time-boxed evidence decision ledger with mandatory stop/pivot outcomes.
