"""Categorize block reasons from today's bot log."""
import re
from collections import Counter

patterns = {
    "already_open":       r"\[Claude\] (\w+) already open on (\w+)",
    "total_full":         r"\[Claude\] Total (\d+)/(\d+)",
    "per_ex_full":        r"\[Claude\] (\w+): (\d+)/(\d+) positions",
    "blacklist":          r"\[Claude\] BLOCKED by (?:blacklist|dynamic)",
    "caution_symbol":     r"\[Claude\] BLOCKED: .* is caution symbol",
    "caution_strategy":   r"\[Claude\] BLOCKED: strategy .* is caution",
    "fee_heavy":          r"\[Claude\] BLOCKED: strategy .* is fee-heavy",
    "correlation":        r"\[Claude\] BLOCKED: .* correlation group",
    "universe_filter":    r"\[Claude\] BLOCKED by universe filter",
    "risk_manager":       r"\[Claude\] BLOCKED by risk manager",
    "exchange_halted":    r"\[Claude\] BLOCKED: \w+ is HALTED",
    "short_needs_bear":   r"\[Claude\] BLOCKED: SHORT .* requires BTC",
    "low_balance":        r"balance \$[0-9.]+ < \$",
    "spot_short":         r"Cannot short on spot",
    "rr_low":             r"R:R .* minimum",
    "tier_rejected":      r"\[Tier\] .* REJECTED",
    "meta_skip":          r"\[MetaFilter\] .* SKIP",
    "meta_review":        r"\[MetaFilter\] REVIEW",
    "symbol_paused":      r"\[Risk/Spec12\] .* is paused",
    "family_paused":      r"\[Risk/Spec12\] strategy family",
    "universe_mode":      r"\[Mode\] BLOCKED: .* outside configured universe",
    "open_pos_rejected":  r"open_position returned None",
}
counts = Counter()
samples = {}
per_symbol_already = Counter()

f = "logs/bot_2026-04-21.log"
with open(f, "r", encoding="utf-8", errors="ignore") as fh:
    for line in fh:
        for name, pat in patterns.items():
            m = re.search(pat, line)
            if m:
                counts[name] += 1
                if name not in samples:
                    samples[name] = line.strip()[:160]
                if name == "already_open":
                    per_symbol_already[m.group(1)] += 1

print(f"=== Block reasons in {f} ===")
for name, n in counts.most_common():
    print(f"  {n:5d}  {name}")
    print(f"         sample: {samples[name][:150]}")

print()
print(f"TOTAL block events: {sum(counts.values())}")

print()
print("=== Which base assets triggered 'already_open' most ===")
for sym, n in per_symbol_already.most_common(15):
    print(f"  {n:4d}  {sym}")
