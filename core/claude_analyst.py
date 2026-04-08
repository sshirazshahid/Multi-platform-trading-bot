"""
core/claude_analyst.py  --  Institutional Research-Integrated Risk Manager

MAJOR UPGRADE: Now integrates ALL 8 research frameworks from
data/research/report_latest.json into every trading decision.

Previously: Only used basic technical indicators (ADX, RSI, ATR)
Now:        Combines all 8 institutional analyses for every coin:

  1. Goldman Sachs  → moat rating, NVT valuation, bull/bear targets, stop-loss
  2. Morgan Stanley → DCF verdict (undervalued/overvalued), price vs fair value
  3. Bridgewater    → recession stress test, max drawdown estimate, hedging
  4. JPMorgan       → catalyst analysis, implied move, earnings play
  5. BlackRock      → regime classification, allocation %, core vs satellite
  6. Citadel        → technical levels, R:R ratio, entry/stop/target from quant model
  7. Harvard        → yield/staking score, income sustainability
  8. Bain           → competitive moat, market share trend, best pick

Decision logic:
  Composite score = weighted average of all 8 framework signals
  Goldman  15% | Morgan Stanley 12% | Bridgewater 14% | JPMorgan 12%
  BlackRock 14% | Citadel 16% | Harvard 10% | Bain 7%

  Action is determined by composite + technical confluence.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib  import Path
from loguru   import logger

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ANALYSIS_FILE = Path("data/claude_analysis.json")
ANALYSIS_HTML = Path("data/claude_analysis.html")
HISTORY_FILE  = Path("data/claude_analysis_history.json")
RESEARCH_FILE = Path("data/research/report_latest.json")
MODEL         = "claude-sonnet-4-20250514"
MAX_TOKENS    = 1500

# Framework weights — must sum to 1.0
FRAMEWORK_WEIGHTS = {
    "goldman":        0.15,
    "morgan_stanley": 0.12,
    "bridgewater":    0.14,
    "jpmorgan":       0.12,
    "blackrock":      0.14,
    "citadel":        0.16,
    "harvard":        0.10,
    "bain":           0.07,
}


def _load_api_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    try:
        for line in Path(".env").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val and not val.startswith("#"):
                    return val
    except Exception:
        pass
    return ""


def _load_research() -> dict:
    """Load the latest 8-framework institutional research report."""
    try:
        if RESEARCH_FILE.exists():
            return json.loads(RESEARCH_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("[Claude] Research load: {}".format(e))
    return {}


class ClaudeAnalyst:
    """
    Claude risk manager — integrates 8 institutional research frameworks.
    Tries Claude Code CLI first, then API, falls back to embedded logic.
    """

    def __init__(self):
        self._analysis   = self._load_cached()
        self._call_count = 0
        self._research   = _load_research()
        from utils.claude_client import is_available, _check_claude_code
        if _check_claude_code():
            logger.info("[Claude] Claude Code CLI available — will use CLI for analysis")
        elif _load_api_key():
            logger.info("[Claude] API key found — will use API as fallback")
        else:
            logger.info(
                "[Claude] No CLI or API key — using embedded 8-framework risk manager (free)")

        if self._research:
            fws = [k for k in ["goldman","morgan_stanley","bridgewater",
                               "jpmorgan","blackrock","citadel","harvard","bain"]
                   if k in self._research]
            logger.info("[Claude] Research loaded: {} frameworks active".format(len(fws)))
        else:
            logger.warning("[Claude] No research data — run: python run_research.py")

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def analyze_now(self, snapshots: dict, news: dict,
                    profiles: dict, open_positions: list) -> dict:
        self._call_count += 1
        self._research = _load_research()  # Refresh research data

        from utils.claude_client import is_available
        now_iso = datetime.now(timezone.utc).isoformat()

        if is_available():
            result = self._call_api_with_key(
                snapshots, news, profiles, open_positions, _load_api_key())
            if result:
                result["generated_at"] = now_iso
                result["call_number"]  = self._call_count
                result["source"]       = "claude"
                self._analysis = result
                self._save(result)
                self._append_history(result)
                self._export_html(result)
                self._log_decisions(result)
                return result
            logger.warning("[Claude] Claude call failed — falling back to embedded logic")

        result = self._embedded_analysis(snapshots, news, profiles)
        result["generated_at"] = now_iso
        result["call_number"]  = self._call_count
        result["source"]       = "embedded"
        self._analysis = result
        self._save(result)
        self._append_history(result)
        self._export_html(result)
        self._log_decisions(result)
        return result

    def get_decision(self, coin: str) -> dict:
        coins = self._analysis.get("coins", {})
        rec   = coins.get(coin) or {}
        return {
            "action":         rec.get("action", "evaluate"),
            "confidence_adj": float(rec.get("confidence_adj", 1.0)),
            "reason":         rec.get("reason", "no analysis"),
        }

    def market_bias(self) -> str:
        return self._analysis.get("market_bias", "neutral")

    def risk_multiplier(self) -> float:
        return float(self._analysis.get("risk_multiplier", 1.0))

    def is_ready(self) -> bool:
        return bool(self._analysis and self._analysis.get("coins"))

    def is_configured(self) -> bool:
        return True

    def last_summary(self) -> str:
        if not self._analysis:
            return "Initialising..."
        ts   = self._analysis.get("generated_at", "")[:16]
        bias = self._analysis.get("market_bias", "?").upper()
        rm   = self._analysis.get("risk_multiplier", 1.0)
        note = self._analysis.get("market_note", "")
        src  = self._analysis.get("source", "embedded")
        return "[Claude-{} #{} {}] bias={} rm={:.2f} | {}".format(
            src.upper(), self._call_count, ts, bias, rm, note[:70])

    # ------------------------------------------------------------------ #
    # Live API call                                                        #
    # ------------------------------------------------------------------ #

    def _call_api_with_key(self, snapshots, news, profiles,
                            open_positions, api_key=None) -> dict | None:
        prompt = self._build_api_prompt(snapshots, news, profiles, open_positions)

        # Primary: Claude Code CLI; Fallback: Anthropic API
        from utils.claude_client import call_claude, strip_markdown_fences
        text = call_claude(
            prompt,
            model="sonnet",
            api_model=MODEL,
            max_tokens=MAX_TOKENS,
            timeout=120,
        )

        if not text:
            logger.warning("[Claude] Both CLI and API returned no response")
            return None

        try:
            text = strip_markdown_fences(text)
            result = json.loads(text)
            logger.info("[Claude] Success: bias={} rm={}".format(
                result.get("market_bias", "?"),
                result.get("risk_multiplier", "?")))
            return result
        except json.JSONDecodeError as e:
            logger.error("[Claude] JSON parse error: {} — text: {}".format(
                e, text[:200]))
            return None

    def _build_api_prompt(self, snapshots, news, profiles, open_positions) -> str:
        # Include research data in the prompt for API mode
        research_summary = self._research_summary_text()

        lines = []
        for symbol, tf_snaps in (snapshots or {}).items():
            lines.append("  {}:".format(symbol))
            for tf in ["1d", "4h", "1h", "15m"]:
                snap = (tf_snaps or {}).get(tf)
                if snap and hasattr(snap, "adx"):
                    lines.append(
                        "    {:4s}  ADX={:5.1f}  RSI={:5.1f}  "
                        "dir={:7s}  regime={}  atr={:.2f}%".format(
                            tf, snap.adx, snap.rsi, snap.direction,
                            snap.regime, snap.atr_pct * 100))

        fg_val = (news or {}).get("fear_greed", {}).get("value", 50)
        fg_lbl = (news or {}).get("fear_greed", {}).get("label", "")

        coin_bases = [c.split("/")[0] for c in (snapshots or {}).keys()]
        if not coin_bases:
            coin_bases = ["BTC","ETH","BNB","SOL","XRP","ADA","DOGE","AVAX"]

        return """You are a professional crypto risk manager with 8 institutional frameworks.
