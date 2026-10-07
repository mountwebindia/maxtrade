# MaxTrade
## Autonomous paper research

Settings includes saved Azure verdicts, summaries and concerns, separate timeout/network/schema errors, and completed-cycle worker diagnostics. A one-shot cycle is not proof of always-on supervision. Closed-trade metrics include average net win/loss and net expectancy; daily autonomous rejection counts and CSV distinguish blocked assessments from missed trades. Local and Cloud records remain separate until shared durable infrastructure is provisioned.

Settings now exposes **AI research mode**, an Azure connection test, and **Start/Pause paper automation**. Autonomous paper mode removes human approval for BTC/ETH USDT spot simulations while retaining evidence, position, freshness and loss-limit vetoes. Closed-trade net win rate, daily P&L/CSV, profit factor and realized drawdown are shown separately from scanner target accuracy.

Run `python -m maxtrade.worker --watch` on a managed backend sharing the dashboard's persistent `MAXTRADE_DATABASE` path for unattended 15-minute cycles. Streamlit Cloud alone does not provide this always-on worker or durable shared storage. Azure-assisted mode requires private credentials and incurs Azure charges; an AI review can veto but cannot override deterministic risk checks. Real orders remain disabled. See [deployment setup](DEPLOYMENT.md#autonomous-paper-operation).


For GitHub publishing, live-link hosting, and Hostinger shared-hosting limitations, see [DEPLOYMENT.md](DEPLOYMENT.md).

Research dashboard for CoinDCX spot/futures signals and Deribit BTC/ETH options watchlists. Uses public market data only and never places orders.

## Prediction quality monitoring

Daily accuracy now separates target hits from net modeled outcomes. New resolved and expired signals include 10 bps fees and 5 bps adverse slippage per side; expiry uses the final completed close of the 24-hour horizon. Funding and borrowing are excluded. Legacy terminal records remain immutable and are excluded from net statistics when cost metadata is absent. Overlapping signals are not a portfolio return series.

New scanner snapshots archive `trend-breakout-volume-shadow-v1` evidence: 20-bar directional efficiency, ATR percentage, EMA regime, prior-range breakout, relative volume and completed higher-timeframe agreement (1h/4h or 4h/daily). CHOP below 0.25 efficiency, ATR above 5%, absent breakout, volume ratio below 1.2 and unavailable/conflicting confirmation block the **shadow candidate**, not the existing baseline or PAPER policy. Thresholds are frozen research hypotheses, not optimized or validated improvements. Reported volume is not order-book liquidity.

The Daily accuracy section displays purged 30-day forward folds comparing baseline and candidate signal outcomes, excluding overlap per market/timeframe. Confidence is a prior comparable net-win frequency with a Wilson 95% interval, requiring 30 non-overlapping samples of the same market, timeframe, direction, regime, candidate decision and version. Both the full horizon and recorded outcome evaluation must predate the decision. Insufficient evidence stays unavailable. Brier scores evaluate estimates frozen at fold start. This is forward shadow monitoring, not trained-model walk-forward certification, guaranteed accuracy or automatic policy promotion. New data must accumulate; older unversioned scans are not retroactively labeled.

## Run locally

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

Streamlit prints a local URL, usually `http://localhost:8501`. Main-panel controls and a full-width **Scan markets now** button avoid hidden sidebar actions. Per-market progress shows scan activity. **Signals**, **Chart**, **History**, and **Settings** tabs separate work areas, with bottom navigation on phones. Cards are the default, with one column on phones; Table remains optional. CSV exports and settings-change checks are retained. Scans run on demand, not automatically.

## CoinDCX credentials (configuration only)

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and edit the copy locally with your own `COINDCX_API_KEY` and `COINDCX_API_SECRET`. Alternatively set those variables in the server environment; environment values take precedence. Restart the app after changes. The API setup tab reports presence only, not successful authentication. No authenticated requests or orders are enabled.

Never send credentials in chat or commit the real secrets file. Use minimum exchange permissions and do not grant withdrawals. Real secrets, `.env` files, and scan data are Git-ignored. `.env` files are not automatically loaded. Public scans require no credentials.

## Mobile access

This is a responsive web dashboard, not a native mobile app or installable PWA. Localhost works only on the computer running it. Phone access requires separately configured trusted-network access or authenticated HTTPS hosting. Do not expose a credential-bearing server without access controls. The launch task remains loopback-only.

Spot and USDT-margined futures candidates are ranked by exchange-reported 24-hour volume before candle analysis. Raw volumes are not normalized across currencies/products and are only a discovery heuristic, not a liquidity guarantee. Each scan inspects at most 30 markets. Futures prices come from the public batch ticker, with candles requested only for selected active instruments.

