# Remaining work

## Prediction quality increment
- Implemented cost-aware forward signal outcomes, expiry closes and daily net sample/win/mean statistics; old terminal results remain immutable.
- Implemented versioned scanner shadow regime vetoes, prior-range breakout, relative-volume and completed higher-timeframe confirmations, archived separately from unchanged baseline/PAPER authorization.
- Implemented purged 30-day forward comparison folds and prior-only empirical confidence with minimum sample size, non-overlap, outcome-availability timestamps, Wilson intervals and fold-frozen Brier scoring.
- Pending: sufficient forward samples, calibration reliability analysis, timestamped historical intraday feature coverage, train/validation/test model fitting and untouched walk-forward certification. Existing hourly gaps and observed historical holdouts remain restrictions. Execution-grade order-book liquidity and independently verified market constraints are not supplied by relative candle volume. No candidate promotion or predictive-improvement claim follows from software tests.

## Historical learning: ordered implementation gates
1. Chart evidence: selectable EMA200, Bollinger Bands, UTC-day VWAP, prior-20-bar support/resistance and MACD implemented locally. Completed bars only; warm-ups and incomplete first-day VWAP stay blank. These additions do not change entry policy, predict returns or train an AI model.
2. Historical ingestion (daily foundation implemented locally): resumable Coinbase Exchange BTC-USD/ETH-USD ingestion preserves raw responses, SHA256, retrieval times and validated candles in a separate research database. Live download for 2016-10-06 inclusive to 2026-10-06 exclusive returned 3,652 daily candles per asset with zero missing buckets. This is Coinbase USD data, not CoinDCX USDT execution history. Ten-year hourly/derived 4h coverage, provider licensing/redistribution review, derivatives, news and on-chain coverage remain unverified gates.
3. Feature/outcome pipeline (daily foundation implemented): checksum-verified complete datasets feed versioned causal EMA, simple-average RSI, ATR, volatility, returns and volume features. Close-time availability and separate next-open five-day outcome labels are tested for prefix invariance. External event publication availability and hourly features remain pending.
4. Evaluation (fixed daily shadow baseline implemented): four chronological rolling windows and a final held-out test purge crossing outcome horizons; sequential long-only trades include fees/slippage and compare with always-long and buy-and-hold. Versioned reports persist in research SQLite. Both assets lost in two earlier windows; no policy promotion or accuracy claim follows. No fitting/parameter selection occurs; full trained-model walk-forward validation and current hourly paper-policy certification remain pending. Observed holdouts cannot be reused for retuning. Historical event research must not ask a present-day LLM to reconstruct past knowledge; timestamped archived evidence and knowledge-leakage disclosure remain required.
5. Agent evidence (daily shadow context implemented): research reports attach checksum-verified daily Coinbase regime features and matured, non-overlapping same-regime five-day samples. Dataset retrieval/generation must precede the report time, with a two-day freshness cap; unavailable/corrupt/stale data is explicitly labelled. Shadow context is archived/exported/displayed but excluded from Azure and paper approval inputs. These retrospective samples are not calibrated probabilities or validation of the current hourly policy. One-year hourly data (2025-10-06 inclusive to 2026-10-06 exclusive) returned 8,750/8,760 hours each for BTC/ETH, leaving 10 missing hours and 4 missing derived 4h buckets each; no filling or hourly-policy promotion. Ten-year hourly coverage and verified external historical events remain pending. Azure may veto supplied execution evidence but cannot override independent risk or automatically retrain.
6. Optional statistical model (gated, not implemented): train only on the training window, select parameters on validation, freeze an untouched test, check probability calibration, then forward-paper test before promotion. Preserve dataset/model/policy versions and rollback. No self-modifying live strategy or automatic fine-tuning is authorized by the research workflow.
7. Production (pending): shared durable database, a single supervised hosted worker, off-host backups and heartbeat monitoring. Freeze policy while collecting forward paper outcomes; propose updates on scheduled evaluation, not after individual wins/losses. No real orders or profitability guarantee.

## Completed
- Pre-hosting reliability increment: saved Azure failure/verdict/concern diagnostics, one-shot versus watch heartbeat warnings, above-chart 1h/4h freshness checklist and exact setup invalidation, closed-trade net expectancy/average win-loss, archived rejection counts/CSV, and fail-closed replay continuity checks. Hosted storage, private integration configuration and current hourly-policy certification remain separate pending gates.
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
Fresh gap audits are implemented with preserved raw provenance and no original-dataset overwrite. The 2026-10-07 Coinbase recheck recovered zero missing hours for BTC/ETH and found no overlapping revisions. Full-range hourly-policy certification remains blocked; next investigate independent venue history or specify and test complete-segment warm-up/reset and open-position-at-gap handling before evaluating segmented data. Other venues must remain separately labelled, not silently spliced into Coinbase history.

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