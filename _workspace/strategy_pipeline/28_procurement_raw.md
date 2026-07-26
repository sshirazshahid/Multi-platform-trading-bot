# 28 — Procurement Reality: Net-Flow / Large-Transfer Feeds (ANGLE B)

Research date: 2026-07-24. RESEARCH ONLY. Feeds a strategy-integration decision.
Scope: is there a machine-readable, **backfillable** net-flow or large-transfer signal
procurable for a ~$420-capital retail account? The bot stores on-chain data for ZERO of
its 43 pairs today (dossier 27_), so nothing is screenable until a feed is ingested +
backfilled — which makes **API-retrievable historical depth the single make-or-break spec.**

Evidence tags: [OFFICIAL] = vendor's own API docs; [VENDOR] = vendor/3rd-party marketing or
review site (treat prices as list-price, not verified invoice).

---

## 1. WHALE ALERT — large-transfer API

Source docs: https://developer.whale-alert.io/documentation/ , https://developer.whale-alert.io/api-account/documentation
(canonical pricing page https://developer.whale-alert.io/pricing.html returned HTTP 404 on 2026-07-24)

### Endpoints [OFFICIAL — developer.whale-alert.io]
- `GET /status` — supported blockchains & symbols
- `GET /{blockchain}/status` — block-height range for a chain
- `GET /{blockchain}/transaction/{hash}` — single tx
- `GET /{blockchain}/transactions` — all txs from a specified block height (the bulk pull)
- `GET /{blockchain}/block/{height}` — block data
- `GET /{blockchain}/address/{hash}/transactions` — address tx history
- WebSocket stream for live alerts (Alerts plan)

### Pricing [VENDOR — quoted verbatim by the docs-page summariser; pricing.html 404]
- **Alerts API: "$29.95 / month"** — WebSocket access, custom alerts, **100 alerts/hour** limit,
  **personal use only**, minimum transaction limit **$100,000**.
- **Enterprise API: "$699 / month"** — REST API access, **1000 calls per minute**,
  includes Alerts API at 500 alerts/hour.
- Plan family names also seen: ALERTS (traders), QUANTITATIVE ("data hungry trading models"),
  COMPLIANCE — exact QUANTITATIVE price not surfaced (quote-based / not published).

### Historical backfill depth [OFFICIAL] — THE DISQUALIFIER
- "The Developer API provides a 1-month history."
- "The Enterprise API comes with a high rate limit and a **30-day transaction history**."
- Standard API access "**does not return transactions older than 30 days**."
- Free personal tier: real-time stream + limited history, day's alert data gated to a
  **minimum value of $10M USD**.
- => **Maximum retrievable history is ~30 days on EVERY tier, including the $699 Enterprise.**
  You cannot assemble a multi-month, let alone multi-year, screenable history from this API.
  The only way to get long history is to run the stream continuously and store it yourself
  going forward (no backfill).

### Rate limits [OFFICIAL]
- REST: "maximum of 1000 calls per minute" (Enterprise).
- WebSocket: max 2 concurrent connections/key; 100 alerts/hr cap (Alerts).

### Latency [OFFICIAL]
- Real-time (WebSocket + REST), "millions of standardized and enriched transactions" per day.

---

## 2. CRYPTOQUANT — exchange net-flow / Exchange Whale Ratio API

Source docs: https://userguide.cryptoquant.com/api/introduction ,
https://userguide.cryptoquant.com/api/btc-exchange-flows ,
https://userguide.cryptoquant.com/cryptoquant-metrics/market/exchange-whale-ratio ,
https://docs.cryptoquant.com/ , https://cryptoquant.com/pricing (JS-only, no server-side price text)

### Endpoints [OFFICIAL — userguide.cryptoquant.com]
- Base URL: `https://api.cryptoquant.com/v1/`
- Netflow: `/btc/exchange-flows/netflow` (also `/eth/...`, stablecoin, ERC20 categories)
  - `window` param: **`day` | `hour` | `block`**, default `day`.
  - `limit` param: **1 to 100,000**, default 100 rows/request.
- Exchange Whale Ratio: under `Flow-Indicator/` ("mpi, whale ratio and other flow indicators").
  Definition [OFFICIAL]: "total BTC amount of the top 10 transactions ... divided by the total
  BTC amount flowing into exchanges."
- Auth [OFFICIAL]: "Authenticate with a Bearer access token or an `api_key` query parameter."

### Which tier unlocks the API [OFFICIAL — userguide]
- "To obtain an access token for the Data API, you must **upgrade your plan to Professional
  or Premium plan**." => the CHEAPEST tier that exposes netflow/whale-ratio via API is
  **Professional**.

### Pricing [VENDOR — CaptainAltcoin review 2026, cross-checked with search]
- Free — charts only, no API.
- **Advanced: "$39 monthly (or $29 if you pay ... yearly)"** — full historical CHARTS, custom
  alerts, **NO API**.
- **Professional: "$109 monthly (or $99 if you pay yearly)"** — "everything from Advanced" +
  **"Data API up to 24H resolution"** + 20 custom alerts. <= cheapest API tier.
- **Premium: "$799 monthly (or $699 if you pay ... yearly)"** — **"Data API up to block-level
  resolution"**, 100 custom alerts.
- Note: a SEPARATE developer/enterprise API product exists at cryptoquant.dev / apis.io with
  "Professional = TBD" and "Enterprise = custom" (quote-based) — distinct from the $109
  consumer Professional plan. For daily netflow the $109 consumer plan is the relevant SKU.

### Resolution gate [OFFICIAL windows + VENDOR tier mapping] — KEY CONSTRAINT
- Professional Data API is **"up to 24H resolution"** => `window=day` netflow/whale-ratio only.
- `window=hour` and `window=block` require **Premium ($799/mo, $699 annual-billed)**.
- For a daily net-flow screen this is FINE. For any intraday whale-ratio timing it is not.

### Historical backfill depth
- [OFFICIAL] netflow endpoint returns flows "**as far back as we track**"; `limit` up to
  100,000 rows/request supports deep pulls.
- [VENDOR/inference] CryptoQuant's public BTC exchange-flow charts run from ~2016; multi-year
  daily history is the norm. Exact earliest date NOT verifiable from official API docs here
  (chart page returned HTTP 403; docs say only "as far back as we track"). Treat "multi-year"
  as robustly supported, exact start-date as unverified.
- => Orders of magnitude more backfill than Whale Alert's 30-day hard cap. This is the whole
  reason CryptoQuant is the viable ingest target.

### Point-In-Time hazard [OFFICIAL] — screening landmine
- The exchange-flow/netflow endpoint "**does not support Point-In-Time (PIT) accuracy due to
  periodic updates to wallet address clustering, and historical data may change as new exchange
  wallets are discovered, added, and validated.**"
- Meaning: the historical series you backfill today is NOT what was observable in real time —
  labels are revised retroactively. A naive backtest on it is lookahead-contaminated. Any screen
  must treat this as a first-class bias (the refuted-families reopen bar demands genuine OOS +
  after-cost; PIT-revised on-chain labels actively threaten the OOS claim).

### Rate limits [UNVERIFIABLE]
- Not disclosed in the public docs or reviews reached. Must be confirmed post-subscription.

### Latency [OFFICIAL/inference]
- Daily-resolution data on Professional; block-level on Premium. Effectively a daily feed for
  the affordable tier.

---

## 3. FREE / CHEAPER ALTERNATIVES (brief)

### CoinGlass — https://www.coinglass.com/pricing [VENDOR]
- **No free tier on the pricing page.** Tiers: **Hobbyist $29/mo (30 req/min, 80+ endpoints)**,
  Startup $79/mo (80 req/min, 130+), Standard $299/mo (300 req/min, 150+), Professional
  $699/mo (1200 req/min, 160+), Enterprise custom.
- Marketing claims coverage of "exchange wallet net inflow/outflow (Netflow)" and "large on-chain
  transfer tracking (Whale Alert)" — but the pricing page did NOT confirm which tier exposes the
  netflow / on-chain whale endpoints, nor their historical depth. Unverified whether Hobbyist $29
  includes them. (CoinGlass historically offered a keyless free tier with limited endpoints —
  not confirmed in this pass.)

### Whale Alert free personal tier [OFFICIAL]
- Real-time stream, personal use, but ≤30-day history and $10M day-data minimum => not usefully
  backfillable. Not a screening source.

### Arkham Intel API — https://intel.arkm.com/api [VENDOR]
- Entity-labeled blockchain data + transaction monitoring, but credit/points-based access — not a
  turnkey-free, backfillable netflow endpoint.

### Genuinely free, API-accessible, BACKFILLABLE netflow/large-transfer source
- **None found among the sources checked (CoinGlass, Arkham, Whale-Alert free tier).** The value in
  "exchange netflow" is the proprietary exchange-address LABELING; free block-explorer/node data
  carries the raw transfers but not the labels, so a free turnkey netflow API with deep history is
  unlikely to exist. Self-labeling from open community address sets is a build project, not a
  procurement.
- **UNCHECKED candidates (this pass did not verify them):** Dune Analytics (free-tier query API +
  community exchange-netflow/balance dashboards with multi-year history — the strongest potential
  free counterexample; needs verification of API access + rate limits on the free tier), Santiment,
  and Glassnode free tiers. Do NOT treat "none free" as settled until these are checked.

---

## 4. BOTTOM LINE

- **Whale Alert is DISQUALIFIED for this bot's stated need**: its hard **30-day API history cap on
  every tier (incl. $699/mo Enterprise)** means you cannot backfill a screenable history — only
  accrue it forward from today. Live-stream-and-store is possible but that is exactly the "wait
  months/years before anything is screenable" position the bot is already in.
- **CryptoQuant Professional ($109/mo month-to-month; $99/mo if a year is prepaid = $1,188/yr) is
  the single realistic ingest target**: the only option exposing **multi-year, backfillable**
  exchange-netflow + Exchange-Whale-Ratio history via a documented REST API at a retail price,
  at daily (24H) resolution sufficient for a daily net-flow screen.
- **Blocking limitations of that target:**
  1. **Cost vs capital**: $109/mo is ~26% of the $420 account PER MONTH. Economically brutal;
     the data cost alone likely exceeds any plausible edge on this account size. This is the
     dominant blocker for a $420 account, independent of signal quality.
  2. **PIT/label-revision bias [OFFICIAL]**: historical netflow is retroactively revised as
     clustering updates — a real lookahead hazard that undermines any OOS backtest and must be
     modelled, not ignored (bears directly on the refuted-families reopen bar).
  3. **Resolution ceiling**: Professional = daily only; intraday windows force Premium $799/mo.
  4. **Rate limits undisclosed** — confirm post-purchase.
  5. Net-flow timing itself is ADJACENT to already-refuted families (ETF-flow timing, dominance
     timing, directional funding — all NO_EDGE 2026-06-07). Procuring the feed does NOT clear the
     reopen bar; it only makes a future pre-registered screen *possible*.

Recommendation implied (research-only): if any feed is ingested, it is **CryptoQuant Professional**,
and only after (a) a peer-reviewed/rigorous 2025+ OOS+after-cost study clears the reopen bar and
(b) the $109/mo data cost is judged acceptable against account economics. Otherwise, hold — the
30-day Whale Alert cap and the $420-vs-$109/mo math make immediate procurement unjustified.
