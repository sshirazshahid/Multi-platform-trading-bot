# 18 — Dual-Model Final Pair Verdicts (2026-07-22)

**Models:** Codex GPT-5.6-Sol vs Claude Fable 5 — independent research, independent 44-pair verdicts, one policed rebuttal round, Fable-side reconciliation (sequential main-loop fallback after subagent session limit; protocol pre-stated in the reconciler brief).

## A1 — READ FIRST (owner action)
Live heartbeat profile is `AGGRESSIVE_RESEARCH` (epoch 2026-07-21T20:24Z) but config.py gates the band-geometry/entry-floor-50/econ-gate-paper_fallback knobs to `PAPER+MAX_FLOW_BAND` (pinned: tests/test_f3_profile_gated_research_knobs.py:22). **The band lane these verdicts describe is not currently active; every FIT verdict is conditional on restoring `PAPER_TRADING_PROFILE=MAX_FLOW_BAND` (+ supervisor restart).** Both models affirmed this conditionality in the rebuttal round. Owner set the current profile 2026-07-21 — restoring it is an owner decision, not unilaterally applied.

## Final distribution (44 pairs)
FIT_BAND_PAPER 5 · FIT_WITH_GAPS 22 · DATA_STARVED 14 · COST_UNFIT 2 · EXCLUDE 1
First-pass agreement 28/44 (64%); 10 Codex concessions (all vocabulary-policed EXCLUDEs), 3 conservative FBP→FWG merges, 3 adjudicated rulings (BTC→FIT_WITH_GAPS over Codex COST_UNFIT dissent; FET→EXCLUDE over Fable dissent, readmit on bybit route verification; XRP→DATA_STARVED over Fable dissent).

| Pair | Final verdict | Provenance |
|---|---|---|
| 1INCH/USDT:USDT | DATA_STARVED | agreed |
| AAVE/USDT:USDT | FIT_WITH_GAPS | agreed |
| ADA/USDT:USDT | FIT_WITH_GAPS | codex-conceded |
| ALGO/USDT:USDT | FIT_BAND_PAPER | agreed |
| APT/USDT:USDT | FIT_WITH_GAPS | codex-conceded |
| ARB/USDT:USDT | FIT_BAND_PAPER | agreed |
| ATOM/USDT:USDT | FIT_WITH_GAPS | conservative (FBP vs FWG) |
| AVAX/USDT:USDT | FIT_BAND_PAPER | agreed |
| BCH/USDT:USDT | FIT_WITH_GAPS | agreed |
| BNB/USDT:USDT | COST_UNFIT | agreed |
| BTC/USDT:USDT | FIT_WITH_GAPS | RULED for Fable: n=1471 measured band outcomes beat a 30d proxy at 0.98 (~breakeven, not dominated; COST_UNFIT definition = TRX-class 0.46). DISSENT C |
| COMP/USDT:USDT | DATA_STARVED | agreed |
| CRV/USDT:USDT | DATA_STARVED | agreed |
| DASH/USDT:USDT | DATA_STARVED | agreed |
| DOT/USDT:USDT | FIT_WITH_GAPS | codex-conceded |
| ENA/USDT:USDT | DATA_STARVED | agreed |
| ETC/USDT:USDT | FIT_WITH_GAPS | codex-conceded |
| ETH/USDT:USDT | FIT_BAND_PAPER | agreed |
| FET/USDT:USDT | EXCLUDE | RULED for Codex (conservative): no live bybit USDT-perp route (2026-07-20 boot log; sole route-dead pair). Readmit on route verification. DISSENT Fabl |
| FIL/USDT:USDT | FIT_WITH_GAPS | codex-conceded |
| GALA/USDT:USDT | DATA_STARVED | agreed |
| GRT/USDT:USDT | DATA_STARVED | agreed |
| HBAR/USDT:USDT | DATA_STARVED | agreed |
| INJ/USDT:USDT | FIT_WITH_GAPS | conservative (FBP vs FWG) |
| JUP/USDT:USDT | DATA_STARVED | agreed |
| LINK/USDT:USDT | FIT_BAND_PAPER | agreed |
| LTC/USDT:USDT | FIT_WITH_GAPS | conservative (FBP vs FWG) |
| MANA/USDT:USDT | FIT_WITH_GAPS | agreed |
| NEAR/USDT:USDT | FIT_WITH_GAPS | agreed |
| ONDO/USDT:USDT | DATA_STARVED | agreed |
| OP/USDT:USDT | FIT_WITH_GAPS | codex-conceded |
| RENDER/USDT:USDT | FIT_WITH_GAPS | agreed |
| SAND/USDT:USDT | FIT_WITH_GAPS | agreed |
| SEI/USDT:USDT | FIT_WITH_GAPS | agreed |
| SNX/USDT:USDT | DATA_STARVED | agreed |
| SOL/USDT:USDT | FIT_WITH_GAPS | codex-conceded |
| SUI/USDT:USDT | FIT_WITH_GAPS | codex-conceded |
| TAO/USDT:USDT | DATA_STARVED | agreed |
| TIA/USDT:USDT | FIT_WITH_GAPS | agreed |
| TRX/USDT:USDT | COST_UNFIT | agreed |
| UNI/USDT:USDT | FIT_WITH_GAPS | codex-conceded |
| VET/USDT:USDT | FIT_WITH_GAPS | codex-conceded |
| XRP/USDT:USDT | DATA_STARVED | RULED for Codex (conservative): band cache n=1 = no usable geometry evidence. DISSENT Fable: FIT_WITH_GAPS. |
| ZEC/USDT:USDT | DATA_STARVED | agreed |

## Candidate dispositions
- **C1 CFTC asset-manager options-pressure (BTC):** QUEUED FOR SCREEN (proposer Codex; Fable SCREEN w/ binding hardened prereg: delta-adjustment artifact control, joint t+1/t+2/t+3 multiplicity, ETF-era split, Friday-lag no-lookahead; expectation NO_GO; needs CFTC report harvest first)
- **C2 Deribit negative-gamma expiry reversal:** INSUFFICIENT_DATA (both models; reopen: timestamped historical chain archive OR >=30 forward-collected qualifying events with pre-frozen GEX convention; unconditioned 08:00 reversal substitute FORBIDDEN as refuted seasonality)
- **C3 Quarter-hour opening imbalance:** QUEUED-BEHIND-C1 as measurement-first pilot per Codex MODIFY (3-month BTC/ETH, opening-10s imbalance residualized vs price/volume, day-clustered inference, >=20bps-after-cost bar to expand, else drop+ledger; historical aggTrades so latency-infeasibility does not bar measurement; expectation NO_GO)

## Debate value assessment (honest)
Dual-model added real signal: Codex found 2 novel candidates + an adverse TSMOM study Fable missed; Fable found the delta-adjustment artifact in Codex's C1, the A1 config conditionality, and the vocabulary drift in Codex's 12 EXCLUDEs (12→1 after policing). Neither model manufactured edge: 0 GO today, all screens queued behind pre-registration + data harvests. Machine-readable: 18_final_pair_verdicts.json. Rebuttal record: 18_rebuttal_codex.md. Inputs: 18_scout_{fable,codex}.md, 18_pair_dossier.{md,json}, 18_verdicts_{fable,codex}.md, 18_verdict_diff.json.
