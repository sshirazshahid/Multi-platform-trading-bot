"""core/research_brief.py — per-asset research brief assembly (DESCRIPTIVE ONLY).

Pure and network-free. Every input arrives as an argument (including `now`), so
the builders are deterministic and unit-testable without touching disk, the
network, or the clock. All IO — ccxt, parquet/CSV/JSON reads, warehouse queries,
rendering to `reports/` — lives in `scripts/research_brief.py`.

WHAT THIS IS
    A dated, sourced RECORD of what the repo already stores about each asset in
    the bot's spec-derived probe universe, plus an honest account of what it
    does NOT store. It is the "research brief" stage of the committee flow.

WHAT THIS IS NOT
    It makes no market claim, no directional statement, and no trading
    instruction. It has no order path, no promotion authority, and no
    language-model call (cost is exactly $0, mechanically asserted by
    tests/test_research_brief.py). Values are measurements, not advice.

STRUCTURAL HONESTY — the whole design
    Every number is a Finding record, never prose:
        {id, kind, scope, section, label, value, numeric, unit, as_of_utc,
         source, source_kind, source_fn, confidence, confidence_basis,
         derived_from, expected_source, remediation}
    - kind FACT       : read/computed directly by a named `source_fn` from a
                        named `source`; `derived_from` is always [].
    - kind INFERENCE  : derived by THIS module from other findings of the SAME
                        asset; `derived_from` is non-empty and every id in it
                        resolves to a FACT of that asset.
    - kind ABSENT     : a RECORD of a missing input — never a missing key —
                        carrying `expected_source` and a `remediation` command.
    - `as_of_utc` is the DATA's timestamp (last bar / last settlement / cache
      fetch time). It is NEVER the run time, so stale inputs keep their true
      old stamp instead of being laundered as fresh.
    - `confidence` comes from the pure `confidence_for()` function of
      (staleness_days, n, min_n, n_sources) and is clamped by the asset's
      `confidence_ceiling`. It is never authored. A single-source measurement
      cannot exceed MEDIUM: corroboration is part of the claim, not decoration.

DATA-QUALITY GATING
    `data_quality` is the first key of every asset and the first rendered
    section. Its verdict comes from `core.pair_dossier.classify_verdict`
    (promoted verbatim from the 27_ provenance run) — DATA_OK / PARTIAL /
    BACKFILL_FIRST, data readiness only, never direction. BACKFILL_FIRST assets
    still emit a full brief, with a readiness banner above every number and the
    confidence ceiling clamped to LOW, because dropping them would hide that the
    data exists and is merely old.

ANTI-LEAK MECHANISM (mandatory, see tests 1 and 4)
    Several upstream feeds emit position-flavoured vocabulary that must never
    reach a descriptive document. Every feed dict is therefore projected through
    the frozen `FEED_ALLOWLIST` (`project_feed`, never `dict.update`), and the
    callables in `BANNED_CALLABLES` are never invoked from this layer (an AST
    test enforces it). Position-flavoured indicator outputs are RENAMED rather
    than exempted:
        pair_dossier `psar_side` long|short   -> `price_vs_psar` above|below
        pair_dossier `supertrend_10_3` up|down-> `price_vs_supertrend` above|below
        pair_dossier `macd.signal`            -> `macd_signal_line`
        pair_dossier `sma50_vs_sma200` golden|death -> above|below
    RSI / KDJ / Williams %R are emitted as numbers plus an own-history
    percentile only — never as a zone label.
    `BANNED_CALLABLES` and `BANNED_FEED_KEYS` are source-only constants: their
    literals contain the very vocabulary being excluded, so they are never
    serialized into a document.

ABSENT BY DESIGN (stated plainly, not papered over)
    Per-asset fundamentals (TVL, revenue, holders, active addresses, emissions)
    and exchange net-flows have no layer in this repo and none is planned —
    they are emitted as ABSENT records with that reason, not as placeholders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType

# ── vocabulary constants (source-only; never serialized into a document) ──────
BANNED_CALLABLES = frozenset({
    "core.market_regime.detect",
    "core.market_regime.detect_detailed",
    "core.market_regime.allowed_strategies",
    "core.news_scanner.NewsScanner.get_news_signals",
    "core.news_scanner.NewsScanner.is_bearish_environment",
    "core.news_scanner.NewsScanner.is_bullish_environment",
})

BANNED_FEED_KEYS = frozenset({
    "fr_side_signal", "fr_extreme", "crowd_signal", "has_veto_news",
    "veto_reason", "smart_money_inflow", "social_hype_extreme",
    "oi_price_divergence", "oi_conviction", "recommendation",
    "psar_side", "supertrend_10_3",
    "news_signal_count", "news_veto", "overall_sentiment",
})
# `sma50_vs_sma200` is NOT in the set above: the key name is harmless, it is its
# pair_dossier VALUES (golden|death) that read as a call, so that one is fixed by
# a value-level rename to above|below rather than by dropping the key.

FEED_ALLOWLIST = MappingProxyType({
    # scripts/run_intel_synthesis.latest_market_intel snapshot
    "market_intel": frozenset({
        "ts_utc", "fng_value", "fng_class", "btc_dom", "eth_dom",
        "breadth_green", "breadth_total", "total_tvl", "stable_total",
    }),
    # scripts/run_intel_synthesis.funding_oi_summary
    "funding_oi": frozenset({"oi_usd", "oi_chg_24h_pct"}),
    # warehouse candidates features_json (carries several banned keys)
    "warehouse_features": frozenset({"atr_pct_1h"}),
    # NewsScanner.get_market_context() / DataCoordinator news feed
    "sentiment": frozenset({"coin_sentiments", "fear_greed_value", "fetched_at"}),
    # data/unlock_calendar/{BASE}.json
    "unlock": frozenset({"max_supply", "circ_calibration", "events", "circ_series"}),
    # one unlock event
    "unlock_event": frozenset({"ts", "tokens", "ratio"}),
})

CONFIDENCE_ORDER = ("NONE", "LOW", "MEDIUM", "HIGH")
CEILING_BY_VERDICT = MappingProxyType({
    "DATA_OK": "HIGH", "PARTIAL": "MEDIUM", "BACKFILL_FIRST": "LOW",
})

SECTION_NAMES = (
    "price_action", "volume", "volatility", "funding", "open_interest",
    "supply_unlocks", "news", "sentiment", "liquidity", "macro_context",
)

MIN_CLOSED_4H = 210  # mirrors core.pair_dossier.MIN_CLOSED_4H
MIN_FUNDING_SETTLEMENTS = 6  # mirrors core.funding_history.MIN_PERIODS_FOR_AVG

DISCLAIMER = (
    "Descriptive record of what this repository already stores about each asset, and of "
    "what it does not store. Every number is a dated, sourced measurement. This document "
    "makes no market claim, no directional statement and no trading instruction; it has no "
    "order path and no promotion authority. Assembled by deterministic Python with no "
    "language-model call."
)


# ── projection ───────────────────────────────────────────────────────────────
def project_feed(feed: str, raw: dict | None) -> dict:
    """Project a feed dict through its frozen allowlist.

    Fail-closed: an unknown feed name raises rather than passing data through.
    Deliberately builds a NEW dict key by key — never `dict.update`, which is
    how banned vocabulary historically leaked into downstream documents.
    """
    allowed = FEED_ALLOWLIST[feed]  # KeyError on an undeclared feed: fail-closed
    src = raw or {}
    return {k: src[k] for k in sorted(allowed) if k in src}


# ── confidence (computed, never authored) ────────────────────────────────────
def _worst(*levels: str) -> str:
    return min(levels, key=CONFIDENCE_ORDER.index)


def cap_confidence(level: str, ceiling: str) -> str:
    """Clamp a computed level by an asset's ceiling. Applied in ONE place."""
    return _worst(level, ceiling)