Hourly candles are native. Four-hour spot candles are aggregated from four contiguous, completed hourly candles aligned to UTC; four-hour futures candles are native. Both feeds are sorted chronologically, deduplicated, and exclude unfinished bars. Stale or invalid candle data produces a DATA ERROR row rather than a signal; individual request failures do not discard other markets. Price shows spot last price or futures mark price, while entry uses the completed candle close.

## Candle chart
**Open chart in new tab** opens an authenticated chart-only workspace with collapsed settings and no research, option-chain or backtest panels. Use the chart's built-in expand control for fullscreen, and its camera control for a PNG. New tabs require their own dashboard login session. **Save interactive chart** downloads a self-contained HTML snapshot (not a live connection). **Save layout**, **Load saved layout** and **Save layout to URL** preserve chart preferences; bookmarks contain no credentials. Browser drawings remain temporary and are not included in saved layouts.

Chart timeframes include 1m, 5m, 15m, 30m, 1h, 4h and 1d; bounded custom choices are 2m, 3m, 45m, 120m and 180m. Unsupported native resolutions are aggregated from contiguous source candles; gaps are never filled. Spot 5m history is limited to roughly 99 bars by the provider's 500-source-bar cap. **Custom UTC range** zooms within loaded history, not arbitrary historical retrieval. Intraday setups remain technical research only; autonomous paper assessment and contract watchlists retain the existing 1h/4h policy.

The Chart page includes a **Backtest** section for spot and futures snapshots. It replays the existing EMA/RSI strategy using Backtesting.py, fractional quantities, next-open market entries on new setups, one unleveraged position, fixed stop/target orders, and an allocation/risk cap. Configure capital in the market quote currency, fee/slippage basis points per fill, and an optional futures funding cost per eight hours. Download the simulated trade ledger and inspect net equity and drawdown.

This is a small-sample historical replay, not live paper trading or strategy certification. Slippage is modelled as an additional cash commission; funding is an entry-notional elapsed-time estimate charged to both directions after replay, not historical funding or a cash constraint on sizing. Stop-first handling is used when a bar touches both stop and target. Remaining trades are liquidated at the final bar open. Gaps may exceed the risk budget; exchange lot sizes, liquidation, taxes and order-book fills are not modelled. Options returns are excluded because underlying candles are not option premium history.


Open **Chart** for CoinDCX spot/futures or Deribit BTC/ETH underlying candles. BTC loads automatically; Browse markets discovers other active CoinDCX markets. Live updates poll the candle API every 10 seconds while Chart is selected, including the forming candle when supplied by the feed. Disable Live updates to pause, or refresh manually. Automatic polling preserves chart pan/zoom; changing controls or leaving and reopening Chart may reset the view. This is REST polling, not TradingView or a tick-by-tick exchange WebSocket; a new fetch timestamp does not guarantee a new exchange price. Each open browser session makes its own requests.

The exchange-style chart has Candles/Line/Area modes, dark/light themes, optional EMA20/50, volume and RSI14 panels, linear/log price scale, a crosshair, scroll zoom, pan, axis scaling, reset/autoscale, temporary line/freehand/rectangle drawings, image download and candle CSV. Optional BUY/SELL arrows mark new completed-candle technical setup transitions, with availability time and reference levels on hover. Spot SELL is bearish research, not a short order. Options arrows are CALL/PUT underlying bias. Past signal records/CSV are recomputed from loaded candles, not contemporaneously archived recommendations; the initial warm-up setup is excluded. Separate diamond/cross markers show saved paper entry/exit events within the chart range on this database. The forming candle is display-only: confirmed indicators, setup arrows and backtests exclude it. Drawings are temporary browser annotations and are not persisted in the ledger. Available timeframes remain 1h/4h; this is not CoinDCX's native TradingView terminal or order-entry interface.

Options charts show the USD perpetual underlying, not coin-denominated option premiums. CALL/PUT bias is directional research only; a separate contract watchlist checks the existing expiry, liquidity, spread, and delta filters. Automatic contract refreshes are limited to once per minute; manual Refresh forces new quotes. No option premium entry, stop, or target is inferred. Invalid or gapped candles suppress chart signals; changed selections automatically load the new market and failed refreshes clear previous chart snapshots. Backtest results remain visible across price-only refreshes but are invalidated when completed OHLC history or replay settings change.

## Historical research data

Download a bounded, resumable Coinbase Exchange research dataset without AI calls or trades:

