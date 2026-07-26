# 03 — Audit: Candidate B (post-listing perp short) — `listing-short`

Auditor: honesty-auditor · Date: 2026-07-09 · Screen audited: `_workspace/strategy_pipeline/02b_screener_listing_short.md`
Artifacts: `research/screen_listing_short.py`, `tests/test_screen_listing_short.py`

## VERDICT: INSUFFICIENT_DATA_CONFIRMED

The screener's INSUFFICIENT_DATA is honest, pre-registered, and **robust to the strongest attack**.
I attacked the one claim that, if false, would overturn it — that funding coverage for the eligible
listings is n=0 — by checking funding sources the screen never reads. Coverage is still 0. The
blocking reason holds. This is a NO_GO for capital (no promotion), correctly.

## What I verified independently
- **Reproduction (exact):** ran `research/screen_listing_short.py` — 525 `_1h` symbols, 8 backfill-cluster
  timestamps, 419 excluded, 103 genuine listings, 31 funding_cache + 8 derivs coins, **0 covered / 103
  uncovered**. Diagnostic 7/30/90d = +9.2% / +26.0% / +37.8% mean, WR 68.9/78.4/78.2%. Byte-for-byte the
  verdict's table. Tests 9/9 pass.
- **Funding n=0 is robust, not an artifact of a narrow check.** The screen only reads `data/funding_cache`
  (31 majors) + `data/derivs_history.jsonl` (8 majors). I additionally cross-checked **every** funding file
  on disk — `data/funding_oi/` (BNB,BTC,ETH,SOL,XRP) and `data/funding_carry/` (BTC,ETH) — against all 103
  listing bases. **Overlap = 0.** Every funding source holds only established majors; none covers a 2025-26
  listing. The killer cost (short pays negative funding) is genuinely unmeasurable locally. Confirmed.
- **No look-ahead.** Entry = day-1 close (`first_ts+24h`), exit = `entry+H·24h`, both from real bars.
  Manual spot-check: 0G 5.751→1.783 (short +68.8%), 2Z 0.522→0.177 (+65.8%) — real post-listing dumps, no
  timestamp leakage. Exit has a +12h data-gap tolerance; entry price is information available at entry.
- **Multiplicity honored.** One family, 3 horizons, `n_trials=3` in the diagnostic DSR. No threshold moving;
  INSUFFICIENT_DATA was pre-declared in the frozen pre-registration ("What INSUFFICIENT_DATA looks like").
- **Charter compliant.** `git status --short`: only untracked `research/`, `tests/`, `_workspace/`. **Zero
  modified/staged tracked files** — no `core/` or `config.py` edits, no commits. Costs pulled live from
  `config.FEE`/`config.SLIPPAGE` (5bps fee + 5bps slip per side = 20bps round-trip), matching the convention.

## Findings (severity-ranked) — none overturn the verdict

1. **[MEDIUM — unblock path is broken] The recommended harvest cannot flip this verdict as written.**
   The "To unblock" command writes funding to `data/funding_oi/<SYM>_funding.csv`, but
   `funding_coverage_sets()`/`has_funding()` only read `data/funding_cache/*.parquet` and
   `data/derivs_history.jsonl` — they **never read `data/funding_oi/`**. Even after a successful harvest the
   screen would still report 0 coverage. The screen must be wired to read the harvested CSVs before any
   re-run can produce GO/NO_GO. Fix required on the remediation, not on today's verdict.

2. **[LOW — diagnostic contamination] The seductive diagnostic universe includes non-crypto and junk symbols.**
   The 103 include 11 tokenized-equity/commodity perps (verified: AAPL short = **−16.9%**, it rallied — the
   opposite of crypto-hype decay) and at least one scrape-junk meme token (`币安人生`). The verdict flags the
   11 equities but not the junk token. Because the diagnostic is explicitly funding-uncharged and
   inadmissible, this is cosmetic — but it reinforces that the +26%/+37.8% figures must NOT be read as edge.

3. **[LOW — correctly disclosed] Diagnostic over-states even before funding.** Survivorship (delisted
   post-listing names absent from the cache), day-1 shortability/borrow limits, touch≠fill, and much wider
   real slippage than 5bps on brand-new illiquid perps all cut against the diagnostic and are already
   disclosed. No undisclosed leakage found.

## Why not GO / NO_GO
A NO_GO would require an evaluable after-cost sample showing no edge; a GO would require gates passing on a
funding-charged sample. Both need n>0 with funding. n=0 → DSR/PBO/OOS-WR/MC are NaN → fail-closed. The
funding-uncharged diagnostic is directionally optimistic on the one cost that decides the trade (new-listing
funding is systematically negative → the short pays it every 8h for 21–270 settlements). INSUFFICIENT_DATA
is the honest verdict. When uncertain, NO_GO for capital — satisfied: nothing promotes.
