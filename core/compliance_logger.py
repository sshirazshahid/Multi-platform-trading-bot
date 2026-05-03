"""
core/compliance_logger.py — Feature 14: Compliance & Regulatory Logging

Maintains an immutable append-only trade log suitable for:
  - Tax reporting (FIFO/LIFO PnL per trade)
  - Regulatory audit trails
  - Portfolio reconciliation

Each trade record includes:
  - Timestamp (UTC), exchange, symbol, side, quantity, price
  - Strategy that generated the signal
  - PnL in USDT, fee estimate
  - DRY RUN flag (paper trade vs real trade)

Output formats:
  - JSON log  : data/compliance/trades_YYYY-MM.json
  - CSV export: data/compliance/trades_YYYY-MM.csv  (for Excel/tax tools)

IMPORTANT:
  Trading crypto may have tax and regulatory obligations in your jurisdiction.
  This log helps you meet those obligations but does NOT constitute legal or
  tax advice. Consult a qualified advisor in your country.
"""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

COMPLIANCE_DIR = Path("data/compliance")


class ComplianceLogger:

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        COMPLIANCE_DIR.mkdir(parents=True, exist_ok=True)

    def _month_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def _json_path(self) -> Path:
        return COMPLIANCE_DIR / f"trades_{self._month_key()}.json"

    def _csv_path(self) -> Path:
        return COMPLIANCE_DIR / f"trades_{self._month_key()}.csv"

    def log_trade(self, exchange: str, symbol: str, side: str,
                  quantity: float, price: float, strategy: str,
                  pnl: float = 0.0, fee_pct: float = 0.001,
                  order_id: str = "", reason: str = "signal"):
        """Append a trade record to the compliance log."""
        ts  = datetime.now(timezone.utc).isoformat()
        fee = quantity * price * fee_pct

        record = {
            "timestamp":  ts,
            "exchange":   exchange,
            "symbol":     symbol,
            "side":       side.upper(),
            "quantity":   round(quantity, 8),
            "price":      round(price, 8),
            "notional":   round(quantity * price, 4),
            "fee_usdt":   round(fee, 6),
            "pnl_usdt":   round(pnl, 6),
            "strategy":   strategy,
            "order_id":   order_id,
            "reason":     reason,
            "paper_trade": self.dry_run,
        }

        self._append_json(record)
        self._append_csv(record)
        logger.debug(
            f"[Compliance] Logged: {side.upper()} {quantity:.6f} {symbol} "
            f"@ {price:.4f} pnl={pnl:+.4f} USDT"
        )

    def export_summary(self) -> dict:
        """Return a summary of this month's trades."""
        path = self._json_path()
        if not path.exists():
            return {"trades": 0, "total_pnl": 0.0, "total_fees": 0.0}
        try:
            records = []
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            # Fallback: try old JSON array format
            if not records:
                try:
                    records = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            real = [r for r in records if not r.get("paper_trade")]
            return {
                "month":       self._month_key(),
                "trades":      len(real),
                "paper_trades": len(records) - len(real),
                "total_pnl":   round(sum(r.get("pnl_usdt", 0) for r in real), 4),
                "total_fees":  round(sum(r.get("fee_usdt", 0) for r in real), 4),
                "csv_path":    str(self._csv_path()),
                "json_path":   str(self._json_path()),
            }
        except Exception as e:
            logger.error(f"[Compliance] Summary error: {e}")
            return {}

    # ── Private ───────────────────────────────────────────────────────

    def _append_json(self, record: dict):
        path = self._json_path()
        try:
            record_line = json.dumps(record)
            with open(path, "a", encoding="utf-8") as f:
                f.write(record_line + "\n")
        except Exception as e:
            logger.error(f"[Compliance] JSON append failed: {e}")

    def _append_csv(self, record: dict):
        path    = self._csv_path()
        headers = list(record.keys())
        exists  = path.exists()
        try:
            with open(path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                if not exists:
                    writer.writeheader()
                writer.writerow(record)
        except Exception as e:
            logger.error(f"[Compliance] CSV append failed: {e}")
