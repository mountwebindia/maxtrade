from __future__ import annotations

from datetime import datetime, timezone

import requests
import streamlit as st

from maxtrade.charts import candle_figure, chart_analysis
from maxtrade.coindcx import CoinDCXClient, INTERVAL_MS
from maxtrade.options import DeribitClient, scan_options
from maxtrade.presentation import display_number, signal_card
from maxtrade.scanner import QUOTE_CURRENCIES


def render_chart_page() -> None:
    product = st.radio("Chart market type", ["Spot", "Futures", "Options"], horizontal=True,
                       label_visibility="collapsed", key="chart_product", width="stretch")
    controls = st.columns(2)
    interval = controls[0].selectbox("Chart timeframe", ["1h", "4h"], key="chart_interval")
    if product == "Options":
        market = controls[1].selectbox("Chart underlying", ["BTC", "ETH"], key="chart_underlying")
        pair = market
        st.caption(f"Deribit · {market}-PERPETUAL · USD underlying, not option premium")
    else:
        if st.button("Browse markets", icon=":material/search:", key="chart_browse"):
            client = CoinDCXClient()
            try:
                with st.spinner("Loading active markets…"):
                    if product == "Spot":
                        catalog = {row["coindcx_name"]: row["pair"] for row in client.spot_markets()
                                   if row.get("status") == "active" and row.get("base_currency_short_name") in QUOTE_CURRENCIES
                                   and row.get("coindcx_name") and row.get("pair")}
                    else:
                        catalog = {name: name for name in client.futures_instruments()}
                    if not catalog:
                        raise ValueError("No active markets available")
                    st.session_state[f"chart_catalog_{product}"] = catalog
            except (requests.RequestException, KeyError, TypeError, ValueError) as error:
                st.error(f"Market discovery unavailable: {error}")
            finally:
                client.session.close()
        catalog = st.session_state.get(f"chart_catalog_{product}", {})
        if not catalog:
            st.info("No chart market selected.")
            return
        names = sorted(catalog)
        preferred = "BTCUSDT" if product == "Spot" else "B-BTC_USDT"
        market = controls[1].selectbox("Chart market", names, index=names.index(preferred) if preferred in names else 0,
                          key=f"chart_market_{product}")
        pair = catalog[market]
        st.caption(f"CoinDCX · {product.lower()} · {market}")
    selection = (product, interval, pair)
    if st.button("Refresh chart", type="primary", icon=":material/refresh:", width="stretch", key="chart_refresh"):
        st.session_state.pop("chart_snapshot", None)
        client = DeribitClient() if product == "Options" else CoinDCXClient()
        try:
            with st.spinner("Loading completed candles…"):
                if product == "Options":
                    candles = client.underlying_candles(pair, interval)
                elif product == "Spot":
                    candles = client.spot_candles(pair, interval)
                else:
                    candles = client.futures_candles(pair, interval)
                analyses = chart_analysis(candles, interval, allow_short=product != "Spot")
                st.session_state["chart_snapshot"] = {"selection": selection, "candles": candles, "analyses": analyses,
                                                     "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                if product == "Options":
                    try:
                        st.session_state["chart_snapshot"]["contracts"] = scan_options(client, pair, interval, 10)
                    except (requests.RequestException, KeyError, TypeError, ValueError) as error:
                        st.session_state["chart_snapshot"]["contract_error"] = str(error)
        except (requests.RequestException, KeyError, TypeError, ValueError) as error:
            st.error(f"Chart unavailable; no signal generated: {error}")
        finally:
            client.session.close()
    snapshot = st.session_state.get("chart_snapshot")
    if not snapshot or snapshot["selection"] != selection:
        st.info("No matching chart snapshot.")
        return
    candles, analyses = snapshot["candles"], snapshot["analyses"]
    latest = analyses[-1]
    close_time = datetime.fromtimestamp((int(candles[-1]["time"]) + INTERVAL_MS[interval]) / 1000, timezone.utc)
    stale = (datetime.now(timezone.utc) - close_time).total_seconds() > INTERVAL_MS[interval] / 1000
    if stale:
        st.warning("Snapshot is outdated. Refresh before assessing a new setup.")
    st.caption(f"Last closed candle {close_time.isoformat(timespec='minutes')} · fetched {snapshot['fetched']}")
    label = {"LONG": "CALL BIAS", "SHORT": "PUT BIAS", "NO TRADE": "NO TRADE"}[latest.action] if product == "Options" else {
        "LONG": "BUY / LONG", "SHORT": "SELL / SHORT", "NO TRADE": "NO TRADE"}[latest.action]
    st.subheader(f"{'Snapshot: ' if stale else ''}{label}")
    st.caption(latest.reason)
    st.plotly_chart(candle_figure(candles, analyses, interval, options=product == "Options"),
                    width="stretch", config={"displaylogo": False, "scrollZoom": False,
                                              "modeBarButtonsToRemove": ["select2d", "lasso2d"]}, key="candle_chart")
    st.markdown('<div class="risk-grid">' + ''.join(
        f'<div><span>{label}</span><strong>{display_number(value, decimals)}</strong></div>'
        for label, value, decimals in [("RSI 14", latest.rsi, 1), ("EMA 20", latest.ema_fast, 4),
                                      ("EMA 50", latest.ema_slow, 4)]) + '</div>', unsafe_allow_html=True)
    if product != "Options" and latest.action != "NO TRADE":
        st.markdown('<div class="risk-grid">' + ''.join(
            f'<div><span>{label}</span><strong>{display_number(value)}</strong></div>'
            for label, value in [("Entry", latest.entry), ("Stop", latest.stop), ("Target", latest.target)])
            + '</div>', unsafe_allow_html=True)
    if product == "Options":
        st.subheader("Contract watchlist")
        st.caption("CALL/PUT bias is underlying direction only. WATCH labels also require current contract liquidity and delta filters; not premium entry/exit signals.")
        if snapshot.get("contract_error"):
            st.warning(f"Contract quotes unavailable: {snapshot['contract_error']}")
        else:
            st.markdown('<div class="signal-grid">' + ''.join(signal_card(row) for row in snapshot.get("contracts", [])) + '</div>',
                        unsafe_allow_html=True)
            st.caption("Contract quotes are a fetched snapshot, not streaming; refresh before using. Options may lose their full premium.")
    with st.expander("Research rules"):
        st.write("Markers show new EMA20/EMA50 and RSI14 setups, computed only after each candle closes. Historical markers are not fills, exits, or a backtest. Entry is the candle close; stop/target are 1.5/3 ATR for spot/futures only. Fees, funding and slippage are not modeled.")
        if product == "Spot":
            st.caption("Spot is buy-only research. Sell/short setups are available on Futures; no position-aware exit rule exists.")
        st.caption("On-demand snapshots · no automated orders · no guarantee of profitability.")