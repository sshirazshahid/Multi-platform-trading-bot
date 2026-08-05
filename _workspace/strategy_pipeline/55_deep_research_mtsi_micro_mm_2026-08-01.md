# 55 — Deep research: Micro Two-Sided Inventory (MTSI)

> **Superseded interpretation (2026-08-01):** this branch incorrectly treated
> the owner's "$1" as a one-dollar CEX inventory cap. The intended invariant is
> the all-in acquisition cost of equal UP + DOWN prediction-market shares
> remaining below their $1 complete-set payout. The frozen preregistration and
> NO_GO result below remain unchanged for research integrity. The corrected,
> venue-specific branch is documented in
> `56_deep_research_complementary_outcome_accumulation_2026-08-01.md`.

*Generated: 2026-08-01 | Sources: ~15 | Confidence: Medium*

## Owner doctrine (binding)

1. Build Up & Down at different times.
2. Keep both sides balanced while total stays below **$1** USD gross inventory.
3. Keep small directional exposure when a model finds an undervalued side.
4. Edge can never be one big trade — capture small inefficiencies thousands of times.

## Mapping

| Doctrine | Literature |
|----------|------------|
| Inventory-balanced two-sided quotes | Avellaneda–Stoikov reservation price |
| Mild undervalued-side tilt | arXiv 1206.4810 (directional bets under inventory constraints) |
| Many tiny edges | Maker spread / rebate capture; funding-aware AS on perps |

## After-cost honesty

- CEX majors: pure HFT MM often uneconomic without rebates (practitioner notes).
- This bot’s venues charge maker fees (~1–2 bps futures in `config.py`).
- Bot loop is minutes-scale — productize as **sub-HFT maker clips**, not co-located HFT.
- Existing `MAKER_ONLY` is a directional fee soak (known not +EV). MTSI is a different family.

## Expectation

**NO_GO** or **INSUFFICIENT_DATA** on CEX majors after realistic fees + adverse selection — falsify via hashed prereg `55_prereg_mtsi_inventory.*` and `research/sim_mtsi_inventory.py`.

## Non-goals

- AccBand WR geometry as the edge
- Grid/DCA enable
- CONTROLLED_LIVE / allowlist reopen from narrative
- Raising the $1 cap before a screen GO
