"""
core/capital_allocator.py — Capital Reallocation Engine (Spot ↔ Futures)

Conservative mode:
- Accumulate: sweep futures profits → spot (BTC/ETH/SOL)
- Deploy: sell spot → USDT for futures margin (only on structure break)
- Hedge: open futures short to protect spot drawdown >10%
- Rebalance: equalize USDT across exchanges when imbalanced

Max 20% of any wallet transferred per cycle. 15-minute cycle.
"""

import json
import time
from pathlib import Path

from loguru import logger

try:
    from config import (
        CAPITAL_ALLOCATION as CFG,
    )
    from config import (
        DRY_RUN,
    )
except ImportError:
    DRY_RUN = True
    CFG = {"enabled": True, "cycle_interval_min": 15}


STATE_FILE = Path("data/capital_allocator.json")
RECOMMENDATIONS_FILE = Path("data/allocation_recommendations.jsonl")

# Transfer circuit-breaker: after this many consecutive failed transfers on a
# single exchange, suppress further attempts for TRANSFER_BACKOFF_SEC to avoid
# futile API hammering / log spam (root-caused 2026-05-27: BybitClient had no
# transfer() method so every ACCUMULATE failed silently 88+ times in a row).
TRANSFER_FAILURE_LIMIT = 5
TRANSFER_BACKOFF_SEC = 2 * 60 * 60  # 2 hours


