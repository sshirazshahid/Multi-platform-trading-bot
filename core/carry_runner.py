"""core/carry_runner.py — Phase 3: F1 standalone delta-neutral carry PAPER runner.

Single-shot per invocation (harvest_derivs pattern; scheduled externally). Each
run loads ``data/carry_positions.json``, reads one market snapshot per symbol
from an injected provider (live: ledger-backed, tests: fixtures), evaluates the
full Rev-5 ``f1_entry_gate`` (logging pass/fail every run), opens an atomic
two-leg PAPER position on pass (pessimistic taker fills; one-leg failure and
reconcile-timeout paths mark the trade FAILED and persist a venue/symbol block
requiring manual review-clear), accrues funding at each passed settlement
boundary using the venue's ACTUAL interval, evaluates every exit gate, and on
exit writes a RESOLVED cycle row (warehouse ``carry_cycles``) and refreshes the
F1 EvidenceRegistry record. State saves are atomic (tmp + replace).

Rev 5.2: execution-integrity anomalies (one-leg failure / reconcile timeout,
notional-mismatch exit) latch a portfolio-wide reduce-only RECOVERY mode in the
shared state file — no new entries on ANY venue/symbol until an operator clears
it (``scripts/run_f1_carry_paper.py --clear-recovery``); open positions keep
being managed and closed.

PAPER/SIM ONLY: no exchange order call and no directional fallback exists here.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from core.cost_model import fee_rate
from core.fill_reality import failed_leg_outcome, perp_short_margin_buffer_x
from research.funding_carry_lab import (
    F1_DEFAULT_SYMBOLS,
    F1_MAX_PNL_CONCENTRATION_PCT,
    F1_MIN_CYCLES,
    CarryPositionState,
    carry_exit_signal,
    f1_entry_gate,
    f1_net_expected_edge_bps,
    f1_sizing_gate,
    pnl_concentration_ok,
)

DEFAULT_STATE_PATH = Path("data/carry_positions.json")
DEFAULT_GATE_LOG = Path("data/carry_gate_log.jsonl")
DEFAULT_HEARTBEAT_PATH = Path("data/carry_heartbeat.json")
DEFAULT_HOLD_SETTLEMENTS = 21  # planning horizon for the edge gate (half of max)
DEFAULT_SLIP_FRAC = 0.0005     # 5 bps pessimistic taker slippage per crossing
RECONCILE_TIMEOUT_SEC = 10.0
# Maintenance-margin rate for the short perp leg's per-position liquidation
# model. Matches fill_reality.liquidation_price and
# funding_carry_lab.per_leg_liquidation_price defaults; conservative vs
# Binance/Bybit tier-1 0.4-0.5% for BTC/ETH at these notionals.
F1_MMR_FRAC = 0.005
# Maker-first execution (research report actionable update): the spot leg is
# modeled as a post-only maker fill at MID (conservative vs the touch) with the
# venue maker fee, and NO slippage — but ONLY when the spot leg spread is wide
# enough to plausibly rest an order. Below this, fall back to a taker cross so
# the model never credits a fill that could not happen. The perp hedge ALWAYS
# stays taker (immediate cross to lock delta-neutrality). NOTE: on Binance/Bybit
# spot maker fee == spot taker fee, so the maker saving here is slippage +
# half-spread, NOT fees; the futures maker discount is only reachable via a fee
# tier (see F2 latch). Default execution mode stays "taker" (unchanged evidence).
F1_MAKER_MIN_SPREAD_BPS = 2.0

_EMPTY_STATE: dict[str, Any] = {
    "positions": {}, "blocks": {}, "cycles": [],
    "recovery": {"active": False},
}

# Static preconditions that MUST be independently verified before ANY live
# activation of the carry strategy. Rendered (always UNMET) in the report;
# nothing in this PAPER runner can evaluate or satisfy them.
LIVE_ACTIVATION_PRECONDITIONS = (
    "collateral unification verified on target venue (UTA/PM): spot collateral "
    "backs the perp short in ONE margin pool — Binance separate spot/futures "
    "wallets do NOT satisfy this; Bybit UTA 2.0 must be verified per account",
    "spot-leg maker/limit-first execution with immediate perp hedge "
    "(paper models pessimistic taker/taker)",
    "event-driven hedge monitoring (user-data stream) replacing the 15-min poll",
)


class CarryRunner:
    """Single-shot F1 carry paper runner (see module docstring)."""

    def __init__(
        self,
        *,
        state_path: Path | str = DEFAULT_STATE_PATH,
        snapshot_provider: Callable[[str], Mapping[str, Any] | None],
        now_fn: Callable[[], float] = time.time,
        paper_equity: float = 10_000.0,
        symbols=F1_DEFAULT_SYMBOLS,
        venue: str = "binance",
        hold_settlements: int = DEFAULT_HOLD_SETTLEMENTS,
        gate_log_path: Path | str = DEFAULT_GATE_LOG,
        failure_hook: Callable[[str], Mapping[str, Any] | None] | None = None,
        reconcile_timeout_sec: float = RECONCILE_TIMEOUT_SEC,
        warehouse: Any = None,
        registry_path: Path | str | None = None,
        slip_frac: float = DEFAULT_SLIP_FRAC,
        heartbeat_path: Path | str | None = DEFAULT_HEARTBEAT_PATH,
        execution_mode: str = "taker",
    ):
        self.state_path = Path(state_path)
        self.snapshot_provider = snapshot_provider
        self.now_fn = now_fn
        self.paper_equity = float(paper_equity)
        self.symbols = tuple(symbols)
        self.venue = venue
        self.hold_settlements = int(hold_settlements)
        self.gate_log_path = Path(gate_log_path)
        self.failure_hook = failure_hook
        self.reconcile_timeout_sec = float(reconcile_timeout_sec)
        self.warehouse = warehouse
        self.registry_path = registry_path
        self.slip_frac = float(slip_frac)
        self.heartbeat_path = Path(heartbeat_path) if heartbeat_path is not None else None
        if execution_mode not in ("taker", "maker_first"):
            raise ValueError(f"execution_mode must be taker|maker_first, got {execution_mode!r}")
        self.execution_mode = execution_mode

    # ── state I/O (atomic) ─────────────────────────────────────────────
    def load_state(self) -> dict:
        """Load state; one-time migration of legacy bare-symbol position keys.

        Positions are keyed ``venue:symbol`` (like blocks) so multiple venue
        runners can share ONE state file for cross-venue evidence aggregation.
        """
        if not self.state_path.exists():
            return json.loads(json.dumps(_EMPTY_STATE))
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state["positions"] = {
            (k if ":" in k else f"{self.venue}:{k}"): v
            for k, v in (state.get("positions") or {}).items()
        }
        # Rev 5.2 migration: pre-recovery state files gain the inactive latch.
        # isinstance guard: setdefault would keep a hand-edited null/scalar.
        if not isinstance(state.get("recovery"), dict):
            state["recovery"] = {"active": False}
        return state

    def save_state(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _log_gate(self, rec: dict) -> None:
        self.gate_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.gate_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    def _latch_recovery(self, state, now, venue, symbol, reason) -> None:
        """Latch portfolio-wide reduce-only RECOVERY mode (Rev 5.2).

        Execution-integrity anomalies (one-leg failure, notional-mismatch
        exit) stop ALL new entries across every venue/symbol sharing this
        state file; existing positions keep being managed and closed. Cleared
        only by an operator via ``run_f1_carry_paper.py --clear-recovery``.
        Idempotent: an already-active latch keeps its original reason/ts;
        every trigger is still gate-logged.
        """
        if not (state.get("recovery") or {}).get("active"):
            state["recovery"] = {
                "active": True, "reason": reason, "ts": now,
                "venue": venue, "symbol": symbol,
                "requires_manual_review": True,
            }
        self._log_gate({"ts": now, "venue": venue, "symbol": symbol,
                        "recovery_latched": True, "reason": reason})

    # ── fills (pessimistic taker, PAPER) ───────────────────────────────
    def _fees_per_leg_frac(self, spot_liq: str = "taker") -> tuple[float, float]:
        # Spot leg fee reflects the fill type (maker on a rested spot leg);
        # the perp hedge always crosses, so it is always taker.
        try:
            spot_fee = fee_rate(self.venue, "spot", spot_liq)
        except KeyError:
            spot_fee = fee_rate(self.venue, "futures", spot_liq)
        perp_fee = fee_rate(self.venue, "futures", "taker")
        return spot_fee, perp_fee

    # ── one run ────────────────────────────────────────────────────────
    def run_once(self) -> dict:
        now = float(self.now_fn())
        state = self.load_state()
        summary = {"ts": now, "venue": self.venue, "opened": 0, "closed": 0,
                   "failed": 0, "blocked": 0, "held": 0, "gate_evals": 0}
        for symbol in self.symbols:
            key = f"{self.venue}:{symbol}"
            if key in state["blocks"]:
                summary["blocked"] += 1
                self._log_gate({"ts": now, "symbol": symbol, "venue": self.venue,
                                "blocked": True,
                                "reason": "venue_symbol_blocked_pending_manual_review"})
                continue
            has_position = key in state["positions"]
            if not has_position and state["recovery"]["active"]:
                # Rev 5.2 reduce-only: manage/close existing positions only;
                # a mid-pass latch stops later symbols in this SAME pass too.
                self._log_gate({"ts": now, "symbol": symbol, "venue": self.venue,
                                "ok": False, "reason": "reduce_only_recovery"})
                continue
            snap = self.snapshot_provider(symbol)
            if snap is None:
                self._log_gate({"ts": now, "symbol": symbol, "venue": self.venue,
                                "ok": False, "reason": "no_snapshot"})
                continue
            if has_position:
                summary["held"] += 1
                self._manage(state, symbol, snap, now, summary)
            else:
                self._maybe_open(state, symbol, snap, now, summary)
            # FIX 3 (crash-safety): _book_cycle / warehouse.record_carry_cycle /
            # registry writes fire IMMEDIATELY inside the calls above, but state
            # persisted only once after the whole loop. A mid-pass exception or
            # kill after this symbol closed (cycle already warehoused) but before
            # a later symbol finished would resurrect the closed position on
            # reload -> double count. Persist after EACH symbol's mutation; the
            # atomic tmp+replace preserves the shared multi-venue file semantics.
            self.save_state(state)
        # set AFTER the pass so a latch fired mid-pass is reflected (and flows
        # into the heartbeat below, which stores this summary).
        summary["recovery_active"] = bool(state["recovery"]["active"])
        self.save_state(state)
        self._write_heartbeat(now, summary)
        return summary

    def _write_heartbeat(self, now: float, summary: dict) -> None:
        """Best-effort atomic liveness marker; a write failure never fails the pass."""
        if self.heartbeat_path is None:
            return
        try:
            self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.heartbeat_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"ts": now, "venue": self.venue, "summary": summary}),
                encoding="utf-8")
            os.replace(tmp, self.heartbeat_path)
        except Exception:  # noqa: BLE001 - liveness marker must not break the run
            pass

    # ── open path ──────────────────────────────────────────────────────
    def _gate_inputs(self, snap: Mapping[str, Any], now: float) -> dict:
        f = snap.get("funding") or {}
        spot_mid = (float(snap["spot_bid"]) + float(snap["spot_ask"])) / 2.0
        perp_mid = (float(snap["perp_bid"]) + float(snap["perp_ask"])) / 2.0
        spot_spread_bps = (float(snap["spot_ask"]) - float(snap["spot_bid"])) / spot_mid * 1e4
        perp_spread_bps = (float(snap["perp_ask"]) - float(snap["perp_bid"])) / perp_mid * 1e4
        next_ts = f.get("next_funding_ts")
        ttf_min = (float(next_ts) - now) / 60.0 if next_ts is not None else None
        snap_age = now - float(snap.get("ts", now))
        feeds_fresh = (not f.get("stale", True)) and snap_age <= 300.0
        # W1: COMPUTE the entry liq buffer from the planned short entry price
        # (perp_bid net of slip, same as _maybe_open) via the per-position
        # liquidation model. NEVER fall back to snap["liq_buffer_x"] at entry;
        # fail-closed: uncomputable -> 0.0 (the gate rejects) + flag for the log.
        liq_uncomputable = False
        try:
            liq_buffer_x = perp_short_margin_buffer_x(
                entry_px=float(snap["perp_bid"]) * (1.0 - self.slip_frac),
                mark_px=float(snap.get("perp_mark", perp_mid)),
                leverage=1.0,
                mmr=F1_MMR_FRAC,
            )["buffer_x"]
        except Exception:  # noqa: BLE001 - fail-closed at entry
            liq_buffer_x, liq_uncomputable = 0.0, True
        # maker-first: rest the spot leg at mid when the spread allows, and
        # recompute the effective round-trip cost so the entry floor
        # (max(15bps, 3x cost)) reflects the cheaper execution. The flat 15bps
        # absolute floor is deliberately NOT lowered.
        use_maker = (self.execution_mode == "maker_first"
                     and spot_spread_bps >= F1_MAKER_MIN_SPREAD_BPS)
        if use_maker:
            m_spot_fee, m_perp_fee = self._fees_per_leg_frac(spot_liq="maker")
            # spot legs rest at mid (no slip); only the 2 perp crossings pay slip.
            rt_cost = 2.0 * m_spot_fee + 2.0 * m_perp_fee + 2.0 * self.slip_frac
        else:
            rt_cost = float(snap["round_trip_cost_frac"])
        out = {
            "funding_per_settlement": float(f.get("rate") or 0.0),
            "hold_settlements": self.hold_settlements,
            "round_trip_cost_frac": rt_cost,
            "depth_ratio": float(snap.get("depth_ratio", 0.0)),
            "liq_buffer_x": liq_buffer_x,
            "funding_age_sec": float(f.get("age_sec", float("inf"))),
            "both_legs_fillable": bool(snap.get("both_legs_fillable", False)),
            "avg_funding_7d": snap.get("avg_funding_7d"),
            "perp_mark": snap.get("perp_mark", perp_mid),
            "spot_mid": spot_mid,
            "spot_spread_bps": spot_spread_bps,
            "perp_spread_bps": perp_spread_bps,
            "time_to_next_funding_min": ttf_min,
            "feeds_fresh": feeds_fresh,
        }
        if liq_uncomputable:
            out["liq_buffer_uncomputable"] = True
        return out

    def _maybe_open(self, state, symbol, snap, now, summary) -> None:
        gi = self._gate_inputs(snap, now)
        liq_uncomputable = gi.pop("liq_buffer_uncomputable", False)
        ok, reason, det = f1_entry_gate(**gi)
        summary["gate_evals"] += 1
        notional = self.paper_equity * 0.05
        if ok:
            total = sum(p["notional"] for p in state["positions"].values()) + notional
            ok, reason = f1_sizing_gate(
                paper_equity=self.paper_equity, symbol_notional=notional,
                total_carry_notional=total, leverage=1.0, is_initial_entry=True)
        rec = {"ts": now, "symbol": symbol, "venue": self.venue,
               "ok": bool(ok), "reason": reason,
               "net_edge_bps": det.get("net_edge_bps"),
               "liq_buffer_x": gi["liq_buffer_x"],
               "feeds_fresh": det.get("feeds_fresh")}
        if liq_uncomputable:
            rec["liq_buffer_uncomputable"] = True
        self._log_gate(rec)
        if not ok:
            return
        f = snap["funding"]
        slip = self.slip_frac
        use_maker = (self.execution_mode == "maker_first"
                     and gi["spot_spread_bps"] >= F1_MAKER_MIN_SPREAD_BPS)
        if use_maker:
            spot_px = gi["spot_mid"]                           # rest spot at mid (maker)
            perp_px = float(snap["perp_bid"]) * (1.0 - slip)   # taker hedge cross
            spot_fee, perp_fee = self._fees_per_leg_frac(spot_liq="maker")
        else:
            spot_px = float(snap["spot_ask"]) * (1.0 + slip)   # buy spot: pay up
            perp_px = float(snap["perp_bid"]) * (1.0 - slip)   # sell perp: hit bid
            spot_fee, perp_fee = self._fees_per_leg_frac()
        spot_qty = notional / spot_px
        perp_qty = notional / perp_px
        entry_fees = notional * (spot_fee + perp_fee)

        # simulate the atomic two-leg fill; a deterministic hook injects failures.
        hook = self.failure_hook(symbol) if self.failure_hook else None
        if hook is not None:
            legs = [
                {"leg": "spot", "target_qty": spot_qty,
                 "filled_qty": spot_qty * float(hook.get("spot_fill_frac", 1.0))},
                {"leg": "perp", "target_qty": perp_qty,
                 "filled_qty": perp_qty * float(hook.get("perp_fill_frac", 1.0))},
            ]
            outcome = failed_leg_outcome(legs)
            reconcile_sec = float(hook.get("reconcile_sec", 0.0))
            if outcome["any_failed"] or reconcile_sec > self.reconcile_timeout_sec:
                exit_reason = ("reconcile_timeout"
                               if reconcile_sec > self.reconcile_timeout_sec
                               else f"one_leg_failure:{outcome['failed_legs']}")
                # close whatever filled, book the unwind cost, block the pair.
                unwind_fees = notional * (spot_fee + perp_fee + 2.0 * slip)
                self._book_cycle(state, {
                    "symbol": symbol, "venue": self.venue,
                    "opened_ts": now, "resolved_ts": now,
                    "notional": notional, "gross_funding": 0.0,
                    "basis_pnl": 0.0, "fees": unwind_fees,
                    "slippage": notional * 2.0 * slip,
                    "net_pnl": -unwind_fees,
                    "settlements_held": 0, "bars_held": 0,
                    "exit_reason": exit_reason, "label_status": "FAILED",
                })
                state["blocks"][f"{self.venue}:{symbol}"] = {
                    "reason": exit_reason, "ts": now,
                    "requires_manual_review": True,
                }
                self._latch_recovery(
                    state, now, self.venue, symbol,
                    exit_reason if exit_reason.startswith("one_leg_failure")
                    else f"one_leg_failure:{exit_reason}")
                summary["failed"] += 1
                return

        state["positions"][f"{self.venue}:{symbol}"] = {
            "symbol": symbol, "venue": self.venue, "notional": notional,
            "spot_qty": spot_qty, "perp_qty": perp_qty,
            "spot_entry_px": spot_px, "perp_entry_px": perp_px,
            "entry_basis_bps": (perp_px - spot_px) / spot_px * 1e4,
            "opened_ts": now, "entry_fees": entry_fees,
            "round_trip_cost_frac": gi["round_trip_cost_frac"],
            "interval_hours": float(f.get("interval_hours") or 8.0),
            "next_settlement_ts": float(f.get("next_funding_ts") or (now + 8 * 3600)),
            "funding_accrued": 0.0, "settlements_held": 0,
            "consec_negative_settlements": 0, "runs_seen": 0,
            "perp_leverage": 1.0, "mmr": F1_MMR_FRAC,
            "execution_mode": self.execution_mode,
        }
        summary["opened"] += 1

    # ── manage path (settlement accrual + exit gates) ──────────────────
    def _manage(self, state, symbol, snap, now, summary) -> None:
        pos = state["positions"][f"{self.venue}:{symbol}"]
        f = snap.get("funding") or {}
        # Feed-staleness guard (mirror the _gate_inputs freshness check): a stale
        # or None-rate funding frame coerces rate->0, drives net_edge negative,
        # and would FORCE-CLOSE a healthy held position (booking full exit fees +
        # poisoning RESOLVED evidence). SKIP management this pass -> HOLD. A
        # genuinely stale hedge is still caught by the hedge-leg-age / stale exit
        # when the frame is FRESH but old.
        snap_age = now - float(snap.get("ts", now))
        feeds_fresh = (
            (not f.get("stale", True)) and snap_age <= 300.0 and f.get("rate") is not None
        )
        if not feeds_fresh:
            self._log_gate({"ts": now, "symbol": symbol, "venue": self.venue,
                            "ok": False, "held": True,
                            "reason": "manage_skipped_stale_feed"})
            return
        rate = float(f.get("rate") or 0.0)
        # accrue every PASSED settlement boundary at the venue's ACTUAL interval
        # (the interval is re-read from the frame at each boundary so a mid-hold
        # interval change takes effect from the next settlement).
        while pos["next_settlement_ts"] <= now:
            pay = rate * pos["notional"]
            pos["funding_accrued"] += pay
            pos["settlements_held"] += 1
            pos["consec_negative_settlements"] = (
                pos["consec_negative_settlements"] + 1 if rate < 0 else 0)
            iv = float(f.get("interval_hours") or pos["interval_hours"])
            pos["interval_hours"] = iv
            pos["next_settlement_ts"] += iv * 3600.0
        pos["runs_seen"] += 1

        spot_mid = (float(snap["spot_bid"]) + float(snap["spot_ask"])) / 2.0
        perp_mid = (float(snap["perp_bid"]) + float(snap["perp_ask"])) / 2.0
        cur_basis_bps = (perp_mid - spot_mid) / spot_mid * 1e4
        mismatch_pct = abs(pos["spot_qty"] * spot_mid - pos["perp_qty"] * perp_mid) / (
            pos["notional"] or 1.0) * 100.0
        spread_bps = max(
            (float(snap["spot_ask"]) - float(snap["spot_bid"])) / spot_mid * 1e4,
            (float(snap["perp_ask"]) - float(snap["perp_bid"])) / perp_mid * 1e4)
        remaining = max(1, self.hold_settlements - pos["settlements_held"])
        # W1: margin buffer from the STORED entry price + current mark via the
        # per-position liquidation model. Fail-safe-but-logged: only when the
        # computation is impossible, fall back to the snapshot's field.
        try:
            margin_buffer_x = perp_short_margin_buffer_x(
                entry_px=float(pos["perp_entry_px"]),
                mark_px=float(snap.get("perp_mark", perp_mid)),
                leverage=float(pos.get("perp_leverage", 1.0)),
                mmr=float(pos.get("mmr", F1_MMR_FRAC)),
            )["buffer_x"]
        except Exception:  # noqa: BLE001 - exit-side fallback, logged
            margin_buffer_x = float(snap.get("liq_buffer_x", 10.0))
            self._log_gate({"ts": now, "symbol": symbol, "venue": self.venue,
                            "liq_buffer_fallback": True})
        cs = CarryPositionState(
            consec_negative_settlements=pos["consec_negative_settlements"],
            adverse_basis_move_bps=max(0.0, cur_basis_bps - pos["entry_basis_bps"]),
            margin_buffer_x=margin_buffer_x,
            hedge_leg_age_sec=now - float(snap.get("ts", now)),
            notional_mismatch_pct=mismatch_pct,
            spread_bps=spread_bps,
            net_edge_bps=f1_net_expected_edge_bps(
                funding_per_settlement=rate, hold_settlements=remaining,
                round_trip_cost_frac=pos["round_trip_cost_frac"]),
            settlements_held=pos["settlements_held"],
        )
        should_exit, why = carry_exit_signal(cs)
        if not should_exit:
            return
        self._close(state, symbol, snap, now, why)
        summary["closed"] += 1
        # Rev 5.2: a hedge-drift (notional mismatch) exit is an execution-
        # integrity anomaly -> latch portfolio-wide reduce-only recovery.
        # "notional_mismatch" is the stable reason prefix emitted by gate 5 of
        # research/funding_carry_lab.carry_exit_signal; the other exits
        # (funding flip, spread widen, max-hold, margin buffer) must NOT latch.
        if why.startswith("notional_mismatch"):
            self._latch_recovery(state, now, self.venue, symbol,
                                 f"notional_mismatch_exit:{why}")

    def _close(self, state, symbol, snap, now, exit_reason) -> None:
        pos = state["positions"].pop(f"{self.venue}:{symbol}")
        slip = self.slip_frac
        spot_mid = (float(snap["spot_bid"]) + float(snap["spot_ask"])) / 2.0
        spot_spread_bps = (float(snap["spot_ask"]) - float(snap["spot_bid"])) / spot_mid * 1e4
        use_maker = (pos.get("execution_mode") == "maker_first"
                     and spot_spread_bps >= F1_MAKER_MIN_SPREAD_BPS)
        if use_maker:
            spot_exit = spot_mid                                # rest spot at mid (maker)
            perp_exit = float(snap["perp_ask"]) * (1.0 + slip)  # buy perp back: taker
            spot_fee, perp_fee = self._fees_per_leg_frac(spot_liq="maker")
        else:
            spot_exit = float(snap["spot_bid"]) * (1.0 - slip)  # sell spot: hit bid
            perp_exit = float(snap["perp_ask"]) * (1.0 + slip)  # buy perp back: pay up
            spot_fee, perp_fee = self._fees_per_leg_frac()
        spot_pnl = (spot_exit - pos["spot_entry_px"]) * pos["spot_qty"]
        perp_pnl = (pos["perp_entry_px"] - perp_exit) * pos["perp_qty"]
        basis_pnl = spot_pnl + perp_pnl
        exit_fees = pos["notional"] * (spot_fee + perp_fee)
        fees = pos["entry_fees"] + exit_fees
        gross_funding = pos["funding_accrued"]
        self._book_cycle(state, {
            "symbol": symbol, "venue": pos["venue"],
            "opened_ts": pos["opened_ts"], "resolved_ts": now,
            "notional": pos["notional"],
            "gross_funding": gross_funding, "basis_pnl": basis_pnl,
            "fees": fees, "slippage": pos["notional"] * 4.0 * slip,
            "net_pnl": gross_funding + basis_pnl - fees,
            "settlements_held": pos["settlements_held"],
            "bars_held": max(0, int((now - pos["opened_ts"]) // 3600)),
            "exit_reason": exit_reason, "label_status": "RESOLVED",
        })

    # ── cycle booking (state + warehouse + evidence registry) ──────────
    def _book_cycle(self, state, cyc: dict) -> None:
        state["cycles"].append(cyc)
        if self.warehouse is not None:
            try:
                self.warehouse.record_carry_cycle(cyc)
            except Exception as e:  # noqa: BLE001 - booking must not lose state
                from loguru import logger
                logger.warning(f"[CarryRunner] warehouse cycle booking failed: {e}")
        if self.registry_path is not None:
            try:
                from core.decision.promotion_loop import register_evidence

                resolved = [c for c in state["cycles"] if c["label_status"] == "RESOLVED"]
                register_evidence(
                    "F1",
                    oos_metrics={
                        "paper_cycles": len(resolved),
                        "paper_net_pnl": sum(c["net_pnl"] for c in resolved),
                        "failed_leg_events": sum(
                            1 for c in state["cycles"] if c["label_status"] == "FAILED"),
                    },
                    promotion_status="lab_paper",
                    path=self.registry_path,
                )
            except Exception as e:  # noqa: BLE001
                # 2026-07-07: was a silent pass — which hid EVERY registry
                # write failing since Jul 2 (registry_path was a directory).
                from loguru import logger
                logger.warning(f"[CarryRunner] evidence-registry write failed: {e}")


# ── report ─────────────────────────────────────────────────────────────
def _pf(nets: list[float]) -> float:
    wins = sum(x for x in nets if x > 0)
    losses = abs(sum(x for x in nets if x < 0))
    return wins / losses if losses > 0 else float("inf") if wins > 0 else 0.0


def promotion_checklist(cycles: list[dict], blocks: dict) -> dict:
    """Rev-5 F1 promotion-gate checklist over the resolved cycle history."""
    res = [c for c in cycles if c.get("label_status") == "RESOLVED"]
    nets = [float(c["net_pnl"]) for c in res]
    total = sum(nets)
    n = len(res)
    folds_pass = 0
    if n >= 3:
        k = n // 3
        folds = [nets[:k], nets[k:2 * k], nets[2 * k:]]
        folds_pass = sum(1 for fd in folds if sum(fd) > 0)
    stress = {}
    for mult in (1.5, 2.0):
        stressed = sum(
            float(c["gross_funding"]) + float(c["basis_pnl"])
            - float(c["fees"]) * mult
            for c in res)
        stress[mult] = stressed > 0
    by_sym = {}
    by_ven = {}
    for c in res:
        by_sym[c["symbol"]] = by_sym.get(c["symbol"], 0.0) + float(c["net_pnl"])
        by_ven[c["venue"]] = by_ven.get(c["venue"], 0.0) + float(c["net_pnl"])
    sym_ok, sym_share, _ = pnl_concentration_ok(by_sym, max_pct=F1_MAX_PNL_CONCENTRATION_PCT)
    ven_ok, ven_share, _ = pnl_concentration_ok(by_ven, max_pct=F1_MAX_PNL_CONCENTRATION_PCT)
    unresolved = len(blocks)
    return {
        "min_cycles": {"pass": n >= F1_MIN_CYCLES, "value": n, "floor": F1_MIN_CYCLES},
        "net_positive": {"pass": total > 0, "value": total},
        "profit_factor": {"pass": _pf(nets) >= 1.25, "value": _pf(nets)},
        "chronological_folds": {"pass": folds_pass >= 2, "value": folds_pass},
        "cost_stress_1_5x": {"pass": stress[1.5]},
        "cost_stress_2x": {"pass": stress[2.0]},
        "zero_unresolved_one_leg": {"pass": unresolved == 0, "value": unresolved},
        "concentration": {"pass": sym_ok and ven_ok,
                          "symbol_share_pct": sym_share, "venue_share_pct": ven_share},
        "by_symbol": by_sym, "by_venue": by_ven,
    }


def _cycle_win_rate_line(res: list[dict]) -> str:
    """Honest measured per-cycle win rate over RESOLVED cycles.

    Informational only — never a gate. Small samples are flagged so a lucky
    early streak cannot masquerade as 'accuracy'.
    """
    n = len(res)
    if n == 0:
        return "n/a (0 resolved cycles — no accuracy claim can be made yet)"
    wr = 100.0 * sum(1 for c in res if float(c["net_pnl"]) > 0) / n
    caveat = " — insufficient sample (n < 20), interpret with caution" if n < 20 else ""
    return f"{wr:.1f}% (n={n}){caveat}"


def write_report(state: dict, *, out_dir: Path | str = "reports",
                 now_fn: Callable[[], float] = time.time) -> Path:
    """Emit reports/f1_carry_report_<YYYYMMDD>.md from the runner state."""
    cycles = state.get("cycles", [])
    res = [c for c in cycles if c.get("label_status") == "RESOLVED"]
    failed = [c for c in cycles if c.get("label_status") == "FAILED"]
    chk = promotion_checklist(cycles, state.get("blocks", {}))
    day = time.strftime("%Y%m%d", time.gmtime(float(now_fn())))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"f1_carry_report_{day}.md"

    def _mark(item):
        return "PASS" if item["pass"] else "FAIL"

    lines = [
        f"# F1 Delta-Neutral Carry — PAPER report ({day})",
        "",
        f"Resolved cycles: {len(res)}  |  Failed-leg count: {len(failed)}",
        f"Open positions: {len(state.get('positions', {}))}  |  "
        f"Active blocks: {len(state.get('blocks', {}))}",
        "",
        "## Per-symbol PnL",
    ]
    for sym, v in sorted(chk["by_symbol"].items()):
        lines.append(f"- {sym}: {v:+.4f}")
    lines += ["", "## Per-venue PnL"]
    for ven, v in sorted(chk["by_venue"].items()):
        lines.append(f"- {ven}: {v:+.4f}")
    lines += [
        "",
        "## Attribution",
        f"- Funding earned (gross): {sum(c['gross_funding'] for c in res):+.4f}",
        f"- Basis PnL: {sum(c['basis_pnl'] for c in res):+.4f}",
        f"- Fees: {sum(c['fees'] for c in res):.4f}",
        f"- Slippage (modeled, in fill px): {sum(c['slippage'] for c in res):.4f}",
        f"- Net PnL: {sum(c['net_pnl'] for c in res):+.4f}",
        f"- Failed-leg count: {len(failed)}",
        "",
        "## Measured accuracy (informational — NOT a promotion gate)",
        f"- Per-cycle win rate: {_cycle_win_rate_line(res)}",
        "- Win rate is not expectancy: profit factor and the checklist below decide "
        "promotion. A high win rate with negative expectancy is a failure mode, "
        "not a target.",
        "",
        "## Promotion-gate checklist (Rev 5)",
        f"- [{_mark(chk['min_cycles'])}] >= 60 cycles ({chk['min_cycles']['value']})",
        f"- [{_mark(chk['net_positive'])}] net > 0 ({chk['net_positive']['value']:+.4f})",
        f"- [{_mark(chk['profit_factor'])}] PF >= 1.25 ({chk['profit_factor']['value']:.3f})",
        f"- [{_mark(chk['chronological_folds'])}] 2/3 chronological folds positive "
        f"({chk['chronological_folds']['value']}/3)",
        f"- [{_mark(chk['cost_stress_1_5x'])}] net > 0 at 1.5x cost stress",
        f"- [{_mark(chk['cost_stress_2x'])}] net > 0 at 2x cost stress",
        f"- [{_mark(chk['zero_unresolved_one_leg'])}] zero unresolved one-leg events "
        f"({chk['zero_unresolved_one_leg']['value']} blocks pending manual review)",
        f"- [{_mark(chk['concentration'])}] no symbol/venue > "
        f"{F1_MAX_PNL_CONCENTRATION_PCT:.0f}% of |PnL| concentration",
        "",
        "## Live-activation preconditions (static — NOT evaluated by this report)",
    ]
    for item in LIVE_ACTIVATION_PRECONDITIONS:
        lines.append(f"- [UNMET] {item}")
    lines += [
        "",
        "All items above are UNMET by definition in PAPER; this report cannot mark "
        "them met. Live activation is prohibited while any item is unmet.",
        "",
        "PAPER/SIM only — no real orders were or can be placed by this runner.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
