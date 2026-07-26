# Equity Investment Committee — Design Spec

**Date:** 2026-07-26 · **Status:** APPROVED, implementation queued behind in-flight workflows
**Owner decisions:** single-ticker deep-dive · build as a proper skill, defer docs/catalog/JA packaging · implement after the edge-concentration and deep-audit workflows land

## Context

The repo's investment committee (bull / bear / debate / final-verdict agents, plus the `ai-reviewer` added 2026-07-26) was built for crypto strategy candidates. One of its 13 roles — **Fundamental Analyst** — can never be filled there: perpetual futures have no earnings, balance sheet, or valuation. A 2026-07-26 sweep confirmed buying crypto-fundamental data does not clear the evidence bar.

Equities are the one configuration where every role can be genuinely filled, and the dependency is now live: `FMP_API_KEY` is wired through `resolve_fmp_key()` (`.agents/skills/_shared_fmp_yahoo_patch.py`) and verified returning HTTP 200 after rotation.

This is **decision support for a human**, not a trading system. No broker is wired and none will be.

## Measured tier constraints (probed 2026-07-26, not assumed)

FMP **Free** tier — evidenced by the server's own `limit must be between 0 and 5 based on your current subscription`:

| Verified working | Verified blocked |
|---|---|
| `stable/quote`, `historical-price-eod/full`, `income-statement` (limit ≤ 5), `earnings-calendar`, `dividends-calendar`, `treasury-rates`, `economic-indicators`, `analyst-estimates` | all `api/v3/*` (403 Legacy — account post-2025-08-31), `economic-calendar` (402), `commitment-of-traders` (402), non-US symbols (402) |

Consequences, binding on this design: **US-listed tickers only**; statements capped at 5 years; **`stable` endpoints exclusively**; ~250 calls/day documented (no rate-limit headers are returned, so this is not observed). This is why the run scope is a single ticker (~15–30 calls) rather than a universe screen, which would exhaust the quota before the committee convened.

## Architecture — deterministic gathering, separated from judgment

```
ticker → core/equity_brief.py  (deterministic, no LLM)
           ├─ Fundamentals   income-statement(≤5y), analyst-estimates, quote
           ├─ Technicals     EOD history → core/pair_dossier.py indicator math
           ├─ Valuation      ratios / key-metrics  (see Build Step 0)
           └─ Catalysts      earnings-calendar, news (see Build Step 0)
                 ↓ typed Finding records
         bull-researcher ─┐
         bear-researcher ─┤→ debate-engine → final-verdict → ai-reviewer → HUMAN decides
```

Python gathers every number; the agents only reason over the brief. They never fetch, so they cannot hallucinate a figure. This caps a run at ~4 LLM calls and makes the brief reproducible for a given ticker and day.

## Components

| File | Role |
|---|---|
| `core/equity_brief.py` | Pure brief builder. Same `Finding` schema as `core/research_brief.py`: `{id, kind: FACT\|INFERENCE\|ABSENT, section, label, value, numeric, unit, as_of_utc, source, source_fn, confidence, confidence_basis, derived_from}`. `INFERENCE` requires non-empty `derived_from` resolving to FACT ids. `ABSENT` is a record carrying `expected_source` + the blocked reason — never a missing key. Confidence from a pure function of (staleness, n, n_sources), never authored. |
| `scripts/equity_committee.py` | CLI shell: `--ticker AAPL`. Writes `reports/equity_committee_<TICKER>_<date>.{md,json}`. Mirrors `scripts/market_intel_report.py` conventions (`_safe()` degradation, `_FAILED` footer). |
| `.claude/skills/equity-committee/SKILL.md` | Orchestrator. Reuses the existing bull/bear/debate/final-verdict/ai-reviewer agents with an equity context block; their crypto ledger-read step is replaced by the equity brief. |

**Reuse, not duplication:** indicator math comes from `core/pair_dossier.py` (tracked, tested, conventions documented) so the repo keeps one RSI/SMA/MACD implementation.

## Honesty rails — deliberately different from the crypto brief

`core/research_brief.py` bans all directional language because it is a descriptive record. A committee's purpose *is* to argue both directions, so that ban would be wrong here. Instead:

1. Every thesis carries **explicit invalidation conditions** and an evidence-quality rating (the existing bull/bear agent definitions already require this).
2. **No price targets, no probability claims, no "will."** FACT and INFERENCE remain structurally separated.
3. Verdict vocabulary is **APPROVE / REVISE / REJECT**, meaning *"worth further human diligence"* — never "buy". No order path exists.
4. Tier gaps surface as **ABSENT records naming the blocked endpoint**, never as silence.
5. The brief states its own data-quality verdict **first**, before any number — same rule that makes the crypto pair dossier trustworthy.

## Build Step 0 — endpoint verification (first task, not an assumption)

`ratios`, `key-metrics`, `balance-sheet`, `cash-flow` and `news` were **not probed**. Step 0 calls each once, records the HTTP status, and wires only those returning 200. Anything blocked becomes a permanent ABSENT record with its status code. No section is designed around an unverified endpoint.

## Testing

Fixture-driven and offline (no live API in CI):

1. Brief assembly from fixtures produces a well-formed document.
2. `INFERENCE` findings have non-empty `derived_from` resolving to FACT ids; every FACT/INFERENCE carries `as_of_utc` + `source`.
3. A blocked endpoint yields an ABSENT record with `expected_source` and status — not a missing key.
4. Language guard: no price-target or "will/should" phrasing in rendered `.md` **or** `json.dumps(doc)`.
5. Data-quality section renders first.
6. Determinism: identical fixtures + injected frozen `now` → byte-identical JSON.
7. No order-path imports (`order_manager`, `risk_manager`, `live_gate`, `bot_engine`, `entry_policy`, `promotion_gate`) — mirrors the idiom in `tests/test_run_intel_synthesis.py`.

Plus one live smoke test on a real ticker, run manually.

## Cost and runtime

Per run: ~15–30 FMP calls (≈8–15 runs/day within the documented Free quota) and ~4 LLM calls. Brief assembly is seconds. On-demand only — never scheduled, never on the bot's critical path.

## Out of scope

No trading, broker, or portfolio management. No backtesting or any claim of validated edge. No non-US tickers (tier-blocked). No universe screening (quota). Nothing that touches the crypto bot's decision path. Docs pages, catalog entries and JA translation deferred until the skill proves useful on real tickers.

## Verification

1. `venv\Scripts\python.exe -m pytest tests/test_equity_brief.py -v` green.
2. `venv\Scripts\python.exe scripts\equity_committee.py --ticker AAPL` produces a report whose data-quality section is first, every number timestamped and sourced, and no price-target language present.
3. `git show --stat` on the commit contains **no** `data/` or `reports/` paths, and no key material.
4. Crypto bot untouched: `data/heartbeat.json` still PAPER / MAX_FLOW_BAND / not halted; no new imports into the bot's graph.
