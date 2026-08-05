"""Scoring helpers: sigmoid, floors, accuracy target, scalp routing."""
import math

from loguru import logger

def _sigmoid(x: float) -> float:
    """Numerically-stable scalar sigmoid; saturates at the float64 edges."""
    try:
        if x >= 0:
            ex = math.exp(-x) if x < 700 else 0.0
            return 1.0 / (1.0 + ex)
        ex = math.exp(x) if x > -700 else 0.0
        return ex / (1.0 + ex)
    except OverflowError:
        return 1.0 if x > 0 else 0.0


def _safe_feat(v) -> float:
    """Coerce model-input feature to float; bools to 0/1, NaN/None to 0.0."""
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        f = float(v)
        if f != f:  # NaN
            return 0.0
        return f
    except (TypeError, ValueError):
        return 0.0


def _apply_accuracy_target(sl_pct: float, tp_pct: float, side: str = None) -> float:
    """ACCURACY_TARGET_MODE (owner goal 2026-07-10): return the TP% for a
    trade whose SL% is ``sl_pct``. When the mode is ON, the TP distance is
    compressed to ``tp_frac_of_sl`` x SL distance (default 0.5), floored at
    ``min_tp_pct`` so the target always clears round-trip costs — a no-edge
    signal then realizes ~60-65% WR (theoretical hit rate SL/(SL+TP) ~ 67%).
    PER-SIDE fracs (2026-07-10 geometry sweep, 8,878 entries + audit): at a
    global frac 0.50 longs ran 67.0% WR vs shorts 56.2% — one frac cannot put
    both sides mid-band. When ``side`` maps to buy/long and ``tp_frac_buy``
    is set (or sell/short and ``tp_frac_sell``), that frac wins; unset (None)
    falls back to the global ``tp_frac_of_sl``.
    OFF (default) returns ``tp_pct`` unchanged, byte-identical to pre-mode.
    HONESTY: accuracy-by-geometry, NOT profit edge — the promotion gate still
    requires after-cost expectancy. SL is never modified here (risk authority
    stays with the ATR/DistFit path)."""
    try:
        from config import ACCURACY_TARGET_MODE as _acc
        if not _acc.get("enabled"):
            return tp_pct
        if not sl_pct or sl_pct <= 0:
            return tp_pct  # no geometry to invert — fail open to original TP
        frac = float(_acc.get("tp_frac_of_sl", 0.5))
        _side = str(side).lower() if side else ""
        if _side in ("buy", "long") and _acc.get("tp_frac_buy"):
            frac = float(_acc["tp_frac_buy"])
        elif _side in ("sell", "short") and _acc.get("tp_frac_sell"):
            frac = float(_acc["tp_frac_sell"])
        if frac <= 0 or frac >= 1.0:
            return tp_pct  # cannot form inverted band shape
        raw = sl_pct * frac
        # Binding cost clearance (2026-07-29): must clear stressed round-trip
        # (~31.5bps under paper_fallback defaults) or economic_gate_stressed_
        # breakeven starves AccBand OPENs. Prefer geometry hit-rate over the
        # legacy min_tp_pct=0.5 inflate — clearance is the only soft floor.
        cost_clearance = float(_acc.get("min_tp_cost_pct", 0.35))
        tp = max(raw, cost_clearance)
        # Cost floor must NEVER put TP >= SL: that collapses theoretical WR
        # to ≤50% and breaks restart band detection (tp_frac < sl_frac).
        # Warehouse 2026-07-24: SL≈0.48% + min_tp=0.5% → measured TP/SL≈1.0
        # and daily WR≈37% instead of the 63-67% geometry band. Prefer the
        # frac-compressed TP (may sit below clearance); stressed-cost entry
        # gate still refuses hopeless brackets.
        if tp >= sl_pct:
            if raw > 0 and raw < sl_pct:
                return raw
            return tp_pct
        return tp
    except Exception:
        return tp_pct


