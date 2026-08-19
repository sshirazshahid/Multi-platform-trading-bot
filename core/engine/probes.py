"""
core/engine/probes.py — BotEngine _ProbesMixin mixin (Phase D5).
"""

from core.engine.helpers import *  # noqa: F403
from core.engine.helpers import _deployable_total


class _ProbesMixin:

    # Log-only shadow probe specs, in registration order. Every probe is
    # config-gated, fail-open, built from READ-ONLY providers only, and never
    # receives an order path. Provenance notes live in each agent's module
    # docstring (tsmom/breakout are OWNER-DIRECTED forward paper tests of
    # refuted families, NOT pipeline GOs — see their headers).
    _PROBE_SPECS = (
        {
            "config": "LISTING_SHORT_PROBE",
            "import_path": "core.agents.listing_short_probe_agent:ListingShortProbeAgent",
            "warn_label": "listing-short",
            "log": "[Engine] ListingShortProbeAgent registered (log-only shadow probe)",
            "kwargs": lambda self, cfg: dict(
                markets_provider=self._listing_markets,
                market_data_provider=self._listing_market_data,
                ohlcv_provider=self._listing_ohlcv,
                venue=str(cfg.get("venue", "binance")),
            ),
            # Prereg 86: one OBSERVATION instance per configured venue. Each
            # closure binds its venue so the shared engine providers serve the
            # right client; the agent derives its own identity/state per venue.
            "multi": lambda self, cfg: [
                dict(
                    markets_provider=(lambda v=v: self._listing_markets(v)),
                    market_data_provider=(
                        lambda sym, v=v: self._listing_market_data(sym, v)),
                    ohlcv_provider=(
                        lambda sym, tf, since, v=v:
                        self._listing_ohlcv(sym, tf, since, v)),
                    venue=v,
                )
                for v in (cfg.get("venues") or (cfg.get("venue", "binance"),))
            ],
        },
        {
            "config": "UNLOCK_SHORT_PROBE",
            "import_path": "core.agents.unlock_short_probe_agent:UnlockShortProbeAgent",
            "warn_label": "unlock-short",
            "log": "[Engine] UnlockShortProbeAgent registered (log-only shadow probe)",
            "kwargs": lambda self, cfg: dict(
                perp_resolver=self._unlock_perp_resolver,
                market_data_provider=self._unlock_market_data,
                ohlcv_provider=self._unlock_ohlcv,
                calendar_dir=str(cfg.get("calendar_dir", "data/unlock_calendar")),
            ),
        },
        {
            "config": "TSMOM_PROBE",
            "import_path": "core.agents.tsmom_probe_agent:TsmomProbeAgent",
            "warn_label": "tsmom",
            "log": "[Engine] TsmomProbeAgent registered (log-only shadow probe; "
                   "owner-directed regime-watch, NOT a pipeline GO)",
            "kwargs": lambda self, cfg: dict(
                ohlcv_provider=self._unlock_ohlcv,
                market_data_provider=self._unlock_market_data,
                venue=str(cfg.get("venue", "bybit")),
            ),
        },
        {
            "config": "BREAKOUT_PROBE",
            "import_path": "core.agents.breakout_probe_agent:BreakoutProbeAgent",
            "warn_label": "breakout",
            "log": "[Engine] BreakoutProbeAgent registered (log-only shadow probe; "
                   "owner-directed Codex deep-run winner, NOT a pipeline GO)",
            "kwargs": lambda self, cfg: dict(
                ohlcv_provider=self._unlock_ohlcv,
                market_data_provider=self._unlock_market_data,
                venue=str(cfg.get("venue", "bybit")),
            ),
        },
        {
            "config": "BUNDLE_MR_PROBE",
            "import_path": "core.agents.bundle_mr_probe_agent:ZfadeProbeAgent",
            "warn_label": "zfade-bundle",
            "log": "[Engine] ZfadeProbeAgent registered (log-only shadow probe; "
                   "owner-directed bundle-test cfg365 candidate, NOT a pipeline GO)",
            "kwargs": lambda self, cfg: dict(
                ohlcv_provider=self._unlock_ohlcv,
                market_data_provider=self._unlock_market_data,
                venue=str(cfg.get("venue", "bybit")),
                symbols=self._bundle_probe_symbols(str(cfg.get("venue", "bybit"))),
            ),
        },
        {
            "config": "BUNDLE_MR_PROBE",
            "import_path": "core.agents.bundle_mr_probe_agent:Rsi2TrackerProbeAgent",
            "warn_label": "rsi2-bundle",
            "log": "[Engine] Rsi2TrackerProbeAgent registered (log-only shadow probe; "
                   "owner-directed bundle-test cfg226 tracker, NOT a pipeline GO)",
            "kwargs": lambda self, cfg: dict(
                ohlcv_provider=self._unlock_ohlcv,
                market_data_provider=self._unlock_market_data,
                venue=str(cfg.get("venue", "bybit")),
                symbols=self._bundle_probe_symbols(str(cfg.get("venue", "bybit"))),
            ),
        },
        {
            "config": "PULLBACK_MOMENTUM_PROBE",
            "import_path": "core.agents.pullback_momentum_probe_agent:"
                           "PullbackMomentumProbeAgent",
            "warn_label": "pullback-momentum",
            "log": "[Engine] PullbackMomentumProbeAgent registered "
                   "(log-only shadow probe; owner-directed pullback-momentum "
                   "forward test, NOT a pipeline GO)",
            "kwargs": lambda self, cfg: dict(
                ohlcv_provider=self._unlock_ohlcv,
                market_data_provider=self._unlock_market_data,
                venue=str(cfg.get("venue", "bybit")),
                symbols=self._bundle_probe_symbols(str(cfg.get("venue", "bybit"))),
            ),
        },
    )

    def _init_shadow_runner(self) -> None:
        """Lazy-init the multi-agent ShadowRunner. Failures don't crash the bot."""
        try:
            from core.shadow_runner import ShadowRunner
            from core.warehouse import Warehouse
            wh = getattr(self, "warehouse", None) or Warehouse()
            self.warehouse = wh
            try:
                from config import ALLOWED_HOURS_UTC, BLACKLIST_HARD
                bl = set(BLACKLIST_HARD or set())
                ah = set(ALLOWED_HOURS_UTC) if ALLOWED_HOURS_UTC else None
            except Exception:
                bl, ah = set(), None
            extra_probes = [p for spec in self._PROBE_SPECS
                            for p in self._build_probe(wh, spec)]
            self._shadow_runner = ShadowRunner(
                warehouse=wh,
                ctx_provider=self._shadow_ctx_for_symbol,
                free_balance_provider=self._shadow_free_balance,
                symbols_provider=self._shadow_symbols,
                blacklist=bl, allowed_hours=ah,
                enabled_flag=self._shadow_enabled_flag,
                extra_probes=extra_probes,
            )
            logger.info("[Engine] ShadowRunner initialized (multi-agent, log-only)")
        except Exception as e:
            logger.error(f"[Engine] ShadowRunner init failed: {e}")
            self._shadow_runner = None

    # Log-only shadow probe specs, in registration order. Every probe is
    # config-gated, fail-open, built from READ-ONLY providers only, and never
    # receives an order path. Provenance notes live in each agent's module
    # docstring (tsmom/breakout are OWNER-DIRECTED forward paper tests of
    # refuted families, NOT pipeline GOs — see their headers).
    def _venue_perp_available(self, venue: str, symbol: str) -> bool:
        """True when ``venue``'s ALREADY-LOADED ccxt markets list ``symbol``
        as an active linear perp. Read-only, never a network call; any
        failure means 'unavailable' (mirrors _unlock_perp_resolver)."""
        try:
            ex = (self.active_exchanges or {}).get(venue)
            m = (getattr(getattr(ex, "exchange", None), "markets", None) or {}).get(symbol)
            return bool(m and m.get("swap") and m.get("active", True))
        except Exception:
            return False

    def _bundle_probe_symbols(self, venue: str) -> tuple:
        """Spec-derived widened universe for BOTH bundle-MR arms
        (owner-approved 2026-07-20): MCP_DIRECTIONAL_PAPER bases x ``venue``
        via core.strategy_spec routes. Resolved ONCE per boot and cached so
        the two arms share one resolution and one boot-log disclosure;
        resolve_universe fails closed to the frozen 5-major basket."""
        cached = getattr(self, "_bundle_mr_universe", None)
        if cached is not None and cached[0] == venue:
            return cached[1]
        import core.agents.bundle_mr_probe_agent as _bundle_mod
        symbols, skipped = _bundle_mod.resolve_universe(
            venue=venue,
            perp_available=lambda sym: self._venue_perp_available(venue, sym),
        )
        logger.info(
            f"[Engine] bundle-MR probe universe: {len(symbols)} symbols "
            f"(spec-derived, venue={venue}, skipped_bases={len(skipped)})"
        )
        self._bundle_mr_universe = (venue, symbols)
        return symbols

    def _build_probe(self, wh, spec: dict) -> list:
        """Construct one log-only shadow probe agent from its _PROBE_SPECS
        entry. Fail-open: any error yields no probe (the shadow lane runs
        without it). The probe never receives an order path."""
        try:
            import config
            cfg = getattr(config, spec["config"])
            if not cfg.get("enabled"):
                logger.info(
                    f"[Engine] {spec['warn_label']} probe skipped "
                    f"(config {spec['config']}.enabled=false)"
                )
                return []
            import importlib
            mod_name, cls_name = spec["import_path"].split(":")
            cls = getattr(importlib.import_module(mod_name), cls_name)
            # Prereg 86: a spec may build MULTIPLE instances (one per venue).
            # Every instance failure is isolated so one venue cannot take the
            # others down; a spec without "multi" behaves exactly as before.
            kwargs_list = (spec["multi"](self, cfg) if "multi" in spec
                           else [spec["kwargs"](self, cfg)])
            probes = []
            for kw in kwargs_list:
                try:
                    probes.append(cls(
                        warehouse=wh,
                        account_balance_provider=self._shadow_free_balance,
                        **kw,
                    ))
                    suffix = (f" venue={kw.get('venue')}"
                              if len(kwargs_list) > 1 else "")
                    logger.info(spec["log"] + suffix)
                except Exception as e:
                    logger.warning(
                        f"[Engine] {spec['warn_label']} probe instance "
                        f"({kw.get('venue', '?')}) init skipped: {e}")
            return probes
        except Exception as e:
            logger.warning(f"[Engine] {spec['warn_label']} probe init skipped: {e}")
            return []

    def _build_listing_probe(self, wh) -> list:
        """Thin wrapper kept for the wire-up test surface
        (tests/test_botengine_shadow_wire.py)."""
        return self._build_probe(wh, self._PROBE_SPECS[0])

    def _run_deep_breakout_lane(self):
        """Scheduled tick for the ACTIVE PAPER deep-breakout lane (owner
        directive 2026-07-11, cohort strategy_family='deep_breakout').
        Lazy-init; never raises into the scheduler. The lane's own
        assert_paper_only() re-refuses every tick off-PAPER — any
        PaperOnlyViolation lands here and is logged loudly, and no order
        path is ever reached."""
        try:
            lane = getattr(self, "_deep_breakout_lane", None)
            if lane is None:
                from config import DEEP_BREAKOUT_LANE as _dbl_cfg
                from core.deep_breakout_lane import DeepBreakoutLane
                from core.warehouse import get_warehouse
                lane = DeepBreakoutLane(
                    exchanges=self.active_exchanges,
                    order_manager=self.order_mgr,
                    tracker=self.tracker,
                    risk=self.risk,
                    equity_provider=lambda: _deployable_total(self._balances),
                    ohlcv_provider=self._unlock_ohlcv,
                    warehouse=get_warehouse(),
                    entry_pause_check=self._deep_breakout_entry_paused,
                    cfg=_dbl_cfg,
                )
                self._deep_breakout_lane = lane
                logger.info(
                    "[Engine] DeepBreakoutLane initialized (ACTIVE PAPER, "
                    "binance+bybit, cohort strategy_family='deep_breakout')")
            lane.tick()
        except Exception as e:
            # includes PaperOnlyViolation — the lane never trades off-PAPER
            logger.error(f"[Engine] deep-breakout lane tick refused/failed: {e}")

    def _get_btc_vol_pause(self):
        """Lazily construct and cache the shared BtcVolPause instance."""
        _bvp = getattr(self, "_btc_vol_pause", None)
        if _bvp is None:
            from core.btc_vol_pause import BtcVolPause
            _bvp = BtcVolPause()
            self._btc_vol_pause = _bvp
        return _bvp

    def _deep_breakout_entry_paused(self) -> tuple:
        """(paused, reason) — the same market-wide BTC vol circuit breaker
        _execute_open applies at C.4, for the deep-breakout lane's entries.
        A2 convention: a BROKEN evaluation blocks entries (fail-closed)."""
        try:
            from config import BTC_VOL_PAUSE as _bvp_cfg
        except ImportError:
            return (False, "")
        if not _bvp_cfg.get("enabled", False):
            return (False, "")
        try:
            _bvp = self._get_btc_vol_pause()
            _ei = getattr(self.mcp_brain, "_indicator_cache", None) if self.mcp_brain else None
            paused, reason, _ = _bvp.update_and_evaluate(_ei)
            return (bool(paused), str(reason or "btc_vol_pause"))
        except Exception as e:
            return (True, f"btc_vol_pause_error:{e}")

    def _unlock_venue_order(self) -> tuple:
        try:
            from config import UNLOCK_SHORT_PROBE
            return tuple(UNLOCK_SHORT_PROBE.get("venue_order") or ())
        except Exception:
            return ("binance", "bybit", "bitget")

    def _unlock_perp_resolver(self, base: str):
        """(venue, unified symbol) of the FIRST venue in the screen's frozen
        fee-preference order listing an active USDT-linear perp for ``base``.
        Read-only; None when no venue lists it (probe logs SKIP_NO_PERP)."""
        want = f"{base}/USDT:USDT"
        for venue in self._unlock_venue_order():
            try:
                ex = (self.active_exchanges or {}).get(venue)
                client = getattr(ex, "exchange", None)
                m = (getattr(client, "markets", None) or {}).get(want)
                if m and m.get("swap") and m.get("active", True):
                    return (venue, want)
            except Exception:
                continue
        return None

    def _unlock_market_data(self, venue: str, symbol: str) -> dict:
        """Best bid/ask/quoteVolume + current funding rate for a perp on
        ``venue``. Read-only; fail-open to {} so a missing symbol skips."""
        out: dict = {}
        try:
            ex = (self.active_exchanges or {}).get(venue)
            if ex is None:
                return out
            t = ex.fetch_ticker(symbol, market_type="futures") or {}
            out.update({
                "bid": t.get("bid"), "ask": t.get("ask"), "last": t.get("last"),
                "quoteVolume": (t.get("quoteVolume")
                                or (t.get("info", {}) or {}).get("quoteVolume")),
                "active": True,
            })
            client = getattr(ex, "exchange", None)
            if client is not None and hasattr(client, "fetch_funding_rate"):
                fr = client.fetch_funding_rate(symbol) or {}
                out["funding_rate"] = fr.get("fundingRate")
        except Exception as e:
            logger.debug(f"[UnlockProbe] market_data {venue} {symbol} failed: {e}")
        return out

    def _unlock_ohlcv(self, venue: str, symbol: str, timeframe: str, since_ms: int) -> list:
        """1h candles on ``venue`` since ``since_ms``. Read-only."""
        try:
            ex = (self.active_exchanges or {}).get(venue)
            if ex is None:
                return []
            client = getattr(ex, "exchange", None)
            if client is not None:
                return client.fetch_ohlcv(
                    symbol, timeframe, since=int(since_ms), limit=1000,
                    params=self._futures_ohlcv_params(ex)) or []
            return ex.fetch_ohlcv(symbol, timeframe, limit=1000, market_type="futures") or []
        except Exception as e:
            logger.debug(f"[UnlockProbe] ohlcv {venue} {symbol} failed: {e}")
            return []

    def _listing_venue_client(self, venue: str = None):
        """The exchange client that supplies listing-probe market data
        (USDT-M perps). READ-ONLY use only. ``venue=None`` keeps the legacy
        single-venue config default (prereg 86 passes it explicitly)."""
        if venue is None:
            try:
                from config import LISTING_SHORT_PROBE
                venue = str(LISTING_SHORT_PROBE.get("venue", "binance"))
            except Exception:
                venue = "binance"
        return (self.active_exchanges or {}).get(venue)

    def _listing_markets(self, venue: str = None) -> list:
        """Current tradeable Binance USDT-M perp unified symbols. Read-only.

        Reloads ccxt markets at most once per hour so brand-new crypto perps
        appear in the listing-short universe. A frozen boot-time ``markets``
        snapshot would make the lane look permanently STARVED after seed
        (only symbols that slip in via incidental reloads would ever detect).
        """
        try:
            ex = self._listing_venue_client(venue)
            client = getattr(ex, "exchange", None)
            if client is None:
                return []
            import time as _time
            ttl = 3600.0
            stamps = getattr(self, "_listing_markets_loaded", None)
            if stamps is None:
                stamps = self._listing_markets_loaded = {}
            key = venue or "_default"
            last = float(stamps.get(key, 0.0) or 0.0)
            now = _time.time()
            if (now - last) >= ttl or not getattr(client, "markets", None):
                try:
                    client.load_markets(reload=True)
                    stamps[key] = now
                except Exception as e:
                    logger.debug(f"[ListingProbe] load_markets reload failed: {e}")
                    # keep whatever snapshot we have; still better than []
                    if not getattr(client, "markets", None):
                        return []
            markets = getattr(client, "markets", None) or {}
            out = []
            for sym, m in markets.items():
                if (m.get("swap") and m.get("active", True)
                        and m.get("quote") == "USDT" and m.get("settle") == "USDT"):
                    out.append(sym)
            return out
        except Exception as e:
            logger.debug(f"[ListingProbe] markets fetch failed: {e}")
            return []

    def _listing_market_data(self, symbol: str, venue: str = None) -> dict:
        """Best bid/ask/last/quoteVolume + current funding rate for a perp.
        Read-only; fail-open to {} so a missing symbol simply skips."""
        out: dict = {}
        try:
            ex = self._listing_venue_client(venue)
            if ex is None:
                return out
            t = ex.fetch_ticker(symbol, market_type="futures") or {}
            out.update({
                "bid": t.get("bid"), "ask": t.get("ask"), "last": t.get("last"),
                "quoteVolume": (t.get("quoteVolume")
                                or (t.get("info", {}) or {}).get("quoteVolume")),
                "active": True,
            })
            client = getattr(ex, "exchange", None)
            if client is not None and hasattr(client, "fetch_funding_rate"):
                fr = client.fetch_funding_rate(symbol) or {}
                out["funding_rate"] = fr.get("fundingRate")
        except Exception as e:
            logger.debug(f"[ListingProbe] market_data {symbol} failed: {e}")
        return out

    def _listing_ohlcv(self, symbol: str, timeframe: str, since_ms: int,
                       venue: str = None) -> list:
        """Forward 1h candles for a listing since ``since_ms``. Read-only."""
        try:
            ex = self._listing_venue_client(venue)
            if ex is None:
                return []
            client = getattr(ex, "exchange", None)
            if client is not None:
                return client.fetch_ohlcv(
                    symbol, timeframe, since=int(since_ms), limit=1000,
                    params=self._futures_ohlcv_params(ex)) or []
            return ex.fetch_ohlcv(symbol, timeframe, limit=1000, market_type="futures") or []
        except Exception as e:
            logger.debug(f"[ListingProbe] ohlcv {symbol} failed: {e}")
            return []

    @staticmethod
    @staticmethod
    def _futures_ohlcv_params(ex) -> dict:
        try:
            return ex._futures_params() if hasattr(ex, "_futures_params") else {}
        except Exception:
            return {}

    def _shadow_enabled_flag(self) -> bool:
        try:
            from config import SHADOW_MODE
            return bool(SHADOW_MODE.get("enabled"))
        except Exception:
            return False

    def _shadow_free_balance(self) -> float:
        """Sum of free USDT across active exchanges (sizing basis)."""
        total = 0.0
        for _name, ex in (self.active_exchanges or {}).items():
            try:
                bal = ex.fetch_balance("futures")
                total += float((bal.get("free") or {}).get("USDT") or 0.0)
            except Exception:
                pass
        return total

    def _shadow_symbols(self) -> list:
        """Return a bounded mover set for log-only shadow evaluation.

        The broad path ranks point-in-time 1h/24h/7d USDT-perpetual movers
        across active venues. It is only ShadowRunner's symbols provider and
        cannot change executable ``current_pairs``. Results are cached to keep
        the all-ticker REST budget to one batch per venue per refresh. Any
        failure falls back to the pre-change shadow list.
        """
        try:
            from config import BROAD_UNIVERSE_MONITOR, SHADOW_MODE
            cap = int(SHADOW_MODE.get("mover_cap", 10))
            if not SHADOW_MODE.get("mover_universe", True):
                return self._shadow_symbols_legacy(5)  # true pre-change budget
            now = time.time()
            cached = getattr(self, "_shadow_mover_cache", None)
            if cached and now - cached[0] < float(SHADOW_MODE.get("mover_refresh_s", 900)):
                return list(cached[1])
            # The broad path is strictly a symbols_provider for ShadowRunner.
            # It never mutates current_pairs and has no reference to an order
            # manager, so it cannot expand executable exposure.
            if BROAD_UNIVERSE_MONITOR.get("enabled", False):
                if not self.active_exchanges:
                    return self._shadow_symbols_legacy(cap)
                try:
                    from core.universe_monitor import (
                        UniverseMonitor,
                        UniverseMonitorConfig,
                    )

                    deep_cap = min(
                        max(0, cap),
                        max(0, int(BROAD_UNIVERSE_MONITOR.get("shortlist_cap", 18))),
                    )
                    monitor = getattr(self, "_broad_universe_monitor", None)
                    if monitor is None:
                        monitor = UniverseMonitor(
                            self.active_exchanges,
                            db_path=Path(BROAD_UNIVERSE_MONITOR.get(
                                "db_path", "data/universe_monitor.sqlite"
                            )),
                            config=UniverseMonitorConfig(
                                min_quote_volume_usdt=float(
                                    BROAD_UNIVERSE_MONITOR.get(
                                        "min_quote_volume_usdt", 5_000_000
                                    )
                                ),
                                max_ticker_age_s=float(BROAD_UNIVERSE_MONITOR.get(
                                    "max_ticker_age_s", 180
                                )),
                                reference_tolerance_s=float(
                                    BROAD_UNIVERSE_MONITOR.get(
                                        "reference_tolerance_s", 1800
                                    )
                                ),
                                retention_days=float(BROAD_UNIVERSE_MONITOR.get(
                                    "retention_days", 8
                                )),
                                per_direction_per_horizon=int(
                                    BROAD_UNIVERSE_MONITOR.get(
                                        "per_direction_per_horizon", 3
                                    )
                                ),
                                max_shortlist=deep_cap,
                                max_contracts_per_venue=int(
                                    BROAD_UNIVERSE_MONITOR.get(
                                        "max_contracts_per_venue", 5000
                                    )
                                ),
                                abs_move_usdt_min=float(
                                    BROAD_UNIVERSE_MONITOR.get(
                                        "abs_move_usdt_min", 0.0
                                    )
                                ),
                                abs_move_usdt_max=float(
                                    BROAD_UNIVERSE_MONITOR.get(
                                        "abs_move_usdt_max", float("inf")
                                    )
                                ),
                                prefer_abs_usdt_rank=bool(
                                    BROAD_UNIVERSE_MONITOR.get(
                                        "prefer_abs_usdt_rank", False
                                    )
                                ),
                                prefer_crypto_shortlist=bool(
                                    BROAD_UNIVERSE_MONITOR.get(
                                        "prefer_crypto_shortlist", True
                                    )
                                ),
                            ),
                        )
                        self._broad_universe_monitor = monitor
                    else:
                        # Venue wrappers may be refreshed after reconnect.
                        monitor.exchanges = self.active_exchanges
                    scan = monitor.scan()
                    out = list(scan.symbols)
                    self._shadow_symbol_venues = {
                        row.symbol: row.venue for row in scan.shortlist
                    }
                    # Persist latest shortlist for MCP / research (log-only).
                    try:
                        from pathlib import Path as _Path

                        snap = {
                            "schema_version": 1,
                            "scan_status": "ok",
                            "ts": now,
                            "completed_at": now,
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
                                    "asset_class": getattr(
                                        r, "asset_class", "crypto"
                                    ),
                                }
                                for r in scan.shortlist
                            ],
                        }
                        atomic_write_json(
                            _Path("data/mover_shortlist_latest.json"),
                            snap,
                            indent=2,
                        )
                    except Exception as _snap_err:
                        logger.warning(
                            f"[Shadow] shortlist snapshot failed: {_snap_err}"
                        )
                    if out:
                        logger.info(
                            f"[Shadow] broad universe accepted "
                            f"{scan.accepted_tickers}/{scan.raw_tickers} tickers; "
                            f"deep-analysis shortlist={len(out)}"
                        )
                    else:
                        logger.warning(
                            "[Shadow] broad universe produced no valid movers "
                            f"(accepted={scan.accepted_tickers}, "
                            f"venue_errors={dict(scan.venue_errors)}) -- "
                            "legacy fallback"
                        )
                        out = self._shadow_symbols_legacy(cap)
                        self._shadow_symbol_venues = {}
                    self._shadow_mover_cache = (now, out)
                    return list(out)
                except Exception as broad_error:
                    logger.warning(
                        f"[Shadow] broad universe failed: {broad_error} -- "
                        "legacy fallback"
                    )
                    out = self._shadow_symbols_legacy(cap)
                    self._shadow_symbol_venues = {}
                    self._shadow_mover_cache = (now, out)
                    return list(out)
            screened: list[str] = []
            for _ename, pairs in (self.current_pairs or {}).items():
                for sym in (pairs.get("futures") or []):
                    if sym not in screened:
                        screened.append(sym)
            if not screened or not self.active_exchanges:
                return self._shadow_symbols_legacy(cap)
            ex = next(iter(self.active_exchanges.values()))
            # Route through the wrapper's fetch_tickers(market_type='futures'),
            # which passes _futures_params() so the call hits the PERP endpoint
            # and returns unified swap-keyed tickers ('BTC/USDT:USDT'). The raw
            # ccxt fetch_tickers() routes by the client's steady-state
            # defaultType='spot' and returns spot keys that never match the
            # swap universe — the silent-fallback bug found 2026-07-06 (24h of
            # shadow proposals stuck on BTC/ETH). Fail-open to {} inside.
            raw = ex.fetch_tickers(market_type="futures") if ex else {}
            min_qv = float(SHADOW_MODE.get("mover_min_qv_usd", 5_000_000))
            moves: list[tuple[float, str]] = []
            for sym in screened:
                t = raw.get(sym)
                if not t:
                    continue
                pct, qv = t.get("percentage"), t.get("quoteVolume")
                if pct is None or qv is None or float(qv) < min_qv:
                    continue
                moves.append((abs(float(pct)), sym))
            moves.sort(reverse=True)
            out = [s for _, s in moves[:cap]]
            if not out:
                # Screened symbols but zero usable tickers = key-format or
                # venue trouble. Be LOUD (silent fallback is undiagnosable)
                # and cache the fallback so the ticker call is not re-fired
                # on every 300s shadow tick.
                logger.warning(
                    f"[Shadow] mover ranking empty ({len(screened)} screened, "
                    f"{len(raw) if raw else 0} tickers) — legacy fallback"
                )
                out = self._shadow_symbols_legacy(cap)
            self._shadow_mover_cache = (now, out)
            return list(out)
        except Exception as e:
            # WARNING, not DEBUG: a silent debug-level fallback here is exactly
            # what hid the 2026-07-06 mover bug for 24h. Make it visible.
            logger.warning(f"[Shadow] mover universe fallback (exception): {e}")
            try:
                return self._shadow_symbols_legacy(5)
            except Exception:
                return []  # never let the provider raise into the tick

    def _shadow_symbols_legacy(self, cap: int = 5) -> list:
        """Pre-2026-07-06 selection: first futures pairs per exchange."""
        out: list[str] = []
        for _ename, pairs in (self.current_pairs or {}).items():
            for sym in (pairs.get("futures") or [])[:2]:
                if sym not in out:
                    out.append(sym)
            if len(out) >= cap:
                break
        return out[:cap]

    def _shadow_ctx_for_symbol(self, symbol: str) -> dict | None:
        """Best-effort OHLCV pull for the multi-timeframe agent context.
        Cached at the exchange-client level — no extra API hit beyond what
        live already does. Returns None if data is unavailable."""
        if not self.active_exchanges:
            return None
        # Broad-universe candidates can originate on any venue.  Preserve the
        # venue that supplied the ranked ticker so shadow OHLCV/outcome replay
        # is not silently priced from the first configured exchange.
        venue_hint = (getattr(self, "_shadow_symbol_venues", {}) or {}).get(symbol)
        ex = self.active_exchanges.get(venue_hint)
        if ex is None:
            ex = next(iter(self.active_exchanges.values()))
        try:
            import pandas as pd
            # 2026-07-07: stamp the venue actually supplying the candles.
            # Without it every shadow Proposal defaulted to venue='binance'
            # even when priced off bybit/bitget, so the outcome resolver
            # replayed the WRONG venue's candles (mispriced barrier hits) or
            # left non-binance symbols PENDING forever.
            ctx: dict = {
                "symbol": symbol,
                "venue": str(
                    venue_hint or getattr(ex, "name", "") or "binance"
                ).lower(),
            }
            for tf, key, lim in (
                ("5m", "ohlcv_5m", 80),
                ("15m", "ohlcv_15m", 60),
                ("1h", "ohlcv_1h", 100),
                ("4h", "ohlcv_4h", 60),
            ):
                try:
                    bars = ex.fetch_ohlcv(symbol, timeframe=tf, limit=lim)
                    if not bars or len(bars) < 30:
                        return None
                    df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
                    ctx[key] = df
                except Exception:
                    return None
            return ctx
        except Exception:
            return None

    def _shadow_loop(self, stop_event) -> None:
        """Daemon loop — invokes ShadowRunner.tick() on the configured cadence."""
        from config import SHADOW_MODE
        interval = max(60, int(SHADOW_MODE.get("tick_interval_s", 300)))
        while not stop_event.is_set():
            try:
                if self._shadow_runner is not None:
                    self._shadow_runner.tick()
            except Exception as e:
                logger.debug(f"[Shadow] tick error: {e}")
            stop_event.wait(interval)