class CapitalAllocator:

    def __init__(self, exchanges: dict, spot_manager=None,
                 risk_manager=None, order_manager=None):
        self._exchanges = exchanges
        self._spot_mgr = spot_manager
        self._risk = risk_manager
        self._order_mgr = order_manager
        self._state = self._load_state()

    def evaluate_allocation(self) -> list:
        """Main decision engine. Returns list of allocation actions."""
        if not CFG.get("enabled", True):
            return []

        actions = []

        # 1. Check for futures profits to accumulate into spot
        accum_actions = self._check_accumulation()
        actions.extend(accum_actions)

        # 2. Check spot holdings for deployment (sell → futures)
        deploy_actions = self._check_deployment()
        actions.extend(deploy_actions)

        # 3. Check for spot hedging needs
        hedge_actions = self._check_hedging()
        actions.extend(hedge_actions)

        # 4. Check cross-exchange USDT rebalancing
        rebal_actions = self._check_rebalance()
        actions.extend(rebal_actions)

        return actions

    def _check_accumulation(self) -> list:
        """If futures have realized profit > threshold, transfer to spot for accumulation."""
        actions = []
        threshold = CFG.get("accumulation_threshold_usd", 10.0)
        max_pct = CFG.get("max_transfer_pct", 0.20)
        spot_targets = CFG.get("spot_targets", {"BTC": 0.40, "ETH": 0.30, "SOL": 0.30})

        for ex_name, exchange in self._exchanges.items():
            if not getattr(exchange, "_connected", False):
                continue
            try:
                futures_bal = exchange.fetch_balance("futures")
                if not futures_bal:
                    continue
                usdt_free = float(futures_bal.get("USDT", {}).get("free", 0)
                                  if isinstance(futures_bal.get("USDT"), dict) else 0)

                # 2026-05-24 — Seed baseline on first observation. Previously
                # `self._state.get("baselines", {}).get(ex_name, usdt_free)`
                # defaulted to the CURRENT free on every call, producing
                # profit=0 forever because nothing wrote to baselines{}.
                # The feature was permanently inert. Now: if no baseline
                # exists, write the current free and skip this cycle so a
                # future increase can be detected as profit.
                baselines = self._state.setdefault("baselines", {})
                if ex_name not in baselines:
                    baselines[ex_name] = usdt_free
                    self._save_state()
                    logger.debug(
                        f"[CapAlloc] Seeded baseline {ex_name}=${usdt_free:.2f}; "
                        f"no action this cycle"
                    )
                    continue
                baseline = baselines[ex_name]
                profit = usdt_free - baseline
                if profit < threshold:
                    continue

                transfer_amt = min(profit, usdt_free * max_pct)
                if transfer_amt < CFG.get("min_transfer_usdt", 5.0):
                    continue

                # Split among spot targets
                for coin, weight in spot_targets.items():
                    amt = transfer_amt * weight
                    if amt < 5.0:
                        continue
                    actions.append({
                        "type": "ACCUMULATE",
                        "exchange": ex_name,
                        "from": "futures",
                        "to": "spot",
                        "coin": coin,
                        "amount_usdt": round(amt, 2),
                        "reason": f"futures_profit=${profit:.2f} → buy {coin}",
                    })

            except Exception as e:
                logger.debug(f"[CapAlloc] {ex_name} accumulation check: {e}")
        return actions

    def _check_deployment(self) -> list:
        """If SpotManager has SELL signals, deploy that capital to futures."""
        actions = []
        if not self._spot_mgr:
            return actions

        max_pct = CFG.get("max_transfer_pct", 0.20)

        for ex_name, holdings in getattr(self._spot_mgr, "_holdings", {}).items():
            for coin, holding in holdings.items():
                eval_result = self._spot_mgr.evaluate_holding(
                    coin, ex_name, self._exchanges.get(ex_name))
                if eval_result.get("action") == "SELL":
                    value = eval_result.get("value_usdt", 0)
                    deploy_amt = min(value, value * max_pct)
                    if deploy_amt < CFG.get("min_transfer_usdt", 5.0):
                        continue
                    actions.append({
                        "type": "DEPLOY",
                        "exchange": ex_name,
                        "from": "spot",
                        "to": "futures",
                        "coin": coin,
                        "amount_usdt": round(deploy_amt, 2),
                        "reason": "structure_break → deploy to futures",
                    })
        return actions

    def _check_hedging(self) -> list:
        """If spot holdings have >10% drawdown and trend is bearish, hedge with futures short."""
        actions = []
        if not self._spot_mgr:
            return actions

        hedge_dd_pct = CFG.get("hedge_drawdown_pct", 0.10)  # noqa: F841

        for ex_name, holdings in getattr(self._spot_mgr, "_holdings", {}).items():
            for coin, holding in holdings.items():
                eval_result = self._spot_mgr.evaluate_holding(
                    coin, ex_name, self._exchanges.get(ex_name))
                if eval_result.get("action") == "HEDGE":
                    value = eval_result.get("value_usdt", 0)
                    hedge_size = value * 0.5
                    if hedge_size < 10.0:
                        continue
                    actions.append({
                        "type": "HEDGE",
                        "exchange": ex_name,
                        "coin": coin,
                        "symbol": f"{coin}/USDT:USDT",
                        "side": "sell",
                        "amount_usdt": round(hedge_size, 2),
                        "reason": eval_result.get("reason", "drawdown_hedge"),
                    })
        return actions

    def _check_rebalance(self) -> list:
        """If one exchange has >60% of total futures USDT, rebalance."""
        actions = []
        threshold = CFG.get("rebalance_threshold_pct", 0.60)
        min_transfer = CFG.get("min_transfer_usdt", 5.0)

        balances = {}
        for ex_name, exchange in self._exchanges.items():
            if not getattr(exchange, "_connected", False):
                continue
            try:
                fut_bal = exchange.fetch_balance("futures")
                if fut_bal:
                    usdt = float(fut_bal.get("USDT", {}).get("free", 0)
                                 if isinstance(fut_bal.get("USDT"), dict) else 0)
                    balances[ex_name] = usdt
            except Exception:
                pass

        total = sum(balances.values())
        if total < 30:
            return actions

        for ex_name, bal in balances.items():
            ratio = bal / total
            if ratio > threshold:
                excess = bal - (total / len(balances))
                transfer_per = excess / max(1, len(balances) - 1)
                if transfer_per < min_transfer:
                    continue
                for target_ex in balances:
                    if target_ex != ex_name:
                        actions.append({
                            "type": "REBALANCE",
                            "from_exchange": ex_name,
                            "to_exchange": target_ex,
                            "amount_usdt": round(transfer_per, 2),
                            "reason": f"{ex_name} has {ratio:.0%} of total",
                        })
        return actions

    def execute_allocation(self, action: dict) -> bool:
        """Execute an allocation action.

        2026-04-14 learning-first pivot: if `recommendation_only` is set
        (default True under the rebuild), we NEVER touch exchange transfer
        APIs. The action is appended to data/allocation_recommendations.jsonl
        for the owner to review and act on manually. Spec §13.
        """
        action_type = action.get("type", "")

        # Recommendation-only/PAPER gate — always writes, never executes.
        # DRY_RUN means the bot is not in CONTROLLED_LIVE. Wallet transfers
        # are real balance movements, not simulated orders, so PAPER must not
        # call exchange.transfer() even when a historical config set
        # recommendation_only=False.
        if DRY_RUN or CFG.get("recommendation_only", True):
            self._emit_recommendation(action)
            logger.info(
                f"[CapAlloc] [RECOMMEND-ONLY] {action_type}: "
                f"written to {RECOMMENDATIONS_FILE.name}, no exchange call"
            )
            self._record_action(action, success=False)
            return False

        # ACCUMULATE executes a REAL futures→spot transfer only in explicit
        # CONTROLLED_LIVE with recommendation_only=False. The transfer
        # circuit-breaker prevents futile retries.
        if action_type == "ACCUMULATE":
            ex_name = action["exchange"]
            if self._transfer_circuit_open(ex_name):
                logger.warning(
                    f"[CapAlloc] Transfer circuit OPEN for {ex_name} — "
                    f"skipping ACCUMULATE (backoff active)")
                self._record_action(action, success=False)
                return False
            exchange = self._exchanges.get(ex_name)
            if not exchange:
                return False
            try:
                # Transfer futures → spot
                success = bool(exchange.transfer(
                    action["amount_usdt"], "futures", "spot"))
            except Exception as e:
                logger.error(f"[CapAlloc] ACCUMULATE transfer error: {e}")
                success = False
            self._record_transfer_result(ex_name, success)
            if success:
                logger.info(
                    f"[CapAlloc] Transferred ${action['amount_usdt']:.2f} "
                    f"futures→spot on {ex_name}")
                # Reset baseline to post-transfer free so the next cycle
                # doesn't re-sweep the same profit window. Refetch fresh;
                # the transfer may have settled slightly differently than
                # `amount_usdt`.
                try:
                    post = exchange.fetch_balance("futures") or {}
                    usdt_post = float(
                        post.get("USDT", {}).get("free", 0)
                        if isinstance(post.get("USDT"), dict) else 0
                    )
                    self._state.setdefault("baselines", {})[ex_name] = usdt_post
                except Exception as _e:
                    logger.debug(
                        f"[CapAlloc] post-transfer baseline refetch failed: {_e}")
            self._record_action(action, success=success)
            return success

        if DRY_RUN:
            logger.info(f"[CapAlloc] [DRY] {action_type}: {action}")
            self._record_action(action, success=True)
            return True

        try:
            if action_type == "DEPLOY":
                exchange = self._exchanges.get(action["exchange"])
                if not exchange:
                    return False
                symbol = f"{action['coin']}/USDT"  # noqa: F841
                # Sell spot coin
                if self._order_mgr:
                    logger.info(
                        f"[CapAlloc] Selling {action['coin']} spot on {action['exchange']}")
                success = exchange.transfer(
                    action["amount_usdt"], "spot", "futures")
                self._record_action(action, success=success)
                return success

            elif action_type == "HEDGE":
                exchange = self._exchanges.get(action["exchange"])
                if not exchange:
                    return False
                if self._order_mgr:
                    logger.info(
                        f"[CapAlloc] Opening hedge short {action['symbol']} "
                        f"${action['amount_usdt']:.2f}")
                self._record_action(action, success=True)
                return True

            elif action_type == "REBALANCE":
                logger.info(
                    "[CapAlloc] Cross-exchange rebalance not yet supported "
                    "(requires withdrawal API)")
                return False

        except Exception as e:
            logger.error(f"[CapAlloc] Execution error: {e}")
            self._record_action(action, success=False)
            return False

        return False

    def run_cycle(self) -> list:
        """Main entry: evaluate → execute. Called every 15 min from BotEngine."""
        if not CFG.get("enabled", True):
            return []

        actions = self.evaluate_allocation()
        executed = []
        for action in actions:
            success = self.execute_allocation(action)
            action["executed"] = success
            executed.append(action)

        if executed:
            logger.info(f"[CapAlloc] Cycle: {len(executed)} actions "
                        f"({sum(1 for a in executed if a.get('executed'))} success)")

        self._state["last_cycle"] = time.time()
        self._save_state()
        return executed

    def _transfer_circuit_open(self, ex_name: str) -> bool:
        """Return True if the transfer circuit-breaker for `ex_name` is open
        (>= TRANSFER_FAILURE_LIMIT consecutive failures and still inside the
        backoff window). Resets the counter and returns False once the backoff
        window has elapsed."""
        breakers = self._state.setdefault("transfer_breakers", {})
        info = breakers.get(ex_name)
        if not info:
            return False
        if info.get("consecutive_failures", 0) < TRANSFER_FAILURE_LIMIT:
            return False
        tripped_at = info.get("tripped_at", 0) or 0
        if time.time() - tripped_at >= TRANSFER_BACKOFF_SEC:
            # Backoff elapsed — reset and allow another attempt.
            info["consecutive_failures"] = 0
            return False
        return True

    def _record_transfer_result(self, ex_name: str, success: bool) -> None:
        """Update the transfer circuit-breaker after a transfer attempt.

        success=True  -> reset the consecutive-failure counter to 0.
        success=False -> increment it; once it reaches TRANSFER_FAILURE_LIMIT,
                         stamp tripped_at so the backoff window starts.
        """
        breakers = self._state.setdefault("transfer_breakers", {})
        info = breakers.setdefault(
            ex_name, {"consecutive_failures": 0, "tripped_at": 0})
        if success:
            info["consecutive_failures"] = 0
            return
        info["consecutive_failures"] = info.get("consecutive_failures", 0) + 1
        if info["consecutive_failures"] >= TRANSFER_FAILURE_LIMIT:
            info["tripped_at"] = time.time()

    def _record_action(self, action: dict, success: bool):
        """Record allocation action to history."""
        entry = {**action, "success": success, "timestamp": time.time()}
        history = self._state.setdefault("allocation_history", [])
        history.append(entry)
        if len(history) > 200:
            self._state["allocation_history"] = history[-100:]

    def _emit_recommendation(self, action: dict) -> None:
        """Append an allocation recommendation to the jsonl audit log.

        Never touches an exchange. The owner / downstream analytics can read
        this file to see what the allocator *would* have done in LIVE mode.
        """
        entry = {**action, "ts": time.time(), "executed": False}
        try:
            RECOMMENDATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with RECOMMENDATIONS_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning(f"[CapAlloc] Could not write recommendation: {e}")

    def _save_state(self):
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(
                json.dumps(self._state, indent=2, default=str),
                encoding="utf-8")
        except Exception as e:
            logger.debug(f"[CapAlloc] Save error: {e}")

    def _load_state(self) -> dict:
        try:
            if STATE_FILE.exists():
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                logger.info(f"[CapAlloc] Loaded state: "
                            f"{len(data.get('allocation_history', []))} history entries")
                return data
        except Exception as e:
            logger.debug(f"[CapAlloc] Load error: {e}")
        return {
            "last_cycle": 0,
            "allocation_history": [],
            "baselines": {},
            "transfer_breakers": {},
        }