def _entry_score_floor(is_scalp: bool = False, scalp_mode: dict = None) -> float:
    """Entry-score floor for the algorithmic rule gate (2026-07-19 max-flow).

    Defaults are unchanged: 66 on the standard path (layers_ok >= 6) and
    SCALP_MODE.entry_threshold (default 65, layers_ok >= 4) on the scalp
    path. When the owner sets config.MCP_ENTRY_MIN_SCORE (PAPER research
    knob), that value replaces BOTH floors; unset (None) -> exactly the
    historical behavior. The layers gates are never modified here."""
    try:
        from config import MCP_ENTRY_MIN_SCORE as _override
    except ImportError:
        _override = None
    if _override is not None:
        return float(_override)
    if is_scalp:
        return float((scalp_mode or {}).get("entry_threshold", 65))
    return 66.0


def _format_scalp_rule_skip_reason(result: dict, *, floor: float) -> str:
    """Stable family-prefixed skip reason when the scalp rule gate fails.

    When all 4 required conditions pass but score < floor, mcp historically
    wrote a bare ``vwap_near=… | rsi=…`` string — Mission Control could not
    family-aggregate those (~25% of 6h SKIPs on 2026-07-30). Prefix them as
    ``scalp_score_below_floor``; leave veto/req_fail/scope reasons intact.
    """
    raw = str(result.get("reason") or "gate_fail").strip() or "gate_fail"
    if (
        raw.startswith("scalp_veto:")
        or raw.startswith("scalp_req_fail")
        or raw.startswith("analysis_only_")
        or raw.startswith("scalp_score_below_floor")
    ):
        return raw
    try:
        score = float(result.get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    if score > 0:
        return f"scalp_score_below_floor({score:.0f}<{floor:.0f}):{raw}"
    return raw


def _accband_tradfi_scope_reason(base: str) -> str | None:
    """Skip ANALYSIS_ONLY bases in the directional decision funnel.

    Does not flip global ANALYSIS_ONLY_ENFORCED — only keeps tokenized
    equities/commodities out of the decision-funnel ALLOW count
    (2026-07-30: all 27/7943 ALLOWs in a 6h window were MSFT).

    2026-07-31: this was originally gated on ACCURACY_TARGET_MODE.enabled, which
    is False on the live bot — so the guard returned None for every base and the
    leak reopened: 18 of 41 ALLOWs in one hour were META/USDT. The rationale for
    the exclusion (no screened edge, pollutes allow-rate) has nothing to do with
    the exit geometry, so it must not depend on it. The reason string keeps its
    original spelling because Mission Control and the warehouse already index it.
    """
    try:
        from config import ANALYSIS_ONLY_BASES
    except ImportError:
        return None
    b = str(base or "").upper().split("/")[0]
    if b in ANALYSIS_ONLY_BASES:
        return "analysis_only_accband_scope"
    return None


def _max_flow_scalp_fallback_enabled(
    operating_mode: str | None = None,
    paper_profile: str | None = None,
) -> bool:
    """PAPER + MAX_FLOW_BAND: allow standard scorer after scalp quiet/ranging.

    2026-08-01 drought: SCALP_MODE ATR veto zeroed the AccBand allow funnel
    (0/2430 ALLOW). Literature treats this as a regime switch (scalp off →
    swing/standard on), not an invitation to loosen SCALP_MIN_ATR without a
    hashed prereg. Defaults read live config when args omitted.
    """
    try:
        import config as _cfg
        mode = operating_mode if operating_mode is not None else getattr(
            _cfg, "OPERATING_MODE", ""
        )
        profile = paper_profile if paper_profile is not None else getattr(
            _cfg, "PAPER_TRADING_PROFILE", ""
        )
    except ImportError:
        mode = operating_mode or ""
        profile = paper_profile or ""
    return str(mode).upper() == "PAPER" and str(profile).upper() == "MAX_FLOW_BAND"


_SCALP_FALLBACK_VETO_PREFIXES = (
    "scalp_veto:quiet",
    "scalp_veto:ranging",
)
