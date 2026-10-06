# Remaining work

## Completed
- Public spot and USDT futures scanning at 1h/4h.
- Explainable EMA/RSI signals and ATR reference levels.
- Completed-candle validation, per-market error handling, CSV export.
- Persistent SQLite snapshots and historical snapshot viewer.
- Deribit BTC/ETH options watchlists with expiry, quote, liquidity and delta checks; underlying CALL/PUT bias, not premium trade levels.
- Mobile-first candle chart with forming bars and 10-second REST polling while Chart is selected; pause/manual refresh and closed-bar signals.
- Snapshot backtesting with next-open entries, fees/slippage, conservative stop handling, and an explicitly estimated funding cost. Not strategy certification or live paper trading.
- Initial on-demand market agent: versioned, source-linked 1h/4h technical evidence with event/retrieval/expiry times, separate SQLite reports and JSON export. Missing downstream agents keep the coordinated decision at NO TRADE.
- Owner login with salted password hashes, sign-out and one-hour sessions; server secrets must be configured. Session-local cooldown does not replace identity-proxy or private-hosting access controls.
- Alternative.me fear/greed evidence now accompanies manual reports, with attribution and range/timestamp/category checks. Bitcoin-focused context only, not a trade signal. News providers remain the next integration.

## Newly implemented
- Compact icon-based shell and mobile bottom navigation, with owner login retained.
- Bounded CoinDesk RSS headline evidence and Deribit BTC/ETH perpetual funding/OI/spread adapters; timestamps, units, limitations and provider failures disclosed.
- Deterministic paper-only coordinator with stale/missing/conflicting evidence vetoes, human event review and independent account risk checks.
- Transactional BTC/ETH USDT spot paper ledger: next-bar snapshot simulation, cost-aware sizing, stop-first exits, duplicate prevention and default-on kill switch.
- One-shot research/reconciliation worker, 15-minute slot deduplication, saved internal alerts and archive viewer. External scheduling is opt-in and not activated on Streamlit Cloud.

## Next: strategy evaluation
### Autonomous paper release
- Implemented persisted Start/Pause automation, automatic worker submissions without human review, deterministic vetoes and reconciliation-failure entry blocking. Real orders remain disabled.
- Added complete closed-ledger net win rate, daily results/CSV, realized drawdown and profit factor. Signal accuracy remains separate.
- Added visible Azure-assisted mode and explicit connection test; structured AI reviews can veto but cannot override risk limits.
- Added backend watch mode. Always-on service provisioning, shared durable storage, backups, heartbeat monitoring and live Azure credentials remain unconfigured infrastructure work.
- Next evaluation gate: collect forward results under frozen policy versions, expand failure/gap fixtures and implement walk-forward evaluation before expanding assets or execution scope.

1. **Extended evaluation:** verify longer historical data availability, historical funding and market constraints; add out-of-sample and walk-forward evaluation. Report sample size, drawdown and costs. Current loaded-chart replay is too short to establish reliability.
2. **Paper evaluation:** accumulate forward paper results and evaluate fixtures, missing-data recovery, gaps and risk limits. Add verified exchange lot constraints and execution-liquidity checks before expanding beyond the current spot simulation.
3. **Risk expansion:** unrealized P&L, portfolio/multi-currency exposure and position-aware emergency exits. Current caps use realized equity and one spot position; this is not live-account risk management.

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

Implementation status: the initial technical, sentiment, headline and derivatives adapters, paper-only risk/coordinator and ledger are implemented. The one-shot worker and internal alerts are available; production scheduling, durable hosting, external alert delivery, backups and monitoring still require infrastructure configuration. Broader macro coverage, provider licensing review, historical evaluation and execution-grade liquidity are not complete. The agent descriptions above remain target acceptance criteria, not a claim of full autonomous coverage. A later chart phase can use a documented exchange WebSocket; the current chart is explicitly polling.

## Separate, gated phases
- **Options execution/evaluation:** require actual contract premium history and verified execution support. Deribit underlying charts cannot establish option returns; CoinDCX options execution is not integrated.
- **Live execution:** not enabled. Requires explicit scope approval, secure credentials, exchange reconciliation, order constraints, idempotency, risk controls, and substantial paper validation. No profitability claim follows from passing software tests.