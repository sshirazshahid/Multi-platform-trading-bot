"""
core/multi_profile_runner.py
Multi-Profile + Claude AI + Email Reports + Cross-Exchange Arbitrage

Exchanges   : Binance | Bybit | Bitget  (all connected ones)
Profiles    : Conservative | Moderate | Aggressive  ($100 each, simultaneous)
Arbitrage   : Scans every exchange pair for spread opportunities,
              applies 8 institutional filters + Claude AI confidence
Email       : Daily at REPORT_EMAIL_HOUR UTC; session-end report on Ctrl+C;
              halt alerts sent immediately
"""

from __future__ import annotations

import json
import math
import signal
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger

# ── Profile definitions ───────────────────────────────────────────────

PROFILES = {
    # GROWTH MODE: $100 → $1000 journey on each exchange
    # Data insight: Short WR 63-83%, Long WR 0% → all profiles favour shorts
    "conservative": {
        "max_position_pct":    0.05,     # 5% (was 2% — too small for $100)
        "max_open_positions":  5,        # More opportunities
        "max_daily_loss_pct":  0.025,    # 2.5% daily loss limit
        "default_stop_loss":   0.015,    # 1.5% SL
        "default_take_profit": 0.040,    # 4.0% TP (R:R 2.7:1)
        "futures_max_leverage":3,
        "default_leverage":    3,        # 3x (was 2x — need growth)
        "trailing_activation": 0.008,    # Activate before TP to ride past it
        "trailing_distance":   0.007,
        "max_drawdown_pct":    0.15,     # 15% (was 10% — too tight for growth)
    },
    "moderate": {
        "max_position_pct":    0.06,     # 6% — balanced growth
        "max_open_positions":  6,
        "max_daily_loss_pct":  0.030,
        "default_stop_loss":   0.015,
        "default_take_profit": 0.050,    # 5.0% TP (R:R 3.3:1)
        "futures_max_leverage":5,
        "default_leverage":    3,
        "trailing_activation": 0.008,
        "trailing_distance":   0.007,
        "max_drawdown_pct":    0.20,
    },
    "aggressive": {
        "max_position_pct":    0.08,     # 8% — max growth
        "max_open_positions":  8,
        "max_daily_loss_pct":  0.040,    # 4% daily loss
        "default_stop_loss":   0.020,
        "default_take_profit": 0.060,    # 6.0% TP (R:R 3:1)
        "futures_max_leverage":7,        # 7x (was 10x — too risky)
        "default_leverage":    5,
        "trailing_activation": 0.006,    # Aggressive: earliest activation to catch peaks
        "trailing_distance":   0.006,
        "max_drawdown_pct":    0.25,     # 25% — aggressive can take more risk
    },
}

COMPARISON_FILE = Path("data/profiles/comparison.json")
ARB_FILE        = Path("data/arbitrage/opportunities.json")
START_BALANCE   = 100.0    # $100 USDT paper wallet per exchange per profile
BASE_MIN_CONF   = 0.55


# ══════════════════════════════════════════════════════════════════════
# Isolated risk / wallet / tracker per profile
# ══════════════════════════════════════════════════════════════════════

