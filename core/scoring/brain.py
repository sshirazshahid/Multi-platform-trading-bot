"""
core/scoring/brain.py — MCPBrain assembly: orchestrates scoring submodules.

Permanent implementation lives here; core/mcp_brain.py is the public facade.
"""

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from core.scoring.accuracy_tracker import AccuracyTracker
from core.scoring.constants import (
    ACCURACY_FILE,
    DATA_CACHE_TTL,
    DECISION_LOG,
    ENTRY_COOLDOWN,
    POSITION_COOLDOWN,
    STATE_FILE,
)
from core.scoring.data_sources import (
    compute_technicals,
    fetch_coingecko,
    fetch_crypto_com,
    fetch_funding_rates,
    fetch_open_interest,
    fetch_orderbook_depth,
)
from core.scoring.entry_score import route_score_coin, score_coin, score_coin_scalp
from core.scoring.helpers import _safe_feat, _sigmoid
from core.scoring.portfolio import algorithmic_portfolio
from core.scoring.position_monitor import algorithmic_position_monitor

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

class MCPBrain:
    """
    Algorithmic Portfolio Manager — multi-factor scoring engine.
    Gathers 5 live data sources, computes multi-TF exchange indicators,
    scores coins for OPEN/CLOSE actions, returns structured trade commands.
    Also monitors open positions with HOLD/CLOSE/TIGHTEN/BREAKEVEN advice.
    No external AI calls — pure deterministic scoring.
    """

    def __init__(self):
        self._last_entry_run = 0
        self._last_position_run = 0
        self._last_decisions = {}
        self._last_position_advice = {}
        self._last_fund_ops = []       # TRANSFER / SELL_PORTFOLIO operations
        self._last_trade_actions = []  # OPEN/CLOSE actions from analyze_portfolio
        self._accuracy = AccuracyTracker()
        # Model-gate state (Phase 6). Loaded lazily so MCPBrain can boot without
        # any trained models present.
        self._model_bundle: dict = {}   # market_type -> {lr, gbm, iso_lr, iso_gbm, weights, version}
        # Track LAST FAILED load timestamp per market_type so a retrain that
        # writes a fresh `*_latest.json` mid-session can be picked up without
        # a process restart. The retry-after window strikes a balance:
        # frequent enough to avoid blocking new models for hours; long enough
        # that a permanently-misconfigured bot doesn't spam the loader.
        self._model_load_failed_at: dict[str, float] = {}
        # Warn-once state: last pointer-rejection reason warned per market so
        # the (expected) no-promoted-model state logs once, not 200x/day.
        self._model_gate_last_warned_reason: dict[str, str] = {}

        # Exchange clients for direct OHLCV fetching (set by bot_engine)
        self._exchanges = {}
        # Cache for exchange indicators (120s TTL)
        self._indicator_cache = {}
        self._indicator_cache_time = 0
        # 2026-06-11: memo of the 4h EMA20/50 gap at a position's ENTRY bar,
        # keyed (base, int(entry_ts)) — computed once per position lifetime
        # (in-memory only). Caveat: entries first checked inside their own
        # still-forming 4h bar memoize that bar's LIVE close (entry gap ≈
        # current gap → exempt) — deliberate, codified in
        # tests/test_entry_staleness_gap_flip.py (same-bar test); the
        # direction of error is fewer fires, handled by SL/TP/trailing.
        self._entry_gap_memo = {}

        self._enabled = True

        # Cache raw data for reuse between entry/position analysis
        self._cached_data = {}
        self._cache_time = 0

        # ── Data Coordinator (2026-05-27) ──────────────────────────────
        # External data feeds: funding rate history, OI time-series,
        # enhanced orderbook, news sentiment, smart money flow.
        # Lazy-initialized; fail-open if coordinator can't load.
        self._data_coordinator = None
        try:
            from core.data_coordinator import get_coordinator
            self._data_coordinator = get_coordinator()
            logger.info("[MCP-Brain] Data Coordinator attached")
        except Exception as e:
            logger.warning(f"[MCP-Brain] Data Coordinator unavailable: {e}")

        # Load persisted state from last session
        self._load_state()

        logger.info(
            "[MCP-Brain] Algorithmic-only scorer ready — "
            "5 sources + multi-TF exchange indicators")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    # ── Model gate (Phase 6) ─────────────────────────────────────────
    # Retry window for failed bundle loads (seconds). 5 minutes balances
    # "let the weekly retrain be picked up promptly" vs "don't spam the
    # loader on every cycle when the model is permanently missing".
    _MODEL_LOAD_RETRY_SEC = 300

    def _load_model_bundle(self, market_type: str) -> dict:
        """Lazy-load the calibrated LR+GBM bundle for `market_type`.

        Returns {} when no `*_latest.json` pointer exists or any artifact
        fails to load — callers fall back to rule-only behavior.

        2026-04-30: previously used a permanent negative cache via
        `_model_load_attempted: set`, which meant the bot never picked up
        a fresh model promoted by the weekly retrain mid-session. Now the
        negative cache expires after `_MODEL_LOAD_RETRY_SEC` so a retrain
        that writes a new `ensemble_*_latest.json` is reflected on the
        next entry cycle without requiring a process restart.
        """
        if market_type in self._model_bundle:
            return self._model_bundle[market_type]
        last_fail = self._model_load_failed_at.get(market_type, 0.0)
        if last_fail and (time.time() - last_fail) < self._MODEL_LOAD_RETRY_SEC:
            return {}

        try:
            latest = Path(f"data/models/ensemble_{market_type}_latest.json")
            if not latest.exists():
                logger.debug(
                    f"[ModelGate] no latest pointer for market={market_type} — "
                    f"rule-only fallback")
                self._model_load_failed_at[market_type] = time.time()
                return {}
            ptr = json.loads(latest.read_text())
            from core.promotion_gate import validate_model_pointer
            ptr_ok, ptr_reason, _ptr_diag = validate_model_pointer(
                ptr, market_type=market_type)
            if not ptr_ok:
                if self._model_gate_last_warned_reason.get(market_type) != ptr_reason:
                    logger.warning(
                        f"[ModelGate] latest pointer rejected for market={market_type}: "
                        f"{ptr_reason}")
                    self._model_gate_last_warned_reason[market_type] = ptr_reason
                else:
                    logger.debug(
                        f"[ModelGate] latest pointer still rejected for "
                        f"market={market_type}: {ptr_reason}")
                self._model_load_failed_at[market_type] = time.time()
                return {}
            art_path = Path(ptr["artifact_path"])
            if not art_path.exists():
                logger.warning(
                    f"[ModelGate] latest pointer references missing artifact: {art_path}")
                self._model_load_failed_at[market_type] = time.time()
                return {}
            payload = json.loads(art_path.read_text())
            from core.calibration import IsotonicCalibrator
            from core.models import GBMModel, LRModel
            arts = payload["artifacts"]
            base = art_path.parent
            bundle = {
                "version":  payload["model_version"],
                "weights":  payload["weights"],
                "feature_keys": payload["feature_keys"],
                "lr":       LRModel.load(base / arts["lr"]),
                "gbm":      GBMModel.load(base / arts["gbm"]),
                "iso_lr":   IsotonicCalibrator.load(base / arts["iso_lr"]),
                "iso_gbm":  IsotonicCalibrator.load(base / arts["iso_gbm"]),
            }
            self._model_bundle[market_type] = bundle
            # Clear any stale failure stamp now that a successful load has
            # happened — defensive in case the same market_type ever rolls
            # from "missing" to "promoted" inside one process lifetime.
            self._model_load_failed_at.pop(market_type, None)
            self._model_gate_last_warned_reason.pop(market_type, None)
            logger.info(
                f"[ModelGate] loaded {market_type} ensemble "
                f"(version={bundle['version']}, weights={bundle['weights']})")
            return bundle
        except Exception as e:
            logger.warning(f"[ModelGate] failed to load {market_type} bundle: {e}")
            self._model_load_failed_at[market_type] = time.time()
            return {}

    def score_via_model(
        self,
        *,
        market_type: str,
        feats: dict,
        rule_score: float,
    ) -> dict:
        """Calibrated p_win blend.

        Returns dict with keys: `p_win_lr`, `p_win_gbm`, `p_win_ensemble`,
        `model_version`. When the bundle is unavailable returns sensible
        rule-only stand-ins so callers don't have to special-case.
        """
        out = {
            "p_win_lr":       float("nan"),
            "p_win_gbm":      float("nan"),
            "p_win_ensemble": _sigmoid((float(rule_score) - 65.0) / 8.0),
            "model_version":  None,
        }
        bundle = self._load_model_bundle(market_type)
        if not bundle:
            return out
        try:
            import numpy as _np
            keys = bundle["feature_keys"]
            x = _np.array([[_safe_feat(feats.get(k)) for k in keys]], dtype=float)
            p_lr_raw = float(bundle["lr"].p_win(x)[0])
            p_gbm_raw = float(bundle["gbm"].p_win(x)[0])
            p_lr = float(bundle["iso_lr"].transform(_np.array([p_lr_raw]))[0])
            p_gbm = float(bundle["iso_gbm"].transform(_np.array([p_gbm_raw]))[0])
            w = bundle["weights"]
            w_lr = float(w.get("lr", 0.5))
            w_gbm = float(w.get("gbm", 0.5))
            w_mcp = float(w.get("mcp_rule", 0.25))
            p_models = w_lr * p_lr + w_gbm * p_gbm
            rule_term = _sigmoid((float(rule_score) - 65.0) / 8.0)
            p_ens = w_mcp * rule_term + (1.0 - w_mcp) * p_models
            out.update({
                "p_win_lr":       p_lr,
                "p_win_gbm":      p_gbm,
                "p_win_ensemble": float(p_ens),
                "model_version":  bundle["version"],
            })
        except Exception as e:
            logger.debug(f"[ModelGate] score_via_model({market_type}) error: {e}")
        return out

    def _save_state(self):
        """Persist decisions, position advice, and timing state for crash recovery."""
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "decisions": self._last_decisions,
                "position_advice": self._last_position_advice,
                "saved_at": time.time(),
                # ── Timing state for restart resilience (2026-04-16) ──
                "last_entry_run": self._last_entry_run,
                "last_position_run": self._last_position_run,
            }
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            tmp.replace(STATE_FILE)
        except Exception as e:
            logger.debug(f"[MCP-Brain] State save: {e}")

    def _load_state(self):
        """Load persisted decisions and timing state from last session."""
        try:
            if STATE_FILE.exists():
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                saved_at = data.get("saved_at", 0)
                age_min = (time.time() - saved_at) / 60

                # ── Timing state: restore regardless of age ──────────
                if "last_entry_run" in data:
                    self._last_entry_run = data["last_entry_run"]
                    self._last_position_run = data.get("last_position_run", 0)
                    logger.info("[MCP-Brain] Restored timing state from disk")

                # ── Decisions/advice: only restore if recent (<10 min) ──
                # Provenance fix #6 (DECLARED BEHAVIOR CHANGE, 2026-06-12):
                # reloaded position_advice is DISCARDED on restart so stale
                # advice (and its decision_ids) is never consumed; the next
                # monitor cycle re-derives it fresh. Timing/budget fields
                # above are kept.
                if age_min < 10:
                    self._last_decisions = data.get("decisions", {})
                    n_dec = len(self._last_decisions)
                    n_adv_dropped = len(data.get("position_advice", {}) or {})
                    logger.info(
                        f"[MCP-Brain] Restored state: {n_dec} decisions "
                        f"({age_min:.1f}min old); dropped {n_adv_dropped} "
                        f"reloaded position advices (stale-advice guard)")
                else:
                    logger.info(f"[MCP-Brain] Stale decisions ({age_min:.0f}min old) — starting fresh")
        except Exception as e:
            logger.debug(f"[MCP-Brain] State load: {e}")

    # ──────────────────────────────────────────────────────────────────
    # DATA FETCHING (shared between entry + position analysis)
    # ──────────────────────────────────────────────────────────────────

    def _fetch_all_data(self, coins: list) -> dict:
        """Fetch all 5 sources in parallel. Cache for DATA_CACHE_TTL seconds."""
        now = time.time()
        if now - self._cache_time < DATA_CACHE_TTL and self._cached_data:
            return self._cached_data

        crypto_data = {}
        gecko_data = {}
        funding_data = {}
        orderbook_data = {}
        oi_data = {}
        sources_ok = 0

        with ThreadPoolExecutor(max_workers=5) as pool:
            f1 = pool.submit(fetch_crypto_com, coins)
            f2 = pool.submit(fetch_coingecko, coins)
            f3 = pool.submit(fetch_funding_rates, coins)
            f4 = pool.submit(fetch_orderbook_depth, coins)
            f5 = pool.submit(fetch_open_interest, coins)
            for name, fut, target in [
                ("crypto.com", f1, "crypto"),
                ("coingecko", f2, "gecko"),
                ("funding", f3, "funding"),
                ("orderbook", f4, "orderbook"),
                ("open_interest", f5, "oi"),
            ]:
                try:
                    result = fut.result(timeout=15)
                    if result:
                        sources_ok += 1
                    if target == "crypto":
                        crypto_data = result
                    elif target == "gecko":
                        gecko_data = result if isinstance(result, dict) else {}
                    elif target == "funding":
                        funding_data = result if isinstance(result, dict) else {}
                    elif target == "orderbook":
                        orderbook_data = result if isinstance(result, dict) else {}
                    elif target == "oi":
                        oi_data = result if isinstance(result, dict) else {}
                except Exception:
                    pass

        # Compute technicals from sparklines
        technicals = {}
        for coin, gd in gecko_data.items():
            spark = gd.get("sparkline", [])
            if spark:
                technicals[coin] = compute_technicals(spark)

        # Resolve past decision accuracy
        current_prices = {}
        for coin in coins:
            p = (gecko_data.get(coin, {}).get("price") or
                 crypto_data.get(coin, {}).get("price") or 0)
            if p:
                current_prices[coin] = p
        self._accuracy.resolve_outcomes(current_prices)

        data = {
            "crypto": crypto_data,
            "gecko": gecko_data,
            "funding": funding_data,
            "orderbook": orderbook_data,
            "oi": oi_data,
            "technicals": technicals,
            "sources_ok": sources_ok,
            "prices": current_prices,
        }
        self._cached_data = data
        self._cache_time = now

        logger.info(
            f"[MCP-Brain] 5-source fetch: {sources_ok}/5 OK | "
            f"prices={len(crypto_data)} gecko={len(gecko_data)} "
            f"funding={len(funding_data)} ob={len(orderbook_data)} "
            f"tech={len(technicals)}")
        return data

    # ──────────────────────────────────────────────────────────────────
    # EXCHANGE INDICATORS (multi-TF from live OHLCV)
    # ──────────────────────────────────────────────────────────────────

    def set_exchanges(self, exchanges: dict):
        """Accept exchange client refs for direct OHLCV fetching."""
        self._exchanges = exchanges

    def is_entry_invalidated(
        self, *, symbol: str, side: str, gap_pct: float = 0.15,
        entry_ts: float = 0.0,
    ) -> tuple[bool, str]:
        """Check whether the 4h EMA20/50 hypothesis still supports `side`.

        2026-05-01 (Tier 1.1): re-evaluates the SIDE-determining signal
        from `_score_coin` (line ~1976: side = 'buy' if ema20_above_50_4h).
        If the 4h EMA20/50 has flipped against the position with margin
        ≥ `gap_pct`%, returns (True, reason) — the entry rationale is
        structurally invalid.

        Returns (False, reason) on:
        - 4h EMAs still aligned with `side`
        - EMAs touching the cross-line (gap < threshold) — whipsaw guard
        - Indicator fetch failure (don't close on transient errors)
        - Born-invalid positions (2026-06-11, see entry_ts below)

        Reuses the 120s `_indicator_cache` so this is cheap on every
        90s monitor cycle when the cache is warm.

        Args:
            symbol: e.g. "ATOM/USDT:USDT" — the perp symbol from the
                    Position object. The base coin is extracted for the
                    indicator lookup.
            side:   'buy' or 'sell' (the Position's side).
            gap_pct: Minimum EMA gap (in WRONG direction) to fire, as a
                     percentage. Default 0.15% matches `_score_coin`'s
                     R1 threshold for "decisive 4h cross".
            entry_ts: Position entry time (epoch seconds, Position.open_time).
                     When > 0, gap-flip semantics apply (2026-06-11): fire
                     ONLY if the gap was NOT already >= gap_pct against the
                     side at the entry bar — born-invalid positions are
                     exempt forever (the 4h EMA was never their hypothesis;
                     SL/TP/trailing manage them). 0/unknown/uncomputable →
                     the pre-2026-06-11 fire-on-current-gap behavior.

        Returns:
            (invalidated: bool, reason: str)
        """
        try:
            base = symbol.split("/")[0].split(":")[0].upper()
        except Exception:
            return False, "symbol_parse_fail"

        # Fetch fresh indicators for just this coin (or read cache)
        ei = self._indicator_cache.get(base) if self._indicator_cache else None
        if not ei:
            try:
                ei_all = self._fetch_exchange_indicators([base])
                ei = ei_all.get(base) if ei_all else None
            except Exception as e:
                return False, f"indicator_fetch_fail({str(e)[:40]})"
        if not ei:
            return False, "no_indicators"
        ei_4h = ei.get("4h", {})
        ema20 = ei_4h.get("ema20", 0)
        ema50 = ei_4h.get("ema50", 0)
        if not ema20 or not ema50 or ema50 <= 0:
            return False, "no_ema_data"

        gap = (ema20 - ema50) / ema50 * 100.0  # signed, in percent

        if side == "buy":
            # Long entry was predicated on ema20 > ema50. Invalidate when
            # ema20 is now BELOW ema50 by at least `gap_pct`%.
            now_against = gap <= -gap_pct
            still = f"4h gap={gap:+.2f}% (long still valid)"
        elif side == "sell":
            # Short entry was predicated on ema20 < ema50. Invalidate when
            # ema20 is now ABOVE ema50 by at least `gap_pct`%.
            now_against = gap >= gap_pct
            still = f"4h gap={gap:+.2f}% (short still valid)"
        else:
            return False, f"unknown_side:{side}"
        if not now_against:
            return False, still

        # ── 2026-06-11 gap-flip semantics ──────────────────────────────
        # The pre-2026-06-11 rule fired unconditionally here, which also
        # killed positions ALREADY invalid at entry (born-invalid; a 4h
        # gap can't move 2% in 30min) — i.e. every long in a 4h-bear
        # regime. Fire ONLY when the gap actually FLIPPED after entry.
        # Unknown entry-bar gap (no entry_ts, bar outside fetched window,
        # NaN, fetch failure) → PRESERVE OLD BEHAVIOR (fire).
        entry_gap = self._entry_gap_at_bar(base, entry_ts)
        if entry_gap is not None:
            born_invalid = ((entry_gap <= -gap_pct) if side == "buy"
                            else (entry_gap >= gap_pct))
            if born_invalid:
                return False, (
                    f"4h gap={gap:+.2f}% but {entry_gap:+.2f}% at entry — "
                    f"born-invalid — exempt ({side})")
            flip = f"flipped after entry (entry gap {entry_gap:+.2f}%)"
        else:
            flip = "entry gap unknown — old-rule fire"
        if side == "buy":
            return True, f"4h ema20-ema50 gap={gap:+.2f}% {flip} (long invalidated)"
        return True, f"4h ema20-ema50 gap={gap:+.2f}% {flip} (short invalidated)"

    def _entry_gap_at_bar(self, base: str, entry_ts: float):
        """4h EMA20/50 gap (%) at the bar containing `entry_ts` (epoch sec).

        Computed over the prefix of a fresh 250-bar 4h window ending at the
        entry bar (250 x 4h ≈ 41 days >> any hold time), memoized per
        (base, int(entry_ts)) — one OHLCV fetch per position lifetime, and
        only on the would-fire path (caller checks the current gap first).

        Returns None when uncomputable (entry_ts<=0, no exchanges, entry bar
        before the window, <50 bars of EMA warmup up to the entry bar, NaN
        EMAs, fetch failure) — caller treats None as "unknown" and preserves
        the old fire-on-current-gap behavior (fail-conservative).
        """
        try:
            if not entry_ts or entry_ts <= 0:
                return None
            # .get() (not key-in + index): the >512 clear() below can race
            # this read across SL/TP worker threads; a KeyError here would
            # fall to the broad except → None → old-rule fire on a position
            # the rule intends to exempt (2026-06-11 review).
            _memo_hit = self._entry_gap_memo.get((base, int(entry_ts)))
            if _memo_hit is not None:
                return _memo_hit
            key = (base, int(entry_ts))
            if not self._exchanges:
                return None

            import pandas as pd

            from utils.indicators import ema

            try:
                from config import ANALYSIS_ONLY_BASES
                _perp_only = base in ANALYSIS_ONLY_BASES
            except ImportError:
                _perp_only = False
            # Mirror _fetch_exchange_indicators venue order exactly: spot
            # across ALL exchanges first, perp fallback second — so the
            # entry-gap EMAs come from the same series family as the cached
            # current-gap EMAs (basis differences matter at the ±0.15%
            # decision boundary; 2026-06-11 review).
            raw = None
            if not _perp_only:
                for exchange in self._exchanges.values():
                    raw = exchange.fetch_ohlcv(f"{base}/USDT", "4h",
                                               limit=250, market_type="spot")
                    if raw and len(raw) >= 50:
                        break
            if not raw or len(raw) < 50:
                for exchange in self._exchanges.values():
                    raw = exchange.fetch_ohlcv(f"{base}/USDT:USDT", "4h",
                                               limit=250, market_type="futures")
                    if raw and len(raw) >= 50:
                        break
            if not raw or len(raw) < 50:
                return None

            # Entry bar = LAST bar whose open timestamp <= entry time.
            # ccxt ts is epoch ms (bar open); entry_ts is epoch seconds.
            entry_ms = float(entry_ts) * 1000.0
            idx = -1
            for i, candle in enumerate(raw):
                if float(candle[0]) <= entry_ms:
                    idx = i
                else:
                    break
            # idx < 49 → entry predates the window, or fewer than 50 bars
            # of recursive-EMA warmup before the entry bar → unknown.
            if idx < 49:
                return None

            close = pd.Series([float(c[4]) for c in raw[: idx + 1]])
            e20 = float(ema(close, 20).iloc[-1])
            e50 = float(ema(close, 50).iloc[-1])
            if pd.isna(e20) or pd.isna(e50) or e50 <= 0:
                return None
            entry_gap = (e20 - e50) / e50 * 100.0

            if len(self._entry_gap_memo) > 512:
                self._entry_gap_memo.clear()
            self._entry_gap_memo[key] = entry_gap
            logger.info(
                f"[MCP-Brain] entry-bar 4h gap {base} @ {int(entry_ts)}: "
                f"{entry_gap:+.2f}% (bar {idx}/{len(raw) - 1})")
            return entry_gap
        except Exception as e:
            logger.debug(f"[MCP-Brain] entry-gap compute failed ({base}): {e}")
            return None

    def _fetch_exchange_indicators(self, coins: list) -> dict:
        """Fetch OHLCV from primary exchange for 3 TFs and compute indicators.
        Returns {COIN: {tf: {adx, rsi, ema_dir, atr_pct, vol_ratio}}}."""
        now = time.time()
        if now - self._indicator_cache_time < 120 and self._indicator_cache:
            return self._indicator_cache

        if not self._exchanges:
            return {}

        import pandas as pd

        from exchanges.base import closed_ohlcv
        from utils.indicators import adx, atr, bbands, ema, macd, rolling_vwap, rsi
        from utils.smart_money import compute_smart_money_signals

        # Build ordered list of exchanges to try per coin
        exchange_list = list(self._exchanges.values())
        if not exchange_list:
            return {}

        results = {}
        timeframes = ["4h", "1h", "15m"]
        # Warmup: scripts/indicator_bias_audit.py (2026-06-08) showed ADX(14) drifts ~34% and
        # EMA50 ~0.9% at <200 bars (ewm adjust=False is recursive). The 4 REQUIRED entry gates
        # (4h/1h EMA20-50, RSI, ADX>=20) must be computed on CONVERGED indicators, so the old
        # 80/100 warmup was too short (ADX needs ~200) → backtest-vs-live divergence on the gate
        # values. 250 bars is one API call on every venue and converges all of EMA50/RSI/ADX.
        # (Correctness/fidelity fix; expectancy-neutral — does not change the NO-EDGE verdict.)
        limit_map = {"4h": 250, "1h": 250, "15m": 250}

        # Fetch the top-25 analyzed coins, PLUS any analysis-only research
        # instruments (commodity/equity perps) so their indicators are computed
        # + warehoused even though they sit beyond the cap. They never trade
        # (hard-blocked in bot_engine._execute_open).
        from config import ANALYSIS_ONLY_BASES
        _fetch_coins = list(coins[:25])
        for _c in coins:
            if _c in ANALYSIS_ONLY_BASES and _c not in _fetch_coins:
                _fetch_coins.append(_c)

        for coin in _fetch_coins:
            symbol = f"{coin}/USDT"
            _perp_only = coin in ANALYSIS_ONLY_BASES  # commodity/equity perps have no spot
            # Every timeframe must describe one executable instrument.  The
            # previous per-timeframe fallback could combine spot and perpetual
            # candles from different venues into a synthetic signal.
            _source_candidates = [
                (exchange, "futures", f"{coin}/USDT:USDT")
                for exchange in exchange_list
            ]
            if not _perp_only:
                _source_candidates.extend(
                    (exchange, "spot", symbol) for exchange in exchange_list
                )
            _selected_source = None
            for _candidate_exchange, _candidate_market, _candidate_symbol in _source_candidates:
                _candidate_rows = {}
                try:
                    for _candidate_tf in timeframes:
                        _candidate_raw = _candidate_exchange.fetch_ohlcv(
                            _candidate_symbol,
                            _candidate_tf,
                            limit=limit_map[_candidate_tf],
                            market_type=_candidate_market,
                        )
                        _candidate_raw = closed_ohlcv(
                            _candidate_raw, _candidate_tf, now_s=now
                        )
                        if len(_candidate_raw) < 30:
                            _candidate_rows = {}
                            break
                        _candidate_rows[_candidate_tf] = _candidate_raw
                except Exception:
                    _candidate_rows = {}
                if len(_candidate_rows) == len(timeframes):
                    _selected_source = (
                        _candidate_exchange,
                        _candidate_market,
                        _candidate_symbol,
                        _candidate_rows,
                    )
                    break
            if _selected_source is None:
                continue
            _source_exchange, _source_market, _source_symbol, _source_rows = _selected_source
            _source_venue = str(getattr(_source_exchange, "name", "") or "").lower()
            coin_data = {}
            for tf in timeframes:
                try:
                    # Try each exchange until one returns valid data
                    raw = _source_rows.get(tf)
                    if raw is None and not _perp_only:
                        for exchange in exchange_list:
                            raw = exchange.fetch_ohlcv(symbol, tf,
                                                        limit=limit_map[tf],
                                                        market_type="spot")
                            if raw and len(raw) >= 30:
                                break
                    # Perp fallback — and the ONLY attempt for perp-only instruments
                    # (XAU, CL, TSLA, ...): the spot name {coin}/USDT 404s, so skip
                    # the doomed spot fetch for those and go straight to the linear perp.
                    if not raw or len(raw) < 30:
                        for exchange in exchange_list:
                            raw = exchange.fetch_ohlcv(f"{coin}/USDT:USDT", tf,
                                                        limit=limit_map[tf],
                                                        market_type="futures")
                            if raw and len(raw) >= 30:
                                break
                    if not raw or len(raw) < 30:
                        continue
                    df = pd.DataFrame(raw,
                                      columns=["ts", "open", "high", "low",
                                               "close", "volume"])
                    df.dropna(inplace=True)
                    if len(df) < 30:
                        continue

                    close = df["close"]
                    high  = df["high"]
                    low   = df["low"]
                    vol   = df["volume"]

                    adx_s, pdi_s, mdi_s = adx(high, low, close, 14)
                    rsi_s = rsi(close, 14)
                    atr_s = atr(high, low, close, 14)
                    ema9  = ema(close, 9)
                    ema20 = ema(close, 20)
                    ema21 = ema(close, 21)
                    ema50_s = ema(close, 50)

                    # MACD (12, 26, 9) — momentum direction + acceleration
                    macd_line, macd_sig, macd_hist = macd(close, 12, 26, 9)

                    # Bollinger Band width — regime detection (squeeze = chop)
                    bb_lo, bb_mid, bb_hi = bbands(close, 20, 2.0)

                    price     = float(close.iloc[-1])
                    adx_val   = float(adx_s.iloc[-1])
                    pdi_val   = float(pdi_s.iloc[-1])
                    mdi_val   = float(mdi_s.iloc[-1])
                    rsi_val   = float(rsi_s.iloc[-1])
                    atr_pct   = float(atr_s.iloc[-1]) / max(price, 1e-9)
                    atr_abs   = float(atr_s.iloc[-1])
                    ema9_val  = float(ema9.iloc[-1])
                    ema21_val = float(ema21.iloc[-1])
                    ema50_val = float(ema50_s.iloc[-1])
                    ema20_val = float(ema20.iloc[-1])
                    ema20_prev = float(ema20.iloc[-2]) if len(ema20) > 1 else ema20_val
                    ema50_prev = float(ema50_s.iloc[-2]) if len(ema50_s) > 1 else ema50_val
                    vol_ma    = float(vol.rolling(20).mean().iloc[-1])
                    vol_ratio = float(vol.iloc[-1]) / max(vol_ma, 1e-9)

                    # MACD histogram: current, previous, and direction
                    macd_hist_val  = float(macd_hist.iloc[-1])
                    macd_hist_prev = float(macd_hist.iloc[-2]) if len(macd_hist) > 1 else 0.0
                    macd_hist_rising = macd_hist_val > macd_hist_prev

                    # EMA20 slope: normalized rate of change (positive = rising)
                    ema20_slope = (ema20_val - ema20_prev) / max(abs(ema20_prev), 1e-9)

                    # Bollinger Band width %  (narrow = squeeze/chop)
                    bb_width = 0.0
                    if bb_mid.iloc[-1] > 0:
                        bb_width = float((bb_hi.iloc[-1] - bb_lo.iloc[-1]) / bb_mid.iloc[-1]) * 100

                    # EMA alignment direction
                    if ema9_val > ema21_val > ema50_val:
                        ema_dir = "up"
                    elif ema9_val < ema21_val < ema50_val:
                        ema_dir = "down"
                    else:
                        ema_dir = "mixed"

                    # EMA 20/50 crossover detection
                    ema_cross_up = (ema20_val > ema50_val and ema20_prev <= ema50_prev)
                    ema_cross_down = (ema20_val < ema50_val and ema20_prev >= ema50_prev)
                    ema20_above_50 = ema20_val > ema50_val

                    # Market structure: 5-bar swing high/low detection
                    # (replaces single-bar comparison which was pure noise)
                    # AUDIT 2026-06-03: the window below INCLUDES the current bar
                    # (range starts at 1 = high.iloc[-1]), so max(recent_highs) >= curr_h and
                    # hh / ll are mathematically ALWAYS False -> the B5 +8 structure bonus never
                    # fires for either side. Left AS-IS DELIBERATELY (not a silent oversight):
                    # to enable it, compare against PRIOR bars only via range(2, n_swing + 2).
                    # But enabling B5 lets more setups reach score>=65 -> MORE entries, and on a
                    # CONFIRMED NO_EDGE system more trades = more fee bleed, not more profit. The
                    # dead bonus is symmetric (gates both long hh and short ll) so it adds no
                    # directional bias. Re-enable ONLY if a validated edge ever justifies higher
                    # entry frequency. (Capital-preservation > feature-completeness here.)
                    hh = hl = ll = lh = False
                    n_swing = min(5, len(high) - 1)
                    if n_swing >= 3:
                        recent_highs = [float(high.iloc[-i]) for i in range(1, n_swing + 1)]
                        recent_lows  = [float(low.iloc[-i])  for i in range(1, n_swing + 1)]
                        curr_h, prev_h = float(high.iloc[-1]), max(recent_highs)
                        curr_l, prev_l = float(low.iloc[-1]),  min(recent_lows)
                        # Swing high = current high exceeds all recent N highs
                        hh = curr_h > prev_h
                        # Swing low = current low above lowest of recent N lows
                        hl = curr_l > min(recent_lows)
                        ll = curr_l < prev_l
                        lh = curr_h < max(recent_highs)

                    price_above_ema20 = price > ema20_val
                    price_above_ema50 = price > ema50_val

                    rvwap = rolling_vwap(high, low, close, vol, 20)
                    vwap_val = float(rvwap.iloc[-1]) if not pd.isna(rvwap.iloc[-1]) else price
                    price_vs_vwap = (price - vwap_val) / max(vwap_val, 1e-9) * 100

                    # Candle body analysis (news candle detection)
                    bodies = (df["close"] - df["open"]).abs()
                    last_body = float(bodies.iloc[-1]) if len(bodies) > 0 else 0
                    avg_body_20 = float(bodies.tail(20).mean()) if len(bodies) >= 20 else float(bodies.mean())

                    # ── Smart Money / ICT indicators (2026-04-16) ──────────
                    try:
                        smc = compute_smart_money_signals(
                            df["open"], high, low, close, vol)
                    except Exception:
                        smc = {"smc_structure": 0, "smc_entry": 0,
                               "smc_confirmation": 0,
                               "fibonacci": {}, "fvg": {}, "supply_demand": {},
                               "liquidity_sweep": {}, "order_blocks": {},
                               "market_structure": {}, "volume_profile": {},
                               "ote": {}, "price_action": {}}

                    coin_data[tf] = {
                        "source_venue": _source_venue,
                        "source_market_type": _source_market,
                        "source_symbol": _source_symbol,
                        "candle_ts": int(df["ts"].iloc[-1]),
                        "adx": round(adx_val, 1),
                        "pdi": round(pdi_val, 1),
                        "mdi": round(mdi_val, 1),
                        "rsi": round(rsi_val, 1),
                        "ema_dir": ema_dir,
                        "atr_pct": round(atr_pct * 100, 2),
                        "atr_abs": round(atr_abs, 6),
                        "vol_ratio": round(vol_ratio, 2),
                        "price": round(price, 6),
                        # MACD momentum
                        "macd_hist": round(macd_hist_val, 6),
                        "macd_hist_prev": round(macd_hist_prev, 6),
                        "macd_hist_rising": macd_hist_rising,
                        # EMA slope & Bollinger
                        "ema20_slope": round(ema20_slope, 6),
                        "bb_width": round(bb_width, 2),
                        # Systematic entry data
                        "ema20": round(ema20_val, 6),
                        "ema50": round(ema50_val, 6),
                        "ema20_above_50": ema20_above_50,
                        "ema_cross_up": ema_cross_up,
                        "ema_cross_down": ema_cross_down,
                        "price_above_ema20": price_above_ema20,
                        "price_above_ema50": price_above_ema50,
                        "hh": hh, "hl": hl,
                        "ll": ll, "lh": lh,
                        "last_body": round(last_body, 6),
                        "avg_body_20": round(avg_body_20, 6),
                        "vwap": round(vwap_val, 6),
                        "price_vs_vwap": round(price_vs_vwap, 4),
                        # Smart Money / ICT composite scores
                        "smc_structure": smc.get("smc_structure", 0),
                        "smc_entry": smc.get("smc_entry", 0),
                        "smc_confirmation": smc.get("smc_confirmation", 0),
                        # Smart Money detail (for logging/debug)
                        "smc_detail": {
                            "fib": smc.get("fibonacci", {}),
                            "fvg": smc.get("fvg", {}),
                            "sd": smc.get("supply_demand", {}),
                            "sweep": smc.get("liquidity_sweep", {}),
                            "ob": smc.get("order_blocks", {}),
                            "ms": smc.get("market_structure", {}),
                            "vp": smc.get("volume_profile", {}),
                            "ote": smc.get("ote", {}),
                            "pa": smc.get("price_action", {}),
                        },
                    }
                except Exception:
                    continue

            if coin_data:
                results[coin] = coin_data

        self._indicator_cache = results
        self._indicator_cache_time = now
        logger.info(f"[MCP-Brain] Exchange indicators: {len(results)} coins, "
                     f"{sum(len(v) for v in results.values())} TF snapshots")
        return results

    @staticmethod
    def _safe_float(val, default: float = 0.0) -> float:
        if val is None:
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_int(val, default: int = 0) -> int:
        if val is None:
            return default
        try:
            return int(float(val))
        except (TypeError, ValueError):
            return default

    # ──────────────────────────────────────────────────────────────────
    # PORTFOLIO ANALYSIS — algorithmic scorer
    # ──────────────────────────────────────────────────────────────────

    def analyze_portfolio(self, coins: list, open_positions: list,
                          exchange_balances: dict, risk_envelope: dict,
                          recent_trades: list, news_context: dict = None) -> list:
        """
        Main entry point: gather all data, score coins algorithmically.
        Returns list of action dicts: [{type, symbol, exchange, ...}]
        """
        if not self._enabled:
            return []

        now = time.time()
        if now - self._last_entry_run < ENTRY_COOLDOWN:
            # 2026-04-11: was `return self._last_trade_actions` — that caused
            # duplicate execution because bot_engine._claude_portfolio_cycle
            # has no dedupe, so replaying the cached list placed the same
            # orders multiple times. Return empty on cooldown; the engine
            # will just log "No actions this cycle" and try again next tick.
            return []
        self._last_entry_run = now

        # Fetch all external data sources; retry once on total blackout
        data = self._fetch_all_data(coins)
        if data["sources_ok"] < 2:
            # Phase 39: clear cache and retry once — transient network blip
            logger.warning("[MCP-Brain] < 2 data sources — retrying once")
            self._cached_data = {}
            self._cache_time  = 0
            import time as _time_mod
            _time_mod.sleep(5)
            data = self._fetch_all_data(coins)
        if data["sources_ok"] < 2:
            logger.warning("[MCP-Brain] < 2 data sources after retry — no actions")
            return []

        # ── Refresh Data Coordinator feeds (2026-05-27) ───────────────
        # Runs in parallel threads; each feed respects its own TTL.
        # Non-blocking: if a feed is slow, stale data is used.
        if self._data_coordinator is not None:
            try:
                coin_bases = [c.split("/")[0].upper() for c in coins]
                self._data_coordinator.set_coins(coin_bases)
                # Compute 6h price changes for OI divergence signal
                _price_changes = {}
                for c in coin_bases[:10]:
                    gecko = data.get("gecko", {}).get(c, {})
                    chg = gecko.get("change_24h", 0)
                    if chg:
                        _price_changes[c] = float(chg) / 100.0 / 4.0  # rough 6h
                self._data_coordinator.set_price_changes(_price_changes)
                self._data_coordinator.refresh()
            except Exception as _dc_err:
                logger.debug(f"[MCP-Brain] DataCoordinator refresh: {_dc_err}")

        # Fetch multi-TF exchange indicators
        exchange_indicators = self._fetch_exchange_indicators(coins)

        actions = []
        if risk_envelope.get("max_new_positions", 0) > 0:
            try:
                actions = self._algorithmic_portfolio(
                    coins, data, exchange_indicators,
                    open_positions, exchange_balances, risk_envelope)
                if actions:
                    logger.info(
                        f"[MCP-Brain] Algorithmic portfolio: {len(actions)} actions")
            except Exception as e:
                logger.error(f"[MCP-Brain] Algorithmic scoring failed: {e}")

        # Provenance backstop: every action must carry a fresh decision_id + source.
        for a in actions:
            if isinstance(a, dict) and not a.get("decision_id"):
                a["decision_id"] = str(uuid.uuid4())
                a.setdefault("source", "algo")

        self._last_trade_actions = actions
        self._log_decisions({"actions": actions}, "portfolio")
        self._save_state()

        # Record for accuracy tracking
        for a in actions:
            if a["type"] == "OPEN":
                coin = a["symbol"].split("/")[0]
                price = data["prices"].get(coin, 0)
                side_action = "BUY" if a["side"] == "buy" else "SELL"
                self._accuracy.record_decision(
                    coin, side_action, price, a["confidence"])

        return actions

    def get_trade_actions(self) -> list:
        """Return OPEN/CLOSE actions from last analyze_portfolio call."""
        return list(self._last_trade_actions)

    # ──────────────────────────────────────────────────────────────────
    # LEGACY: analyze_coins (kept for backward compatibility)
    # ──────────────────────────────────────────────────────────────────

    def analyze_coins(self, coins: list, open_positions: list = None,
                      portfolio_value: float = 0, daily_pnl: float = 0,
                      exchange_balances: dict = None,
                      portfolio_coins: dict = None,
                      strategy_signals: dict = None) -> dict:
        """Legacy entry analysis — returns BUY/SELL/HOLD per coin using scoring engine."""
        return self._last_decisions

    # ──────────────────────────────────────────────────────────────────
    # MODE B: POSITION MONITOR (every 45s for open positions)
    # ──────────────────────────────────────────────────────────────────

    def monitor_positions(self, positions: list) -> dict:
        """
        Lightweight position monitoring. Returns per-position advice:
          HOLD     — keep position, signals still aligned
          CLOSE    — close immediately, reversal detected
          TIGHTEN  — tighten SL (move closer)
          BREAKEVEN — move SL to breakeven now
        """
        if not self._enabled or not positions:
            return {}

        now = time.time()
        # Adaptive cooldown: check faster when positions are profitable or volatile
        cooldown = POSITION_COOLDOWN
        for p in positions:
            pnl_pct = p.get("pnl_pct", 0) or 0
            if pnl_pct >= 2.0:
                cooldown = min(45, POSITION_COOLDOWN // 3)  # 30s for profitable positions (profit-taking priority)
                break
            if abs(pnl_pct) > 1.5:
                cooldown = min(60, POSITION_COOLDOWN // 2)  # Faster checks for volatile positions
                break
        if now - self._last_position_run < cooldown:
            return self._last_position_advice
        self._last_position_run = now

        coins = list(set(p.get("symbol", "?").split("/")[0] for p in positions))
        data = self._fetch_all_data(coins)

        # Fetch exchange indicators for position coins
        exchange_indicators = self._fetch_exchange_indicators(coins)

        advice = {}
        try:
            advice = self._algorithmic_position_monitor(
                positions, data, exchange_indicators)
            if advice:
                logger.info(
                    f"[MCP-Brain] Algorithmic monitor: "
                    f"{sum(1 for a in advice.values() if a.get('action') != 'HOLD')} actions")
        except Exception as e:
            logger.error(f"[MCP-Brain] Algorithmic monitor failed: {e}")

        if advice:
            # Provenance backstop — mirror of the analyze_portfolio merge
            for adv in advice.values():
                if isinstance(adv, dict) and not adv.get("decision_id"):
                    adv["decision_id"] = str(uuid.uuid4())
                    adv.setdefault("source", "algo")
            self._last_position_advice = advice
            self._log_decisions(advice, "position_monitor")
            self._save_state()
            return advice

        return self._last_position_advice

    # ──────────────────────────────────────────────────────────────────
    # CONFIDENCE ADJUSTMENT (entry pipeline)
    # ──────────────────────────────────────────────────────────────────

    def get_confidence_adjustment(self, coin: str, direction: str) -> float:
        """Returns multiplier for bot engine confidence pipeline."""
        dec = self._last_decisions.get(coin, {})
        action = dec.get("action", "HOLD")
        mcp_conf = dec.get("confidence", 0.5)

        if action == "HOLD":
            return 0.90

        # Aligned → boost (scaled by confidence)
        if (action == "BUY" and direction == "buy") or \
           (action == "SELL" and direction == "sell"):
            return min(1.30, 0.90 + mcp_conf * 0.40)

        # Conflicting → reduce harder
        if (action == "BUY" and direction == "sell") or \
           (action == "SELL" and direction == "buy"):
            return max(0.55, 0.80 - mcp_conf * 0.25)

        return 1.0

    # ──────────────────────────────────────────────────────────────────
    # EXIT INTELLIGENCE (used by anti-loss gate)
    # ──────────────────────────────────────────────────────────────────

    def should_hold_position(self, coin: str, side: str, loss_pct: float) -> bool:
        """
        Should we hold a losing position instead of closing at SL?
        Opus analyzes whether the trade thesis is still valid.
        More generous with holding — only force-close on confirmed reversals.
        """
        if not self._enabled:
            return False

        # Check position monitor advice first (most current — Opus analysis)
        for pid, adv in self._last_position_advice.items():
            if coin in str(pid):
                action = adv.get("action", "HOLD")
                conf = adv.get("confidence", 0)
                if action == "CLOSE" and conf >= 0.80:
                    return False  # Opus is confident: close it
                if action in ("HOLD", "BREAKEVEN"):
                    # Hold if Opus says so and loss isn't catastrophic
                    if conf >= 0.55 and loss_pct < 4.0:
                        logger.info(
                            f"[MCP-Brain] HOLD (Opus monitor): {coin} {side} — "
                            f"advice={action} conf={conf:.0%}, loss={loss_pct:.2f}%")
                        return True

        # Fall back to entry decisions
        dec = self._last_decisions.get(coin, {})
        if not dec:
            return False

        action = dec.get("action", "HOLD")
        confidence = dec.get("confidence", 0)

        # If loss < 3% and entry thesis still holds, hold
        if loss_pct > 3.0:
            return False

        if side == "buy" and action == "BUY" and confidence >= 0.55:
            logger.info(
                f"[MCP-Brain] HOLD (entry thesis): {coin} BUY — "
                f"conf={confidence:.0%}, loss={loss_pct:.2f}%")
            return True
        if side == "sell" and action == "SELL" and confidence >= 0.55:
            logger.info(
                f"[MCP-Brain] HOLD (entry thesis): {coin} SELL — "
                f"conf={confidence:.0%}, loss={loss_pct:.2f}%")
            return True

        return False

    def get_position_advice(self, position_id: str) -> dict:
        """Get specific advice for a position from last monitor run."""
        return self._last_position_advice.get(position_id, {})

    def should_take_profit(self, coin: str, side: str, pnl_pct: float) -> bool:
        """
        Should we take profit on this position NOW?
        Called by order_manager when price hits TP or position is profitable.
        MCP Brain is the SOLE authority — if it says TAKE_PROFIT or CLOSE, we exit.
        If it says HOLD (strong trend), we let trailing stop ride.
        """
        if not self._enabled:
            return True  # No brain = use default TP behavior

        # Check position monitor advice first (most recent Opus analysis)
        for pid, adv in self._last_position_advice.items():
            if coin in str(pid):
                action = adv.get("action", "HOLD")
                conf = adv.get("confidence", 0)
                # MCP explicitly says take profit or close
                if action in ("TAKE_PROFIT", "CLOSE") and conf >= 0.60:
                    logger.info(
                        f"[MCP-Brain] TAKE PROFIT approved: {coin} {side} "
                        f"pnl={pnl_pct:+.1f}% — MCP says {action} conf={conf:.0%}")
                    return True
                # MCP says TIGHTEN — let trailing handle it, don't take profit yet
                if action == "TIGHTEN":
                    logger.info(
                        f"[MCP-Brain] TIGHTEN (not taking profit yet): {coin} {side} "
                        f"pnl={pnl_pct:+.1f}% — MCP wants tighter SL")
                    return False
                # MCP says HOLD with high confidence — trend strong, let it ride
                if action == "HOLD" and conf >= 0.75:
                    logger.info(
                        f"[MCP-Brain] RIDE (MCP says HOLD): {coin} {side} "
                        f"pnl={pnl_pct:+.1f}% — trend still strong conf={conf:.0%}")
                    return False

        # No MCP advice available — take profit if > 3% (safe default)
        if pnl_pct >= 3.0:
            return True
        # Small profit with no MCP guidance — let trailing stop handle it
        return False

    # ──────────────────────────────────────────────────────────────────

    def _score_coin(self, coin: str, data: dict, ei: dict) -> dict:
        return score_coin(data_coordinator=self._data_coordinator, coin=coin, data=data, ei=ei)

    def _score_coin_scalp(self, coin: str, data: dict, ei: dict) -> dict:
        return score_coin_scalp(coin, data, ei)

    def _route_score_coin(self, coin: str, data: dict, ei: dict) -> dict:
        return route_score_coin(self, coin, data, ei)

    def _algorithmic_portfolio(self, coins, data, exchange_indicators,
                                open_positions, exchange_balances,
                                risk_envelope) -> list:
        return algorithmic_portfolio(
            self, coins, data, exchange_indicators,
            open_positions, exchange_balances, risk_envelope,
        )

    def _algorithmic_position_monitor(self, positions: list, data: dict,
                                       exchange_indicators: dict) -> dict:
        return algorithmic_position_monitor(positions, data, exchange_indicators)

    # ──────────────────────────────────────────────────────────────────
    # LOGGING
    # ──────────────────────────────────────────────────────────────────

    def _append_decision_line(self, record: dict):
        """Shared append path for decision + rejection rows.

        Rotation (E-1 CRITICAL fix, 2026-06-12): the old truncate-to-500-lines
        destroyed the week's history and made the reconciler report phantom
        orphans. At >2MB the file is now ARCHIVE-RENAMED to
        mcp_decisions.<YYYYMMDD-HHMMSS>.jsonl and a fresh active file is
        created at the SAME filename (health_watchdog tails it). History is
        bounded per-file but never destroyed.
        """
        try:
            DECISION_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(DECISION_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
            if DECISION_LOG.stat().st_size > 2_000_000:  # > 2MB
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                archive = DECISION_LOG.with_name(
                    f"{DECISION_LOG.stem}.{stamp}{DECISION_LOG.suffix}")
                DECISION_LOG.replace(archive)
                DECISION_LOG.touch()
                logger.info(
                    f"[MCP-Brain] Decision log rotated → {archive.name}")
        except Exception:
            pass

    def _log_decisions(self, decisions: dict, dtype: str = "entry"):
        self._append_decision_line({
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": dtype, "decisions": decisions,
        })

    def log_rejection(self, decision_id: str | None, symbol: str,
                      reason: str, stage: str):
        """Provenance (2026-06-12): link an execution-layer rejection back to
        the decision that produced the action. Same file as decisions so the
        reconciler joins on decision_id without a new store (plan 0E)."""
        self._append_decision_line({
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "rejection",
            "decision_id": decision_id,
            "symbol": symbol,
            "reason": reason,
            "stage": stage,
        })

    def last_decisions(self) -> dict:
        return self._last_decisions

    def last_fund_ops(self) -> list:
        """Return fund management operations from last analysis, then clear."""
        ops = list(self._last_fund_ops)
        self._last_fund_ops = []
        return ops

    def last_position_advice(self) -> dict:
        return self._last_position_advice

    def accuracy_stats(self) -> dict:
        return self._accuracy.stats()

# ─────────────────────────────────────────────────────────────────────
# 2026-05-19 Patch #3 — mcp_take_profit path amplification.
#
# Returns 0.0 → 1.0 indicating how close the position is to firing
# `mcp_take_profit`. `_try_soft_close` in core/order_manager.py reads
# this and defers a STALE / AGE_LIMIT / AGE_LOSS close when proximity
# meets config.MCP_TP_PROXIMITY_THRESHOLD.
#
# Plan deviation (declared): spec §5.3 references position attributes
# that don't exist on the dataclass (`tp_price`, `entry_atr`,
# `target_pnl_pct`). The Position dataclass at
# core/position_tracker.py:117 only has `take_profit` and `entry_price`.
# Falls back as follows:
#   tp_price          ← pos.take_profit
#   entry_atr proxy   ← abs(take_profit − entry_price) — the entry-to-TP
#                       distance unit. Means dist_score and pnl_score
#                       carry correlated signal (both measure entry→TP
#                       travel fraction), so the formula effectively
#                       weights that travel 80% and momentum 20%. That's
#                       acceptable — the patch's purpose is to detect
#                       "close to firing TP", which both terms answer.
#   target_pnl_pct    ← (take_profit − entry_price) / entry_price for
#                       longs, sign-flipped for shorts. Same units as
#                       pos.unrealized_pnl_pct (fraction, not %).
#   ema_aligned helper ← absent → momentum_score = 0.5 (neutral). When
#                       a future EMA-alignment helper lands, swap it in.
#
# Callers in `_try_soft_close` populate `pos.current_px` and
# `pos.unrealized_pnl_pct` before invoking. Missing attributes
# return 0.0 (safe default — skips the amplification, soft-close
# fires normally).
# ─────────────────────────────────────────────────────────────────────
def score_take_profit_proximity(position) -> float:
    """Score 0.0 (far from TP) → 1.0 (about to fire TP).

    Weights: distance-to-TP 50%, pnl-progress 30%, momentum 20%.
    Returns 0.0 if required attributes are missing.
    """
    try:
        tp = getattr(position, "take_profit", None)
        entry = getattr(position, "entry_price", None)
        current = getattr(position, "current_px", None)
        upnl_frac = getattr(position, "unrealized_pnl_pct", None)
        side = getattr(position, "side", "buy")

        if not tp or not entry or current is None or upnl_frac is None:
            return 0.0
        if tp <= 0 or entry <= 0:
            return 0.0

        # ATR proxy = entry→TP distance unit. Cannot be zero (would
        # mean TP == entry, which never happens for a legit setup).
        atr_proxy = abs(float(tp) - float(entry))
        if atr_proxy <= 0:
            return 0.0

        # Distance from current price to TP, in ATR-proxy units.
        # /2.0 keeps the score curve gentle: at 1 ATR-proxy away the
        # score contribution is 0.5, at 2 ATR-proxy away it's 0.0.
        dist_atr = abs(float(current) - float(tp)) / atr_proxy
        dist_score = max(0.0, 1.0 - dist_atr / 2.0)

        # PnL progress as fraction of target_pnl_pct.
        # target_pnl_pct: how much price must move from entry to TP,
        # as a signed fraction. For longs positive, for shorts negative.
        # We use abs() on both sides so the ratio is in [0, 1].
        if side == "buy":
            target_frac = (float(tp) - float(entry)) / float(entry)
        else:
            target_frac = (float(entry) - float(tp)) / float(entry)
        if abs(target_frac) <= 0:
            return 0.0
        pnl_progress = float(upnl_frac) / target_frac
        pnl_score = max(0.0, min(1.0, pnl_progress))

        # Momentum: spec calls ema_aligned(); not implemented in this
        # brain yet. Neutral 0.5 keeps the term well-defined.
        momentum_score = 0.5

        return 0.5 * dist_score + 0.3 * pnl_score + 0.2 * momentum_score
    except Exception:
        return 0.0
