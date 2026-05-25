"""Phase A leak-check: re-run the futures OOS metrics at embargo=24 vs >=96.

Hypothesis (no-edge-forensics 2026-05-25): the live ensemble's 0.80 OOS-WR is
a label-leakage artifact because the CV ran with embargo_bars=24 < the 96-bar
label horizon. If the OOS metrics collapse toward the base rate under
embargo>=96, leakage is confirmed.

Read-only. Reuses the trainer's real API (load_dataset / _walk_forward_oos /
_summarise / LRModel). Writes a verdict to reports/.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from core.models import LRModel  # noqa: E402
from scripts.train_models import (  # noqa: E402
    _summarise,
    _walk_forward_oos,
    load_dataset,
)


def _run(market: str, embargo: int) -> dict:
    X, y, ts, syms, source = load_dataset(market)
    if X.shape[0] == 0:
        return {"embargo": embargo, "n_oos": 0, "base_rate": 0.0,
                "wr_at_0.55": float("nan"), "auc": float("nan"),
                "dsr": float("nan")}
    oos, _folds, _mat = _walk_forward_oos(
        X, y, n_splits=5, embargo_bars=embargo,
        build_model=lambda: LRModel(C=0.5))
    s = _summarise(y, oos, n_trials=1)
    return {
        "embargo": embargo,
        "n_oos": int(s.get("n_oos", 0) or 0),
        "base_rate": float(np.mean(y)) if len(y) else 0.0,
        "wr_at_0.55": float(s.get("wr_at_0.55", float("nan"))),
        "auc": float(s.get("auc", float("nan"))),
        "dsr": float(s.get("deflated_sharpe", float("nan"))),
    }


def main() -> int:
    market = "futures"
    lo = _run(market, 24)
    hi = _run(market, 96)

    def _f(v):
        return "nan" if v != v else f"{v:.3f}"  # NaN-safe

    delta = (lo["wr_at_0.55"] - hi["wr_at_0.55"]
             if (lo["wr_at_0.55"] == lo["wr_at_0.55"]
                 and hi["wr_at_0.55"] == hi["wr_at_0.55"]) else float("nan"))
    verdict = (
        "LEAKAGE CONFIRMED — WR collapses when embargo >= label horizon"
        if (delta == delta and delta > 0.10)
        else "no large embargo effect — investigate PBO/label join elsewhere")

    body = (
        f"# Leak check — {market} (2026-05-25)\n\n"
        f"| embargo | n_oos | base_rate | WR@0.55 | AUC | DSR |\n"
        f"|---|---:|---:|---:|---:|---:|\n"
        f"| 24 (current) | {lo['n_oos']} | {_f(lo['base_rate'])} | "
        f"{_f(lo['wr_at_0.55'])} | {_f(lo['auc'])} | {_f(lo['dsr'])} |\n"
        f"| 96 (>= horizon) | {hi['n_oos']} | {_f(hi['base_rate'])} | "
        f"{_f(hi['wr_at_0.55'])} | {_f(hi['auc'])} | {_f(hi['dsr'])} |\n\n"
        f"WR@0.55 delta (24 minus 96): {_f(delta)}\n\n"
        f"**Verdict: {verdict}**\n"
    )
    out = ROOT / "reports" / "leak_check_2026-05-25.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(body, encoding="utf-8")
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
