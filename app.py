from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from html import escape

import pandas as pd
import streamlit as st

from maxtrade.coindcx import CoinDCXClient
from maxtrade.history import ScanHistory
from maxtrade.options import DeribitClient, scan_options
from maxtrade.presentation import signal_card
from maxtrade.scanner import scan_futures, scan_spot
from maxtrade.settings import credential_status


st.set_page_config(page_title="MaxTrade | Signal Desk", page_icon="M", layout="wide")
st.markdown(
    """
    <style>
    :root { --ink: #172c27; --muted: #697871; --paper: #f4f5ef; --line: #dce2d9; --lime: #c8ef62; }
    .stApp { background: var(--paper); color: var(--ink); }
    .block-container { padding: 3.5rem 1.2rem 2rem; max-width: 1050px; }
    [data-testid="stVerticalBlock"] { gap: .65rem; }
    header[data-testid="stHeader"] { background: transparent; }
    .eyebrow { color: #57705f; font-size: .72rem; font-weight: 700; letter-spacing: .12em; }
    .desk-title { color: var(--ink); font-size: 1.65rem; line-height: 1.1; font-weight: 760; }
    .desk-subtitle { color: var(--muted); margin: .3rem 0 .6rem; font-size: .85rem; }
    .notice { padding: .85rem 1rem; border-left: 3px solid #96bc36; background: #eaf0df; color: #334537; }
    div[data-testid="stMetric"] { background: #fff; border: 1px solid var(--line); padding: .8rem 1rem; border-radius: 6px; }
    div[data-testid="stDataFrame"] { border: 1px solid var(--line); }
        .signal-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .65rem; }
        .signal-card { background: white; border: 1px solid var(--line); border-radius: 14px; padding: .9rem; min-width: 0; overflow-wrap: anywhere; }
        .card-top { display: flex; justify-content: space-between; align-items: center; gap: .5rem; }
        .badge { font-size: .65rem; font-weight: 700; padding: .25rem .45rem; border-radius: 20px; white-space: nowrap; }
        .long { color: #235334; background: #e2f3e4; } .short, .error { color: #87362e; background: #fce9e5; }
        .neutral { color: #52615c; background: #eef1ed; }
        .card-price { font-size: 1.3rem; margin: .55rem 0; font-weight: 650; }
        .card-price small { float: right; font-size: .75rem; font-weight: 400; color: var(--muted); }
        .risk-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .5rem; }
        .risk-grid span, .risk-grid strong { display: block; font-size: .75rem; }
        .risk-grid span { color: var(--muted); }
        .signal-card p { font-size: .75rem; color: var(--muted); margin: .65rem 0 0; }
        .summary { display: flex; flex-wrap: wrap; gap: .4rem; margin: .25rem 0; }
        .summary span { background: #eaf0df; border-radius: 8px; padding: .4rem .6rem; font-size: .8rem; }
        @media (max-width: 640px) {
            .block-container { padding: 3.5rem .7rem 1.5rem; }
            .desk-title { font-size: 1.4rem; }
            .signal-grid { grid-template-columns: minmax(0, 1fr); }
            [data-testid="stHorizontalBlock"] { flex-wrap: wrap; gap: .4rem; }
            [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { min-width: 0 !important; flex: 1 1 100% !important; width: 100% !important; }
            button { min-height: 44px; }
        }
    </style>
    <div class="eyebrow">MAXTRADE / RESEARCH MODE</div>
    <div class="desk-title">MaxTrade Signal Desk</div>
    <div class="desk-subtitle">CoinDCX spot/futures · Deribit options · research only</div>
    """,
    unsafe_allow_html=True,
)

controls = st.columns([2, 1, 1])
product = controls[0].selectbox("Market type", ["Spot", "Futures", "Options"])
interval = controls[1].selectbox("Candle interval", ["1h", "4h"])
limit = controls[2].select_slider("Markets to inspect", options=[5, 10, 15, 20, 25, 30], value=10)

currency = st.selectbox("Options underlying", ["BTC", "ETH"]) if product == "Options" else None
if product == "Options":
    st.caption(f"Deribit · {currency} options · 7–45 days to expiry · {interval} underlying trend · premiums in {currency}")
else:
    st.caption(f"Selected: {product} · {interval} · up to {limit} markets. Scans run on demand, not automatically.")
scan = st.button("Scan markets now", type="primary", width="stretch")
signals_tab, history_tab, api_tab = st.tabs(["Signals", "History", "API setup"])

def show_signals(rows: list[dict], key: str) -> None:
    view = st.radio("Display", ["Cards", "Table"], horizontal=True, key=f"{key}_view")
    if view == "Cards":
        st.markdown('<div class="signal-grid">' + ''.join(signal_card(row) for row in rows) + '</div>', unsafe_allow_html=True)
    else:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

