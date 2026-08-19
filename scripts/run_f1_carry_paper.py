"""Single-shot F1 delta-neutral carry PAPER runner (Rev 5 Phase 3, three-venue).

Copies the harvest_derivs pattern: one pass per invocation, scheduled
externally (e.g. Windows Task Scheduler, ~15 min). Each run loops the PAPER
venues (binance, bybit, bitget — opted in via the build_f1_spec Rev-5 latch) over ONE
shared ``data/carry_positions.json`` state file, reads a live BTC/ETH snapshot
per venue (spot BBO/depth, perp BBO/mark, Phase-1 funding frame incl interval +
next_funding_ts, fees via cost_model), evaluates the full Rev-5 entry gate,
manages/settles open PAPER positions, and saves state atomically.

PAPER/SIM ONLY: no exchange order call exists on this path and there is no
directional fallback. Read-only w.r.t. the live bot.

    python scripts/run_f1_carry_paper.py                  # one runner pass
    python scripts/run_f1_carry_paper.py --report         # markdown report
    python scripts/run_f1_carry_paper.py --clear-recovery # unlatch reduce-only
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 2026-07-07: load .env explicitly — this standalone entrypoint never imports
# config.py, so the owner knobs (F1_UNIVERSE at module level below and
# F1_EXECUTION_MODE in main) silently fell back to defaults in the scheduled
# task. Must run BEFORE the module-level F1_UNIVERSE read.
try:
    from dotenv import dotenv_values, load_dotenv
    load_dotenv(ROOT / ".env")
    from core.entry_policy import resolve_dotenv_entry_policy
    # dotenv never overrides inherited vars. Pin ENTRY_POLICY from the file
    # so a stale parent APPROVED_PAPER cannot keep F1 (or later config
    # imports) opening after the owner flipped `.env` to SHADOW_ONLY.
    os.environ["ENTRY_POLICY"] = resolve_dotenv_entry_policy(
        dotenv_values(ROOT / ".env")
    )
except Exception:  # dotenv optional: env vars set by the shell still work
    pass

from core.carry_runner import (  # noqa: E402
    DEFAULT_SLIP_FRAC,
    DEFAULT_STATE_PATH,
    CarryRunner,
    acquire_carry_state_lock,
    write_report,
)
from core.funding_history import (  # noqa: E402
    avg_7d,
    load_recent_realized_settlements,
)

STATE_PATH = ROOT / DEFAULT_STATE_PATH
HEARTBEAT_PATH = ROOT / "data" / "carry_heartbeat.json"
VENUES = ("binance", "bybit", "bitget")
# 2026-07-05 pre-registered PAPER universe expansion (owner-approved; see
# research/prereg_carry_universe_expansion_2026_07_05.md). The full 15-symbol
# set is frozen — including the 0-entry symbols — to keep later evaluation
# selection-bias-free. Env escape hatch: F1_UNIVERSE=legacy reverts to the
# Rev-5 BTC/ETH pair without a code change. Symbols missing on a venue are
# gate-skipped per symbol (snapshot unavailable -> no entry), fail-closed.
from research.funding_carry_lab import (  # noqa: E402
    DEFAULT_MAX_HEDGE_STALENESS_SEC,
    F1_EXPANDED_UNIVERSE_2026_07_05,
    F1_MAX_FUNDING_AGE_SEC,
)

SYMBOLS = (
    ("BTC/USDT", "ETH/USDT")
    if os.getenv("F1_UNIVERSE", "expanded").lower() == "legacy"
    else F1_EXPANDED_UNIVERSE_2026_07_05
)
# EXIT-SIDE FALLBACK ONLY: entry gating and exit management now compute a
# per-position margin buffer via fill_reality.perp_short_margin_buffer_x;
# this constant is used only when that computation is impossible mid-hold
# (the runner logs {"liq_buffer_fallback": true} when it happens).
PAPER_LIQ_BUFFER_X = 10.0
PAPER_NOTIONAL_HINT = 500.0  # depth_ratio denominator (paper clip size)


def carry_round_trip_cost_frac(venue: str) -> float:
    """TRUE delta-neutral carry round-trip cost fraction (TAKER execution).

    The entry gate's edge floor is ``max(15bps, 3x cost)``, so the cost fed to it
    MUST equal what ``CarryRunner._maybe_open``/``_close`` actually book in taker
    mode, or the floor is silently understated. Booked cost = spot round-trip FEE
    + perp round-trip FEE + slippage on all 4 crossings (buy spot + sell perp at
    entry; sell spot + buy perp at exit) — mirrors ``_close``'s booked
    ``fees`` (2 legs x entry+exit) plus ``slippage`` (``notional * 4 * slip``).

    ~50bps on binance vs the ~20bps futures-only figure the provider fed before
    (which counted only the perp leg). This correctly RAISES the effective entry
    floor to ~150bps => fewer/None entries, which is the intended behavior.
    The maker-first branch in ``CarryRunner._gate_inputs`` computes its OWN
    cheaper cost and does not use this value.
    """
    from core.cost_model import round_trip_fee

    return (
        round_trip_fee(venue, market_type="spot")
        + round_trip_fee(venue, market_type="futures")
        + 4.0 * DEFAULT_SLIP_FRAC
    )


def _epoch_seconds(value) -> float | None:
    """Normalize a source timestamp to epoch seconds without using receipt time."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc).timestamp()
    if isinstance(value, str):
        encoded = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(encoded)
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                return None
            return parsed.astimezone(timezone.utc).timestamp()
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(timestamp) or timestamp < 0.0:
        return None
    if timestamp >= 100_000_000_000.0:
        timestamp /= 1000.0
    return timestamp