```sh
.venv/bin/python -m maxtrade.historical --product BTC-USD --interval 1d --start 2016-10-06 --end 2026-10-06
.venv/bin/python -m maxtrade.historical --product ETH-USD --interval 1d --start 2016-10-06 --end 2026-10-06
```

Dates are UTC, start inclusive and end exclusive; only completed candles are accepted. The separate `data/historical.sqlite3` stores raw pages, SHA256 checksums, retrieval timestamps, validated candles and gap reports. Repeating the same range uses cached pages and verifies their checksums. A provider failure stops the run; rerun to resume, respecting rate limits. Conflicting stored prices are rejected, not overwritten. Missing buckets stay missing.

The ten-year daily download above was verified locally: 3,652 candles each for BTC-USD and ETH-USD, 100% bucket coverage. This does not establish price accuracy, profitability or complete crypto history. `--interval 1h` is supported, but long hourly/4h coverage is not yet verified. Coinbase USD prices and base-asset volume are not CoinDCX USDT execution evidence. Provider licensing/redistribution review remains required. Historical downloads are not contemporaneously archived evidence; no model training or paper-policy changes follow from ingestion.

### Causal shadow evaluation

```sh
.venv/bin/python -m maxtrade.historical_features --product BTC-USD --start 2016-10-06 --end 2026-10-06
.venv/bin/python -m maxtrade.historical_features --product ETH-USD --start 2016-10-06 --end 2026-10-06
```

The loader verifies raw-page checksums and requires contiguous complete coverage. Versioned daily features include EMA20/50/200, simple-average RSI14 (not the live Wilder RSI), ATR percentage, returns, volatility and relative volume. Features become available at candle close; five-day next-open-to-close outcome labels remain separate. Prefix-invariance and future-window mutation tests guard against future-price leakage.

The frozen research baseline takes sequential, full-notional five-day long trades when EMA20 > EMA50 > EMA200. The first 60% is development history; the next 20% contains four chronological evaluation windows and the final 20% is a held-out test. Outcomes crossing window ends are purged. No model fitting, parameter search or live paper policy modification occurs. Costs are 10 bps fees and 5 bps slippage per side. Always-long five-day trades and buy-and-hold are reported for comparison. This is not the current hourly stop/target strategy. Drawdown is realized closed-trade equity only, with no publication-delay, liquidity or intratrade-risk model.

Reports are persisted by SHA256 in `historical_evaluations`. Local runs of `daily-uptrend-five-day-v1` produced final-test BTC: 63 trades, 46.03% win rate, 40.10% net return, 25.70% realized drawdown; ETH: 36 trades, 44.44% win rate, 30.76% net return, 24.75% realized drawdown. Two of four earlier rolling windows lost money for each asset. These are hypothetical results, not evidence of improved live accuracy or permission to promote the policy. This holdout has now been observed; retuning needs a new untouched holdout and forward-paper validation. Statistical training and historical evidence integration into agent decisions remain pending.

### Hourly coverage and shadow context

```sh
.venv/bin/python -m maxtrade.historical --product BTC-USD --interval 1h --start 2025-10-06 --end 2026-10-06
.venv/bin/python -m maxtrade.historical --product ETH-USD --interval 1h --start 2025-10-06 --end 2026-10-06
```

UTC-aligned hourly ranges also report derived 4h coverage using only four contiguous completed hours. These local one-year downloads returned 8,750 of 8,760 hourly candles per asset: 10 missing hours, and 2,186 of 2,190 derived 4h buckets. Gaps remain missing; hourly-policy certification is not complete and ten-year hourly coverage is unverified.

Coordinated research now attaches `historical_shadow` from the separate research database. `MAXTRADE_HISTORICAL_DATABASE` can select another path; the default is workspace `data/historical.sqlite3`. This database does not upload automatically to hosted deployments. Complete daily data must have been retrieved/generated before the research time and end no more than two days before it. The raw-page loader verifies checksums. Current daily regime features and prior matured same-regime five-day samples are displayed and saved with the report; missing, stale or corrupt data is labelled UNAVAILABLE. No provider requests are made by this adapter. These retrospective samples are descriptive, not calibrated predictions or contemporaneous historical knowledge. They are deliberately excluded from Azure review and deterministic paper approval inputs; trade rules stay unchanged.

## Market research agent

Open **Chart > Market research > Run market research** for a coordinated evidence report. Completed 1h/4h technical evidence records venue, units, source and expiry. Alternative.me provides Bitcoin-focused fear/greed context; CoinDesk RSS supplies deduplicated, attributed headline links, not exhaustive macro/event coverage; Deribit BTC/ETH inverse perpetuals supply funding, USD open interest and spread context. Deribit liquidity is not CoinDCX execution liquidity. Missing, conflicting, stale or invalid evidence keeps the paper decision at NO TRADE. Options reports describe USD underlying direction, never option-premium risk levels.

