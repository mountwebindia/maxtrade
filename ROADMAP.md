# Remaining work

## Completed
- Public spot and USDT futures scanning at 1h/4h.
- Explainable EMA/RSI signals and ATR reference levels.
- Completed-candle validation, per-market error handling, CSV export.
- Persistent SQLite snapshots and historical snapshot viewer.

## Next: strategy evaluation
1. **Backtesting:** chronological evaluation without look-ahead; next-bar entry, explicit stop/target collision handling, fees/slippage and futures funding assumptions. Report sample size, drawdown, returns, and out-of-sample results; test against deterministic fixtures. Historical data availability must be verified first.
2. **Paper-trade ledger:** explicit simulated fills, position lifecycle, restart-safe storage, duplicate-entry prevention, and conservative exit rules. Never present archived signals as actual fills.
3. **Risk controls:** user-set paper capital, per-trade risk, exposure caps, minimum liquidity checks, daily-loss limits, and kill switch. Distinguish quote units before summing exposure.

## Operational improvements
4. Rate-limit-aware retries, bounded concurrency/caching, scan duration/progress, and stable dependency locking.
5. Opt-in scheduled scans/alerts, deduplication, and visible data freshness. External delivery requires a user-selected destination; secrets stay outside source control.
6. CI, repeatable deployment, access control for remote hosting, retention/backup policy, and monitoring. The current server remains loopback-only.

## Separate, gated phases
- **Options:** blocked on a verified provider and contracts/expiry/strike/IV/open-interest feed.
- **Live execution:** not enabled. Requires explicit scope approval, secure credentials, exchange reconciliation, order constraints, idempotency, risk controls, and substantial paper validation. No profitability claim follows from passing software tests.