def _record_observed_at(record: dict) -> float | None:
    for field in (
        "observed_at", "exchange_ts", "source_ts", "timestamp", "datetime", "ts",
    ):
        observed_at = _epoch_seconds(record.get(field))
        if observed_at is not None:
            return observed_at
    return None


def _observed_age_sec(observed_at: float | None, received_at: float) -> float:
    if observed_at is None or observed_at > received_at:
        return float("inf")
    return received_at - observed_at


def _bbo_depth(
    book: dict,
    *,
    executable_side: str,
) -> tuple[float, float, float, float | None] | None:
    """Return BBO plus top-5 depth for the side this carry leg executes.

    Spot entry buys consume asks; the perp hedge sells into bids. Opposite-side
    liquidity must never make an unfillable leg look deep.
    """
    if executable_side not in {"buy", "sell"}:
        raise ValueError("executable_side must be buy or sell")
    bids, asks = book.get("bids") or [], book.get("asks") or []
    if not bids or not asks:
        return None
    try:
        bid = float(bids[0][0])
        ask = float(asks[0][0])
        levels = asks if executable_side == "buy" else bids
        executable_notional = sum(
            float(price) * float(quantity)
            for price, quantity, *_ in levels[:5]
            if float(price) > 0.0 and float(quantity) > 0.0
        )
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        not math.isfinite(bid)
        or not math.isfinite(ask)
        or not math.isfinite(executable_notional)
        or bid <= 0.0
        or ask <= bid
    ):
        return None
    return bid, ask, executable_notional, _record_observed_at(book)