class ProfileRiskManager:

    def __init__(self, name: str, s: dict):
        self.profile_name       = name
        self.max_position_pct   = s["max_position_pct"]
        self.max_open_positions = s["max_open_positions"]
        self.max_daily_loss_pct = s["max_daily_loss_pct"]
        self.default_sl         = s["default_stop_loss"]
        self.default_tp         = s["default_take_profit"]
        self.futures_max_lev    = s["futures_max_leverage"]
        self.default_leverage   = s["default_leverage"]
        self.max_drawdown_pct   = s.get("max_drawdown_pct", 0.10)

        from datetime import date
        self._daily_pnl     = 0.0
        self._start_balance = START_BALANCE
        self._peak_balance  = START_BALANCE
        self._trading_day   = date.today()
        self._halted        = False
        self._halt_reason   = ""

    def can_trade(self, open_count: int) -> bool:
        if self._halted:
            # SMART RECOVERY: check if we should auto-resume
            import time as _time
            if not hasattr(self, '_halt_time'): self._halt_time = 0.0
            if not hasattr(self, '_recent_results'): self._recent_results = []
            elapsed = (_time.time() - self._halt_time) / 60
            # Daily loss resets at midnight
            from datetime import date
            if "daily" in self._halt_reason and date.today() != self._trading_day:
                self._halted = False; self._halt_reason = ""
            # Drawdown: resume after 30min cooldown + winning streak or positive PnL
            elif "drawdown" in self._halt_reason and elapsed >= 30:
                recent = self._recent_results[-5:] if len(self._recent_results) >= 5 else []
                recent_wr = (sum(1 for r in recent if r) / len(recent) * 100) if recent else 0
                if recent_wr >= 60 or self._daily_pnl > 0:
                    from loguru import logger
                    logger.info(f"[{self.profile_name}] AUTO-RESUMED: WR={recent_wr:.0f}% dailyPnL={self._daily_pnl:+.4f}")
                    self._halted = False; self._halt_reason = ""
            if self._halted:
                return False
        return open_count < self.max_open_positions

    def calculate_position_size(self, balance: float, price: float,
                                  leverage: int = 1) -> float:
        if balance <= 0 or price <= 0: return 0.0
        return balance * self.max_position_pct * leverage / price

    def validate_leverage(self, req: int) -> int:
        return min(req, self.futures_max_lev)

    def get_sl_tp(self, entry: float, side: str, atr: float = None):
        sl_d = (atr * 1.0) if (atr and atr > 0) else (entry * self.default_sl)
        tp_d = (atr * 3.0) if (atr and atr > 0) else (entry * self.default_tp)
        if side == "buy":
            return round(entry - sl_d, 8), round(entry + tp_d, 8)
        return round(entry + sl_d, 8), round(entry - tp_d, 8)

    def record_trade_pnl(self, pnl: float, wallet_balance: float,
                          is_win: bool = None, pnl_pct: float = 0.0):
        """FIXED: wallet_balance is actual wallet total (not trade notional).
        FIXED: Smart halt — pauses then auto-resumes when profitable."""
        from datetime import date
        today = date.today()
        if today != self._trading_day:
            self._daily_pnl   = 0.0
            self._trading_day = today
            if self._halted and "daily" in self._halt_reason:
                self._halted = False; self._halt_reason = ""
        self._daily_pnl += pnl
        # Track recent results for smart recovery
        if is_win is not None:
            if not hasattr(self, '_recent_results'): self._recent_results = []
            self._recent_results.append(is_win)
            if len(self._recent_results) > 20: self._recent_results.pop(0)
        # FIX: Use wallet_balance directly — already includes PnL
        if wallet_balance > self._peak_balance:
            self._peak_balance = wallet_balance
        limit = self._start_balance * self.max_daily_loss_pct
        if self._daily_pnl < -limit and not self._halted:
            self._halted = True
            self._halt_reason = f"daily loss limit ({self._daily_pnl:+.4f})"
            if not hasattr(self, '_halt_time'): self._halt_time = 0.0
            self._halt_time = __import__('time').time()
        if self._peak_balance > 0:
            dd = (self._peak_balance - wallet_balance) / self._peak_balance
            if dd >= self.max_drawdown_pct and not self._halted:
                self._halted = True
                self._halt_reason = f"drawdown {dd * 100:.1f}% — will auto-resume"
                if not hasattr(self, '_halt_time'): self._halt_time = 0.0
                self._halt_time = __import__('time').time()

    def set_start_balance(self, bal: float):
        self._start_balance = bal
        if bal > self._peak_balance: self._peak_balance = bal

    def drawdown_pct(self, current: float) -> float:
        if self._peak_balance <= 0: return 0.0
        return max(0.0, (self._peak_balance - current) / self._peak_balance)

    @property
    def is_halted(self) -> bool:  return self._halted
    @property
    def halt_reason(self) -> str: return self._halt_reason
    @property
    def daily_pnl(self) -> float: return self._daily_pnl


