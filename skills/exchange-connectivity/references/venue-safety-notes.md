# Venue Safety Notes

Use these notes as routing constraints, then verify current official documentation and the installed
ccxt version before implementation. Exchange APIs and limits change.

## Repository reality

- `PAPER`/`DRY_RUN` simulates execution locally. Market data may still come from production public
  endpoints; that does not turn paper execution into venue testnet execution.
- `exchanges/binance_client.py` accepts a testnet flag. The Bybit and Bitget constructors currently
  hard-code production-mode clients. Do not claim test/demo coverage for those adapters.
- `core/live_gate.py` owns controlled-live authorization and a signed, read-only capability preflight.
  Account/position mode must be verified there, not changed by an adapter constructor.
- `core/execution_guard.py` distinguishes venue/source observation time from local receipt time.
- `core/realtime_streams.py` and `core/realtime_hub.py` own reconnect and replacement behavior;
  `core/order_manager.py` owns ambiguous-order and protection reconciliation.

## Environment distinctions

### Binance USD-M

Enable sandbox endpoints only from explicit test configuration and before other exchange calls. Do
not assume that production keys work on testnet or that a sandbox has production liquidity/behavior.
Binance publishes request-weight/order limits through exchange metadata and response headers; one
shared venue budget must account for all bot consumers.

- [Binance USD-M WebSocket streams](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/public)
- [Binance USD-M general information and limits](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info)

### Bybit V5

Testnet and demo trading are different environments with different domains and restrictions. Keep
their credentials and endpoint selection explicit. Linear instruments are paginated; do not assume
one instruments response represents the whole universe. Follow V5 connection heartbeat/reconnect
requirements and response rate-limit headers.

- [Bybit demo trading](https://bybit-exchange.github.io/docs/v5/demo)
- [Bybit WebSocket connection guidance](https://bybit-exchange.github.io/docs/v5/ws/connect)
- [Bybit rate limits](https://bybit-exchange.github.io/docs/v5/rate-limit)
- [Bybit instruments information](https://bybit-exchange.github.io/docs/v5/market/instrument)

### Bitget

Demo trading requires demo credentials and the documented `paptrading: 1` request header. Treat
that as an explicit transport mode, not a generic boolean assumption. Respect connection, message,
and subscription limits; shard broad streams deliberately and keep private reconciliation separate.

- [Bitget demo trading REST guidance](https://www.bitget.com/api-doc/classic/demotrading/restapi)
- [Bitget WebSocket guidance](https://www.bitget.com/api-doc/common/websocket-intro)

## Cross-venue invariants

1. Reuse one exchange instance per configured venue/account/market routing domain. ccxt's built-in
   throttler is instance-local; repeatedly constructing instances defeats its coordination.
2. Layer a bot-wide budget over ccxt so scans, reconciliation, health checks, and orders cannot each
   consume the full nominal allowance independently.
3. Persist client order IDs and intent before a create request. Reconcile an ambiguous response by
   ID before retrying.
4. On private-stream recovery, reconcile positions, regular orders, conditional orders, and recent
   fills. An exception or timeout is unknown state, not an empty venue.
5. Keep event/source time and local receipt time separately. Reject stale or future-skewed market
   evidence even if it was received just now.

- [CCXT manual: exchange instances and rate limiter](https://github.com/ccxt/ccxt/wiki/manual)
