"""
core/engine/portfolio_state.py — BotEngine _PortfolioStateMixin mixin (Phase D5).
"""

from core.engine.helpers import *  # noqa: F403
from core.engine.helpers import _UNIFIED_EXCHANGES, _deployable_total


class _PortfolioStateMixin:
    def _build_strategy_pool(self):
        """Build lightweight pool — only DCA + rebalance (scheduled independently).
        All entry/exit decisions now come from Claude portfolio cycle."""
        kwargs = dict(order_manager=self.order_mgr, risk_manager=self.risk)
        self.pool = {}
        for name, mod_cls, mtype in [
            ("dca_spot",  ("strategies.dca_strategy", "DCAStrategy"),         "spot"),
            ("rebalance", ("strategies.rebalancing",  "RebalancingStrategy"), "spot"),
        ]:
            try:
                mod = __import__(mod_cls[0], fromlist=[mod_cls[1]])
                self.pool[name] = getattr(mod, mod_cls[1])(**kwargs, market_type=mtype)
            except (ImportError, AttributeError):
                pass
        logger.info(f"[Engine] Strategy pool (DCA/rebal only): {list(self.pool.keys())}")

    def _resolve_pairs(self) -> dict:
        if TRADING_MODE == "portfolio":
            logger.info("[Engine] Portfolio mode — scanning wallets...")
            import portfolio_scanner as ps
            ps.MIN_VALUE_USD = PORTFOLIO_MIN_VALUE_USD
            pairs = ps.build_portfolio_pairs(self.active_exchanges)
            logger.info("[Engine] Scan complete.")
            return pairs

        if TRADING_MODE == "all":
            logger.info("[Engine] ALL mode — scanning everything available...")
            return self._resolve_all_mode_pairs()

        logger.info("[Engine] USDT-only mode — using configured pairs.")
        return TRADING_PAIRS

    def _resolve_all_mode_pairs(self) -> dict:
        """
        ALL mode: Aggressive discovery of every tradeable market.
        1. Start with configured TRADING_PAIRS as base
        2. Scan wallet holdings (like portfolio mode)
        3. Run expanded pair discovery (higher limits, all asset classes)
        4. Merge everything — maximum opportunity surface
        """
        # Start with configured base pairs
        pairs = {}
        for ex_name, type_dict in TRADING_PAIRS.items():
            pairs[ex_name] = {
                "spot":    list(type_dict.get("spot", [])),
                "futures": list(type_dict.get("futures", [])),
            }

        # Layer 1: Scan wallet holdings (portfolio scan)
        try:
            import portfolio_scanner as ps
            ps.MIN_VALUE_USD = 1.0  # Lower threshold — scan everything
            for ex_name, exchange in self.active_exchanges.items():
                if ex_name not in pairs:
                    pairs[ex_name] = {"spot": [], "futures": []}
                for pair in ps.scan_spot_holdings(exchange):
                    if pair not in pairs[ex_name]["spot"]:
                        pairs[ex_name]["spot"].append(pair)
                for pair in ps.scan_futures_holdings(exchange):
                    if pair not in pairs[ex_name]["futures"]:
                        pairs[ex_name]["futures"].append(pair)
        except Exception as e:
            logger.debug(f"[Engine] ALL mode wallet scan: {e}")

        # Layer 2: Aggressive pair discovery (expanded limits)
        try:
            from core.pair_discovery import discover_all_mode
            discovered = discover_all_mode(self.active_exchanges)
            for ex_name, disc_pairs in discovered.items():
                if ex_name not in pairs:
                    pairs[ex_name] = {"spot": [], "futures": []}
                for mtype in ("spot", "futures"):
                    for sym in disc_pairs.get(mtype, []):
                        if sym not in pairs[ex_name][mtype]:
                            pairs[ex_name][mtype].append(sym)
        except Exception as e:
            logger.debug(f"[Engine] ALL mode discovery: {e}")

        total = sum(
            len(p.get("spot", [])) + len(p.get("futures", []))
            for p in pairs.values()
        )
        logger.info(
            f"[Engine] ALL mode: {total} total pairs across "
            f"{len(pairs)} exchanges")
        for ex_name, type_dict in pairs.items():
            logger.info(
                f"[Engine]   {ex_name.upper()}: "
                f"{len(type_dict['spot'])} spot + "
                f"{len(type_dict['futures'])} futures")

        return pairs

    def _rescan_portfolio(self):
        if TRADING_MODE not in ("portfolio", "all"):
            return
        logger.info("[Engine] Re-scanning portfolio...")
        import portfolio_scanner as ps
        ps.MIN_VALUE_USD = PORTFOLIO_MIN_VALUE_USD
        new_pairs = ps.build_portfolio_pairs(self.active_exchanges)
        added = []
        for ex_name, type_dict in new_pairs.items():
            old = self.current_pairs.get(ex_name, {"spot": [], "futures": []})
            for mtype in ("spot", "futures"):
                for sym in type_dict.get(mtype, []):
                    if sym not in old.get(mtype, []):
                        added.append((ex_name, mtype, sym))
        if added:
            logger.info(f"[Engine] New holdings: {added}")
            self.current_pairs = new_pairs
        else:
            logger.info("[Engine] No new holdings.")

    def _log_balances(self):
        logger.info("[Engine] Fetching balances...")
        # Two parallel totals (2026-05-02 fix):
        #   total        = sum of FREE margin (used for sizing, conservative)
        #   total_equity = sum of WALLET BALANCE (used for drawdown tracking,
        #                  stable across position open/close margin shuffles)
        # Drawdown computed against equity prevents phantom flakes from margin
        # reallocation. Sizing uses free margin to avoid "ab not enough" rejects.
        total = 0.0
        total_equity = 0.0
        balances = {}
        equity_cache = getattr(self, "_equity_balances", {})
        if DRY_RUN:
            wallet = self.order_mgr.wallet
            for ex_name in self.active_exchanges:
                bal = wallet.balance(ex_name)
                logger.info(
                    f"[Engine] {ex_name.upper()} virtual: {bal:.4f} USDT (paper)")
                total += bal
                total_equity += bal  # paper: free == equity
                balances[ex_name] = {"spot": bal, "futures": bal}
                equity_cache[ex_name] = {"spot": bal, "futures": bal}
        else:
            for name, ex in self.active_exchanges.items():
                # Retain previous balance on fetch failure (don't default to 0 for 15 min)
                prev = self._balances.get(name, {"spot": 0.0, "futures": 0.0})
                balances[name] = {"spot": prev.get("spot", 0.0), "futures": prev.get("futures", 0.0)}
                prev_eq = equity_cache.get(name, {"spot": 0.0, "futures": 0.0})
                eq_now = {"spot": prev_eq.get("spot", 0.0), "futures": prev_eq.get("futures", 0.0)}
                if name in _UNIFIED_EXCHANGES:
                    # Bybit: single unified account — fetch once only
                    try:
                        bal  = ex.fetch_balance("spot")
                        usdt = self._extract_usdt(bal, name)
                        usdt_eq = self._extract_usdt_equity(bal, name)
                        if usdt > 0:
                            logger.info(f"[Engine] {name.upper()} unified: free=${usdt:.2f} equity=${usdt_eq:.2f}")
                            total += usdt
                            balances[name] = {"spot": usdt, "futures": usdt}
                        else:
                            total += balances[name]["spot"]  # Use retained balance
                        if usdt_eq > 0:
                            total_equity += usdt_eq
                            eq_now = {"spot": usdt_eq, "futures": 0.0}
                        else:
                            total_equity += eq_now["spot"]  # retained equity
                    except Exception as e:
                        logger.debug(f"[Engine] {name} balance: {e}")
                        total += balances[name]["spot"]  # Use retained balance
                        total_equity += eq_now["spot"]
                else:
                    for mtype in ("spot", "futures"):
                        try:
                            bal  = ex.fetch_balance(mtype)
                            usdt = self._extract_usdt(bal, name)
                            usdt_eq = self._extract_usdt_equity(bal, name)
                            if usdt > 0:
                                balances[name][mtype] = usdt
                                logger.info(
                                    f"[Engine] {name.upper()} {mtype}: free=${usdt:.2f} equity=${usdt_eq:.2f}")
                            total += balances[name][mtype]
                            if usdt_eq > 0:
                                eq_now[mtype] = usdt_eq
                            total_equity += eq_now[mtype]
                        except Exception as e:
                            logger.debug(f"[Engine] {name} balance: {e}")
                            total += balances[name][mtype]  # Use retained balance
                            total_equity += eq_now[mtype]
                equity_cache[name] = eq_now
        self._balances = balances
        self._equity_balances = equity_cache
        if total > 0 or total_equity > 0:
            # Drawdown tracker reads EQUITY (stable wallet balance).
            # Sizing reads FREE via self._balances (conservative).
            equity_for_risk = total_equity if total_equity > 0 else total
            if not getattr(self, '_balance_initialized', False):
                self.risk.set_start_balance(equity_for_risk)
                self._balance_initialized = True
            else:
                self.risk.update_current_balance(equity_for_risk)
            logger.info(
                f"[Engine] Total USDT: free=${total:.2f} equity=${total_equity:.2f} "
                f"(drawdown tracked against equity)")
        else:
            logger.warning(
                "[Engine] Could not read balance — "
                "ensure 'Enable Reading' is on in your exchange API settings.")

    def _extract_usdt(self, bal: dict, exchange_name: str = "") -> float:
        """
        Extract USDT balance from a ccxt fetch_balance() response.

        Bybit Unified Account uses multiple balance fields that are NOT
        interchangeable for order sizing:
          - totalEquity            = wallet + unrealized PnL + ALL collateral
                                     (BTC/ETH posted as margin, locked margin
                                     on open positions). This is display-only
                                     and NOT spendable as fresh USDT margin.
          - totalWalletBalance     = deposited balance excluding unrealized PnL
          - totalAvailableBalance  = free USDT-equivalent margin for NEW orders
                                     ← this is what sizing must use
          - per-coin availableToWithdraw = free USDT (excludes locked margin)

        2026-04-12 FIX (Bybit 110007 "ab not enough"):
        Previously preferred totalEquity → bot thought it had $700 available
        when only $400 was actually free, sized trades on $700 and Bybit
        rejected them. Now we try totalAvailableBalance first, then the
        per-coin USDT availableToWithdraw, and only fall back to
        totalEquity as a last-resort display value.
        """
        if not bal:
            return 0.0

        ex = exchange_name.lower() if exchange_name else ""

        # ── Bybit: Use totalAvailableBalance for sizing, NOT totalEquity
        if ex == "bybit":
            try:
                lst = bal.get("info", {}).get("result", {}).get("list", [])
                if lst and isinstance(lst, list):
                    acct = lst[0] if lst else {}
                    # Priority 1: free margin available for new orders
                    for field in ("totalAvailableBalance",
                                  "totalMarginBalance",
                                  "totalWalletBalance"):
                        val = acct.get(field)
                        if val is not None:
                            try:
                                v = float(val)
                                if v > 0:
                                    return v
                            except (TypeError, ValueError):
                                pass
                    # Priority 2: per-coin USDT free balance
                    coins = acct.get("coin", [])
                    if isinstance(coins, list):
                        for c in coins:
                            if c.get("coin") == "USDT":
                                for f2 in ("availableToWithdraw",
                                           "availableBalance",
                                           "walletBalance"):
                                    val2 = c.get(f2)
                                    if val2 is not None:
                                        try:
                                            v2 = float(val2)
                                            if v2 > 0:
                                                return v2
                                        except (TypeError, ValueError):
                                            pass
                    # Priority 3 (last resort): totalEquity — display only,
                    # NOT reliable for sizing but better than returning 0.
                    te = acct.get("totalEquity")
                    if te is not None:
                        try:
                            v = float(te)
                            if v > 0:
                                logger.warning(
                                    "[Bybit] _extract_usdt falling back to "
                                    "totalEquity — available margin fields "
                                    "missing. Sizing may be over-estimated.")
                                return v
                        except (TypeError, ValueError):
                            pass
            except Exception:
                pass
            # Fallback: ccxt parsed fields — prefer free over total
            usdt = bal.get("USDT") or {}
            if isinstance(usdt, dict):
                for key in ("free", "total"):
                    val = usdt.get(key)
                    if val is not None:
                        try:
                            v = float(val)
                            if v > 0:
                                return v
                        except (TypeError, ValueError):
                            pass
            free_d = bal.get("free") or {}
            if isinstance(free_d, dict):
                val = free_d.get("USDT")
                if val is not None:
                    try:
                        v = float(val)
                        if v > 0:
                            return v
                    except (TypeError, ValueError):
                        pass
            total_d = bal.get("total") or {}
            if isinstance(total_d, dict):
                val = total_d.get("USDT")
                if val is not None:
                    try:
                        v = float(val)
                        if v > 0:
                            return v
                    except (TypeError, ValueError):
                        pass
            return 0.0

        # ── Standard ccxt (Binance, Bitget) ────────────────────────────
        usdt = bal.get("USDT")
        if isinstance(usdt, dict):
            for key in ("free", "total"):
                val = usdt.get(key)
                if val is not None:
                    try:
                        v = float(val)
                        if v > 0:
                            return v
                    except (TypeError, ValueError):
                        pass
        free = bal.get("free", {})
        if isinstance(free, dict) and free.get("USDT"):
            try:
                return float(free["USDT"])
            except (TypeError, ValueError):
                pass
        total = bal.get("total", {})
        if isinstance(total, dict) and total.get("USDT"):
            try:
                return float(total["USDT"])
            except (TypeError, ValueError):
                pass
        return 0.0

    def _extract_usdt_equity(self, bal: dict, exchange_name: str = "") -> float:
        """Return wallet equity (free + locked margin) for drawdown tracking.

        Different from `_extract_usdt` which returns FREE balance for sizing.

        Why a separate function:
          Drawdown should track stable wallet equity, not free margin which
          swings violently as positions open/close (margin gets locked/freed).
          Using free margin for drawdown tracking causes phantom flake-rejects
          and false halts every time the bot opens or closes a position.

          Pre-fix (2026-05-02): bot used `_extract_usdt` for both sizing and
          drawdown. Bybit returned $50 free + $157 locked = $207 wallet, but
          bot tracked drawdown against the $50 free number — which fluctuated
          with margin allocation, not P&L. Result: 53 phantom flake rejections
          per day from margin-locking noise misinterpreted as balance drops.

        Field priority:
          - Bybit: `totalWalletBalance` (deposited + closed PnL, excludes
                   unrealized — stable across position state changes).
                   Falls back to `totalMarginBalance`, then 'total' from
                   ccxt's parsed view.
          - Other: ccxt's `total` field (free + used) — wallet balance.
                   Falls back to `free` if total missing (degraded but safe).
        """
        if not bal:
            return 0.0
        ex = exchange_name.lower() if exchange_name else ""

        if ex == "bybit":
            try:
                lst = bal.get("info", {}).get("result", {}).get("list", [])
                if lst and isinstance(lst, list):
                    acct = lst[0] if lst else {}
                    # totalWalletBalance excludes unrealized PnL — stable across
                    # margin allocation. totalMarginBalance similar fallback.
                    for field in ("totalWalletBalance", "totalMarginBalance"):
                        val = acct.get(field)
                        if val is not None:
                            try:
                                v = float(val)
                                if v > 0:
                                    return v
                            except (TypeError, ValueError):
                                pass
                    # Per-coin walletBalance for USDT — same idea per-coin
                    coins = acct.get("coin", [])
                    if isinstance(coins, list):
                        for c in coins:
                            if c.get("coin") == "USDT":
                                val2 = c.get("walletBalance")
                                if val2 is not None:
                                    try:
                                        v2 = float(val2)
                                        if v2 > 0:
                                            return v2
                                    except (TypeError, ValueError):
                                        pass
            except Exception:
                pass

        # Standard ccxt: prefer 'total' (wallet = free + used) over 'free'
        usdt = bal.get("USDT")
        if isinstance(usdt, dict):
            for key in ("total", "free"):
                val = usdt.get(key)
                if val is not None:
                    try:
                        v = float(val)
                        if v > 0:
                            return v
                    except (TypeError, ValueError):
                        pass
        total_d = bal.get("total", {})
        if isinstance(total_d, dict) and total_d.get("USDT"):
            try:
                return float(total_d["USDT"])
            except (TypeError, ValueError):
                pass
        free_d = bal.get("free", {})
        if isinstance(free_d, dict) and free_d.get("USDT"):
            try:
                return float(free_d["USDT"])
            except (TypeError, ValueError):
                pass
        return 0.0

    def _collect_all_coins(self) -> list:
        """Collect all unique base assets from TRADING_PAIRS."""
        all_coins = set()
        for pairs in self.current_pairs.values():
            for sym in pairs.get("spot", []) + pairs.get("futures", []):
                base = sym.split("/")[0].split(":")[0]
                all_coins.add(base)
        # Also add coins from open positions
        for p in self.tracker.get_open():
            base = p.symbol.split("/")[0].split(":")[0]
            all_coins.add(base)
        # Prioritize major coins
        _priority = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE",
                     "XAU", "ADA", "AVAX", "LINK", "DOT"}
        priority = sorted(all_coins & _priority)
        others = sorted(all_coins - _priority)
        result = (priority + others)[:40]
        # 2026-06-02: always include the analysis-only research instruments
        # (commodity/equity perps) beyond the cap so they are fetched + scored +
        # warehoused. They are NOT a tradeable universe: pair_discovery rejects
        # them as tradfi_asset/disabled_asset, AccBand skips ALLOW, and the
        # directional spec has no BZ/CL routes. is_analysis_only() in
        # _execute_open is a redundant choke (ANALYSIS_ONLY_ENFORCED default
        # OFF) — do not read a False return as "TradFi is on".
        from config import ANALYSIS_ONLY_BASES
        for _b in sorted(ANALYSIS_ONLY_BASES):
            if _b not in result:
                result.append(_b)
        return result

    def _build_position_snapshot(self) -> list:
        """Build snapshot of all open positions for Claude."""
        result = []
        for p in self.tracker.get_open():
            current_price = 0
            for ex_name, exchange in self.active_exchanges.items():
                if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                    try:
                        ticker = exchange.fetch_ticker(p.symbol, p.market_type)
                        current_price = float(ticker.get("last", 0) or 0)
                    except Exception:
                        pass
                    break
            if not current_price:
                # Use entry price as fallback so position is never invisible
                current_price = p.entry_price
            lev = max(1, getattr(p, "leverage", 1) or 1)
            if p.side == "buy":
                pnl_pct = (current_price - p.entry_price) / p.entry_price * 100 * lev
            else:
                pnl_pct = (p.entry_price - current_price) / p.entry_price * 100 * lev
            result.append({
                "id": p.id,
                "symbol": p.symbol,
                "side": p.side,
                "entry_price": p.entry_price,
                "current_price": current_price,
                "pnl_pct": round(pnl_pct, 2),
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit,
                "age_min": p.duration_minutes,
                "exchange": p.exchange,
                "market_type": p.market_type,
                "leverage": p.leverage,
                "strategy": getattr(p, "strategy", ""),
            })
        return result

    def _build_risk_envelope(self) -> dict:
        """Build risk constraints for Claude, including the active mechanism gates.

        Exposing gates to Claude means it stops proposing trades that will
        be blocked downstream — tighter loop, fewer wasted cycles.
        """
        from config import (
            ALLOWED_HOURS_UTC,
            BLACKLIST_HARD,
            BLOCKED_HOURS_UTC,
            LEVERAGE_TIERS,
            MAX_LOSS_PER_TRADE_PCT,
            PEAK_HOURS_UTC,
            SHORTS_REQUIRE_BTC_BEAR,
            WHITELIST_SYMBOLS,
        )

        total_open = self.tracker.count_open()
        max_new = max(0, RISK.get("max_open_positions", 8) - total_open)

        total_bal = _deployable_total(self._balances)

        daily_loss_pct = 0
        if total_bal > 0:
            daily_loss_pct = abs(min(0, self.risk.daily_pnl)) / total_bal

        dd_pct = RISK.get("max_drawdown_pct", 0.25)

        # Live mechanism state — fed to Claude so its proposals respect gates
        hour = self._current_utc_hour()
        hour_class = self._classify_hour(hour)
        btc_trend = self._get_btc_trend()

        # Effective blacklist = static ∪ dynamic (AutoMutator)
        dyn_bl = (self.auto_mutator.get_effective_blacklist()
                  if self.auto_mutator else set())
        effective_bl = sorted(set(BLACKLIST_HARD) | dyn_bl)

        # Throttle state
        throttle = self._consec_loss_state()
        throttle_paused = throttle["pause_until"] > time.time()
        tier_cap = throttle.get("tier_cap") or (
            "STANDARD" if (self.auto_mutator
                           and self.auto_mutator.get_leverage_cap() is not None)
            else None)

        mutator_snap = (self.auto_mutator.snapshot()
                        if self.auto_mutator else {})

        return {
            "max_new_positions": max_new,
            "total_open": total_open,
            "daily_loss_pct": round(daily_loss_pct, 4),
            "drawdown_headroom_pct": round((dd_pct - daily_loss_pct) * 100, 1),
            "total_balance": round(total_bal, 2),
            # Mechanism state — helps Claude propose better trades
            "hour_utc": hour,
            "hour_class": hour_class,           # peak|allowed|warmup|blocked
            "btc_4h_trend": btc_trend,          # bull|bear|neutral
            "blacklist": effective_bl,
            "whitelist": sorted(WHITELIST_SYMBOLS),
            "allowed_hours_utc": sorted(ALLOWED_HOURS_UTC),
            "peak_hours_utc": sorted(PEAK_HOURS_UTC),
            "blocked_hours_utc": sorted(BLOCKED_HOURS_UTC),
            "shorts_require_btc_bear": SHORTS_REQUIRE_BTC_BEAR,
            "shorts_allowed_now": btc_trend == "bear",
            "max_loss_per_trade_pct": MAX_LOSS_PER_TRADE_PCT,
            "leverage_tiers": {
                name: {
                    "leverage": t["leverage"],
                    "min_confidence": t["min_confidence"],
                    "sl_pct": t["sl_pct"],
                    "tp_pct": t["tp_pct"],
                    "requires_whitelist": t["requires_whitelist"],
                    "requires_peak_hour": t["requires_peak_hour"],
                    "requires_btc_aligned": t["requires_btc_aligned"],
                }
                for name, t in LEVERAGE_TIERS.items()
            },
            "consec_loss_throttled": throttle_paused,
            "effective_tier_cap": tier_cap,     # None | "STANDARD"
            "auto_mutations": mutator_snap,
        }

    def _get_recent_trades(self, n: int = 20) -> list:
        """Get last N closed trades with P&L for accuracy feedback."""
        closed = getattr(self.tracker, '_closed', [])[-n:]
        result = []
        for t in closed:
            result.append({
                "symbol": t.symbol,
                "side": t.side,
                "pnl": round(getattr(t, "pnl", 0) or 0, 4),
                "strategy": getattr(t, "strategy", ""),
                "exchange": t.exchange,
            })
        return result

    # ==============================================================
    # HIGH-WR MECHANISM GATES (2026-04-11 rewrite)
    # Hour gate + blacklist + BTC macro trend + leverage tier selector
    # + $-loss-per-trade clamp + consecutive-loss throttle
    # ==============================================================

    def _current_utc_hour(self) -> int:
        return datetime.now(timezone.utc).hour

    # Hour-gate evidence file (refreshed by scripts/refresh_hour_gates.py).
    # Cached for 5 min in-process so the file is read at most ~12×/hour.
    _HOUR_GATE_PATH = Path("data/hour_gate_evidence.json")
    _HOUR_GATE_TTL_SEC = 300
    _HOUR_GATE_MAX_AGE_DAYS = 14

    def _load_hour_gate_evidence(self) -> dict:
        """Read hour_gate_evidence.json → {"blocked": set, "profitable": set}.

        Empty sets when:
        - file is missing
        - file is older than _HOUR_GATE_MAX_AGE_DAYS (stale evidence is ignored)
        - file is malformed
        Cached for _HOUR_GATE_TTL_SEC so disk hits are bounded.
        """
        now = time.time()
        if (now - getattr(self, "_hour_gate_loaded_at", 0)) < self._HOUR_GATE_TTL_SEC:
            return getattr(self, "_hour_gate_cached",
                           {"blocked": set(), "profitable": set()})

        ev: dict = {"blocked": set(), "profitable": set()}
        try:
            p = self._HOUR_GATE_PATH
            if p.exists():
                age_days = (now - p.stat().st_mtime) / 86400
                if age_days <= self._HOUR_GATE_MAX_AGE_DAYS:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    for k in ("blocked", "profitable"):
                        raw = data.get(k) or []
                        ev[k] = {int(h) for h in raw if isinstance(h, (int, float))}
        except Exception as e:
            logger.debug(f"[HourGate] evidence load skipped: {e}")
            ev = {"blocked": set(), "profitable": set()}

        self._hour_gate_cached = ev
        self._hour_gate_loaded_at = now
        return ev

    def _load_dynamic_blocked_hours(self) -> set:
        """Back-compat shim: the `blocked` set from the evidence file."""
        return self._load_hour_gate_evidence()["blocked"]

    def _classify_hour(self, hour: int) -> str:
        """Return 'peak' | 'allowed' | 'warmup' | 'blocked'.

        Combines the static config sets with `data/hour_gate_evidence.json`
        which is refreshed weekly by `scripts/refresh_hour_gates.py`.

        2026-06-11 (owner: "Trade only in those hours where its profitable"):
        profit-only mode — when HOUR_GATE_PROFIT_ONLY is on and the evidence
        file is fresh with a non-empty `profitable` list, every hour NOT on
        that list classifies as 'blocked'. Fail-open on missing/stale/empty
        evidence (an empty list is indistinguishable from insufficient data;
        a silently-halted bot is worse than an ungated one). Supersedes the
        2026-05-27 decision that disabled the dynamic hour gate.
        """
        from config import (
            BLOCKED_HOURS_UTC,
            HOUR_GATE_PROFIT_ONLY,
            PEAK_HOURS_UTC,
            WARMUP_HOURS_UTC,
        )
        if hour in BLOCKED_HOURS_UTC:
            return "blocked"
        if HOUR_GATE_PROFIT_ONLY:
            _prof = self._load_hour_gate_evidence().get("profitable")
            if _prof and hour not in _prof:
                return "blocked"
        if hour in PEAK_HOURS_UTC:
            return "peak"
        if hour in WARMUP_HOURS_UTC:
            return "warmup"
        # Default: allowed (any hour not explicitly blocked/warmup/peak)
        return "allowed"

    def _get_btc_trend(self) -> str:
        """
        Return 'bull' | 'bear' | 'neutral' from BTC 4h EMA200 slope.
        Cached 15 minutes to avoid repeated API calls.

        Rules:
          - close > EMA200 AND EMA200 5-bar slope > +0.2% → bull
          - close < EMA200 AND EMA200 5-bar slope < −0.2% → bear
          - otherwise → neutral
        """
        cache_ttl = 900  # 15 minutes
        now = time.time()
        if getattr(self, '_btc_trend_cached_at', 0) + cache_ttl > now:
            return getattr(self, '_btc_trend_cached', "neutral")

        try:
            import pandas as pd

            from config import BTC_TREND_EMA_PERIOD, BTC_TREND_TIMEFRAME
            from exchanges.base import closed_ohlcv

            exchange = (self.active_exchanges.get('binance')
                        or next(iter(self.active_exchanges.values()), None))
            if not exchange:
                return "neutral"

            raw = exchange.fetch_ohlcv(
                "BTC/USDT", BTC_TREND_TIMEFRAME,
                BTC_TREND_EMA_PERIOD + 20, "spot")
            raw = closed_ohlcv(raw, BTC_TREND_TIMEFRAME, now_s=now)
            if not raw or len(raw) < BTC_TREND_EMA_PERIOD + 5:
                return "neutral"

            df = pd.DataFrame(raw, columns=["ts", "o", "h", "l", "c", "v"])
            ema = df["c"].ewm(span=BTC_TREND_EMA_PERIOD, adjust=False).mean()
            last_ema = float(ema.iloc[-1])
            past_ema = float(ema.iloc[-6])
            close = float(df["c"].iloc[-1])

            slope_pct = (last_ema - past_ema) / past_ema if past_ema > 0 else 0.0
            above_ema = close > last_ema

            if above_ema and slope_pct > 0.002:
                trend = "bull"
            elif not above_ema and slope_pct < -0.002:
                trend = "bear"
            else:
                trend = "neutral"

            self._btc_trend_cached = trend
            self._btc_trend_cached_at = now
            logger.info(
                f"[BTC-Trend] {trend} "
                f"(slope={slope_pct*100:+.2f}%, close_above_ema={above_ema})")
            # Persist for the dashboard's BTC macro panel (dashboard.py reads
            # data/btc_trend.json; nothing wrote it before -> blank panel). Local
            # file, atomic, no trade-logic impact. Write the trend string too so the
            # dashboard's BULL/BEAR matches the bot's actual +/-0.2% logic, not just
            # the raw slope sign.
            try:
                _btc_p = Path("data/btc_trend.json")
                _btc_p.parent.mkdir(parents=True, exist_ok=True)
                _btc_tmp = _btc_p.with_name(_btc_p.name + ".tmp")
                _btc_tmp.write_text(json.dumps({
                    "trend": trend,
                    "ema200_slope": slope_pct,
                    "close_above_ema": above_ema,
                    "ts": now,
                }), encoding="utf-8")
                _btc_tmp.replace(_btc_p)
            except Exception as _btc_e:
                logger.debug(f"[BTC-Trend] persist skipped: {_btc_e}")
            return trend
        except Exception as e:
            logger.debug(f"[BTC-Trend] error: {e}")
            return "neutral"

