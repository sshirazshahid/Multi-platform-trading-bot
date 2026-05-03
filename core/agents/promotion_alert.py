"""Promotion-gate notifier — emits alert when PROMOTE flips."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from core.multi_agent_promotion_gate import MAEvaluation, MAVerdict


def maybe_alert(
    evaluation: MAEvaluation,
    state_path: Path,
    notifier=None,
) -> bool:
    """If verdict transitions HOLD/REJECT → PROMOTE, send alert.

    Idempotent: persists last_verdict so alert fires once per transition.
    Returns True if an alert was sent.
    """
    state_path = Path(state_path)
    last: Optional[str] = None
    if state_path.exists():
        try:
            last = json.loads(state_path.read_text())["last_verdict"]
        except Exception:
            last = None

    sent = False
    if evaluation.verdict == MAVerdict.PROMOTE and last != "PROMOTE":
        if notifier is not None:
            try:
                msg = (
                    f"SHADOW PROMOTION GATE: PROMOTE\n"
                    f"Shadow n={evaluation.shadow_n} WR={evaluation.shadow_wr:.1%} "
                    f"sum=${evaluation.shadow_sum_pnl:.2f}\n"
                    f"Live n={evaluation.live_n} WR={evaluation.live_wr:.1%} "
                    f"sum=${evaluation.live_sum_pnl:.2f}\n"
                    f"Reason: {evaluation.reason}\n\n"
                    f"NEXT STEP: User confirmation required to advance to "
                    f"Phase C cutover (A/B traffic split).\n"
                    f"Run: python scripts/promotion_advance.py --confirm"
                )
                notifier.info(msg)
            except Exception:
                pass
        sent = True

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "last_verdict": str(evaluation.verdict.value),
        "last_n": evaluation.shadow_n,
    }), encoding="utf-8")
    return sent
