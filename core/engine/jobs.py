"""
core/engine/jobs.py — BotEngine _JobsMixin mixin (Phase D5).
"""

from core.engine.helpers import *  # noqa: F403


class _JobsMixin:
    def _run_learning(self):
        try:
            self.learner.learn(force=True)
        except Exception as e:
            logger.debug(f"[Engine] Learning error: {e}")

        # Update Kelly stats from closed trades
        if hasattr(self, 'order_mgr') and hasattr(self.order_mgr, 'kelly'):
            try:
                closed_trades = [
                    vars(p) if hasattr(p, '__dict__') else p
                    for p in self.tracker._closed
                ]
                self.order_mgr.kelly.update_from_trades(closed_trades)
            except Exception as e:
                logger.debug(f"[Engine] Kelly update error: {e}")

        # Clean up stale sl_widened entries for closed positions
        if hasattr(self, 'order_mgr'):
            try:
                self.order_mgr.cleanup_sl_widened()
            except Exception:
                pass

    def _run_promotion_funnel(self):
        """PAPER-safe, fail-soft refresh of data/promotion_funnel.json (log-only)."""
        try:
            from scripts.promotion_funnel import main as _funnel_main
            rc = int(_funnel_main() or 0)
            if rc != 0:
                logger.debug(f"[Engine] promotion_funnel exited rc={rc}")
        except Exception as e:
            logger.debug(f"[Engine] promotion_funnel refresh skipped: {e}")

    def _run_optimizer(self):
        from core.auto_optimizer import AutoOptimizer
        logger.info("[Engine] Starting auto-optimization...")
        for ex in self.active_exchanges.values():
            try:
                AutoOptimizer(ex).run_all()
                break
            except Exception as e:
                logger.error(f"[Engine] Optimizer: {e}")

    def _run_self_healing(self):
        healer = getattr(self, "self_healer", None)
        if healer is None:
            return
        try:
            report = healer.tick()
            verdict = report.get("verdict")
            if verdict not in {"SKIPPED_COOLDOWN", "DISABLED"}:
                logger.info(
                    f"[SelfHeal] verdict={verdict} "
                    f"actions={len(report.get('actions') or [])}"
                )
        except Exception as e:
            logger.debug(f"[SelfHeal] tick skipped: {e}")

    def _run_self_improve(self):
        """PAPER-only autonomous mutate->validate->promote cycle (WS4).

        Gated by ``guardrails.self_improve_enabled()`` (env SELF_IMPROVE_ENABLED, hard
        PAPER). Builds short-lookback machine-strategy candidates, runs them through the
        honest gate + trade-sequence Monte Carlo, and promotes winners shadow->active-paper
        (never live — the kernel + PAPER latch forbid it). Fully fail-soft: any error is
        logged and skipped so it can never disturb trading."""
        try:
            from core.decision.guardrails import self_improve_enabled

            if not self_improve_enabled():
                return
            from core.decision import promotion_loop
            from research.mutator import build_candidates

            candidates = build_candidates()
            if not candidates:
                return
            report = promotion_loop.tick(candidates)
            if report.get("n_promoted"):
                logger.info(
                    f"[SelfImprove] promoted {report['n_promoted']}/{report['n_candidates']} "
                    "variant(s) shadow->active-paper (PAPER)"
                )
            else:
                logger.debug(
                    f"[SelfImprove] {report.get('n_candidates', 0)} candidates evaluated, "
                    "0 promoted (no edge cleared the gate)"
                )
        except Exception as e:
            logger.debug(f"[SelfImprove] cycle skipped: {e}")

    def _daily_self_check(self):
        """Daily health audit at 00:00 UTC — exchange connectivity, data freshness,
        strategy health, balance reconciliation, risk state."""
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "exchanges": {},
            "risk": {},
            "positions": {},
            "strategies": {},
        }
        # Exchange connectivity
        for ex_name, exchange in self.exchanges.items():
            connected = getattr(exchange, "_connected", False)
            halted = ex_name in self._exchange_halted
            latency = self._api_latency.get(ex_name, -1)
            report["exchanges"][ex_name] = {
                "connected": connected,
                "halted": halted,
                "latency_ms": round(latency, 0) if latency > 0 else -1,
                "consecutive_fails": self._consecutive_api_fails.get(ex_name, 0),
            }
        # Risk state
        report["risk"] = {
            "is_halted": self.risk.is_halted,
            "halt_reason": self.risk.halt_reason if self.risk.is_halted else None,
            "daily_pnl": getattr(self.risk, "_daily_pnl", 0),
        }
        # Open positions
        open_count = self.tracker.count_open()
        report["positions"] = {
            "open_count": open_count,
            "per_exchange": {
                ex_name: self.tracker.count_open(exchange=ex.name)
                for ex_name, ex in self.active_exchanges.items()
            },
        }
        # Strategy health from knowledge model
        try:
            from core.knowledge_model import KnowledgeModel
            km = KnowledgeModel()
            caution = []
            fee_heavy = km.get_fee_heavy_strategies()
            for strat in km.model.get("strategies", {}):
                if km.is_caution_strategy(strat):
                    caution.append(strat)
            report["strategies"] = {
                "caution_strategies": caution,
                "fee_heavy_strategies": list(fee_heavy),
            }
        except Exception:
            pass
        # Save report
        try:
            Path("data").mkdir(parents=True, exist_ok=True)
            Path("data/daily_check.json").write_text(
                json.dumps(report, indent=2, default=str), encoding="utf-8")
        except Exception:
            pass
        # Notify
        ex_status = ", ".join(
            f"{n}:{'OK' if r.get('connected') and not r.get('halted') else 'DOWN'}"
            for n, r in report["exchanges"].items())
        summary = (
            f"Daily Self-Check\n"
            f"Exchanges: {ex_status}\n"
            f"Open positions: {open_count}\n"
            f"Risk halted: {self.risk.is_halted}"
        )
        logger.info(f"[Engine] {summary}")
        self.notifier.alert(summary)

        # 2026-05-01: also generate the gate-effectiveness report and STAR
        # review as part of the daily check. These produce dated markdown
        # reports under data/reports/ that the operator can review.
        # Failures here do NOT crash the daily check — best-effort only.
        try:
            import subprocess
            subprocess.run(
                [sys.executable, "scripts/gate_effectiveness_report.py", "--window", "7"],
                cwd=str(Path(__file__).resolve().parents[1]),
                timeout=120, capture_output=True, check=False,
            )
            logger.info("[Engine] daily gate effectiveness report generated")
        except Exception as e:
            logger.debug(f"[Engine] gate effectiveness report skipped: {e}")
        try:
            import subprocess
            subprocess.run(
                [sys.executable, "scripts/star_review.py", "--window", "30"],
                cwd=str(Path(__file__).resolve().parents[1]),
                timeout=60, capture_output=True, check=False,
            )
            logger.info("[Engine] daily STAR review generated")
        except Exception as e:
            logger.debug(f"[Engine] STAR review skipped: {e}")

        # 2026-05-02: orphan stop-order cleanup. Bybit accumulates stuck
        # conditional orders when positions close without cancelling their
        # SL/TP. Per-symbol limit is ~10; once hit, new entries fail-close.
        # Run --commit (cancels orphans automatically). Script's defensive
        # design (per-symbol position verification, dry-run on uncertainty)
        # makes auto-run safe.
        try:
            import subprocess
            result = subprocess.run(
                [sys.executable, "scripts/cleanup_orphan_stop_orders.py",
                 "--exchange", "bybit", "--commit"],
                cwd=str(Path(__file__).resolve().parents[1]),
                timeout=180, capture_output=True, check=False,
            )
            out = (result.stdout or b"").decode("utf-8", errors="replace")
            cancelled = "Cancelled:"
            if cancelled in out:
                line = next((l for l in out.splitlines() if cancelled in l), "")
                logger.info(f"[Engine] daily orphan stop-order cleanup: {line.strip()}")
            else:
                logger.info("[Engine] daily orphan stop-order cleanup: no orphans")
        except Exception as e:
            logger.debug(f"[Engine] orphan stop-order cleanup skipped: {e}")
        # 2026-05-02: shadow vs live daily compare report (Phase A.13)
        try:
            import subprocess
            subprocess.run(
                [sys.executable, "scripts/shadow_vs_live_report.py",
                 "--window-hours", "24"],
                cwd=str(Path(__file__).resolve().parents[1]),
                timeout=60, capture_output=True, check=False,
            )
            logger.info("[Engine] daily shadow-vs-live report generated")
        except Exception as e:
            logger.debug(f"[Engine] shadow compare report skipped: {e}")