def build_live_snapshot_provider(venue: str):
    """Ledger + exchange-backed snapshot provider for ``venue`` (lazy imports).

    Uses the WRAPPED ``client.fetch_order_book(symbol, limit, market_type=...)``
    — Bybit requires the wrapper's _defaultType_lock; never call
    ``client.exchange.fetch_order_book`` directly.
    """
    from core.market_data_ledger import MarketDataLedger
    from exchanges.binance_client import BinanceClient
    from exchanges.bitget_client import BitgetClient
    from exchanges.bybit_client import BybitClient

    factory = {"binance": BinanceClient, "bybit": BybitClient,
               "bitget": BitgetClient}
    client = factory[venue]()
    # Single-venue ledger: this provider only reads its own venue, and a
    # multi-client ledger would double the funding API calls per pass.
    ledger = MarketDataLedger(clients={venue: client})
    # TRUE carry round-trip cost (spot+perp fees + 4-crossing slip, ~50bps), NOT
    # the futures-only ~20bps — so the gate's edge floor == the booked cost.
    rt_cost = carry_round_trip_cost_frac(venue)

    def provider(symbol: str) -> dict | None:
        coin = symbol.split("/")[0]
        try:
            spot = _bbo_depth(
                client.fetch_order_book(symbol, limit=5, market_type="spot"),
                executable_side="buy",
            )
            perp = _bbo_depth(
                client.fetch_order_book(f"{symbol}:USDT", limit=5,
                                        market_type="futures"),
                executable_side="sell",
            )
            mi = ledger.mark_index(symbol).get(venue) or {}
            funding = dict((ledger.funding([coin]).get(coin) or {}).get(venue) or {})
        except Exception:  # noqa: BLE001 - fail-open: runner logs no_snapshot
            return None
        if spot is None or perp is None or not funding:
            return None
        try:
            perp_exchange_symbol = client.exchange.market_id(f"{symbol}:USDT")
        except Exception:  # noqa: BLE001 - identity must be venue-derived
            return None

        # Stamp receipt AFTER all network work. Funding meta harvesters set
        # ``ts=time.time()`` at fetch completion; an early received_at made
        # funding look "future" (age=inf) → feeds_fresh permanently false.
        received_at = time.time()

        funding_observed_at = _record_observed_at(funding)
        funding_age = _observed_age_sec(funding_observed_at, received_at)
        funding.update({
            "observed_at": funding_observed_at,
            "received_at": received_at,
            "age_sec": funding_age,
            "stale": (
                bool(funding.get("stale", True))
                or funding_age > F1_MAX_FUNDING_AGE_SEC
            ),
        })

        mark_observed_at = _record_observed_at(dict(mi))
        try:
            perp_mark = float(mi.get("mark"))
            if not math.isfinite(perp_mark) or perp_mark <= 0.0:
                raise ValueError("invalid mark")
        except (TypeError, ValueError, OverflowError):
            perp_mark = (perp[0] + perp[1]) / 2.0
            mark_observed_at = perp[3]

        # Binance (and some ccxt spot paths) return timestamp=None on books.
        # For a just-completed REST poll, receipt time is the honest observation
        # clock — not a cache backdate. Missing exchange ts must not fail-closed
        # the entire F1 lane forever.
        spot_observed_at = spot[3] if spot[3] is not None else received_at
        perp_observed_at = perp[3] if perp[3] is not None else received_at
        if mark_observed_at is None:
            mark_observed_at = received_at

        market_observations = (spot_observed_at, perp_observed_at, mark_observed_at)
        observed_at = min(market_observations)
        market_stale = any(
            _observed_age_sec(value, received_at)
            > DEFAULT_MAX_HEDGE_STALENESS_SEC
            for value in market_observations
        )
        spot_buy_depth = spot[2]
        perp_sell_depth = perp[2]
        executable_depth = min(spot_buy_depth, perp_sell_depth)
        trailing = load_recent_realized_settlements(
            venue,
            coin,
            limit=21,
            before_ts=received_at,
            base_dir=ROOT / "data" / "funding_history",
        )
        return {
            "spot_bid": spot[0],
            "spot_ask": spot[1],
            "perp_bid": perp[0],
            "perp_ask": perp[1],
            "perp_mark": perp_mark,
            "perp_exchange_symbol": perp_exchange_symbol,
            "spot_buy_depth_notional": spot_buy_depth,
            "perp_sell_depth_notional": perp_sell_depth,
            "depth_ratio": executable_depth / PAPER_NOTIONAL_HINT,
            "liq_buffer_x": PAPER_LIQ_BUFFER_X,
            "both_legs_fillable": executable_depth >= PAPER_NOTIONAL_HINT,
            # 7d funding regime from the hourly harvester's rolling history;
            # honest None (gate pass-through, exactly as before) until >=6
            # periods exist in the window (core/funding_history).
            "avg_funding_7d": avg_7d(venue, coin,
                                     base_dir=ROOT / "data" / "funding_carry"),
            "trailing_funding_rates": (
                [row.rate for row in trailing] if trailing is not None else None
            ),
            "round_trip_cost_frac": rt_cost,
            "funding": funding,
            "spot_observed_at": spot_observed_at,
            "perp_observed_at": perp_observed_at,
            "mark_observed_at": mark_observed_at,
            "observed_at": observed_at,
            "received_at": received_at,
            "stale": market_stale,
            "ts": observed_at,
        }

    return provider


def run_report_only(
    *, state_path: Path | str = STATE_PATH, out_dir: Path | str = ROOT / "reports"
) -> Path:
    """Emit reports/f1_carry_report_<YYYYMMDD>.md from the persisted state."""
    p = Path(state_path)
    state = (
        json.loads(p.read_text(encoding="utf-8"))
        if p.exists()
        else {"positions": {}, "blocks": {}, "cycles": []}
    )
    return write_report(state, out_dir=out_dir)


