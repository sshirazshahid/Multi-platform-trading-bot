"""Decision accuracy tracking."""
import json
import time

from core.scoring.constants import ACCURACY_FILE

class AccuracyTracker:
    """Tracks how accurate past MCP decisions were. Feeds stats back to Claude."""

    def __init__(self):
        self._records = self._load()

    def record_decision(self, coin: str, action: str, price: float, confidence: float):
        """Record a decision at the time it was made."""
        ts = time.time()
        self._records.append({
            "ts": ts, "coin": coin, "action": action,
            "price": price, "confidence": confidence,
            "resolved": False, "outcome": None,
        })
        # Keep last 200 decisions
        if len(self._records) > 200:
            self._records = self._records[-200:]
        self._save()

    def resolve_outcomes(self, current_prices: dict):
        """Check unresolved decisions against current prices (30-min min age, 4-h expiry)."""
        now = time.time()
        for rec in self._records:
            if rec["resolved"]:
                continue
            age = now - rec["ts"]
            if age < 1800:  # Wait at least 30 minutes
                continue
            if age > 14400:  # Expire after 4 hours
                rec["resolved"] = True
                rec["outcome"] = "expired"
                continue
            coin = rec["coin"]
            if coin not in current_prices:
                continue
            entry = rec["price"]
            current = current_prices[coin]
            if entry <= 0:
                continue
            chg = (current - entry) / entry
            action = rec["action"]
            # 0.3% threshold — wins/losses above noise, flat band inside ±0.3%
            if action == "BUY":
                rec["outcome"] = "win" if chg > 0.003 else "loss" if chg < -0.003 else "flat"
            elif action == "SELL":
                rec["outcome"] = "win" if chg < -0.003 else "loss" if chg > 0.003 else "flat"
            else:
                rec["outcome"] = "flat"
            rec["resolved"] = True
        self._save()

    def stats(self) -> dict:
        """Return accuracy stats for last 50 resolved decisions."""
        resolved = [r for r in self._records if r["resolved"] and r["outcome"] != "expired"]
        recent = resolved[-50:]
        if not recent:
            return {"total": 0, "win_rate": 0.5, "avg_confidence": 0.5}
        wins = sum(1 for r in recent if r["outcome"] == "win")
        total = len(recent)
        avg_conf = sum(r.get("confidence", 0.5) for r in recent) / total
        return {
            "total": total,
            "wins": wins,
            "losses": sum(1 for r in recent if r["outcome"] == "loss"),
            "flat": sum(1 for r in recent if r["outcome"] == "flat"),
            "win_rate": round(wins / total, 3) if total > 0 else 0.5,
            "avg_confidence": round(avg_conf, 3),
        }

    def _save(self):
        try:
            ACCURACY_FILE.parent.mkdir(parents=True, exist_ok=True)
            ACCURACY_FILE.write_text(
                json.dumps(self._records[-200:], default=str), encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> list:
        try:
            if ACCURACY_FILE.exists():
                data = json.loads(ACCURACY_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
        except Exception:
            pass
        return []
