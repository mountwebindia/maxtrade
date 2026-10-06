# MaxTrade

For GitHub publishing, live-link hosting, and Hostinger shared-hosting limitations, see [DEPLOYMENT.md](DEPLOYMENT.md).

Research dashboard for CoinDCX spot/futures signals and Deribit BTC/ETH options watchlists. Uses public market data only and never places orders.

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
The Chart page includes a **Backtest** section for spot and futures snapshots. It replays the existing EMA/RSI strategy using Backtesting.py, fractional quantities, next-open market entries on new setups, one unleveraged position, fixed stop/target orders, and an allocation/risk cap. Configure capital in the market quote currency, fee/slippage basis points per fill, and an optional futures funding cost per eight hours. Download the simulated trade ledger and inspect net equity and drawdown.

This is a small-sample historical replay, not live paper trading or strategy certification. Slippage is modelled as an additional cash commission; funding is an entry-notional elapsed-time estimate charged to both directions after replay, not historical funding or a cash constraint on sizing. Stop-first handling is used when a bar touches both stop and target. Remaining trades are liquidated at the final bar open. Gaps may exceed the risk budget; exchange lot sizes, liquidation, taxes and order-book fills are not modelled. Options returns are excluded because underlying candles are not option premium history.


Open **Chart** for CoinDCX spot/futures or Deribit BTC/ETH underlying candles. BTC loads automatically; Browse markets discovers other active CoinDCX markets. Live updates poll the candle API every 10 seconds while Chart is selected, including the forming candle when supplied by the feed. Disable Live updates to pause, or refresh manually. Automatic polling preserves chart pan/zoom; changing controls or leaving and reopening Chart may reset the view. This is REST polling, not TradingView or a tick-by-tick exchange WebSocket; a new fetch timestamp does not guarantee a new exchange price. Each open browser session makes its own requests.

The chart includes OHLC candlesticks, EMA20/50, RSI14, and new BUY/LONG or SELL/SHORT setup markers calculated only from completed candles available at each close. The forming candle is display-only: confirmed indicators, signals, and backtests exclude it. Spot remains buy-only; SHORT is not a position-aware sell/exit instruction. Latest qualifying spot/futures setups show candle-close entry and ATR stop/target levels. These are research references, not fills or execution instructions.

Options charts show the USD perpetual underlying, not coin-denominated option premiums. CALL/PUT bias is directional research only; a separate contract watchlist checks the existing expiry, liquidity, spread, and delta filters. Automatic contract refreshes are limited to once per minute; manual Refresh forces new quotes. No option premium entry, stop, or target is inferred. Invalid or gapped candles suppress chart signals; changed selections automatically load the new market and failed refreshes clear previous chart snapshots. Backtest results remain visible across price-only refreshes but are invalidated when completed OHLC history or replay settings change.

## Saved history

## Market research agent

Open **Chart > Market research > Run market research** for an on-demand 1h/4h technical evidence report. It records venue, symbol, quote units, source endpoint, candle-close time, retrieval time and expiry. Forming, stale or gapped candles cannot produce valid evidence. Aligned directions are technical bias only; the coordinated decision stays NO TRADE until news, sentiment, derivatives/liquidity, portfolio risk and paper-validation gates exist. Options reports describe USD underlying direction, never option-premium entry or risk levels.

Reports are saved separately from scans in the local SQLite database and downloadable as JSON. They are not orders or fills. Reports can expire, and stored technical bias is historical rather than a refreshed recommendation. Manual runs also fetch the Bitcoin-focused [Alternative.me fear/greed index](https://alternative.me/crypto/fear-and-greed-index/), rejecting future/stale timestamps, invalid classifications and values outside 0–100. Its 24-hour freshness limit and scope are explicit; it does not generate trade signals. Provider failures retain the sentiment blocker. No background scheduler, news adapter, independent risk engine or multi-agent decision system is enabled yet. Cloud-local SQLite remains shared and non-durable; a production scheduler needs separately provisioned durable storage.

Dashboard access requires server-configured username and hashed password. No default credentials exist. See [DEPLOYMENT.md](DEPLOYMENT.md) for private setup, session limitations and hosted access precautions.

## Saved scans

Each completed scan is saved locally in `data/scan_history.sqlite3`, including DATA ERROR rows. Open **History** to inspect and export previous snapshots after a restart. The viewer lists the latest 50; older records remain in the database. This directory is Git-ignored. Back up the file to preserve history. Storage failures do not hide current results. Archived signals are not paper fills or realized performance.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Public API references: [CoinDCX documentation](https://docs.coindcx.com/). Spot uses `api.coindcx.com/market_data/candles`; futures uses `public.coindcx.com/market_data/candlesticks` (`pcode=f`) and `/market_data/v3/current_prices/futures/rt`. HTTPS certificate verification stays enabled.

## Signal method

The research signal uses 20/50 EMA ordering and Wilder-smoothed RSI(14) for trend and momentum alignment. Candidate risk levels use the simple mean of the last 14 true ranges (ATR): 1.5 ATR for the stop and 3 ATR for the target. Spot signals do not recommend shorting. These rules are a baseline for testing, not a validated or profitable strategy. Fees, slippage, funding, and portfolio sizing are not modeled.

## Crypto options research

Select **Options**, then BTC or ETH, and scan. The source is **Deribit**, not CoinDCX. Public `get_instruments`, `get_book_summary_by_currency`, `ticker`, and `get_tradingview_chart_data` endpoints require no keys. See [Deribit API documentation](https://docs.deribit.com/).

Open coin-quoted contracts with 7–45 days until expiry are ranked by reported USD volume; at most 30 are inspected. The selected interval applies to the underlying Deribit perpetual trend, not option-premium candles. Four-hour bars are aggregated from completed hourly bars. `WATCH CALL` or `WATCH PUT` requires aligned underlying EMA/RSI direction, absolute delta 0.25–0.75, a two-sided spread at most 10%, open interest at least 10 base coins, and positive 24-hour base-coin volume. Quotes older than five minutes, invalid Greeks, missing/crossed quotes, and closed books produce DATA ERROR, not candidates.

Cards and CSV include contract expiry, USD strike, coin-denominated premium/bid/ask, IV, delta, spread, and open interest. CSV also includes exchange gamma/theta/vega and quote timestamps. IV and Greeks are exchange estimates, not independently modeled. No option entry/stop/target is invented from underlying ATR. Watchlists are unbacktested research filters, not fair-value assessments or execution instructions. Options can lose their entire premium; liquidity filters do not guarantee fills. Fees, slippage and portfolio risk are not modeled.

CoinDCX options execution, all other order placement, and authenticated account access remain disabled. Backtesting and paper-trading records should be added before considering any execution integration. Cloud-local history is shared across users and may disappear on restart or redeployment; it is not durable private storage.

See [ROADMAP.md](ROADMAP.md) for remaining phases and acceptance criteria.