class ProfileWallet:

    def __init__(self, name: str):
        self.name  = name
        self._path = Path(f"data/profiles/{name}/wallet.json")
        self._balances: dict[str, float] = {}
        self._load()

    def balance(self, exchange: str = "binance") -> float:
        return round(self._balances.get(exchange.lower(), START_BALANCE), 4)

    def total_balance(self) -> float:
        return round(sum(self._balances.values()), 4) if self._balances else START_BALANCE

    def on_open(self, exchange: str, size: float, price: float, fee: float) -> bool:
        ex   = exchange.lower()
        bal  = self._balances.get(ex, START_BALANCE)
        cost = size * price + fee
        if cost > bal:
            return False
        self._balances[ex] = bal - cost
        self._save(); return True

    def on_close(self, exchange: str, size: float, exit_price: float,
                 exit_fee: float, entry_price: float = 0.0,
                 gross_pnl: float = 0.0, market_type: str = "spot"):
        """FIXED: Futures returns margin + leveraged PnL, not size*exit_price."""
        ex  = exchange.lower()
        bal = self._balances.get(ex, START_BALANCE)
        if market_type == "futures" and entry_price > 0:
            proceeds = size * entry_price + gross_pnl - exit_fee
        else:
            proceeds = size * exit_price - exit_fee
        proceeds = max(0.0, proceeds)
        self._balances[ex] = bal + proceeds
        self._save()

    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"balances": self._balances,
                            "start": START_BALANCE,
                            "saved_at": time.time()}, indent=2),
                encoding="utf-8")
        except Exception as e:
            logger.debug(f"[{self.name}] wallet save: {e}")

    def _load(self):
        try:
            if self._path.exists():
                self._balances = json.loads(
                    self._path.read_text(encoding="utf-8")
                ).get("balances", {})
        except Exception:
            self._balances = {}


class ProfileTracker:

    def __init__(self, name: str):
        from core.position_tracker import PositionTracker
        self._path    = Path(f"data/profiles/{name}/positions.json")
        self._tracker = PositionTracker.__new__(PositionTracker)
        self._tracker.SAVE_PATH = self._path
        self._tracker._open   = {}
        self._tracker._closed = []
        self._tracker._load()

    def __getattr__(self, n):
        return getattr(self._tracker, n)

    @property
    def _closed(self): return self._tracker._closed
    @property
    def _open(self):   return self._tracker._open


@dataclass
class ProfileInstance:
    name:      str
    risk:      ProfileRiskManager
    wallet:    ProfileWallet
    tracker:   ProfileTracker
    executor:  object
    arb_engine: object = None
    blacklist: object = None
    cycle:     int    = 0
    errors:    int    = 0


# ══════════════════════════════════════════════════════════════════════
# MultiProfileRunner
# ══════════════════════════════════════════════════════════════════════

