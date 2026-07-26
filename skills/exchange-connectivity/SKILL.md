---
name: exchange-connectivity
description: Build, audit, and debug safe ccxt/ccxt.pro connectivity for Binance, Bybit, and Bitget spot or USDT perpetual markets. Use for adapter changes, endpoint or credential-mode selection, market metadata and quantization, REST or WebSocket liveness, rate limits, observed timestamps, account/position-mode verification, idempotent order submission, ambiguous acknowledgements, reconnect reconciliation, or exchange-specific order failures. Also use when reviewing whether exchange startup and constructors are read-only or whether PAPER, test/demo, and controlled-live paths are correctly isolated.
---

# Exchange Connectivity

## Overview

Keep venue quirks inside the exchange adapter and expose normalized market data, account state,
orders, positions, and errors to the rest of the bot. Treat connectivity as a state-reconciliation
problem: a successful HTTP response alone is not proof that local and venue state agree.

Enforce these boundaries:

- Keep adapter construction read-only with respect to the venue account. Loading markets, reading
  server time, and signed reads are allowed; placing/cancelling orders, transfers, leverage changes,
  and account/position-mode mutations are not.
- Treat `PAPER` as local simulated execution using market data. It is not a venue testnet.
- Treat testnet/demo as a separate endpoint and credential domain. Never infer it from `DRY_RUN`.
- Enter controlled live only through the repository's signed runtime gates and read-only preflight.
- Record source/event time as `observed_at` and local ingestion time as `received_at`; receipt time
  must never make old market data appear fresh.
- Never promise fills, uptime, or profitability from connectivity checks.

## When to Use

- Add or modify an exchange adapter, market-data feed, private stream, or order endpoint.
- Diagnose authentication, timestamp, symbol, precision, account-mode, rate-limit, or routing errors.
- Review startup for account mutations or for accidental production/test endpoint mixing.
- Implement reconnect, stream replacement, order idempotency, or post-gap reconciliation.
- Validate that a paper/test workflow cannot activate controlled live.

## Prerequisites

- Python 3.9+ and the repository environment with its pinned ccxt version.
- The actual exchange adapter, live gate, real-time stream, order manager, and relevant tests.
- Market metadata for the exact venue, market type, and settlement asset.
- For authenticated tests, purpose-made least-privilege test/demo credentials supplied outside source
  control. Do not put secrets in commands, fixtures, reports, or logs.
- Explicit authorization before any state-changing venue call. Offline and paper tests require none.

Before changing venue behavior, read `references/venue-safety-notes.md`. Re-check current official
venue and ccxt documentation when endpoint semantics or limits may have changed.

## Workflow

### 1. Map the execution mode and endpoint

Write down the intended combination of `PAPER`, venue test/demo, or `CONTROLLED_LIVE`, plus endpoint,
credential class, and whether private reads are expected. Fail closed on any mismatch.

Current repository reality matters:

- Binance has an explicit adapter testnet option.
- Bybit and Bitget adapters currently construct production clients; do not describe them as testnet
  capable until explicit endpoint/header configuration and isolated tests exist.
- Bybit testnet and demo trading are distinct environments. Bitget demo requires demo credentials
  and its paper-trading header. A generic `sandboxMode` assumption is insufficient.

### 2. Preserve the adapter boundary

Route strategies and execution through `exchanges/base.py` and venue adapters. Normalize:

- canonical symbol and market type;
- tick, amount step, minimum amount/notional, contract size, and fee fields;
- order, position, balance, fill, funding, and error shapes;
- `observed_at`, `received_at`, venue identifiers, and raw provenance.

Do not string-build venue symbols when `exchange.market(symbol)` can resolve them. Use `Decimal` or
the repository's central precision path for order values.

### 3. Keep startup read-only and verify account mode

Run the constructor audit before and after adapter edits:

```bash
python scripts/audit_constructor_safety.py --project-root ../.. --json
```

Verify one-way/hedge mode with a signed read during controlled-live preflight. Compare it with the
order path's `positionSide`, `positionIdx`, or equivalent assumptions. Reject an unverifiable or
mismatched mode; do not mutate account mode in a constructor or silently repair it at startup.

### 4. Budget REST traffic globally

Reuse exchange instances because ccxt rate limiting is per instance. Add one shared per-venue budget
covering foreground execution, reconciliation, health checks, and background scanners. Prefer a
batched all-tickers or all-instruments request, then shortlist locally, over one request per symbol.
Honor response headers and venue-specific weights, back off on 429/5xx/timeouts with jitter, and
stop retrying on authentication, permission, invalid-order, or account-mode failures.

### 5. Make order submission idempotent

Generate and persist the bot-owned client order ID before submission. On a timeout or ambiguous
acknowledgement, query by that same ID and reconcile open orders/fills before deciding whether to
retry. Never blind-retry a create request. Classify failures as retryable, order-fatal, or bot-fatal
and include a redacted venue code plus correlation ID in logs.

### 6. Maintain WebSocket liveness

Track source sequence where available, plus last event and receipt times per stream. Use bounded
backoff with jitter, but keep attempting at the cap until deliberately stopped. Replace or evict dead
stream managers safely so a cached manager cannot remain permanently dead.

After every private-stream gap or reconnect, reconcile via REST:

1. positions;
2. regular and conditional/open orders, including separate stop ledgers;
3. recent fills since the last durable cursor;
4. persisted entry/protection intents and bot-owned client IDs.

Treat a failed fetch as unknown, never as an empty list. Pause new entries until reconciliation
completes; do not cancel or recreate protection based on unknown state.

### 7. Validate in increasing-risk tiers

1. Run unit/contract tests with fakes and no network.
2. Run paper replay and reconnect/clock/rate-limit fault injection.
3. If explicitly authorized, test venue test/demo with minimum-size orders and zero production keys.
4. Produce evidence for controlled-live review; do not activate live as part of skill validation.

Use assertions for quantization, stale-source rejection, client-ID reconciliation, mode mismatch,
429 backoff, private-gap reconciliation, conditional-order visibility, and restart recovery.

Run the skill tests:

```bash
python -m pytest scripts/tests -q
```

## Output

Return a concise connectivity evidence report containing:

- mode/endpoint/credential-domain matrix with secrets redacted;
- changed adapter contracts and venue-specific behavior;
- constructor audit and offline test results;
- REST budget and stream liveness/reconciliation behavior;
- unresolved risks and the exact gate keeping controlled live disabled.

A passing report proves only the checked contracts. It does not prove continuous availability, order
fills, strategy edge, or profitability.

## Resources

- `scripts/audit_constructor_safety.py`: deterministic AST check for venue mutations reachable from
  adapter construction hooks.
- `scripts/tests/test_audit_constructor_safety.py`: safe/unsafe constructor fixtures.
- `references/venue-safety-notes.md`: repository-specific mode, rate-budget, timestamp, and venue
  notes with primary documentation links.
- Repository implementation: `exchanges/base.py`, `exchanges/*_client.py`, `core/live_gate.py`,
  `core/realtime_streams.py`, `core/realtime_hub.py`, and `core/order_manager.py`.