Reports are saved separately from scans and downloadable as JSON. History includes archived reports and deduplicated internal worker alerts. These are historical observations, not refreshed recommendations. Provider timestamps and errors remain visible. Review provider terms before redistributing headlines commercially; articles are data, never tool instructions.

## Paper simulation

Above-chart 1h/4h checks show LONG agreement, freshness, missing timeframes and assessment age. Setup invalidation follows the existing completed-candle EMA/RSI rules and does not move an existing paper position's saved stop. Snapshot replay rejects missing, duplicate and unaligned candles instead of bridging or filling gaps; this is not certification of the combined hourly/news/Azure policy.

The chart's Trade decision panel separates completed-candle LONG/SHORT setups from paper eligibility. It shows research entry/stop/target only for fresh candidates, account gates and worker heartbeat above the candles. Stale candles or missing matching full research remain NO TRADE; technical direction alone never authorizes a fill. Settings shows the latest saved autonomous decisions and their blockers.

The supervised worker continues public-data research and existing-position reconciliation if private integration configuration is missing or invalid, but explicitly blocks all new entries. Azure-assisted mode still requires a successful CLEAR review; missing keys are not bypassed. A running process without a recent completed-cycle heartbeat is not evidence of healthy trading automation.

Settings includes a paper account with a default-on kill switch. Only BTC/ETH USDT spot BUY entries are supported. Fresh unanimous LONG evidence, manual news/event review, available account state and risk approval are required to queue an entry. The reviewed snapshot authorizes only the immediately next hourly open; it is not revalidated as a fresh recommendation at fill. Completed hourly bars reconstruct simulated fills, with 10 bps fees and 5 bps slippage per side, stop-first exits, 1% planned risk, 25% allocation cap, one pending/open position globally and a 3% realized daily-loss veto. Gaps can exceed planned risk. Equity excludes unrealized P&L; capital is fixed after the first decision. Kill switch cancels pending entries, not open positions.

Positions and their source snapshots persist in SQLite, with duplicate-entry prevention and CSV downloads. The worker queues eligible simulated entries only when autonomous paper policy is enabled; it never sends exchange orders. Completed cycles save a heartbeat and verified daily online backup. Unattended operation requires an awake Mac or a managed persistent backend. Cloud-local SQLite and same-disk backups can disappear on redeployment. Futures/options paper fills remain excluded. See [DEPLOYMENT.md](DEPLOYMENT.md).

Dashboard access requires server-configured username and hashed password. No default credentials exist. See [DEPLOYMENT.md](DEPLOYMENT.md) for private setup, session limitations and hosted access precautions.

## Saved scans

Each completed scan is saved locally in `data/scan_history.sqlite3`, including DATA ERROR rows. Open **History** to inspect and export previous snapshots after a restart. The viewer lists the latest 50; older records remain in the database. This directory is Git-ignored. Back up the file to preserve history. Storage failures do not hide current results. Archived signals are not paper fills or realized performance.

### Gold-backed tokens and daily accuracy

Signals > Asset group > Gold-backed tokens filters active CoinDCX PAXG/XAUT spot or futures listings. Availability depends on the current venue catalog. These are gold-backed crypto tokens, not MCX gold options; the Deribit options watchlist remains BTC/ETH only.

History > Daily accuracy records new LONG/SHORT scan predictions with their market pair and completed signal-candle timestamp. Repeated product/pair/timeframe/action signals from the same candle count once. Older snapshots lacking provenance and options/NO TRADE rows are not scored. Entries use the first timeframe boundary strictly after the scan was saved, avoiding retrospective fills. The next 24 hours are checked with completed hourly OHLC bars; a stop/target tie counts as a loss, stop gaps use the adverse opening price, and an entry already outside stop/target is invalid.

Target accuracy is wins divided by wins + losses + 24-hour expiries. Expiries count as misses; pending, data gaps and invalid entries remain separately visible and unscored. Daily grouping uses UTC scan date, not closing date. Per-prediction outcomes and daily CSV are available. Update outcomes fetches public candles only on request, with a recent 480-hour lookback; older missing history cannot be reconstructed with this update path. Final outcomes are immutable, but providers may revise historical bars before scoring. This is a gross-price scenario evaluation, not actual fills, fee-adjusted profitability, option-premium accuracy, or certification. Cloud-local records may disappear on restart; no background scheduling or durable storage is activated.

