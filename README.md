# MaxTrade

For GitHub publishing, live-link hosting, and Hostinger shared-hosting limitations, see [DEPLOYMENT.md](DEPLOYMENT.md).

CoinDCX research dashboard for explainable spot and futures paper signals. The first version uses public market data only and never places orders.

## Run locally

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

Streamlit prints a local URL, usually `http://localhost:8501`. Main-panel controls and a full-width **Scan markets now** button avoid hidden sidebar actions. Per-market progress shows scan activity. **Signals**, **History**, and **API setup** tabs separate work areas. Cards are the default, with one column on phones; Table remains optional. CSV exports and settings-change checks are retained. Scans run on demand, not automatically.

## CoinDCX credentials (configuration only)

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and edit the copy locally with your own `COINDCX_API_KEY` and `COINDCX_API_SECRET`. Alternatively set those variables in the server environment; environment values take precedence. Restart the app after changes. The API setup tab reports presence only, not successful authentication. No authenticated requests or orders are enabled.

Never send credentials in chat or commit the real secrets file. Use minimum exchange permissions and do not grant withdrawals. Real secrets, `.env` files, and scan data are Git-ignored. `.env` files are not automatically loaded. Public scans require no credentials.

## Mobile access

This is a responsive web dashboard, not a native mobile app or installable PWA. Localhost works only on the computer running it. Phone access requires separately configured trusted-network access or authenticated HTTPS hosting. Do not expose a credential-bearing server without access controls. The launch task remains loopback-only.

Spot and USDT-margined futures candidates are ranked by exchange-reported 24-hour volume before candle analysis. Raw volumes are not normalized across currencies/products and are only a discovery heuristic, not a liquidity guarantee. Each scan inspects at most 30 markets. Futures prices come from the public batch ticker, with candles requested only for selected active instruments.

Hourly candles are native. Four-hour spot candles are aggregated from four contiguous, completed hourly candles aligned to UTC; four-hour futures candles are native. Both feeds are sorted chronologically, deduplicated, and exclude unfinished bars. Stale or invalid candle data produces a DATA ERROR row rather than a signal; individual request failures do not discard other markets. Price shows spot last price or futures mark price, while entry uses the completed candle close.

## Saved history

Each completed scan is saved locally in `data/scan_history.sqlite3`, including DATA ERROR rows. Open **History** to inspect and export previous snapshots after a restart. The viewer lists the latest 50; older records remain in the database. This directory is Git-ignored. Back up the file to preserve history. Storage failures do not hide current results. Archived signals are not paper fills or realized performance.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Public API references: [CoinDCX documentation](https://docs.coindcx.com/). Spot uses `api.coindcx.com/market_data/candles`; futures uses `public.coindcx.com/market_data/candlesticks` (`pcode=f`) and `/market_data/v3/current_prices/futures/rt`. HTTPS certificate verification stays enabled.

## Signal method

The research signal uses 20/50 EMA ordering and Wilder-smoothed RSI(14) for trend and momentum alignment. Candidate risk levels use the simple mean of the last 14 true ranges (ATR): 1.5 ATR for the stop and 3 ATR for the target. Spot signals do not recommend shorting. These rules are a baseline for testing, not a validated or profitable strategy. Fees, slippage, funding, and portfolio sizing are not modeled.

Options remain disabled until a supported CoinDCX options feed is verified. Order placement, API keys, and account access are not part of this version. Backtesting and paper-trading records should be added before considering any execution integration.

See [ROADMAP.md](ROADMAP.md) for remaining phases and acceptance criteria.
