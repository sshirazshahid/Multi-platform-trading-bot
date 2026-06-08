"""
preflight_check.py - advisory live-readiness check. READ-ONLY; never trades.
Surfaces the high-severity audit findings so the bot can't be flipped to
CONTROLLED_LIVE with disabled safety rails. Run: python -m quant_suite.preflight_check
"""
from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _func_unconditional_false(path: str, func: str) -> bool:
    try:
        txt = (ROOT / path).read_text()
    except Exception:
        return False
    i = txt.find("def " + func)
    if i < 0:
        return False
    body = txt[i: i + 600]
    end = body.find("\n    def ", 5)
    if end > 0:
        body = body[:end]
    lines = [ln.strip() for ln in body.splitlines()[1:] if ln.strip() and not ln.strip().startswith("#")
             and not ln.strip().startswith('"""') and not ln.strip().startswith("'''")]
    code = [ln for ln in lines if not ln.startswith('"')]
    if not code:
        return False
    return code[0] == "return False" or (code[0].startswith("return False") and not any(
        k in " ".join(code[:1]) for k in ("if ", "for ", "while ")))


def check() -> dict:
    issues, ok = [], []
    op = os.getenv("OPERATING_MODE", "PAPER").upper()
    ok.append(f"OPERATING_MODE={op}  CONTROLLED_LIVE_ENABLED={os.getenv('CONTROLLED_LIVE_ENABLED', 'false')}")
    try:
        _am = (ROOT / "core" / "auto_mutator.py").read_text()
    except Exception:
        _am = ""
    if "def shorts_blocked" in _am and ("UNBLOCK_ALL" in _am or _func_unconditional_false("core/auto_mutator.py", "shorts_blocked")):
        issues.append("HIGH: auto_mutator.shorts_blocked() is short-circuited (UNBLOCK_ALL early return) -> "
                      "config.SHORTS_DISABLED and the loss-driven short-block window are DEAD; shorts always "
                      "execute (trade data: shorts -109 vs longs -13). Fix: remove the early 'return False'.")
    if _func_unconditional_false("core/risk_manager.py", "is_halted"):
        issues.append("HIGH: risk_manager.is_halted() returns False -> hard drawdown/streak halt disabled. Re-enable before live.")
    if op == "CONTROLLED_LIVE":
        issues.append("HIGH: live mode active while config holds 'PAPER aggressive' constants. Load a vetted live profile first.")
    else:
        ok.append("Bot is paper/observation (not live) - safe.")
    issues.append("MED: realized stops (~0.89%) < documented 1.5-3.5% ATR floor -> verify SL clamp in order/risk path.")
    return {"operating_mode": op, "ok": ok, "issues": issues,
            "live_ready": op != "CONTROLLED_LIVE" and not any(i.startswith("HIGH") for i in issues)}


if __name__ == "__main__":
    r = check()
    print("=" * 66); print("PREFLIGHT / LIVE-READINESS CHECK (advisory)"); print("=" * 66)
    for o in r["ok"]:
        print("  OK   ", o)
    for i in r["issues"]:
        print("  !!   ", i)
    print("\nlive_ready:", r["live_ready"], "- stay PAPER until HIGH items resolved")