def confidence_for(staleness_days, n, min_n, n_sources: int) -> tuple[str, str]:
    """Pure confidence rule. Returns (level, basis) — both fully determined.

    staleness_days: age of the DATA (not of the file); None = unknown.
    n / min_n:      sample size actually behind the number and the count below
                    which it is not usable; either may be None (not applicable).
    n_sources:      how many independent sources corroborate it. One named
                    source caps at MEDIUM by design — a single uncorroborated
                    measurement is not a high-confidence one.
    """
    parts: list[str] = []

    if staleness_days is None:
        s_lvl = "LOW"
        parts.append("staleness=unknown:LOW")
    else:
        d = float(staleness_days)
        s_lvl = "HIGH" if d <= 2 else "MEDIUM" if d <= 7 else "LOW" if d <= 30 else "NONE"
        parts.append(f"staleness={d:g}d:{s_lvl}")

    if n is None:
        n_lvl = "HIGH"
        parts.append("n=n/a")
    elif int(n) == 0:
        n_lvl = "NONE"
        parts.append("n=0:NONE")
    elif min_n is None:
        n_lvl = "HIGH"
        parts.append(f"n={int(n)} (no min_n):HIGH")
    else:
        k = int(n)
        m = int(min_n)
        n_lvl = "HIGH" if k >= m else "MEDIUM" if k >= m / 2 else "LOW"
        parts.append(f"n={k} vs min_n={m}:{n_lvl}")

    src = int(n_sources or 0)
    src_lvl = "NONE" if src <= 0 else "MEDIUM" if src == 1 else "HIGH"
    parts.append(f"sources={src}:{src_lvl}")

    level = _worst(s_lvl, n_lvl, src_lvl)
    parts.append(f"combined={level}")
    return level, "; ".join(parts)


# ── timestamps ───────────────────────────────────────────────────────────────
_STAMP_FORMATS = ("%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M UTC")