def clear_recovery(*, state_path: Path | str = STATE_PATH,
                   heartbeat_path: Path | str = HEARTBEAT_PATH) -> bool:
    """Clear the recovery latch while excluding every carry state writer."""

    state_lock = acquire_carry_state_lock(state_path)
    if state_lock is None:
        print("[f1_carry_paper] carry state is busy — recovery was not cleared")
        return False
    try:
        return _clear_recovery_locked(
            state_path=state_path,
            heartbeat_path=heartbeat_path,
        )
    finally:
        state_lock.close()


def _clear_recovery_locked(*, state_path: Path | str,
                           heartbeat_path: Path | str) -> bool:
    """Operator latch-clear for Rev 5.2 reduce-only recovery.

    Prints the latched record, archives it (with an added ``cleared_ts``) to
    ``state["recovery_history"]``, resets ``state["recovery"]`` to inactive and
    saves atomically (tmp + os.replace, same pattern as CarryRunner.save_state).
    Never runs a market pass. Returns True iff a latch was cleared.
    """
    p = Path(state_path)
    try:
        state = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"[f1_carry_paper] cannot read state file ({e}) — nothing cleared")
        return False
    rec = state.get("recovery") or {}
    if not rec.get("active"):
        print("[f1_carry_paper] recovery is not latched — nothing to clear")
        return False
    print(f"[f1_carry_paper] recovery record: {json.dumps(rec, sort_keys=True)}")
    archived = dict(rec)
    archived["cleared_ts"] = time.time()
    state.setdefault("recovery_history", []).append(archived)
    state["recovery"] = {"active": False}
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)
    # Best-effort: also drop the heartbeat's recovery flag so the watchdog
    # re-arms immediately (per-episode alerting) instead of waiting for the
    # next scheduled pass to rewrite the heartbeat.
    try:
        hb_p = Path(heartbeat_path)
        hb = json.loads(hb_p.read_text(encoding="utf-8"))
        if (hb.get("summary") or {}).get("recovery_active"):
            hb["summary"]["recovery_active"] = False
            hb_tmp = hb_p.with_suffix(".json.tmp")
            hb_tmp.write_text(json.dumps(hb), encoding="utf-8")
            os.replace(hb_tmp, hb_p)
            print("[f1_carry_paper] heartbeat recovery flag cleared (watchdog re-arms)")
    except Exception:  # noqa: BLE001 - heartbeat is advisory; never block the clear
        pass
    print("[f1_carry_paper] recovery cleared — new entries allowed from next pass")
    return True


def main() -> None:
    if "--report" in sys.argv[1:]:
        out = run_report_only()
        print(f"[f1_carry_paper] report -> {out}")
        return
    if "--clear-recovery" in sys.argv[1:]:
        clear_recovery()
        return

    # A4 (2026-07-16): singleton lock — the 15-min scheduler fires while a
    # previous pass is still running (observed LastTaskResult 267009), and two
    # concurrent passes race the ONE shared state file (load->mutate->save is a
    # lost-update race), corrupting F1's promotion evidence. Skip this fire;
    # the next one picks up. The handle must stay referenced until exit.
    from utils.process_lock import acquire_process_lock

    _lock = acquire_process_lock("f1_carry_paper", root=ROOT)
    if _lock is None:
        print("[f1_carry_paper] another pass is still running — skipping this fire")
        return
    from core.warehouse import Warehouse
    from research.funding_carry_lab import build_f1_spec

    # Rev-5 documented opt-in latch: raises loudly at startup if this venue
    # set is not explicitly allowed (bybit and bitget are outside the F1
    # default universe).
    build_f1_spec(symbols=SYMBOLS, venues=VENUES, allow_extended_universe=True)
    warehouse = Warehouse()
    summaries = []
    for venue in VENUES:  # sequential; ONE shared state + heartbeat file
        runner = CarryRunner(
            state_path=STATE_PATH,
            snapshot_provider=build_live_snapshot_provider(venue),
            symbols=SYMBOLS,
            venue=venue,
            gate_log_path=ROOT / "data" / "carry_gate_log.jsonl",
            warehouse=warehouse,
            # 2026-07-07: must be the registry FILE — passing the
            # data/strategy_specs DIRECTORY made every F1 evidence write a
            # silent no-op since Jul 2 (promotion evidence never recorded).
            registry_path=ROOT / "data" / "active_strategies.json",
            heartbeat_path=HEARTBEAT_PATH,
            execution_mode=os.getenv("F1_EXECUTION_MODE", "taker").lower(),
        )
        summaries.append(runner.run_once())
    print(f"[f1_carry_paper] PAPER pass: {json.dumps(summaries)}")


if __name__ == "__main__":
    main()