class MultiProfileRunner:

    SCAN_INTERVAL = 60 * 15
    ARB_INTERVAL  = 60 * 2

    def __init__(self):
        from config import DRY_RUN, TRADING_PAIRS
        from core.report_emailer import ReportEmailer
        from core.strategy_selector import StrategySelector, build_futures_map
        from exchanges import (
            BinanceClient,
            BitgetClient,
            BybitClient,
        )

        logger.info("=" * 66)
        logger.info("  MULTI-PROFILE + CLAUDE AI + ARBITRAGE + EMAIL")
        logger.info("  Spot LONG | Futures LONG+SHORT | Cross-Exchange ARB")
        logger.info("  Conservative | Moderate | Aggressive  (all simultaneous)")
        logger.info("  Exchanges: Binance | Bybit | Bitget")
        logger.info("  Paper wallet: $100 USDT per exchange per profile")
        logger.info("=" * 66)

        if not DRY_RUN:
            raise RuntimeError(
                "Multi-profile mode requires DRY_RUN=true. "
                "Set DRY_RUN=true in .env before running multi-profile.")
        self.dry_run = DRY_RUN

        self.exchanges = {
            "binance": BinanceClient(),
            "bybit":   BybitClient(),
            "bitget":  BitgetClient(),
        }
        self.active_exchanges = {
            n: ex for n, ex in self.exchanges.items()
            if getattr(ex, "_connected", False)
        }
        if not self.active_exchanges:
            raise RuntimeError(
                "No exchanges connected. "
                "Add at least one exchange's API keys to .env")
        logger.info(f"[MultiProfile] Connected: {list(self.active_exchanges.keys())}")

        self.selector      = StrategySelector()
        self.emailer       = ReportEmailer()
        self.trading_pairs = TRADING_PAIRS
        self.futures_map   = build_futures_map(TRADING_PAIRS)

        self.profiles: dict[str, ProfileInstance] = {}
        for name, settings in PROFILES.items():
            self.profiles[name] = self._build_profile(name, settings)

        # FIX: Set correct total start = $100 × num_exchanges
        _num_ex = len(self.active_exchanges)
        _total_start = START_BALANCE * _num_ex
        for _inst in self.profiles.values():
            _inst.risk.set_start_balance(_total_start)
        logger.info(f"[MultiProfile] Start: ${START_BALANCE:.0f}/ex × {_num_ex} = ${_total_start:.0f} per profile")

        from core.arbitrage_engine import ArbitrageEngine
        self.arb_engine = ArbitrageEngine(
            active_exchanges=self.active_exchanges,
            dry_run=self.dry_run,
            profile_name="multi",
        )
        for inst in self.profiles.values():
            inst.arb_engine = self.arb_engine

        self._scan_count     = 0
        self._arb_scan_count = 0
        self._halted_alerted: set[str] = set()
        self._shutdown_requested       = False

        signal.signal(signal.SIGINT,  self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        if self.emailer.is_configured():
            import os
            hour = int(os.getenv("REPORT_EMAIL_HOUR", "8"))
            logger.info(f"[MultiProfile] Email active -- daily {hour:02d}:00 UTC + session end")
        else:
            logger.info("[MultiProfile] Email disabled (GMAIL_* not set in .env)")

    def _handle_shutdown(self, signum, frame):
        logger.info("[MultiProfile] Shutdown signal — saving & emailing...")
        self._shutdown_requested = True

    # ── Build one profile ─────────────────────────────────────────────

    def _build_profile(self, name: str, settings: dict) -> ProfileInstance:
        from core.blacklist_manager import BlacklistManager
        from core.direct_executor import DirectExecutor
        from core.trailing_stop_manager import TrailingStopManager

        risk      = ProfileRiskManager(name, settings)
        wallet    = ProfileWallet(name)
        tracker   = ProfileTracker(name)
        trailing  = TrailingStopManager()
        blacklist = BlacklistManager(profile_name=name)  # FIX: isolated per-profile blacklist
        risk.set_start_balance(START_BALANCE)

        def open_position(exchange, symbol, side, market_type,
                          strategy, size, sl, tp, leverage=1, price=None):
            import uuid
            if not risk.can_trade(tracker.count_open()): return None
            if size <= 0:                                  return None
            fill_price = float(price or 0)
            if not fill_price:
                try:
                    t = exchange.fetch_ticker(symbol, market_type)
                    fill_price = float(t.get("last") or t.get("close") or 0)
                except Exception:
                    pass
            if fill_price <= 0: return None
            if market_type == "futures" and leverage > 1:
                leverage = risk.validate_leverage(leverage)
            from core.position_tracker import Position
            oid = f"DRY-{name[:3].upper()}-{uuid.uuid4().hex[:6]}"
            pos = Position(
                id=oid, exchange=exchange.name, symbol=symbol,
                side=side, market_type=market_type,
                strategy=f"{strategy}|{name}",
                entry_price=fill_price, size=size,
                stop_loss=sl, take_profit=tp,
                leverage=leverage, order_id=oid, paper_trade=self.dry_run,
            )
            if not wallet.on_open(exchange.name, size, fill_price, pos.entry_fee):
                return None
            tracker.add(pos)
            return pos

        def close_position(exchange, pos, reason, price):
            closed = tracker.close(pos.id, price, reason)
            if closed:
                wallet.on_close(
                    exchange=pos.exchange, symbol=pos.symbol,
                    side=pos.side, size=pos.size,
                    exit_price=price, entry_price=pos.entry_price,
                    exit_fee=closed.exit_fee,
                    gross_pnl=closed.gross_pnl or 0.0,
                    market_type=pos.market_type,
                    leverage=pos.leverage,
                )
                # FIX: Pass wallet total (not trade notional) for DD tracking
                risk.record_trade_pnl(
                    closed.pnl, wallet.total_balance(),
                    is_win=(closed.pnl or 0) > 0,
                    pnl_pct=closed.pnl_pct or 0.0)
                logger.info(
                    "[{}] CLOSED {} {} @ {:.6f}  net={:+.4f} bal=${:.2f}  ({})".format(
                        name.upper(),
                        "LONG" if pos.side == "buy" else "SHORT",
                        pos.symbol, price, closed.pnl,
                        wallet.total_balance(), reason))
            trailing.remove(pos.id)

        executor = DirectExecutor(
            profile_name=name,
            risk=risk, wallet=wallet, tracker=tracker,
            blacklist=blacklist, trailing=trailing,
            open_position_fn=open_position,
            close_position_fn=close_position,
        )

        inst = ProfileInstance(
            name=name, risk=risk, wallet=wallet,
            tracker=tracker, executor=executor, blacklist=blacklist,
        )
        logger.info(
            "[MultiProfile] '{}' ready | ${:.0f} paper | "
            "SL={:.1f}%  TP={:.1f}%  lev={}x  max={}pos".format(
                name, START_BALANCE,
                settings["default_stop_loss"]   * 100,
                settings["default_take_profit"] * 100,
                settings["default_leverage"],
                settings["max_open_positions"]))
        return inst

    # ── Main loop ─────────────────────────────────────────────────────

    def run(self):
        logger.info("[MultiProfile] Starting — $100 per exchange per profile")

        self._scan_all()
        self._scan_arbitrage()

        import schedule
        schedule.every(self.SCAN_INTERVAL).seconds.do(self._scan_all)
        schedule.every(self.ARB_INTERVAL).seconds.do(self._scan_arbitrage)
        schedule.every(60).minutes.do(self._log_comparison)
        schedule.every(15).minutes.do(self._check_email_schedule)

        try:
            while not self._shutdown_requested:
                schedule.run_pending()
                time.sleep(10)
        except KeyboardInterrupt:
            pass
        finally:
            logger.info("[MultiProfile] Finalising...")
            self._log_comparison()
            self._save_comparison()
            self._send_session_end_report()

    # ── Email ─────────────────────────────────────────────────────────

    def _send_session_end_report(self):
        try:
            if self.emailer.is_configured():
                logger.info("[MultiProfile] Sending session-end email report...")
                self.emailer.send_daily(force=True)
        except Exception as e:
            logger.debug(f"[MultiProfile] Session-end email: {e}")

    def _check_email_schedule(self):
        try:
            if self.emailer.should_send_daily():
                self.emailer.send_daily()
            for name, inst in self.profiles.items():
                if inst.risk.is_halted and name not in self._halted_alerted:
                    self._halted_alerted.add(name)
                    self.emailer.send_alert(
                        f"{name.upper()} Profile HALTED",
                        f"The {name.upper()} profile has been halted.\nReason: {inst.risk.halt_reason}\n\n"
                        "No new trades will be opened for this profile.\n"
                        "Auto-resets at midnight UTC if daily loss limit.")
                elif not inst.risk.is_halted:
                    self._halted_alerted.discard(name)
        except Exception as e:
            logger.debug(f"[MultiProfile] Email check: {e}")

    # ── Arbitrage scan ────────────────────────────────────────────────

    def _scan_arbitrage(self):
        if len(self.active_exchanges) < 2:
            return

        self._arb_scan_count += 1
        logger.info(f"[Arb] Scan #{self._arb_scan_count} across {len(self.active_exchanges)} exchanges")

        all_symbols = set()
        for ex_name in self.active_exchanges:
            pairs = self.trading_pairs.get(ex_name, {})
            for sym in pairs.get("spot", []):
                all_symbols.add(sym)

        symbols  = sorted(all_symbols)
        mod_inst = self.profiles.get("moderate")
        if mod_inst:
            self.arb_engine._open_fn  = mod_inst.executor._open_fn  \
                if hasattr(mod_inst.executor, "_open_fn") else None
            self.arb_engine._close_fn = mod_inst.executor._close_fn \
                if hasattr(mod_inst.executor, "_close_fn") else None

        try:
            opps    = self.arb_engine.scan_and_trade(symbols)
            summary = self.arb_engine.summary()
            if opps:
                logger.info("[Arb] {} opportunities | open={} pnl={:+.4f}".format(
                    len(opps), summary["open"], summary["total_pnl"]))
        except Exception as e:
            logger.warning(f"[Arb] Scan failed: {e}")

    # ── Strategy scan ─────────────────────────────────────────────────

    def _scan_all(self):
        self._scan_count += 1
        fg = self._fear_greed_from_cache()
        logger.info(f"[MultiProfile] ═══ Scan #{self._scan_count} | F&G={fg} | ${int(START_BALANCE)} per profile/exchange ═══")

        for ex_name, exchange in self.active_exchanges.items():
            pairs    = self.trading_pairs.get(ex_name, {})
            all_base = list(dict.fromkeys(pairs.get("spot", [])))
            if not all_base:
                continue

            logger.info(f"[MultiProfile] {ex_name} — {len(all_base)} coins (3 profiles simultaneous)")

            all_opps = self.selector.analyze_batch(
                exchange, all_base, self.futures_map)
            if not all_opps:
                logger.info(f"[MultiProfile] {ex_name} — no signals")
                continue

            logger.info(f"[MultiProfile] Signals ({ex_name}):\n{self.selector.summary_table(all_opps)}")

            eff_min = BASE_MIN_CONF

            flat_opps = sorted(
                [opp for opps in all_opps.values() for opp in opps],
                key=lambda o: o.confidence, reverse=True)

            for prof_name, inst in self.profiles.items():
                if inst.risk.is_halted:
                    logger.debug(f"[{ex_name.upper()}][{prof_name}] PAUSED — {inst.risk.halt_reason}")
                    continue

                inst.executor.check_exits(exchange)

                executed = 0
                for opp in flat_opps:
                    if opp.direction == "skip":
                        continue

                    if opp.confidence < eff_min:
                        continue

                    traded = inst.executor.execute(exchange, opp)

                    if traded:
                        executed += 1
                        inst.cycle += 1
                    time.sleep(0.05)

                bal = inst.wallet.total_balance()
                s   = inst.tracker.summary()
                logger.info(
                    "[{:<4}][{:<13}] exec={}  bal=${:.2f}  "
                    "trades={}  W={}  L={}  WR={:.1f}%  pnl={:+.4f}".format(
                        ex_name.upper(), prof_name.upper(),
                        executed, bal,
                        s["total_trades"], s["wins"], s["losses"],
                        s["win_rate"], s["total_pnl"]))

        self._save_comparison()

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _fear_greed_from_cache() -> str:
        try:
            p = Path("data/news_cache.json")
            if p.exists():
                fg = json.loads(p.read_text(encoding="utf-8")).get("fear_greed") or {}
                return str(fg.get("value", "?"))
        except Exception:
            pass
        return "?"

    def _collect_profile_stats(self):
        stats = {}
        for name, inst in self.profiles.items():
            s = inst.tracker.summary()
            stats[name] = {
                "total_trades": s["total_trades"],
                "win_rate":     s["win_rate"],
                "net_pnl":      s["total_pnl"],
                "max_drawdown": inst.risk.drawdown_pct(
                    inst.wallet.total_balance()) * 100,
            }
        return stats

    def _collect_open_positions(self):
        seen = set(); positions = []
        for inst in self.profiles.values():
            for pos in inst.tracker.get_open():
                key = (pos.symbol, pos.side, pos.market_type)
                if key not in seen:
                    seen.add(key)
                    positions.append({
                        "symbol": pos.symbol, "side": pos.side,
                        "market_type": pos.market_type,
                        "entry_price": pos.entry_price, "pnl": pos.pnl,
                    })
        return positions

    # ── Comparison ────────────────────────────────────────────────────

    def _compute_comparison(self):
        results = {}
        for name, inst in self.profiles.items():
            s   = inst.tracker.summary()
            bal = inst.wallet.total_balance()
            total_start = START_BALANCE * max(1, len(inst.wallet._balances))
            ret = (bal - total_start) / max(total_start, 1) * 100
            dd  = inst.risk.drawdown_pct(bal)
            wr  = s["win_rate"]
            closed = inst.tracker._closed
            longs  = [p for p in closed if (p.side or "") == "buy"]
            shorts = [p for p in closed if (p.side or "") == "sell"]
            spots  = [p for p in closed if (p.market_type or "") == "spot"]
            futs   = [p for p in closed if (p.market_type or "") == "futures"]

            def _wr(lst):
                if not lst: return 0.0
                return sum(1 for p in lst if (p.pnl or 0) > 0) / len(lst) * 100

            pnls   = [p.pnl or 0 for p in closed]
            sharpe = 0.0
            if len(pnls) >= 2:
                avg = sum(pnls) / len(pnls)
                std = math.sqrt(sum((x-avg)**2 for x in pnls) / len(pnls))
                sharpe = avg / std if std > 0 else 0.0

            score = (
                (wr/100)*55 + (min(ret,20)/20)*25 +
                max(0,1-dd/0.20)*15 + min(sharpe,1)*5)

            results[name] = {
                "name": name, "balance": round(bal, 4),
                "return_pct": round(ret, 2),
                "total_trades": s["total_trades"],
                "wins": s["wins"], "losses": s["losses"],
                "win_rate": round(wr, 1), "net_pnl": round(s["total_pnl"], 4),
                "fees_paid": round(s["total_fees"], 4),
                "max_drawdown": round(dd*100, 2), "sharpe": round(sharpe, 3),
                "daily_pnl": round(inst.risk.daily_pnl, 4),
                "is_halted": inst.risk.is_halted, "halt_reason": inst.risk.halt_reason,
                "score": round(score, 2), "cycles": inst.cycle,
                "longs": len(longs), "shorts": len(shorts),
                "long_wr": round(_wr(longs),1), "short_wr": round(_wr(shorts),1),
                "spot_trades": len(spots), "futures_trades": len(futs),
                "spot_wr": round(_wr(spots),1), "futures_wr": round(_wr(futs),1),
                "settings": {
                    "position_pct":  PROFILES[name]["max_position_pct"],
                    "stop_loss":     PROFILES[name]["default_stop_loss"],
                    "take_profit":   PROFILES[name]["default_take_profit"],
                    "leverage":      PROFILES[name]["default_leverage"],
                    "max_positions": PROFILES[name]["max_open_positions"],
                },
            }

        ranked = sorted(results.values(), key=lambda x: x["score"], reverse=True)
        leader = ranked[0]["name"] if ranked else "none"
        arb_summary = self.arb_engine.summary() if self.arb_engine else {}

        return {
            "updated_at":     datetime.now().isoformat(),
            "scan_count":     self._scan_count,
            "arb_scan_count": self._arb_scan_count,
            "leader":         leader,
            "profiles":       results,
            "ranked":         [r["name"] for r in ranked],
            "recommendation": self._recommendation(results, leader),
            "arbitrage":      arb_summary,
            "exchanges":      list(self.active_exchanges.keys()),
        }

    def _recommendation(self, results, leader):
        data = results.get(leader, {})
        n    = data.get("total_trades", 0)
        if n < 5:
            return "Collecting first trades. All profiles active ($100 per exchange)."
        wr = data.get("win_rate", 0); ret = data.get("return_pct", 0)
        dd = data.get("max_drawdown", 0)
        if wr >= 55 and ret > 0 and dd < 15:
            return f"READY FOR LIVE: '{leader}' WR={wr:.1f}% Return={ret:.2f}% DD={dd:.1f}%"
        return f"'{leader}' leads WR={wr:.1f}% Return={ret:.2f}% DD={dd:.1f}%. Keep running."

    def _save_comparison(self):
        try:
            comp = self._compute_comparison()
            COMPARISON_FILE.parent.mkdir(parents=True, exist_ok=True)
            COMPARISON_FILE.write_text(
                json.dumps(comp, indent=2, default=str), encoding="utf-8")
            self._feed_knowledge()
        except Exception as e:
            logger.debug(f"[MultiProfile] Save: {e}")

    def _feed_knowledge(self):
        try:
            from dataclasses import asdict

            from core.knowledge_model import KnowledgeModel
            km     = KnowledgeModel()
            trades = []
            for name, inst in self.profiles.items():
                for pos in inst.tracker._closed:
                    try:
                        d = asdict(pos)
                    except Exception:
                        d = {}
                    d["profile"] = name
                    trades.append(d)
            if trades:
                km.update_from_trades(trades, mode="dry" if self.dry_run else "live")
        except Exception as e:
            logger.debug(f"[MultiProfile] Knowledge: {e}")

    def _log_comparison(self):
        try:
            comp   = self._compute_comparison()
            leader = comp.get("leader","?")
            arb    = comp.get("arbitrage", {})
            logger.info(f"[MultiProfile] ── COMPARISON (Scan #{self._scan_count}) ──")
            for name in comp.get("ranked",[]):
                d = comp["profiles"][name]
                logger.info(
                    "{:<14} ${:.2f}  WR={:.0f}%  pnl={:+.4f}  DD={:.1f}%  "
                    "L{}  S{}  sc={:.1f}{}".format(
                        name.upper(), d["balance"], d["win_rate"],
                        d["net_pnl"], d["max_drawdown"],
                        d["longs"], d["shorts"],
                        d["score"], " <- LEADER" if name == leader else ""))
            if arb.get("total_arbs", 0) > 0:
                logger.info("[Arb] total={} wins={} pnl={:+.4f}".format(
                    arb["total_arbs"], arb["wins"], arb["total_pnl"]))
            logger.info("[MultiProfile] {}".format(comp.get("recommendation","")))
        except Exception as e:
            logger.debug(f"[MultiProfile] Log: {e}")