def parse_stamp(stamp: str | None) -> datetime | None:
    """Parse one of the repo's UTC stamp formats. None on anything unparseable."""
    if not stamp:
        return None
    for fmt in _STAMP_FORMATS:
        try:
            return datetime.strptime(str(stamp), fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def stamp_from_epoch(seconds) -> str | None:
    """Format an epoch-seconds value as the repo's minute-resolution UTC stamp."""
    if seconds is None:
        return None
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return None
    if s > 1e11:  # milliseconds
        s /= 1000.0
    return datetime.fromtimestamp(s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def staleness_days(as_of: str | None, now: datetime) -> float | None:
    dt = parse_stamp(as_of)
    if dt is None:
        return None
    return round((now - dt).total_seconds() / 86400.0, 2)


# ── the Finding record ───────────────────────────────────────────────────────
_FINDING_KEYS = (
    "id", "kind", "scope", "section", "label", "value", "numeric", "unit",
    "as_of_utc", "source", "source_kind", "source_fn", "confidence",
    "confidence_basis", "derived_from", "expected_source", "remediation",
)


def _round(x):
    if isinstance(x, bool) or x is None:
        return x
    if isinstance(x, float):
        return round(x, 8)
    return x


class Emitter:
    """Builds Findings for one scope. The ONLY place the ceiling is applied."""

    def __init__(self, scope: str, key: str, ceiling: str, now: datetime):
        self.scope = scope
        self.key = key
        self.ceiling = ceiling
        self.now = now

    def _make(self, kind, section, label, **kw) -> dict:
        rec = {k: None for k in _FINDING_KEYS}
        rec.update({
            "id": f"{self.key}:{section}:{label}",
            "kind": kind, "scope": self.scope, "section": section, "label": label,
            "derived_from": [],
        })
        for k, v in kw.items():
            rec[k] = v
        rec["value"] = _round(rec["value"])
        rec["numeric"] = _round(rec["numeric"])
        return {k: rec[k] for k in _FINDING_KEYS}

    def fact(self, section, label, *, value, numeric=None, unit=None, as_of_utc,
             source, source_kind, source_fn, n=None, min_n=None, n_sources=1):
        level, basis = confidence_for(
            staleness_days(as_of_utc, self.now), n, min_n, n_sources
        )
        return self._make(
            "FACT", section, label, value=value, numeric=numeric, unit=unit,
            as_of_utc=as_of_utc, source=source, source_kind=source_kind,
            source_fn=source_fn, confidence=cap_confidence(level, self.ceiling),
            confidence_basis=basis,
        )

    def inference(self, section, label, *, value, derived_from, numeric=None, unit=None,
                  as_of_utc, source, source_kind, source_fn, n=None, min_n=None,
                  n_sources=1):
        if not derived_from:
            raise ValueError(f"INFERENCE {label} needs a non-empty derived_from")
        level, basis = confidence_for(
            staleness_days(as_of_utc, self.now), n, min_n, n_sources
        )
        rec = self._make(
            "INFERENCE", section, label, value=value, numeric=numeric, unit=unit,
            as_of_utc=as_of_utc, source=source, source_kind=source_kind,
            source_fn=source_fn, confidence=cap_confidence(level, self.ceiling),
            confidence_basis=basis,
        )
        rec["derived_from"] = list(derived_from)
        return rec

    def absent(self, section, label, *, expected_source, remediation, value=None):
        return self._make(
            "ABSENT", section, label, value=value,
            confidence="NONE",
            confidence_basis="absent record: nothing was measured, so no level applies",
            expected_source=expected_source, remediation=remediation,
        )


def _section(findings: list[dict]) -> dict:
    kinds = {f["kind"] for f in findings}
    if not findings or kinds == {"ABSENT"}:
        status = "ABSENT"
    elif "ABSENT" in kinds:
        status = "DEGRADED"
    else:
        status = "OK"
    return {"status": status, "findings": findings}


# ── inputs ───────────────────────────────────────────────────────────────────
@dataclass
class AssetInputs:
    """Everything the shell gathered for one asset. Local artifacts only."""

    symbol: str
    base: str
    coverage: dict
    indicators: dict | None
    bars_computable: bool
    live_1h_ok: bool
    n_bars_4h: int

    price_source: str | None = None          # e.g. data/ohlcv_cache/ADA-USDT_4h.parquet
    indicator_pctls: dict | None = None      # compute_indicator_percentiles() output
    atr_4h: float | None = None              # core.market_regime._atr latest value
    warehouse_features: dict | None = None   # latest candidates features_json
    warehouse_as_of: str | None = None
    warehouse_vol_sample: list = field(default_factory=list)  # 30d atr_pct_1h sample
    funding_settlements: list = field(default_factory=list)   # [(settlement_ts, rate)]
    funding_source: str | None = None
    oi: dict | None = None                   # funding_oi_summary()[base]
    oi_as_of: str | None = None
    unlock: dict | None = None               # data/unlock_calendar/{BASE}.json
    news_mentions: int | None = None
    news_as_of: str | None = None
    sentiment_map: dict | None = None        # coin -> score; MEMBERSHIP decides
    sentiment_as_of: str | None = None
    l2_as_of: str | None = None
    aggtrades_as_of: str | None = None


# ── indicator percentiles (reuses the promoted math; never re-implements it) ──
def compute_indicator_percentiles(closed_bars: list, lookback: int = 120) -> dict | None:
    """Own-history percentile of RSI14 / KDJ-K / Williams %R at the last bar.

    Evaluated by calling `core.pair_dossier`'s own functions on expanding
    prefixes of the SAME closed-bar list used for the posture — exact, not
    approximate, because each of those is a causal recursion or a trailing
    window. The percentile itself is `core.features._pctl` (fraction of the
    sample at or below the value). Pure: no clock, no IO.
    """
    import numpy as np

    from core.features import _pctl
    from core.pair_dossier import kdj, wilder_rsi, williams_r

    if not closed_bars or len(closed_bars) < 40:
        return None
    a = np.asarray(closed_bars, dtype=float)
    h, low, c = a[:, 2], a[:, 3], a[:, 4]
    n = len(c)
    start = max(30, n - int(lookback))
    rsi_s, k_s, wr_s = [], [], []
    for i in range(start, n):
        r = wilder_rsi(c[: i + 1], 14)
        if r is not None:
            rsi_s.append(r)
        kd = kdj(h[: i + 1], low[: i + 1], c[: i + 1], 9)
        if kd is not None:
            k_s.append(kd["k"])
        w = williams_r(h[: i + 1], low[: i + 1], c[: i + 1], 14)
        if w is not None:
            wr_s.append(w)
    if not rsi_s or not k_s or not wr_s:
        return None
    return {
        "n": n - start,
        "rsi14_pctl": _pctl(rsi_s[-1], rsi_s),
        "kdj_k_pctl": _pctl(k_s[-1], k_s),
        "williams_r14_pctl": _pctl(wr_s[-1], wr_s),
    }


# ── section builders ─────────────────────────────────────────────────────────
_BACKFILL_4H = (
    "venv\\Scripts\\python.exe scripts/backfill_universe_ohlcv.py --timeframe 4h --base {base}"
)
_BACKFILL_1H = (
    "venv\\Scripts\\python.exe scripts/backfill_universe_ohlcv.py --timeframe 1h --base {base}"
)
_BACKFILL_FUNDING = (
    "venv\\Scripts\\python.exe scripts/backfill_funding_history.py --venue bybit --coin {base}"
)


def _price_action(em: Emitter, a: AssetInputs) -> dict:
    sec = "price_action"
    src = a.price_source or f"data/ohlcv_cache/{a.base}-USDT_4h.parquet"
    ind = a.indicators
    if not ind:
        return _section([em.absent(
            sec, "closed_4h_bars",
            expected_source=src,
            remediation=_BACKFILL_4H.format(base=a.base),
            value=(f"no local 4h bar set with at least {MIN_CLOSED_4H} closed bars "
                   "was available to the builder"),
        )])

    stamp = ind.get("asof_closed_4h_utc")
    fn = "core.pair_dossier.compute_indicators"
    out: list[dict] = []

    def F(label, value, **kw):
        f = em.fact(sec, label, value=value, as_of_utc=stamp, source=src,
                    source_kind="local_parquet_4h", source_fn=fn,
                    n=a.n_bars_4h, min_n=MIN_CLOSED_4H, **kw)
        out.append(f)
        return f["id"]

    close_id = F("close_4h", ind.get("close"), numeric=ind.get("close"), unit="quote")
    smas = ind.get("sma") or {}
    for n in (20, 50, 200):
        v = smas.get(f"sma{n}")
        if v is not None:
            F(f"sma{n}_4h", v, numeric=v, unit="quote")
        rel = smas.get(f"price_vs_sma{n}")
        if rel is not None:
            F(f"price_vs_sma{n}", rel)
    s50, s200 = smas.get("sma50"), smas.get("sma200")
    if s50 is not None and s200 is not None:
        # renamed from pair_dossier's golden|death, which reads as a call
        out.append(em.inference(
            sec, "sma50_vs_sma200", value=("above" if s50 > s200 else "below"),
            derived_from=[f"{a.base}:{sec}:sma50_4h", f"{a.base}:{sec}:sma200_4h"],
            as_of_utc=stamp, source=src, source_kind="local_parquet_4h",
            source_fn="core.research_brief._price_action",
            n=a.n_bars_4h, min_n=MIN_CLOSED_4H,
        ))
    emas = ind.get("ema") or {}
    for n in (20, 50):
        v = emas.get(f"ema{n}")
        if v is not None:
            F(f"ema{n}_4h", v, numeric=v, unit="quote")
        rel = emas.get(f"price_vs_ema{n}")
        if rel is not None:
            F(f"price_vs_ema{n}", rel)

    macd = ind.get("macd") or {}
    if macd:
        # `signal` renamed to `macd_signal_line` rather than exempted
        F("macd_line", macd.get("line"), numeric=macd.get("line"))
        F("macd_signal_line", macd.get("signal"), numeric=macd.get("signal"))
        F("macd_histogram", macd.get("hist"), numeric=macd.get("hist"))
        F("macd_histogram_sign", macd.get("hist_sign"))

    rsi = ind.get("rsi14")
    if rsi is not None:
        rsi_id = F("rsi14_4h", rsi, numeric=rsi, unit="index_0_100")
    else:
        rsi_id = None
    kd = ind.get("kdj_9_3_3") or {}
    kdj_id = None
    for part in ("k", "d", "j"):
        if kd.get(part) is not None:
            fid = F(f"kdj_{part}_4h", kd[part], numeric=kd[part], unit="index")
            if part == "k":
                kdj_id = fid
    wr = ind.get("williams_r14")
    wr_id = F("williams_r14_4h", wr, numeric=wr, unit="index_-100_0") if wr is not None else None

    # own-history percentiles: the ONLY context these oscillators are given
    pct = a.indicator_pctls or {}
    pairs = (("rsi14_pctl", rsi_id), ("kdj_k_pctl", kdj_id), ("williams_r14_pctl", wr_id))
    if pct:
        for label, parent in pairs:
            v = pct.get(label)
            if v is None or parent is None:
                continue
            out.append(em.inference(
                sec, label, value=v, numeric=v, unit="fraction_of_own_history",
                derived_from=[parent], as_of_utc=stamp, source=src,
                source_kind="local_parquet_4h",
                source_fn="core.research_brief.compute_indicator_percentiles",
                n=pct.get("n"), min_n=60,
            ))
    else:
        out.append(em.absent(
            sec, "oscillator_own_history_percentiles",
            expected_source=src,
            remediation=_BACKFILL_4H.format(base=a.base),
            value=("oscillator levels are reported as plain numbers; their own-history "
                   "percentile needs the closed 4h bar set, which was not supplied"),
        ))

    # position-flavoured outputs, renamed to a price relation
    for label, raw_key, up_value in (
        ("price_vs_psar", "psar_side", "long"),
        ("price_vs_supertrend", "supertrend_10_3", "up"),
    ):
        raw = ind.get(raw_key)
        if raw is None:
            continue
        out.append(em.inference(
            sec, label, value=("above" if raw == up_value else "below"),
            derived_from=[close_id], as_of_utc=stamp, source=src,
            source_kind="local_parquet_4h",
            source_fn="core.research_brief._price_action",
            n=a.n_bars_4h, min_n=MIN_CLOSED_4H,
        ))
    return _section(out)


def _volume(em: Emitter, a: AssetInputs) -> dict:
    sec = "volume"
    src = a.price_source or f"data/ohlcv_cache/{a.base}-USDT_4h.parquet"
    ind = a.indicators or {}
    vol = ind.get("volume_20")
    obv = ind.get("obv_slope_20")
    if not vol and obv is None:
        return _section([em.absent(
            sec, "volume_4h",
            expected_source=src, remediation=_BACKFILL_4H.format(base=a.base),
            value="no closed 4h bar set was available, so no volume statistic was computed",
        )])
    stamp = ind.get("asof_closed_4h_utc")
    out = []
    if vol:
        out.append(em.fact(
            sec, "volume_20_zscore", value=vol.get("zscore"), numeric=vol.get("zscore"),
            unit="stdev", as_of_utc=stamp, source=src, source_kind="local_parquet_4h",
            source_fn="core.pair_dossier.volume_trend", n=20, min_n=20,
        ))
        out.append(em.fact(
            sec, "volume_20_slope_direction", value=vol.get("trend"), as_of_utc=stamp,
            source=src, source_kind="local_parquet_4h",
            source_fn="core.pair_dossier.volume_trend", n=20, min_n=20,
        ))
    if obv is not None:
        out.append(em.fact(
            sec, "obv_slope_20_sign", value=obv, as_of_utc=stamp, source=src,
            source_kind="local_parquet_4h", source_fn="core.pair_dossier.obv_slope_sign",
            n=20, min_n=20,
        ))
    return _section(out)


def _volatility(em: Emitter, a: AssetInputs) -> dict:
    sec = "volatility"
    src4 = a.price_source or f"data/ohlcv_cache/{a.base}-USDT_4h.parquet"
    ind = a.indicators or {}
    stamp = ind.get("asof_closed_4h_utc")
    out: list[dict] = []

    bb = ind.get("bollinger") or {}
    if bb:
        em_kw = dict(as_of_utc=stamp, source=src4, source_kind="local_parquet_4h",
                     source_fn="core.pair_dossier.bollinger", n=a.n_bars_4h,
                     min_n=MIN_CLOSED_4H)
        out.append(em.fact(sec, "bollinger_pct_b", value=bb.get("pct_b"),
                           numeric=bb.get("pct_b"), unit="fraction_of_band", **em_kw))
        bw_id = em.fact(sec, "bollinger_bandwidth", value=bb.get("bandwidth"),
                        numeric=bb.get("bandwidth"), unit="fraction_of_mid", **em_kw)
        out.append(bw_id)
        if bb.get("bandwidth_pctl") is not None:
            out.append(em.inference(
                sec, "bollinger_bandwidth_pctl", value=bb["bandwidth_pctl"],
                numeric=bb["bandwidth_pctl"], unit="fraction_of_own_history",
                derived_from=[bw_id["id"]], **em_kw,
            ))

    close = ind.get("close")
    if a.atr_4h is not None and close:
        atr_id = em.fact(
            sec, "atr14_4h", value=a.atr_4h, numeric=a.atr_4h, unit="quote",
            as_of_utc=stamp, source=src4, source_kind="local_parquet_4h",
            source_fn="core.market_regime._atr", n=a.n_bars_4h, min_n=MIN_CLOSED_4H,
        )
        out.append(atr_id)
        pct = 100.0 * float(a.atr_4h) / float(close)
        out.append(em.inference(
            sec, "atr14_pct_4h", value=round(pct, 4), numeric=round(pct, 4), unit="percent",
            derived_from=[atr_id["id"], f"{a.base}:price_action:close_4h"],
            as_of_utc=stamp, source=src4, source_kind="local_parquet_4h",
            source_fn="core.research_brief._volatility", n=a.n_bars_4h, min_n=MIN_CLOSED_4H,
        ))
    else:
        out.append(em.absent(
            sec, "atr14_4h",
            expected_source=f"{src4} via core.market_regime._atr",
            remediation=_BACKFILL_4H.format(base=a.base),
            value="no closed 4h bar set was supplied, so no true-range average was computed",
        ))

    # 1h volatility from the warehouse — kept in its OWN timeframe, never mixed
    wf = project_feed("warehouse_features", a.warehouse_features)
    atr1 = wf.get("atr_pct_1h")
    sample = [s for s in (a.warehouse_vol_sample or []) if s is not None]
    if atr1 is not None:
        f1 = em.fact(
            sec, "atr_pct_1h_latest_candidate", value=atr1, numeric=atr1, unit="percent",
            as_of_utc=a.warehouse_as_of,
            source="data/warehouse.sqlite candidates.features_json",
            source_kind="warehouse_sqlite", source_fn="core.warehouse candidates query",
            n=len(sample) or None, min_n=30,
        )
        out.append(f1)
        if sample:
            from core.features import _pctl

            p = _pctl(float(atr1), [float(s) for s in sample])
            out.append(em.inference(
                sec, "atr_pct_1h_pctl_30d", value=p, numeric=p,
                unit="fraction_of_30d_1h_sample", derived_from=[f1["id"]],
                as_of_utc=a.warehouse_as_of,
                source="data/warehouse.sqlite candidates.features_json (30d window)",
                source_kind="warehouse_sqlite", source_fn="core.features._pctl",
                n=len(sample), min_n=30,
            ))
    else:
        out.append(em.absent(
            sec, "atr_pct_1h_latest_candidate",
            expected_source="data/warehouse.sqlite candidates.features_json",
            remediation=("no action available offline: the bot writes candidates rows only "
                         "while it scans this base"),
            value="this base has no warehouse candidates row carrying a 1h true-range percent",
        ))
    return _section(out)


def _median_settlement_interval_s(rows) -> float | None:
    """Median seconds between consecutive settlements, or None if unobservable.

    None (fewer than two settlements, or degenerate/non-positive spacing) means
    the interval cannot be measured — callers must omit the derived quantity
    rather than assume a venue-wide constant.
    """
    deltas = sorted(
        float(rows[i][0]) - float(rows[i - 1][0]) for i in range(1, len(rows))
    )
    if not deltas:
        return None
    mid = len(deltas) // 2
    med = deltas[mid] if len(deltas) % 2 else (deltas[mid - 1] + deltas[mid]) / 2.0
    return med if med > 0 else None


def _funding(em: Emitter, a: AssetInputs) -> dict:
    sec = "funding"
    src = a.funding_source or f"data/funding_history/bybit_{a.base}.csv"
    rows = list(a.funding_settlements or [])
    if not rows:
        return _section([em.absent(
            sec, "realized_funding_settlements",
            expected_source=src, remediation=_BACKFILL_FUNDING.format(base=a.base),
            value="no realized funding settlement history is stored for this base on bybit",
        )])
    rows = sorted(rows, key=lambda r: float(r[0]))
    last_ts, last_rate = float(rows[-1][0]), float(rows[-1][1])
    stamp = stamp_from_epoch(last_ts)
    kw = dict(as_of_utc=stamp, source=src, source_kind="local_csv",
              source_fn="core.funding_history.load_recent_realized_settlements",
              n=len(rows), min_n=MIN_FUNDING_SETTLEMENTS)
    out = [
        em.fact(sec, "funding_rate_latest", value=last_rate, numeric=last_rate,
                unit="rate_per_interval", **kw),
        em.fact(sec, "funding_settlements_recorded", value=len(rows), numeric=len(rows),
                unit="count", **kw),
    ]
    mean = sum(float(r[1]) for r in rows) / len(rows)
    mean_f = em.fact(sec, "funding_rate_mean_recent", value=round(mean, 10),
                     numeric=round(mean, 10), unit="rate_per_interval", **kw)
    out.append(mean_f)
    # Settlements/year comes from the OBSERVED interval of these very rows, never
    # a constant: the perp funding interval is time-varying per symbol (bases have
    # switched 8h -> 4h mid-history), so any literal misstates some base by 2x.
    interval_s = _median_settlement_interval_s(rows)
    if interval_s is not None:
        ann = mean * (365.0 * 24.0 * 3600.0 / interval_s) * 100.0
        out.append(em.inference(
            sec, "funding_rate_annualized_pct", value=round(ann, 4), numeric=round(ann, 4),
            unit="percent_per_year", derived_from=[mean_f["id"]],
            as_of_utc=stamp, source=src, source_kind="local_csv",
            source_fn="core.research_brief._funding", n=len(rows),
            min_n=MIN_FUNDING_SETTLEMENTS,
        ))
    return _section(out)


def _open_interest(em: Emitter, a: AssetInputs) -> dict:
    sec = "open_interest"
    src = f"data/funding_oi/{a.base}_oi.csv"
    oi = project_feed("funding_oi", a.oi)
    if not oi or a.oi_as_of is None:
        return _section([em.absent(
            sec, "open_interest",
            expected_source=src,
            remediation=(f"venv\\Scripts\\python.exe scripts/fetch_binance_funding_oi.py "
                         f"--symbols {a.base}"),
            value=("no local open-interest series with a usable record timestamp exists "
                   "for this base"),
        )])
    kw = dict(as_of_utc=a.oi_as_of, source=src, source_kind="local_csv",
              source_fn="scripts.run_intel_synthesis.funding_oi_summary")
    out = []
    if oi.get("oi_usd") is not None:
        out.append(em.fact(sec, "open_interest_usd", value=oi["oi_usd"],
                           numeric=oi["oi_usd"], unit="usd", **kw))
    if oi.get("oi_chg_24h_pct") is not None:
        out.append(em.fact(sec, "open_interest_change_24h_pct", value=oi["oi_chg_24h_pct"],
                           numeric=oi["oi_chg_24h_pct"], unit="percent", **kw))
    return _section(out)


def _supply_unlocks(em: Emitter, a: AssetInputs, now: datetime) -> dict:
    sec = "supply_unlocks"
    src = f"data/unlock_calendar/{a.base}.json"
    art = project_feed("unlock", a.unlock)
    if not art:
        return _section([em.absent(
            sec, "unlock_schedule",
            expected_source=src,
            remediation=("venv\\Scripts\\python.exe scripts/backfill_unlock_calendar.py "
                         "--forward-days 60"),
            value="no token-unlock schedule artifact is stored for this base",
        )])
    series = art.get("circ_series") or []
    stamp = stamp_from_epoch(series[-1][0]) if series else None
    if stamp is None:
        return _section([em.absent(
            sec, "unlock_schedule",
            expected_source=src,
            remediation=("venv\\Scripts\\python.exe scripts/backfill_unlock_calendar.py "
                         "--forward-days 60"),
            value="the unlock artifact carries no circulating-supply series timestamp",
        )])
    events = [project_feed("unlock_event", e) for e in (art.get("events") or [])]
    kw = dict(as_of_utc=stamp, source=src, source_kind="local_json",
              source_fn="scripts/backfill_unlock_calendar.py artifact",
              n=len(events) or None, min_n=1)
    out = []
    if art.get("max_supply") is not None:
        out.append(em.fact(sec, "max_supply", value=art["max_supply"],
                           numeric=art["max_supply"], unit="tokens", **kw))
    circ = (art.get("circ_calibration") or {}).get("documented_now")
    if circ is not None:
        out.append(em.fact(sec, "circulating_supply_documented", value=circ, numeric=circ,
                           unit="tokens", **kw))
    out.append(em.fact(sec, "scheduled_events_recorded", value=len(events),
                       numeric=len(events), unit="count", **kw))
    now_s = now.timestamp()
    upcoming = sorted((e for e in events if e.get("ts") and float(e["ts"]) >= now_s),
                      key=lambda e: float(e["ts"]))
    if upcoming:
        nxt = upcoming[0]
        nxt_f = em.fact(sec, "next_scheduled_event_ts", value=stamp_from_epoch(nxt["ts"]),
                        numeric=float(nxt["ts"]), unit="epoch_seconds", **kw)
        out.append(nxt_f)
        if nxt.get("tokens") is not None:
            out.append(em.fact(sec, "next_scheduled_event_tokens", value=nxt["tokens"],
                               numeric=nxt["tokens"], unit="tokens", **kw))
        days = (float(nxt["ts"]) - now_s) / 86400.0
        out.append(em.inference(
            sec, "days_until_next_scheduled_event", value=round(days, 2),
            numeric=round(days, 2), unit="days", derived_from=[nxt_f["id"]],
            as_of_utc=stamp, source=src, source_kind="local_json",
            source_fn="core.research_brief._supply_unlocks", n=len(events), min_n=1,
        ))
    else:
        out.append(em.absent(
            sec, "next_scheduled_event_ts",
            expected_source=src,
            remediation=("venv\\Scripts\\python.exe scripts/backfill_unlock_calendar.py "
                         "--forward-days 60"),
            value="the stored schedule has no event dated at or after this run",
        ))
    return _section(out)


def _news(em: Emitter, a: AssetInputs) -> dict:
    sec = "news"
    src = "data/news_cache.json"
    if a.news_mentions is None or a.news_as_of is None:
        return _section([em.absent(
            sec, "headline_mentions",
            expected_source=src,
            remediation=("run the bot's news scan cycle, or "
                         "venv\\Scripts\\python.exe scripts/market_intel_report.py"),
            value=("no cached headline set with a usable fetch timestamp was supplied; "
                   "headline TEXT is deliberately never copied into this document, only "
                   "the per-base mention count"),
        )])
    return _section([em.fact(
        sec, "headline_mentions", value=int(a.news_mentions), numeric=int(a.news_mentions),
        unit="count", as_of_utc=a.news_as_of, source=src, source_kind="local_json",
        source_fn="core.news_scanner._extract_coin_symbols",
        n=int(a.news_mentions), min_n=3,
    )])


def _sentiment(em: Emitter, a: AssetInputs) -> dict:
    """MEMBERSHIP decides. A coin the scanner never saw is ABSENT, never a 0."""
    sec = "sentiment"
    src = "data/news_cache.json (per-coin sentiment map)"
    remediation = ("run the bot's news scan cycle so the coin enters the sentiment map; "
                   "per-coin coverage is limited to the scanner's name/tag table")
    if a.sentiment_map is None or a.sentiment_as_of is None:
        return _section([em.absent(
            sec, "coin_sentiment_score", expected_source=src, remediation=remediation,
            value="no per-coin sentiment map was supplied to the builder",
        )])
    if a.base not in a.sentiment_map:
        return _section([em.absent(
            sec, "coin_sentiment_score", expected_source=src, remediation=remediation,
            value=("this base is absent from the sentiment map: the scanner never saw it, "
                   "which is NOT the same as a measured score of zero"),
        )])
    score = a.sentiment_map[a.base]
    return _section([em.fact(
        sec, "coin_sentiment_score", value=score, numeric=float(score), unit="index_-1_1",
        as_of_utc=a.sentiment_as_of, source=src, source_kind="local_json",
        source_fn="core.news_scanner.NewsScanner.get_market_context",
        n=1, min_n=1,
    )])


def _liquidity(em: Emitter, a: AssetInputs) -> dict:
    sec = "liquidity"
    cov = a.coverage or {}
    out = []
    for label, cov_key, src, stamp, remediation in (
        ("orderbook_depth_artifact", "orderbook_depth", "data/l2_history.jsonl", a.l2_as_of,
         "venv\\Scripts\\python.exe scripts/harvest_l2.py"),
        ("trade_tape_artifact", "trades", "data/aggtrades_qh/", a.aggtrades_as_of,
         "venv\\Scripts\\python.exe scripts/harvest_binance_aggtrades_qh.py"),
    ):
        present = str(cov.get(cov_key, "ABSENT")) != "ABSENT"
        if present and stamp:
            out.append(em.fact(
                sec, label, value="present", as_of_utc=stamp, source=src,
                source_kind="local_harvest",
                source_fn="scripts/research_brief.py l2_last_hours / aggtrades_last_stamps",
                n=1, min_n=1,
            ))
        elif present:
            out.append(em.absent(
                sec, label, expected_source=src, remediation=remediation,
                value=("the harvest lists this base but no record timestamp was supplied, "
                       "so the measurement cannot be dated"),
            ))
        else:
            out.append(em.absent(
                sec, label, expected_source=src, remediation=remediation,
                value="this base is not covered by the harvest",
            ))
    return _section(out)


def _macro_context(em: Emitter, a: AssetInputs) -> dict:
    """ABSENT BY DESIGN — stated plainly rather than filled with placeholders."""
    sec = "macro_context"
    return _section([
        em.absent(
            sec, "asset_fundamentals",
            expected_source="none — no per-asset fundamentals layer exists in this repository",
            remediation=("none planned: total value locked, protocol revenue, holder counts, "
                         "active addresses and emission schedules are not collected here"),
            value="absent by design, not by failure",
        ),
        em.absent(
            sec, "exchange_net_flows",
            expected_source="none — deliberate repository-wide omission",
            remediation="none planned: no free honest source was found (see 28_whale_flow_verdict)",
            value="absent by design, not by failure",
        ),
        em.absent(
            sec, "market_wide_context",
            expected_source="the document-level market_context block (scope: market)",
            remediation="market-wide records are never nested inside an asset; read them there",
            value="market-wide records live at the document level by construction",
        ),
    ])


# ── asset + document assembly ────────────────────────────────────────────────
def _data_quality(a: AssetInputs, now: datetime) -> dict:
    from core.pair_dossier import classify_verdict

    verdict, reasons = classify_verdict(
        a.coverage, bars_computable=a.bars_computable, live_1h_ok=a.live_1h_ok,
        n_bars_4h=a.n_bars_4h,
    )
    cov = a.coverage or {}
    missing = []
    if not reasons.get("bybit_funding_csv"):
        missing.append(f"data/funding_history/bybit_{a.base}.csv")
    if not reasons.get("local_1h_fresh"):
        missing.append(f"data/ohlcv_cache/{a.base}-USDT_1h.parquet (absent or stale)")
    if not reasons.get("local_4h_fresh"):
        missing.append(f"data/ohlcv_cache/{a.base}-USDT_4h.parquet (absent or stale)")
    if not reasons.get("live_4h_computable"):
        missing.append(f"at least {MIN_CLOSED_4H} closed 4h bars")

    stamps: list[tuple[float, str]] = []
    for key in ("ohlcv_cache_1h", "ohlcv_cache_4h"):
        c = cov.get(key) or {}
        s = c.get("last_bar_utc")
        dt = parse_stamp(s)
        if dt is not None:
            stamps.append((dt.timestamp(), s))
    ind_stamp = (a.indicators or {}).get("asof_closed_4h_utc")
    dt = parse_stamp(ind_stamp)
    if dt is not None:
        stamps.append((dt.timestamp(), ind_stamp))
    oldest = min(stamps)[1] if stamps else None

    # max_staleness_days is derived from the SAME stamp set as
    # oldest_input_as_of_utc, so the banner's "(N days old)" always describes the
    # timestamp printed next to it. (An earlier version read the coverage probes'
    # own staleness_days, which are measured at a different instant and exclude
    # the bar set the numbers actually came from — the two could disagree.)
    max_stale = staleness_days(oldest, now)

    return {
        "verdict": verdict,
        "reasons": dict(reasons),
        "missing_inputs": missing,
        "oldest_input_as_of_utc": oldest,
        "max_staleness_days": max_stale,
        "confidence_ceiling": CEILING_BY_VERDICT.get(verdict, "LOW"),
    }


def build_asset_brief(a: AssetInputs, *, now: datetime) -> dict:
    """Assemble one asset's brief. Pure: every input is an argument."""
    dq = _data_quality(a, now)
    em = Emitter("asset", a.base, dq["confidence_ceiling"], now)
    sections = {
        "price_action": _price_action(em, a),
        "volume": _volume(em, a),
        "volatility": _volatility(em, a),
        "funding": _funding(em, a),
        "open_interest": _open_interest(em, a),
        "supply_unlocks": _supply_unlocks(em, a, now),
        "news": _news(em, a),
        "sentiment": _sentiment(em, a),
        "liquidity": _liquidity(em, a),
        "macro_context": _macro_context(em, a),
    }
    sections = {k: sections[k] for k in SECTION_NAMES}
    flat = [f for sec in sections.values() for f in sec["findings"]]
    counts = {
        "facts": sum(1 for f in flat if f["kind"] == "FACT"),
        "inferences": sum(1 for f in flat if f["kind"] == "INFERENCE"),
        "absent": sum(1 for f in flat if f["kind"] == "ABSENT"),
    }
    return {
        "symbol": a.symbol,
        "base": a.base,
        "data_quality": dq,
        "sections": sections,
        "counts": counts,
    }


def build_market_context(market_intel: dict | None, *, now: datetime,
                         stale: bool | None = None) -> dict:
    """Document-level, scope=market. Never nested inside an asset."""
    em = Emitter("market", "market", "HIGH", now)
    sec = "market_regime_snapshot"
    src = "data/market_intel_history.jsonl"
    intel = project_feed("market_intel", market_intel)
    if not intel:
        return {
            "source": src, "as_of_utc": None, "stale": stale,
            "findings": [em.absent(
                sec, "market_snapshot", expected_source=src,
                remediation="venv\\Scripts\\python.exe scripts/market_intel_report.py",
                value="no market snapshot has been recorded",
            )],
        }
    stamp = None
    dt = parse_stamp(intel.get("ts_utc"))
    if dt is not None:
        stamp = dt.strftime("%Y-%m-%dT%H:%MZ")
    kw = dict(as_of_utc=stamp, source=src, source_kind="local_jsonl",
              source_fn="scripts.run_intel_synthesis.latest_market_intel")
    out = []
    for label, key, unit in (
        ("fear_greed_value", "fng_value", "index_0_100"),
        ("btc_dominance_pct", "btc_dom", "percent"),
        ("eth_dominance_pct", "eth_dom", "percent"),
        ("total_defi_tvl_usd", "total_tvl", "usd"),
        ("stablecoin_supply_usd", "stable_total", "usd"),
    ):
        v = intel.get(key)
        if v is not None:
            out.append(em.fact(sec, label, value=v, numeric=float(v), unit=unit, **kw))
    if intel.get("fng_class") is not None:
        out.append(em.fact(sec, "fear_greed_label", value=intel["fng_class"], **kw))
    g, t = intel.get("breadth_green"), intel.get("breadth_total")
    if g is not None and t:
        gf = em.fact(sec, "breadth_pairs_green", value=g, numeric=float(g), unit="count", **kw)
        tf = em.fact(sec, "breadth_pairs_measured", value=t, numeric=float(t),
                     unit="count", **kw)
        out += [gf, tf]
        pct = round(100.0 * float(g) / float(t), 2)
        out.append(em.inference(
            sec, "breadth_green_pct", value=pct, numeric=pct, unit="percent",
            derived_from=[gf["id"], tf["id"]],
            as_of_utc=stamp, source=src, source_kind="local_jsonl",
            source_fn="core.research_brief.build_market_context",
        ))
    return {"source": src, "as_of_utc": stamp, "stale": stale, "findings": out}


SOURCE_REGISTRY = MappingProxyType({
    "local_parquet_4h": "data/ohlcv_cache/{BASE}-USDT_4h.parquet — closed 4h bars; the "
                        "as-of stamp is the last bar's timestamp, never the file mtime",
    "local_csv": "data/funding_history/{venue}_{BASE}.csv and data/funding_oi/{BASE}_*.csv",
    "local_json": "data/unlock_calendar/{BASE}.json and data/news_cache.json",
    "local_jsonl": "data/market_intel_history.jsonl (the market snapshot history)",
    "local_harvest": "data/l2_history.jsonl and data/aggtrades_qh/ (separate harvesters)",
    "warehouse_sqlite": "data/warehouse.sqlite, opened mode=ro; candidates.features_json",
})


def build_doc(*, assets: list[dict], now: datetime, mode: str, universe_source: str,
              skipped_bases: list | None = None, market_intel: dict | None = None,
              market_intel_stale: bool | None = None,
              diagnostics: dict | None = None) -> dict:
    """Assemble the whole document. `now` is injected: two builds are identical."""
    counts = {"DATA_OK": 0, "PARTIAL": 0, "BACKFILL_FIRST": 0}
    for a in assets:
        v = a["data_quality"]["verdict"]
        counts[v] = counts.get(v, 0) + 1
    return {
        "schema_version": "research_brief/1",
        "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "scripts/research_brief.py + core/research_brief.py",
        "mode": mode,
        "llm_used": False,
        "disclaimer": DISCLAIMER,
        "universe": {
            "source": universe_source,
            "count": len(assets),
            "skipped_bases": sorted(skipped_bases or []),
        },
        "market_context": build_market_context(market_intel, now=now, stale=market_intel_stale),
        "verdict_counts": counts,
        "assets": assets,
        "source_registry": dict(SOURCE_REGISTRY),
        "run_diagnostics": dict(diagnostics or {}),
    }


# ── rendering ────────────────────────────────────────────────────────────────
def _banner(dq: dict) -> str:
    return (f"> DATA READINESS: **{dq['verdict']}** — ceiling {dq['confidence_ceiling']}; "
            f"oldest input {dq['oldest_input_as_of_utc'] or 'unknown'} "
            f"({dq['max_staleness_days']} days old). Read every number below through this.")


def _rows(findings: list[dict]) -> list[str]:
    lines = ["| label | kind | value | unit | as of (UTC) | confidence | source |",
             "|---|---|---|---|---|---|---|"]
    for f in findings:
        val = f["value"]
        if f["kind"] == "ABSENT":
            val = "ABSENT"
        lines.append(
            f"| {f['label']} | {f['kind']} | {val} | {f['unit'] or '-'} | "
            f"{f['as_of_utc'] or '-'} | {f['confidence']} | "
            f"{f['source'] or f['expected_source'] or '-'} |"
        )
    return lines


def render_md(doc: dict) -> str:
    """Markdown view. data_quality is the first rendered section of each asset."""
    c = doc["verdict_counts"]
    L = [
        f"# Per-Asset Research Brief — {doc['generated_utc']}",
        "",
        f"_{doc['disclaimer']}_",
        "",
        f"Universe: {doc['universe']['count']} assets from {doc['universe']['source']}. "
        f"Mode: {doc['mode']}. Language-model calls: 0.",
        "",
        f"Readiness: DATA_OK {c.get('DATA_OK', 0)}, PARTIAL {c.get('PARTIAL', 0)}, "
        f"BACKFILL_FIRST {c.get('BACKFILL_FIRST', 0)}.",
        "",
    ]
    for a in doc["assets"]:
        dq = a["data_quality"]
        L += [f"## {a['base']} ({a['symbol']})", "", _banner(dq), ""]
        L += ["### data_quality", ""]
        L += [f"- verdict: **{dq['verdict']}** (confidence ceiling {dq['confidence_ceiling']})"]
        L += [f"- readiness checks: {dq['reasons']}"]
        L += [f"- missing inputs: {', '.join(dq['missing_inputs']) or 'none'}"]
        L += [f"- oldest input as of: {dq['oldest_input_as_of_utc'] or 'unknown'}; "
              f"max staleness: {dq['max_staleness_days']} days", ""]
        for name in SECTION_NAMES:
            sec = a["sections"][name]
            L += [f"### {name} — {sec['status']}", "", _banner(dq), ""]
            L += _rows(sec["findings"])
            L += [""]
        L += [f"_Records: {a['counts']['facts']} measured, {a['counts']['inferences']} derived, "
              f"{a['counts']['absent']} absent._", ""]

    mkt = doc["market_context"]
    L += ["## Market context (scope: market — never nested inside an asset)", "",
          f"Source: {mkt['source']}; as of {mkt['as_of_utc'] or 'unknown'}; "
          f"stale flag: {mkt['stale']}.", ""]
    L += _rows(mkt["findings"])
    L += [""]
    if doc["run_diagnostics"].get("failed"):
        L += [f"> Inputs unavailable this run: {', '.join(doc['run_diagnostics']['failed'])} "
              "— the affected rows read ABSENT and must not be read as real absence.", ""]
    L += ["---", "", "_Source registry:_"]
    for kind in sorted(doc["source_registry"]):
        L.append(f"- `{kind}` — {doc['source_registry'][kind]}")
    return "\n".join(L)
