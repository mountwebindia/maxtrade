# Remaining work

## Completed
- Public spot and USDT futures scanning at 1h/4h.
- Explainable EMA/RSI signals and ATR reference levels.
- Completed-candle validation, per-market error handling, CSV export.
- Persistent SQLite snapshots and historical snapshot viewer.
- Deribit BTC/ETH options watchlists with expiry, quote, liquidity and delta checks; underlying CALL/PUT bias, not premium trade levels.
- Mobile-first candle chart with forming bars and 10-second REST polling while Chart is selected; pause/manual refresh and closed-bar signals.
- Snapshot backtesting with next-open entries, fees/slippage, conservative stop handling, and an explicitly estimated funding cost. Not strategy certification or live paper trading.

## Next: strategy evaluation
1. **Extended evaluation:** verify longer historical data availability, historical funding and market constraints; add out-of-sample and walk-forward evaluation. Report sample size, drawdown and costs. Current loaded-chart replay is too short to establish reliability.
2. **Paper-trade ledger:** explicit simulated fills, position lifecycle, restart-safe storage, duplicate-entry prevention, and conservative exit rules. Never present archived signals as actual fills.
3. **Risk controls:** user-set paper capital, per-trade risk, exposure caps, minimum liquidity checks, daily-loss limits, and kill switch. Distinguish quote units before summing exposure.

## Operational improvements
4. Rate-limit-aware retries, bounded concurrency/caching, scan duration/progress, and stable dependency locking.
5. Opt-in scheduled scans/alerts, deduplication, and visible data freshness. External delivery requires a user-selected destination; secrets stay outside source control.
6. CI, repeatable deployment, access control for remote hosting, retention/backup policy, and monitoring. Public dashboard hosting exists; the local launch remains loopback-only. Cloud-local SQLite is shared and non-durable.

## Coordinated research agents: next implementation phase
Build this as a scheduled backend with durable evidence and decision storage, not a collection of Streamlit session labels. Streamlit presents the results. Provider selection, licensing, freshness limits and evaluation criteria must be verified before connecting each source.

1. **Market/technical agent:** exchange candles, multi-timeframe trend, volatility, volume and liquidity. Every observation records symbol, venue, units, event time, retrieval time and source.
2. **News/events agent:** timestamped, source-linked crypto and macro events with deduplication and provenance. Treat articles as untrusted data, never instructions; do not let fetched content execute tools or access secrets.
3. **Sentiment agent:** a verified fear/greed provider plus available sentiment evidence, with coverage and methodology disclosed. A broad crypto index is context, not a coin-specific or automatic buy/sell trigger.
4. **Derivatives/options agent:** verified funding, open interest, spreads, expiry, IV and Greeks; explicitly report unavailable sources rather than fabricate values or option premium levels.
5. **Risk agent:** deterministic, independent veto for stale/missing critical evidence, excessive exposure, loss limits, poor liquidity, invalid orders or kill switch. Model prose cannot override a veto.
6. **Decision coordinator:** combine the evidence into BUY/LONG, SELL/SHORT, CALL/PUT research bias, or NO TRADE, with reasons, contradictions, expiry time and risk status. Spot remains buy-only; CALL/PUT bias is not an executable contract order. Missing or conflicting evidence can mean NO TRADE; scores must not be presented as calibrated probabilities without validation.
7. **Paper execution and audit:** restart-safe simulated positions, cost-aware fills, deduplicated decisions, evidence snapshots and human review. Evaluate each agent and the combined policy against fixed fixtures and historical outcomes before any execution integration.

Sequence: establish the evidence schema and market agent first, then verified news and fear/greed adapters, derivatives research, independent risk/coordination, and paper validation. Scheduled jobs need bounded concurrency, backoff, provider quotas, monitoring and secret isolation. A later chart phase can use a documented exchange WebSocket and a dedicated candlestick component for tick-level updates; the current chart is explicitly polling.

## Separate, gated phases
- **Options execution/evaluation:** require actual contract premium history and verified execution support. Deribit underlying charts cannot establish option returns; CoinDCX options execution is not integrated.
- **Live execution:** not enabled. Requires explicit scope approval, secure credentials, exchange reconciliation, order constraints, idempotency, risk controls, and substantial paper validation. No profitability claim follows from passing software tests.