Time: {}

LIVE INDICATORS:
{}

SENTIMENT: F&G={} ({})

INSTITUTIONAL RESEARCH:
{}

COINS: {}

Return ONLY valid JSON:
{{"market_bias":"bullish|bearish|neutral","market_note":"one sentence","risk_multiplier":1.0,
"coins":{{"BTC":{{"action":"long_spot|long_futures|short_futures|skip","confidence_adj":1.0,"reason":"<60 chars"}},...}},
"best_trade":"COIN action","avoid":[],"advice":"one sentence"}}

Rules: spot=LONG only. futures=LONG or SHORT. Use Goldman moat+targets, Morgan Stanley DCF verdict,
Citadel entry/stop/target, Bridgewater risk levels. Include ALL coins.""".format(
            datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "\n".join(lines) or "  (scanning...)",
            fg_val, fg_lbl,
            research_summary,
            ", ".join(coin_bases))

    def _research_summary_text(self) -> str:
        """Build a concise text summary of all 8 research frameworks."""
        r = self._research
        if not r:
            return "  (no research data — run python run_research.py)"
        lines = []

        # Goldman
        goldman = r.get("goldman", {})
        if goldman.get("coins"):
            lines.append("  GOLDMAN SACHS SCREENER:")
            for c in goldman["coins"][:8]:
                lines.append("    {} score={} moat={} bull=${} bear=${} stop=${}".format(
                    c.get("ticker","?"), c.get("score","?"), c.get("moat","?"),
                    c.get("bull_target","?"), c.get("bear_target","?"),
                    c.get("stop_loss","?")))

        # Morgan Stanley
        ms = r.get("morgan_stanley", {})
        if ms.get("base"):
            lines.append("  MORGAN STANLEY DCF: {} verdict={} dcf_price=${}".format(
                ms.get("coin","?"), ms["base"].get("verdict","?"),
                ms["base"].get("avg_dcf_price","?")))

        # Bridgewater
        bw = r.get("bridgewater", {})
        if bw:
            lines.append("  BRIDGEWATER: severity={} est_drawdown={}%".format(
                bw.get("bear_severity","?"), bw.get("est_max_drawdown","?")))

        # BlackRock
        br = r.get("blackrock", {})
        if br:
            lines.append("  BLACKROCK: regime={} neutral_return={}".format(
                br.get("regime","?"), br.get("returns",{}).get("neutral_year","?")))

        # Citadel
        ct = r.get("citadel", {})
        if ct:
            trade = ct.get("trade_setup", {})
            lines.append("  CITADEL: {} entry=${} stop=${} target=${} R:R={}".format(
                ct.get("coin","?"), trade.get("entry","?"),
                trade.get("stop","?"), trade.get("target","?"),
                trade.get("rr","?")))

        # Bain
        bn = r.get("bain", {})
        if bn.get("best_pick"):
            bp = bn["best_pick"]
            lines.append("  BAIN BEST PICK: {} — {}".format(
                bp.get("coin","?"), bp.get("rationale","?")[:60]))

        return "\n".join(lines) if lines else "  (no data)"

    # ------------------------------------------------------------------ #
    # Embedded analysis — NOW WITH ALL 8 FRAMEWORKS                       #
    # ------------------------------------------------------------------ #

    def _embedded_analysis(self, snapshots: dict, news: dict,
                             profiles: dict) -> dict:
        fg_val = 50
        try:
            fg_val = int((news or {}).get("fear_greed", {}).get("value", 50))
        except (ValueError, TypeError):
            fg_val = 50

        mkt_chg = (news or {}).get("global", {}).get("market_cap_change_24h", 0)
        btc_dom = (news or {}).get("global", {}).get("btc_dominance", 50)

        market_bias, risk_mult, market_note = self._assess_market(
            fg_val, mkt_chg, btc_dom)

        coin_decisions = {}
        for symbol, tf_snaps in (snapshots or {}).items():
            coin     = symbol.split("/")[0]
            decision = self._decide_coin_integrated(
                coin, tf_snaps, market_bias, fg_val)
            if decision:
                coin_decisions[coin] = decision

        for coin in ["BTC","ETH","BNB","SOL","XRP","ADA","DOGE","AVAX"]:
            if coin not in coin_decisions:
                # Even without snapshots, use research data if available
                decision = self._decide_from_research_only(coin, market_bias, fg_val)
                coin_decisions[coin] = decision

        best   = self._find_best(coin_decisions, snapshots)
        avoid  = [c for c, d in coin_decisions.items()
                  if d["action"] == "skip"
                  and d.get("confidence_adj", 1.0) < 0.5]
        advice = self._generate_advice(market_bias, fg_val, best, avoid)

        return {
            "market_bias":     market_bias,
            "market_note":     market_note,
            "risk_multiplier": risk_mult,
            "coins":           coin_decisions,
            "best_trade":      best,
            "avoid":           avoid[:3],
            "advice":          advice,
        }

    def _assess_market(self, fg, mkt_chg, btc_dom):
        # Also use BlackRock regime from research
        br_regime = (self._research.get("blackrock") or {}).get("regime", "")
        bw_severity = (self._research.get("bridgewater") or {}).get(
            "bear_severity", "MODERATE")

        if fg < 15:
            bias, rm = "bullish", 1.20
            note = "Extreme Fear F&G={} — historical buy opportunity".format(fg)
        elif fg < 25:
            bias, rm = "bullish", 1.10
            note = "Fear F&G={} — lean bullish, follow strong signals".format(fg)
        elif fg < 45:
            bias, rm = "neutral", 1.00
            note = "Cautious F&G={} — neutral, trade technicals".format(fg)
        elif fg < 55:
            bias, rm = "neutral", 1.00
            note = "Neutral F&G={} — follow technical signals".format(fg)
        elif fg < 75:
            bias, rm = "neutral", 0.95
            note = "Mild greed F&G={} — reduce position sizes".format(fg)
        else:
            bias, rm = "bearish", 0.80
            note = "Extreme Greed F&G={} — high reversal risk".format(fg)

        # BlackRock regime override
        if "CRASH" in br_regime or "VOLATILE" in br_regime:
            rm *= 0.85
            note += " | BlackRock: {} regime".format(br_regime)
        elif "ACCUMULATION" in br_regime and bias != "bearish":
            rm *= 1.05
            note += " | BlackRock: accumulation phase"

        # Bridgewater severity adjustment
        if bw_severity == "SEVERE" and bias != "bearish":
            rm *= 0.90
            note += " | Bridgewater: severe risk"

        if mkt_chg > 3.0 and bias != "bearish":
            bias = "bullish"
        elif mkt_chg < -3.0 and bias != "bullish":
            bias = "bearish"
            rm *= 0.90

        return bias, round(rm, 2), note

    # ------------------------------------------------------------------ #
    # Per-coin decision: integrates technical + ALL 8 research frameworks  #
    # ------------------------------------------------------------------ #

    def _decide_coin_integrated(self, coin: str, tf_snaps: dict,
                                 market_bias: str, fg: int) -> dict:
        """
        Full decision using both technical snapshots AND 8-framework research.
        Each framework produces a score from -1.0 (strong sell) to +1.0 (strong buy).
        Weighted composite determines action.
        """
        if not tf_snaps:
            return self._decide_from_research_only(coin, market_bias, fg)

        primary = (tf_snaps.get("1h") or tf_snaps.get("4h")
                   or tf_snaps.get("15m"))
        if not primary:
            return self._decide_from_research_only(coin, market_bias, fg)

        adx     = getattr(primary, "adx",     20.0)
        rsi     = getattr(primary, "rsi",     50.0)
        atr_pct = getattr(primary, "atr_pct", 0.03)
        regime  = getattr(primary, "regime",  "weak_trend")
        direct  = getattr(primary, "direction","neutral")

        # Multi-TF confluence
        tf_dirs = []
        for tf in ["4h", "1h", "15m"]:
            snap = tf_snaps.get(tf)
            if snap:
                tf_dirs.append(getattr(snap, "direction", "neutral"))
        bull_n = tf_dirs.count("bull")
        bear_n = tf_dirs.count("bear")
        total  = max(len(tf_dirs), 1)

        # ── Score from each framework ─────────────────────────────────
        scores = {}

        # 1. Goldman Sachs — moat + momentum + NVT
        scores["goldman"] = self._score_goldman(coin)

        # 2. Morgan Stanley — DCF verdict
        scores["morgan_stanley"] = self._score_morgan_stanley(coin)

        # 3. Bridgewater — risk/drawdown assessment
        scores["bridgewater"] = self._score_bridgewater(coin, atr_pct)

        # 4. JPMorgan — catalyst + implied move
        scores["jpmorgan"] = self._score_jpmorgan(coin, fg)

        # 5. BlackRock — regime + allocation
        scores["blackrock"] = self._score_blackrock(coin)

        # 6. Citadel — technical quant model
        scores["citadel"] = self._score_citadel(coin, rsi, adx, direct, bull_n, bear_n, total)

        # 7. Harvard — yield/staking
        scores["harvard"] = self._score_harvard(coin)

        # 8. Bain — competitive moat
        scores["bain"] = self._score_bain(coin)

        # ── Weighted composite ────────────────────────────────────────
        composite = sum(
            scores.get(fw, 0) * w
            for fw, w in FRAMEWORK_WEIGHTS.items()
        )

        # ── Hard filters ──────────────────────────────────────────────
        if atr_pct > 0.08:
            return {"action": "skip", "confidence_adj": 0.4,
                    "reason": "volatile ATR={:.1f}% — all frameworks defer".format(
                        atr_pct * 100),
                    "scores": scores, "composite": round(composite, 3)}

        if rsi > 82 and adx > 28:
            return {"action": "short_futures", "confidence_adj": 1.10,
                    "reason": "RSI={:.0f} overbought + ADX={:.0f} Citadel short".format(
                        rsi, adx),
                    "scores": scores, "composite": round(composite, 3)}

        if rsi < 20:
            cadj = 1.30 if market_bias in ("bullish", "neutral") else 1.0
            return {"action": "long_spot", "confidence_adj": cadj,
                    "reason": "RSI={:.0f} deeply oversold — Goldman+Citadel BUY".format(rsi),
                    "scores": scores, "composite": round(composite, 3)}

        # ── Decision from composite ───────────────────────────────────
        if composite > 0.35:
            # Strong bullish consensus across frameworks
            if adx > 30 and bull_n >= 2:
                action = "long_futures"
                cadj   = min(1.40, 0.90 + composite)
                reason = "8-fw composite={:.2f} + strong trend ADX={:.0f}".format(
                    composite, adx)
            else:
                action = "long_spot"
                cadj   = min(1.25, 0.85 + composite)
                reason = "8-fw composite={:.2f} bullish consensus".format(composite)
            if market_bias == "bearish":
                cadj *= 0.75
                reason += " (vs bearish mkt)"
            return {"action": action, "confidence_adj": round(cadj, 2),
                    "reason": reason,
                    "scores": scores, "composite": round(composite, 3)}

        if composite < -0.20:
            # Bearish consensus
            if adx > 28 and bear_n >= 2:
                action = "short_futures"
                cadj   = min(1.30, 0.80 + abs(composite))
                reason = "8-fw composite={:.2f} + downtrend ADX={:.0f}".format(
                    composite, adx)
            else:
                action = "skip"
                cadj   = 0.60
                reason = "8-fw composite={:.2f} bearish — skip".format(composite)
            if market_bias == "bullish":
                cadj *= 0.75
            return {"action": action, "confidence_adj": round(cadj, 2),
                    "reason": reason,
                    "scores": scores, "composite": round(composite, 3)}

        # Mixed signals
        if composite > 0.10 and bull_n > bear_n:
            return {"action": "long_spot", "confidence_adj": 0.85,
                    "reason": "8-fw composite={:.2f} mild bullish".format(composite),
                    "scores": scores, "composite": round(composite, 3)}

        if composite < -0.05 and bear_n > bull_n and adx > 25:
            return {"action": "short_futures", "confidence_adj": 0.80,
                    "reason": "8-fw composite={:.2f} mild bearish".format(composite),
                    "scores": scores, "composite": round(composite, 3)}

        # Ranging — use Goldman entry zones
        gs = self._get_goldman_data(coin)
        if gs and rsi < 38:
            return {"action": "long_spot", "confidence_adj": 0.90,
                    "reason": "ranging RSI={:.0f} near Goldman entry ${:.0f}".format(
                        rsi, gs.get("entry_low", 0)),
                    "scores": scores, "composite": round(composite, 3)}

        return {"action": "skip", "confidence_adj": 0.70,
                "reason": "mixed signals composite={:.2f}".format(composite),
                "scores": scores, "composite": round(composite, 3)}

    def _decide_from_research_only(self, coin: str, market_bias: str,
                                    fg: int) -> dict:
        """Decision when NO technical snapshots available — use research only."""
        scores = {
            "goldman":        self._score_goldman(coin),
            "morgan_stanley": self._score_morgan_stanley(coin),
            "bridgewater":    self._score_bridgewater(coin, 0.03),
            "jpmorgan":       self._score_jpmorgan(coin, fg),
            "blackrock":      self._score_blackrock(coin),
            "citadel":        0.0,  # No technical data
            "harvard":        self._score_harvard(coin),
            "bain":           self._score_bain(coin),
        }
        composite = sum(scores.get(fw, 0) * w for fw, w in FRAMEWORK_WEIGHTS.items())

        if composite > 0.30 and fg < 30:
            return {"action": "long_spot", "confidence_adj": 0.85,
                    "reason": "research-only composite={:.2f} + F&G={}".format(
                        composite, fg),
                    "scores": scores, "composite": round(composite, 3)}
        if composite > 0.20:
            return {"action": "long_spot", "confidence_adj": 0.75,
                    "reason": "research-only composite={:.2f}".format(composite),
                    "scores": scores, "composite": round(composite, 3)}
        if composite < -0.15:
            return {"action": "skip", "confidence_adj": 0.50,
                    "reason": "research bearish composite={:.2f}".format(composite),
                    "scores": scores, "composite": round(composite, 3)}

        return {"action": "skip", "confidence_adj": 0.80,
                "reason": "no technicals, research neutral",
                "scores": scores, "composite": round(composite, 3)}

    # ------------------------------------------------------------------ #
    # Individual framework scoring — each returns -1.0 to +1.0             #
    # ------------------------------------------------------------------ #

    def _get_goldman_data(self, coin: str) -> dict:
        """Get Goldman Sachs screener data for a coin."""
        gs = self._research.get("goldman", {})
        for c in gs.get("coins", []):
            if c.get("ticker") == coin:
                return c
        return {}

    def _score_goldman(self, coin: str) -> float:
        """Goldman: moat + NVT + momentum → -1.0 to +1.0"""
        c = self._get_goldman_data(coin)
        if not c:
            return 0.0
        score = 0.0
        moat = c.get("moat", "Weak")
        if moat == "Strong":    score += 0.3
        elif moat == "Moderate":score += 0.1
        else:                   score -= 0.2

        nvt = c.get("nvt_rating", "Fair")
        if nvt == "Cheap":      score += 0.3
        elif nvt == "Overvalued": score -= 0.3

        chg30 = c.get("chg_30d", 0)
        if chg30 > 10:          score += 0.2
        elif chg30 > 0:         score += 0.1
        elif chg30 < -10:       score -= 0.2
        else:                   score -= 0.1

        trend = c.get("trend", "Neutral")
        if trend == "Bull":     score += 0.2
        elif trend == "Bear":   score -= 0.2

        return max(-1.0, min(1.0, score))

    def _score_morgan_stanley(self, coin: str) -> float:
        """Morgan Stanley: DCF verdict → -1.0 to +1.0"""
        ms = self._research.get("morgan_stanley", {})
        if not ms:
            return 0.0
        # Research stores the DCF for one coin (typically BTC)
        # Check if this coin matches
        ms_coin = ms.get("coin", "")
        if ms_coin and ms_coin.upper() != coin:
            # General: use base verdict directionally
            verdict = ms.get("base", {}).get("verdict", "Fairly Valued")
            if "Undervalued" in verdict: return 0.3
            if "Overvalued" in verdict:  return -0.3
            return 0.0

        base    = ms.get("base", {})
        verdict = base.get("verdict", "Fairly Valued")
        vs_mkt  = base.get("vs_market_pct", 0)

        if "Undervalued" in verdict:
            return min(1.0, 0.3 + abs(vs_mkt) / 100)
        if "Overvalued" in verdict:
            return max(-1.0, -0.3 - abs(vs_mkt) / 100)
        return 0.0

    def _score_bridgewater(self, coin: str, atr_pct: float) -> float:
        """Bridgewater: risk assessment → -1.0 to +1.0 (negative = high risk)"""
        bw = self._research.get("bridgewater", {})
        if not bw:
            return 0.0
        score = 0.0
        severity = bw.get("bear_severity", "MODERATE")
        if severity == "SEVERE":    score -= 0.4
        elif severity == "MILD":    score += 0.2
        else:                       score -= 0.1

        est_dd = bw.get("est_max_drawdown", 30)
        if est_dd > 50:             score -= 0.3
        elif est_dd > 30:           score -= 0.1
        else:                       score += 0.1

        # High volatility penalty
        if atr_pct > 0.05:          score -= 0.2
        return max(-1.0, min(1.0, score))

    def _score_jpmorgan(self, coin: str, fg: int) -> float:
        """JPMorgan: F&G catalyst + earnings play → -1.0 to +1.0"""
        score = 0.0
        if fg < 15:    score += 0.5   # Extreme fear = buy
        elif fg < 25:  score += 0.3
        elif fg < 45:  score += 0.0
        elif fg < 55:  score += 0.0
        elif fg < 75:  score -= 0.1
        else:          score -= 0.4   # Extreme greed = sell

        jpm = self._research.get("jpmorgan", {})
        if jpm:
            catalysts = jpm.get("catalysts", [])
            for cat in catalysts:
                if cat.get("coin") == coin and cat.get("type") == "bullish":
                    score += 0.2
                    break

        return max(-1.0, min(1.0, score))

    def _score_blackrock(self, coin: str) -> float:
        """BlackRock: regime + allocation weighting → -1.0 to +1.0"""
        br = self._research.get("blackrock", {})
        if not br:
            return 0.0
        score = 0.0
        regime = br.get("regime", "GROWTH")
        if "GROWTH" in regime or "ACCUMULATION" in regime:
            score += 0.3
        elif "VOLATILE" in regime or "CRASH" in regime:
            score -= 0.4
        elif "BEAR" in regime:
            score -= 0.2

        # Check if coin is in BlackRock's core holdings
        alloc = br.get("allocation", {})
        for pos in alloc.get("positions", []):
            if pos.get("symbol") == coin:
                role = pos.get("role", "satellite")
                if role == "core":  score += 0.2
                else:               score += 0.1
                break

        return max(-1.0, min(1.0, score))

    def _score_citadel(self, coin: str, rsi: float, adx: float,
                        direction: str, bull_n: int, bear_n: int,
                        total: int) -> float:
        """Citadel: technical quant model → -1.0 to +1.0"""
        score = 0.0

        # RSI
        if rsi < 30:    score += 0.3
        elif rsi < 40:  score += 0.1
        elif rsi > 70:  score -= 0.3
        elif rsi > 60:  score -= 0.1

        # ADX + direction
        if adx > 30:
            if direction == "bull":   score += 0.4
            elif direction == "bear": score -= 0.4
        elif adx > 22:
            if direction == "bull":   score += 0.2
            elif direction == "bear": score -= 0.2

        # Multi-TF confluence
        if bull_n == total and total >= 2:
            score += 0.3
        elif bear_n == total and total >= 2:
            score -= 0.3

        # Use Citadel research entry/stop levels
        ct = self._research.get("citadel", {})
        if ct and ct.get("coin", "").upper() == coin:
            trade = ct.get("trade_setup", {})
            rr = trade.get("rr", "0")
            try:
                rr_val = float(str(rr).split(":")[0])
                if rr_val >= 3.0:  score += 0.2
                elif rr_val >= 2.0: score += 0.1
            except (ValueError, IndexError):
                pass

        return max(-1.0, min(1.0, score))

    def _score_harvard(self, coin: str) -> float:
        """Harvard: yield/staking quality → -1.0 to +1.0"""
        hv = self._research.get("harvard", {})
        if not hv:
            return 0.0
        for y in hv.get("yields", []):
            if y.get("symbol") == coin:
                safety = y.get("safety_score", 5)
                if safety >= 8:  return 0.3
                elif safety >= 6: return 0.1
                elif safety <= 3: return -0.2
                return 0.0
        return 0.0

    def _score_bain(self, coin: str) -> float:
        """Bain: competitive moat + best pick → -1.0 to +1.0"""
        bn = self._research.get("bain", {})
        if not bn:
            return 0.0
        score = 0.0

        # Best pick bonus
        bp = bn.get("best_pick", {})
        if bp.get("coin", "").upper() == coin:
            score += 0.4

        # Competitive moat from landscape
        for comp in bn.get("landscape", []):
            if comp.get("symbol") == coin:
                moat = comp.get("moat", "Moderate")
                if moat == "Strong":    score += 0.2
                elif moat == "Weak":    score -= 0.2
                mgmt = comp.get("management", "")
                if "Strong" in str(mgmt): score += 0.1
                break

        return max(-1.0, min(1.0, score))

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _find_best(self, decisions: dict, snapshots: dict) -> str:
        best_score = 0
        best       = ""
        for coin, dec in decisions.items():
            if dec.get("action", "skip") == "skip":
                continue
            cadj      = dec.get("confidence_adj", 1.0)
            composite = dec.get("composite", 0)
            score     = cadj * (1 + composite)
            if score > best_score:
                best_score = score
                best = "{} {}".format(coin, dec["action"])
        return best or "none"

    def _generate_advice(self, bias, fg, best, avoid):
        r = self._research
        bain_bp = (r.get("bain") or {}).get("best_pick", {}).get("coin", "")
        gs_top = ""
        if r.get("goldman", {}).get("coins"):
            gs_top = r["goldman"]["coins"][0].get("ticker", "")

        if fg < 20:
            return ("Extreme Fear — 8 frameworks favor accumulation. "
                    "Goldman top: {}. Bain pick: {}. "
                    "Buy spot longs, use Citadel entry levels.".format(
                        gs_top or "BTC", bain_bp or "ETH"))
        if fg > 75:
            return ("Extreme Greed — Bridgewater warns of reversal risk. "
                    "Tighten stops to Goldman levels. Short futures only with "
                    "Citadel confirmation.")
        if bias == "bullish":
            return ("Bullish — Goldman+Citadel+Bain aligned. Best: {}. "
                    "Use Morgan Stanley DCF as value anchor.".format(best))
        if bias == "bearish":
            return ("Bearish — Bridgewater+BlackRock caution. "
                    "Short futures on 4h+1h bear confluence. "
                    "Avoid spot longs unless Harvard yield > 5%.")
        return ("Neutral — trade Citadel setups with R:R > 2:1. "
                "Goldman entry zones as support. Best signal: {}.".format(best))

    # ------------------------------------------------------------------ #
    # Logging                                                              #
    # ------------------------------------------------------------------ #

    def _log_decisions(self, analysis: dict):
        src    = analysis.get("source", "embedded").upper()
        bias   = analysis.get("market_bias", "?").upper()
        rm     = analysis.get("risk_multiplier", 1.0)
        note   = analysis.get("market_note", "")
        best   = analysis.get("best_trade", "?")
        avoid  = analysis.get("avoid", [])
        advice = analysis.get("advice", "")

        logger.info("[Claude-{}] ---- ANALYSIS #{} (8 FRAMEWORKS) ----".format(
            src, self._call_count))
        logger.info("[Claude] Bias={} rm={:.2f} | {}".format(bias, rm, note))
        logger.info("[Claude] Best: {} | Avoid: {}".format(
            best, ", ".join(avoid) if avoid else "none"))
        logger.info("[Claude] {}".format(advice))

        labels = {"long_spot":     "BUY   SPOT   ",
                  "long_futures":  "LONG  FUTURES",
                  "short_futures": "SHORT FUTURES",
                  "skip":          "SKIP         "}
        for coin, rec in analysis.get("coins", {}).items():
            action    = rec.get("action", "?")
            cadj      = rec.get("confidence_adj", 1.0)
            reason    = rec.get("reason", "")
            composite = rec.get("composite", 0)
            logger.info("[Claude]   {:<6} {} adj={:.2f}x comp={:+.2f} | {}".format(
                coin, labels.get(action, action.upper()[:13]),
                cadj, composite, reason[:55]))

    # ------------------------------------------------------------------ #
    # HTML export                                                          #
    # ------------------------------------------------------------------ #

    def _export_html(self, a: dict):
        try:
            bias   = a.get("market_bias", "neutral")
            note   = a.get("market_note", "")
            rm     = a.get("risk_multiplier", 1.0)
            best   = a.get("best_trade", "")
            avoid  = ", ".join(a.get("avoid", []))
            advice = a.get("advice", "")
            ts     = a.get("generated_at", "")[:16]
            n      = a.get("call_number", 0)
            src    = a.get("source", "embedded")

            bc = {"bullish": "#4ade80", "bearish": "#f87171"}.get(bias, "#fbbf24")
            ac = {"long_spot": "#4ade80", "long_futures": "#34d399",
                  "short_futures": "#f87171", "skip": "#94a3b8"}
            src_color = "#34d399" if src == "api" else "#94a3b8"
            src_label = "LIVE CLAUDE API" if src == "api" else "EMBEDDED 8-FRAMEWORK"

            rows = ""
            for coin, rec in a.get("coins", {}).items():
                action    = rec.get("action", "skip")
                cadj      = rec.get("confidence_adj", 1.0)
                reason    = rec.get("reason", "")
                composite = rec.get("composite", 0)
                color     = ac.get(action, "#94a3b8")
                cc        = "#4ade80" if cadj >= 1.1 else "#f87171" if cadj <= 0.7 else "#e2e8f0"
                comp_c    = "#4ade80" if composite > 0.2 else "#f87171" if composite < -0.1 else "#fbbf24"
                rows  += ("<tr><td style='font-weight:bold'>{}</td>"
                          "<td style='color:{}'>{}</td>"
                          "<td style='color:{}'>{:.2f}x</td>"
                          "<td style='color:{}'>{:+.2f}</td>"
                          "<td style='color:#94a3b8'>{}</td></tr>".format(
                    coin, color, action.replace("_", " ").upper(),
                    cc, cadj, comp_c, composite, reason[:80]))

            html = (
                "<!DOCTYPE html><html><head><meta charset='UTF-8'>"
                "<meta http-equiv='refresh' content='60'>"
                "<title>Claude 8-Framework Analysis</title>"
                "<style>body{{background:#0f172a;color:#e2e8f0;"
                "font-family:monospace;padding:24px}}"
                "h1{{color:#38bdf8}}"
                ".card{{background:#1e293b;border-radius:8px;padding:16px;margin:10px 0}}"
                ".badge{{padding:3px 10px;border-radius:4px;font-size:.8em;font-weight:bold}}"
                "table{{border-collapse:collapse;width:100%}}"
                "th,td{{padding:8px 12px;text-align:left;border-bottom:1px solid #1e293b}}"
                "th{{color:#94a3b8;font-size:.8em;text-transform:uppercase}}"
                "</style></head><body>"
                "<h1>Claude 8-Framework Risk Manager — #{n}</h1>"
                "<p style='color:#64748b'>{ts} UTC | 60s refresh | "
                "<span class='badge' style='background:{sc};color:#0f172a'>{sl}</span></p>"
                "<div class='card'>"
                "<b>Bias: <span style='color:{bc}'>{bias}</span></b>"
                " &nbsp; Risk Mult: <b>{rm:.2f}x</b><br>"
                "<span style='color:#94a3b8'>{note}</span></div>"
                "<div class='card'>"
                "<b>Best:</b> {best} &nbsp; <b>Avoid:</b> {avoid}<br>"
                "<b>Advice:</b> {advice}</div>"
                "<h2 style='color:#7dd3fc;margin-top:20px'>Per-Coin (8-Framework Composite)</h2>"
                "<table><tr><th>Coin</th><th>Action</th>"
                "<th>Conf</th><th>Composite</th><th>Reason</th></tr>"
                "{rows}</table>"
                "<p style='color:#94a3b8;margin-top:12px;font-size:.85em'>"
                "Frameworks: Goldman(15%) Morgan Stanley(12%) Bridgewater(14%) "
                "JPMorgan(12%) BlackRock(14%) Citadel(16%) Harvard(10%) Bain(7%)</p>"
                "<p style='color:#334155;margin-top:24px;font-size:.8em'>"
                "Updated every 15 min | data/claude_analysis.json</p>"
                "</body></html>"
            ).format(
                n=n, ts=ts, sc=src_color, sl=src_label, bc=bc,
                bias=bias.upper(), rm=rm, note=note,
                best=best or "none", avoid=avoid or "none",
                advice=advice, rows=rows)

            ANALYSIS_HTML.parent.mkdir(parents=True, exist_ok=True)
            ANALYSIS_HTML.write_text(html, encoding="utf-8")
        except Exception as e:
            logger.debug("[Claude] HTML: {}".format(e))

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _save(self, analysis: dict):
        try:
            ANALYSIS_FILE.parent.mkdir(parents=True, exist_ok=True)
            ANALYSIS_FILE.write_text(
                json.dumps(analysis, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.debug("[Claude] Save: {}".format(e))

    def _load_cached(self) -> dict:
        try:
            if ANALYSIS_FILE.exists():
                return json.loads(ANALYSIS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _append_history(self, analysis: dict):
        try:
            history = []
            if HISTORY_FILE.exists():
                history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            history.append(analysis)
            history = history[-96:]
            HISTORY_FILE.write_text(
                json.dumps(history, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.debug("[Claude] History: {}".format(e))
