# 31 — Candidate queue: whale / network big-move sources
*Date: 2026-07-24 | Research only — no screen, no probe, no live wire*

Parent report: [`31_deep_research_whale_network_sources_2026-07-24.md`](31_deep_research_whale_network_sources_2026-07-24.md).

Priority relative to existing queue ([`30_edge_queue_2026-07-23.md`](30_edge_queue_2026-07-23.md)):
**VPIN jump-risk veto remains #1.** Network briefs below are **parked behind VPIN** unless owner funds a vendor and reorders.

## Already in bot (not new)

| Item | Path | Role |
|------|------|------|
| Binance Web3 smart-money inflow rank | `core/data_feeds/smart_money_feed.py` | MCP bonus B13 (+5 on aligned buy) |
| Social hype / crowd | same feed | Size/risk context, contrarian framing |

Do **not** escalate B13 to required entry gate without a CONFIRMED_GO screen.

## Queued briefs

### N1 — BTC/ETH exchange netflow regime veto
- **Novelty:** ADJACENT to OPEN network-flow (scout 26); not ETF-timing, not OI-divergence.
- **Mechanism:** When z-scored exchange *inflow* (1–24h) exceeds pre-registered θ, veto or size-down AccBand OPEN (distribution risk). Outflow extreme → optional risk-on size restore only after separate prereg (default: veto-only).
- **Data:** Hourly exchange netflow PIT series — Glassnode / CryptoQuant / CoinGlass (paid) or licensed Arkham aggregate. Land under `data/network_flows/` with `available_at_utc`.
- **Cost@$420:** Veto adds $0 orders; subscription cost is the real hurdle.
- **Expectation:** NO_GO or INSUFFICIENT_DATA until harvest; literature mixed on BTC netflow return forecast.
- **Harvest if starved:** Owner picks vendor + API key → `scripts/harvest_exchange_netflow.py` (not written until authorized).

### N2 — USDT exchange-inflow risk / opportunity flag
- **Novelty:** ADJACENT; arXiv 2411.06327 finds USDT inflows positively associated with short-horizon BTC/ETH returns.
- **Mechanism:** Pre-register as (a) size-up only when already MCP-allowed, or (b) veto shorts — **not** standalone entry. Multiplicity across horizons 1h/2h/4h.
- **Expectation:** NO_GO prior for standalone; possible weak overlay.
- **Blocker:** Same PIT USDT flow history.

### N3 — Whale→CEX entity transfer event (Arkham / Whale Alert)
- **Novelty:** OPEN event class; rare.
- **Mechanism:** Log-only shadow: when labeled entity sends ≥$X to Binance/Bybit/Bitget, record event; measure forward 4h/24h/7d perp returns after costs. Filter known internal reshuffles if labels allow.
- **Expectation:** INSUFFICIENT_DATA (n events); likely NO_GO after costs; alert latency kills retail chase.
- **Blocker:** Arkham API access request or Whale Alert developer key + historical window ≥30 independent events.

## Refused now

| Request | Why |
|---------|-----|
| “Integrate Arkham into strategies” as live OPEN | No prereg, no PIT data, no GO |
| Chase Whale Alert tweets | Priced-in; fee death; not reproducible |
| ETF/dominance timing via on-chain story | Ledger REFUTED 2026-06-07 |

## Owner decisions needed to advance

1. Fund **one** vendor (recommend: Whale Alert developer for events **or** CoinGlass/CryptoQuant for netflow — not both at once).
2. Confirm network stays **behind VPIN** or explicitly reorder heavy-stage budget.
3. Accept that first outcome is likely **INSUFFICIENT_DATA / NO_GO**, not AccBand profit.