with signals_tab:

 if scan:
     source = "Deribit" if product == "Options" else "CoinDCX"
     with st.spinner(f"Scanning {product.lower()} markets on {source}…"):
        progress_bar = st.progress(0, text="Discovering active markets and loading tickers…")
        def report_progress(done: int, total: int, market: str) -> None:
            progress_bar.progress(done / total, text=f"Checked {done}/{total}: {market}")

        client = DeribitClient() if product == "Options" else CoinDCXClient()
        try:
            if product == "Options":
                results = scan_options(client, currency, interval, limit, progress=report_progress)
            elif product == "Spot":
                results = scan_spot(client, interval, limit, progress=report_progress)
            else:
                results = scan_futures(client, interval, limit, progress=report_progress)
            st.session_state["scan_results"] = results
            st.session_state["scan_product"] = product
            st.session_state["scan_interval"] = interval
            st.session_state["scan_limit"] = limit
            st.session_state["scan_currency"] = currency
            st.session_state["scan_time"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            try:
                scan_id = ScanHistory().save(product, interval, limit, st.session_state["scan_time"], results)
                st.success(f"Saved locally as scan #{scan_id}.")
            except (OSError, sqlite3.Error, ValueError) as error:
                st.warning(f"Scan completed, but history could not be saved: {error}")
        except Exception as error:
            st.error(f"Market scan failed: {error}")
            st.session_state.pop("scan_results", None)
        finally:
            client.session.close()
            progress_bar.empty()

 results = st.session_state.get("scan_results", [])
 if results:
    if (
        st.session_state.get("scan_product") != product
        or st.session_state.get("scan_interval") != interval
        or st.session_state.get("scan_limit") != limit
        or st.session_state.get("scan_currency") != currency
    ):
        st.info("Settings changed. Run a scan to load matching signals.")
    else:
        frame = pd.DataFrame(results)
        long_count = int(frame["Signal"].eq("LONG").sum())
        short_count = int(frame["Signal"].eq("SHORT").sum())
        no_trade_count = int(frame["Signal"].eq("NO TRADE").sum())
        error_count = int(frame["Signal"].eq("DATA ERROR").sum())
        st.markdown(f'<div class="summary"><span>{len(frame)} markets</span><span>{long_count} long</span><span>{short_count} short</span><span>{no_trade_count} no trade</span><span>{error_count} errors</span></div>', unsafe_allow_html=True)
        if product == "Options":
            watch_count = int(frame["Signal"].isin(["WATCH CALL", "WATCH PUT"]).sum())
            st.caption(f"{watch_count} options research candidates · Deribit source · no CoinDCX options execution")
        st.caption(f"Snapshot completed: {st.session_state['scan_time']} (UTC). Not auto-refreshing.")
        if error_count:
            st.warning("Some markets have unavailable or invalid data. Those rows contain no trade signal.")
        show_signals(results, "live")
        with st.expander("Signal assumptions"):
            if product == "Options":
                st.caption("WATCH CALL/PUT: Deribit perpetual EMA/RSI trend aligned with option direction, 7–45 days to expiry, absolute delta 0.25–0.75, spread ≤10%, open interest ≥10 base coins and positive 24h volume. Premiums/bid/ask are in BTC or ETH; strikes are USD. IV and Greeks are exchange estimates. No option fair-value model, premium stop/target, fees or backtest. A candidate is not a recommendation; options can lose their full premium.")
            else:
                st.caption("Price: spot last / futures mark. Entry: completed candle close. Stop/target: 1.5× / 3× ATR. Raw volumes are not comparable across quotes. Fees, funding and slippage are not modeled. Signals are not execution instructions.")
        st.download_button(
            "Download scan CSV",
            data=frame.assign(Product=product, Interval=interval, ScannedAtUTC=st.session_state["scan_time"]).to_csv(index=False).encode("utf-8"),
            file_name=f"maxtrade-{product.lower()}-{interval}.csv",
            mime="text/csv",
        )
 else:
    st.markdown("#### Awaiting scan")
    st.write("Choose a market type and run a scan to load current research results.")

with history_tab:
    st.caption("Local snapshots—not executed trades or performance records. Latest 50 scans shown.")
    try:
        history = ScanHistory()
        summaries = history.recent()
        if summaries:
            labels = {row["id"]: f"#{row['id']} · {row['scanned_at']} · {row['product']} / {row['interval']} · {row['markets']} markets" for row in summaries}
            selected = st.selectbox("Saved snapshot", list(labels), format_func=labels.get)
            snapshot = history.load(selected)
            if snapshot is not None:
                archived = pd.DataFrame(snapshot["results"])
                show_signals(snapshot["results"], "history")
                st.download_button(
                    "Download saved snapshot CSV",
                    archived.assign(Product=snapshot["product"], Interval=snapshot["interval"], ScannedAtUTC=snapshot["scanned_at"]).to_csv(index=False).encode("utf-8"),
                    file_name=f"maxtrade-scan-{selected}.csv", mime="text/csv", key="history_csv",
                )
        else:
            st.info("No saved scans yet. Run a market scan to create the first snapshot.")
    except (OSError, sqlite3.Error, ValueError) as error:
        st.warning(f"History is unavailable: {error}")

with api_tab:
    st.markdown("#### CoinDCX configuration")
    try:
        local_secrets = st.secrets.to_dict()
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        local_secrets = {}
    st.info(credential_status(secrets=local_secrets))
    st.caption("Configuration only. Credentials are not sent to CoinDCX; authentication and order execution are disabled.")
    st.markdown("Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and edit the copy locally. Alternatively set `COINDCX_API_KEY` and `COINDCX_API_SECRET` in the server environment. Restart the app afterward.")
    st.warning("Never paste keys into chat or commit the secrets file. Use the minimum exchange permissions available; do not grant withdrawal permissions. Do not add exchange keys to a public deployment until authentication and access controls are in place.")
    st.caption("This is a responsive web dashboard, not a native mobile app. Hosted links can be opened on a phone. Local scan history on cloud hosting may be lost when the app restarts or is redeployed, and is shared across app users.")

st.caption("Research only · No orders · Public market scans")