### Fresh Historical Gap Audit

Cached ingestion does not refresh missing buckets. Recheck a saved dataset with bounded fresh requests:

```sh
.venv/bin/python -m maxtrade.historical --product BTC-USD --interval 1h --start 2025-10-06 --end 2026-10-06 --audit-gaps
```

Repeat with `ETH-USD` for Ethereum. The audit verifies original raw checksums, requests gap windows with adjacent candles, and saves fresh raw responses, retrieval times, SHA256, recovered bars and overlapping provider revisions in `historical_gap_audits`. At most 20 requests are allowed, each covering at most 300 hourly/daily buckets. It never fills or overwrites the original dataset and cannot approve trades.

The 2026-10-07 fresh checks recovered zero of the 10 missing hours per asset; adjacent candles were unchanged. Both assets still have two five-hour gaps and incomplete derived 4h coverage. Full-range hourly-policy certification remains blocked. Independent source verification or an explicitly designed segment/reset policy is required; interpolation is not execution evidence.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Public API references: [CoinDCX documentation](https://docs.coindcx.com/). Spot uses `api.coindcx.com/market_data/candles`; futures uses `public.coindcx.com/market_data/candlesticks` (`pcode=f`) and `/market_data/v3/current_prices/futures/rt`. HTTPS certificate verification stays enabled.

## Signal method

The research signal uses 20/50 EMA ordering and Wilder-smoothed RSI(14) for trend and momentum alignment. Candidate risk levels use the simple mean of the last 14 true ranges (ATR): 1.5 ATR for the stop and 3 ATR for the target. Spot signals do not recommend shorting. These rules are a baseline for testing, not a validated or profitable strategy. Fees, slippage, funding, and portfolio sizing are not modeled.

## Crypto options research

Select **Options**, then BTC or ETH, and scan. The source is **Deribit**, not CoinDCX. Public `get_instruments`, `get_book_summary_by_currency`, `ticker`, and `get_tradingview_chart_data` endpoints require no keys. See [Deribit API documentation](https://docs.deribit.com/).

Signals and Chart automatically load the full BTC/ETH chain with readable UTC expiry-date selection and a strike-range filter. The exchange-style table pairs CALL quotes on the left and PUT quotes on the right of the highlighted USD strike, with bid/ask/mark, IV, open interest, volume and quote status. CSV exports only the selected expiry and strike range. Refresh updates the snapshot; snapshots older than one minute are flagged. Stale/unavailable quotes stay blank. Premiums are BTC/ETH; strikes are USD. No CoinDCX public options-chain endpoint was found in the official reference inspected, so this fallback is explicitly labeled Deribit. Candle setup arrows describe underlying CALL/PUT bias, not option-premium entry or exit signals.

Worker cycles record aligned autonomous technical signals and automatically score unresolved outcomes. Private Telegram bot configuration, delivery testing and persisted retries support PAPER ONLY decision/position alerts. Daily UTC reports separate closed-trade net win rate from technical target accuracy, with a next-day provisional report and 48-hour update. Setup and limitations are in [DEPLOYMENT.md](DEPLOYMENT.md#telegram-paper-alerts); credentials and always-on hosting are separate prerequisites.

Open coin-quoted contracts with 7–45 days until expiry are ranked by reported USD volume; at most 30 are inspected. The selected interval applies to the underlying Deribit perpetual trend, not option-premium candles. Four-hour bars are aggregated from completed hourly bars. `WATCH CALL` or `WATCH PUT` requires aligned underlying EMA/RSI direction, absolute delta 0.25–0.75, a two-sided spread at most 10%, open interest at least 10 base coins, and positive 24-hour base-coin volume. Quotes older than five minutes, invalid Greeks, missing/crossed quotes, and closed books produce DATA ERROR, not candidates.

Cards and CSV include contract expiry, USD strike, coin-denominated premium/bid/ask, IV, delta, spread, and open interest. CSV also includes exchange gamma/theta/vega and quote timestamps. IV and Greeks are exchange estimates, not independently modeled. No option entry/stop/target is invented from underlying ATR. Watchlists are unbacktested research filters, not fair-value assessments or execution instructions. Options can lose their entire premium; liquidity filters do not guarantee fills. Fees, slippage and portfolio risk are not modeled.

CoinDCX options execution, all other order placement, and authenticated account access remain disabled. Backtesting and paper-trading records should be added before considering any execution integration. Cloud-local history is shared across users and may disappear on restart or redeployment; it is not durable private storage.

See [ROADMAP.md](ROADMAP.md) for remaining phases and acceptance criteria.
