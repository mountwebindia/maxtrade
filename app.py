from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

import pandas as pd
import streamlit as st

from maxtrade.coindcx import CoinDCXClient
from maxtrade.chart_page import render_chart_page
from maxtrade.history import ScanHistory
from maxtrade.options import DeribitClient, scan_options, render_option_chain
from maxtrade.presentation import signal_card
from maxtrade.scanner import scan_futures, scan_spot
from maxtrade.settings import credential_status
from maxtrade.auth import clear_session, require_login


st.set_page_config(page_title="MaxTrade | Signal Desk", page_icon="M", layout="wide")
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');
    @import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded');
    :root { --ink: #192b30; --muted: #64767b; --paper: #f3f6f8; --line: #dce5e8; --lime: #137b69; }
    .stApp { background: linear-gradient(180deg, #e8f0f3 0, var(--paper) 240px); color: var(--ink); }
    .stApp, .stApp input, .stApp button, .stApp select { font-family: 'IBM Plex Sans', sans-serif; letter-spacing: 0; }
    .block-container { padding: 3.8rem 1.2rem 2rem; max-width: 1050px; }
    h1, h2, h3 { letter-spacing: 0 !important; }
    h3 { font-size: 1.25rem !important; }
    [data-testid="stTextInput"] input { min-height: 44px; }
    [data-testid="stForm"] { max-width: 440px; margin: .5rem auto; border-radius: 8px; background: #fff; padding: 1.25rem; }
    [data-testid="stSidebar"] { background: #fff; border-right: 1px solid var(--line); }
    [data-testid="stSidebar"] [data-testid="stSidebarContent"] { padding-top: 1rem; }
    [data-testid="stSidebar"] .desk-header { padding-bottom: 1.2rem; margin-bottom: 1rem; }
    [data-testid="stSidebar"] .desk-title { font-size: 1.4rem; }
    [data-testid="stSidebar"] button { justify-content: flex-start; min-height: 48px; border-radius: 6px; }
    [data-testid="stSidebar"] button[kind="secondary"] { border-color: transparent; background: transparent; }
    [data-testid="stSidebar"] button[kind="secondary"]:hover { background: #edf4f2; }
    .st-key-auth_logout { margin-top: 1.5rem; border-top: 1px solid var(--line); padding-top: .75rem; }
    .st-key-auth_logout button { color: #87362e; }
    [data-testid="stVerticalBlock"] { gap: .65rem; }
    header[data-testid="stHeader"] { background: #f3f6f8; }
    .desk-header { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: .6rem 0 1rem; border-bottom: 1px solid var(--line); }
    .desk-brand { display: flex; align-items: center; gap: .75rem; min-width: 0; }
    .brand-mark { display: grid; place-items: center; flex: 0 0 42px; height: 42px; background: #137b69; color: #fff; border-radius: 8px; }
    .app-icon { font-family: 'Material Symbols Rounded'; font-size: 25px; line-height: 1; font-weight: normal; }
    .desk-title { color: var(--ink); font-size: 1.65rem; line-height: 1.1; font-weight: 700; }
    .desk-subtitle { color: var(--muted); margin-top: .3rem; font-size: .75rem; }
    .research-status { color: #137b69; background: #e1f2ed; padding: .3rem .5rem; font-size: .7rem; font-weight: 600; border-radius: 4px; white-space: nowrap; }
    [data-testid="stRadio"] [role="radiogroup"] { width: 100%; gap: .25rem; flex-wrap: nowrap; }
    [data-testid="stRadio"] [role="radiogroup"] > div { flex: 1; min-width: 0; }
    [data-testid="stRadio"] [data-testid="stRadioOption"] { flex: 1 1 0; min-width: 0; width: 100%; justify-content: center; background: #e6edf0; padding: .5rem; border-radius: 6px; min-height: 44px; }
    [data-testid="stRadioOption"] > div { justify-content: center; }
    [data-testid="stRadioOption"] > div > div:not([data-testid="stMarkdownContainer"]) { display: none; }
    [data-testid="stRadioOption"]:has(input:checked) { background: #192b30; color: white; }
    [data-testid="stRadioOption"]:has(input:focus-visible) { outline: 2px solid #137b69; outline-offset: 2px; }
    [data-testid="stRadioOption"] p { font-size: .8rem; overflow-wrap: normal; }
    [data-testid="stButton"] button[kind="primary"] { background: #137b69; border-color: #137b69; min-height: 48px; border-radius: 6px; }
    [data-testid="stSelectbox"] p { font-size: .75rem; }
    [data-testid="stSelectbox"] [data-baseweb="select"] > div { border-radius: 6px; min-height: 44px; }
    .notice { padding: .85rem 1rem; border-left: 3px solid #96bc36; background: #eaf0df; color: #334537; }
    div[data-testid="stMetric"] { background: #fff; border: 1px solid var(--line); padding: .8rem 1rem; border-radius: 6px; }
    div[data-testid="stDataFrame"] { border: 1px solid var(--line); }
        .signal-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .65rem; }
        .signal-card { background: white; border: 1px solid var(--line); border-radius: 8px; padding: .85rem; min-width: 0; overflow-wrap: anywhere; }
        .card-top { display: flex; justify-content: space-between; align-items: center; gap: .5rem; }
        .card-top > strong { font-size: .85rem; min-width: 0; }
        .badge { font-size: .6rem; font-weight: 700; padding: .25rem .45rem; border-radius: 4px; white-space: nowrap; }
        .long { color: #235334; background: #e2f3e4; } .short, .error { color: #87362e; background: #fce9e5; }
        .neutral { color: #52615c; background: #eef1ed; }
        .card-price { font-size: 1.3rem; margin: .55rem 0; font-weight: 650; }
        .card-price { display: flex; align-items: baseline; justify-content: space-between; gap: .5rem; flex-wrap: wrap; }
        .card-price small { font-size: .75rem; font-weight: 400; color: var(--muted); }
        .risk-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .5rem; }
        .risk-grid > div { min-width: 0; }
        .risk-grid strong { overflow-wrap: anywhere; }
        .risk-grid span, .risk-grid strong { display: block; font-size: .75rem; }
        .risk-grid span { color: var(--muted); }
        .signal-card p { font-size: .75rem; color: var(--muted); margin: .65rem 0 0; }
        .signal-card details { border-top: 1px solid var(--line); margin-top: .7rem; padding-top: .4rem; }
        .signal-card summary { cursor: pointer; min-height: 44px; display: flex; align-items: center; justify-content: space-between; font-size: .75rem; color: var(--muted); }
        .signal-card summary::after { content: '+'; font-size: 1rem; }
        .signal-card details[open] summary::after { content: '-'; }
        .summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .5rem; padding: .75rem 0; border-bottom: 1px solid var(--line); }
        .summary span { font-size: .7rem; color: var(--muted); }
        .summary strong { display: block; font-size: 1.2rem; color: var(--ink); font-weight: 600; }
        .empty-state { padding: 2.5rem 0; text-align: center; color: var(--muted); }
        .empty-state .app-icon { display: block; font-size: 36px; color: #72999c; margin-bottom: .75rem; }
        .empty-state strong { display: block; color: var(--ink); font-size: 1.1rem; margin-bottom: .3rem; }
        [role="tablist"] { gap: .4rem; border-bottom: 1px solid var(--line); padding-bottom: .4rem; }
        [role="tab"] { flex: 1; min-height: 48px; justify-content: center; gap: .5rem; border-radius: 6px; color: var(--muted); }
        [role="tab"]::before { font-family: 'Material Symbols Rounded'; font-size: 23px; font-weight: normal; line-height: 1; }
        [role="tab"]:nth-child(1)::before { content: 'radar' / ''; }
        [role="tab"]:nth-child(2)::before { content: 'candlestick_chart' / ''; }
        [role="tab"]:nth-child(3)::before { content: 'history' / ''; }
        [role="tab"]:nth-child(4)::before { content: 'tune' / ''; }
        [role="tab"][aria-selected="true"] { background: #e1f2ed; color: #137b69; }
        [role="tab"]:focus-visible { outline: 2px solid #137b69; outline-offset: 2px; }
        [data-testid="stExpander"] { border-radius: 6px; border-color: var(--line); }
        [data-testid="stExpander"] summary { min-height: 44px; }
        @media (min-width: 641px) {
            [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] { display: none !important; }
        }
        @media (max-width: 640px) {
            .st-key-desktop_logout { display: none; }
            .block-container { padding: 3.8rem .9rem 6rem; }
            .desk-title { font-size: 1.4rem; }
            .signal-grid { grid-template-columns: minmax(0, 1fr); }
            [data-testid="stHorizontalBlock"] { flex-wrap: nowrap; gap: .5rem; }
            [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { min-width: 0 !important; flex: 1 1 0 !important; width: auto !important; }
            [role="tablist"] { position: fixed; bottom: 0; left: 0; right: 0; z-index: 999; background: #fff; border-top: 1px solid var(--line); padding: .25rem .5rem calc(.25rem + env(safe-area-inset-bottom)); box-shadow: 0 -4px 18px #192b3008; }
            [data-baseweb="tab-highlight"], [data-baseweb="tab-border"], .react-aria-SelectionIndicator { display: none; }
            [role="tab"] { min-height: 58px; border-radius: 6px; font-size: .75rem; flex-direction: column; gap: .15rem; }
            [role="tab"] p { font-size: .7rem; margin: 0; }
            [role="tab"][aria-selected="true"] { background: #e1f2ed; color: #137b69; }
            button { min-height: 44px; }
        }
    </style>
    <div class="desk-header"><div class="desk-brand"><div class="brand-mark" aria-hidden="true"><span class="app-icon">candlestick_chart</span></div><div><div class="desk-title">MaxTrade</div>
    <div class="desk-subtitle">CoinDCX / Deribit</div></div></div><span class="research-status">Research only</span></div>
    """,
    unsafe_allow_html=True,
)
require_login()

def select_page(page: str) -> None:
    st.session_state["navigation"] = page

with st.sidebar:
    st.markdown('<div class="desk-header"><div class="desk-brand"><div class="brand-mark" aria-hidden="true"><span class="app-icon">candlestick_chart</span></div><div><div class="desk-title">MaxTrade</div><div class="desk-subtitle">Research desk</div></div></div></div>', unsafe_allow_html=True)
    for page, icon in [("Signals", "radar"), ("Chart", "candlestick_chart"), ("History", "history"), ("Settings", "tune")]:
        st.button(page, icon=f":material/{icon}:", key=f"menu_{page.lower()}",
                  type="primary" if st.session_state.get("navigation", "Signals") == page else "secondary",
                  width="stretch", on_click=select_page, args=(page,))
    if st.button("Sign out", icon=":material/logout:", key="auth_logout", width="stretch"):
        clear_session()
        st.rerun()

signals_tab, chart_tab, history_tab, api_tab = st.tabs(["Signals", "Chart", "History", "Settings"], key="navigation", on_change="rerun")
with signals_tab:
    product = st.radio("Market type", ["Spot", "Futures", "Options"], horizontal=True, label_visibility="collapsed", key="market_type", width="stretch")
    controls = st.columns([1, 1, 1] if product == "Options" else [1, 1])
    interval = controls[0].selectbox("Timeframe", ["1h", "4h"])
    limit = controls[1].selectbox("Scan limit", [5, 10, 15, 20, 25, 30], index=1)
    currency = controls[2].selectbox("Underlying", ["BTC", "ETH"]) if product == "Options" else None
    gold_only = st.selectbox("Asset group", ["All markets", "Gold-backed tokens"], key="scan_asset_group") == "Gold-backed tokens" if product != "Options" else False
    if gold_only:
        st.caption("PAXG / XAUT · gold-backed tokens · active CoinDCX markets only, not gold options")
    if product == "Options":
        st.caption(f"Deribit · 7–45 day expiry · premiums in {currency}")
    else:
        st.caption(f"CoinDCX · {product.lower()} · {interval} candles")
    scan = st.button("Scan markets now", type="primary", width="stretch", icon=":material/radar:")
    if product == 'Options':
        render_option_chain(currency, 'signals')

def show_signals(rows: list[dict], key: str) -> None:
    tools = st.columns([1, 1])
    view = tools[0].radio("Display", ["Cards", "Table"], horizontal=True, key=f"{key}_view", label_visibility="collapsed", width="stretch")
    selected_filter = tools[1].selectbox("Signal filter", ["All signals", "Candidates", "No trade", "Data errors"], key=f"{key}_filter", label_visibility="collapsed")
    actions = {"Candidates": {"LONG", "SHORT", "WATCH CALL", "WATCH PUT"}, "No trade": {"NO TRADE"}, "Data errors": {"DATA ERROR"}}
    visible = rows if selected_filter == "All signals" else [row for row in rows if row.get("Signal") in actions[selected_filter]]
    if not visible:
        st.info("No matching signals in this snapshot.")
        return
    if view == "Cards":
        st.markdown('<div class="signal-grid">' + ''.join(signal_card(row) for row in visible) + '</div>', unsafe_allow_html=True)
    else:
        st.dataframe(pd.DataFrame(visible), hide_index=True, width="stretch")

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
                results = scan_spot(client, interval, limit, progress=report_progress, **({"gold_only": True} if gold_only else {}))
            else:
                results = scan_futures(client, interval, limit, progress=report_progress, **({"gold_only": True} if gold_only else {}))
            st.session_state["scan_results"] = results
            st.session_state["scan_product"] = product
            st.session_state["scan_interval"] = interval
            st.session_state["scan_limit"] = limit
            st.session_state["scan_currency"] = currency
            st.session_state["scan_gold_only"] = gold_only
            st.session_state["scan_time"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            try:
                scan_id = ScanHistory().save(product, interval, limit, st.session_state["scan_time"], results)
                st.caption(f"Snapshot #{scan_id} saved")
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
        or st.session_state.get("scan_gold_only", False) != gold_only
    ):
        st.info("Settings changed. Run a scan to load matching signals.")
    else:
        frame = pd.DataFrame(results)
        long_count = int(frame["Signal"].eq("LONG").sum())
        short_count = int(frame["Signal"].eq("SHORT").sum())
        no_trade_count = int(frame["Signal"].eq("NO TRADE").sum())
        error_count = int(frame["Signal"].eq("DATA ERROR").sum())
        actionable_count = long_count + short_count + int(frame["Signal"].isin(["WATCH CALL", "WATCH PUT"]).sum())
        st.markdown(f'<div class="summary"><span><strong>{len(frame)}</strong>Markets</span><span><strong>{actionable_count}</strong>Candidates</span><span><strong>{no_trade_count}</strong>No trade</span><span><strong>{error_count}</strong>Data errors</span></div>', unsafe_allow_html=True)
        if product == "Options":
            watch_count = int(frame["Signal"].isin(["WATCH CALL", "WATCH PUT"]).sum())
            st.caption(f"Deribit · {watch_count} watchlist candidates · no execution")
        st.caption(f"Updated {st.session_state['scan_time']} · UTC")
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
            icon=":material/download:",
        )
 else:
    st.markdown('<div class="empty-state"><span class="app-icon" aria-hidden="true">radar</span><strong>No snapshot yet</strong></div>', unsafe_allow_html=True)

with chart_tab:
    if chart_tab.open:
        render_chart_page()

with history_tab:
    st.caption("Local analysis records · Latest 50 scans shown.")
    try:
        history = ScanHistory()
        from maxtrade.accuracy import render_daily_accuracy
        render_daily_accuracy(history)
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
                    icon=":material/download:",
                )
        else:
            st.info("No saved scans yet. Run a market scan to create the first snapshot.")
        reports = history.recent_research()
        if reports:
            st.markdown("#### Research archive")
            archived_reports = {row["id"]: row["report"] for row in reports}
            saved = st.selectbox("Saved research", list(archived_reports),
                                 format_func=lambda report_id: f"#{report_id} · {archived_reports[report_id]['created_at']} · {archived_reports[report_id]['symbol']}")
            if saved is not None:
                st.json(archived_reports[saved], expanded=False)
        alerts = history.recent_alerts()
        st.markdown("#### Research alerts")
        if alerts:
            st.dataframe(alerts, hide_index=True, width="stretch")
        else:
            st.caption("No worker alerts recorded.")
    except (OSError, sqlite3.Error, ValueError) as error:
        st.warning(f"History is unavailable: {error}")

with api_tab:
    from maxtrade.research import render_paper_account
    from maxtrade.azure_ai import render_azure_settings
    from maxtrade.notifications import render_telegram_settings

    render_azure_settings()
    render_telegram_settings()
    render_paper_account()
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
    if st.button("Sign out", icon=":material/logout:", key="desktop_logout"):
        clear_session()
        st.rerun()

st.caption("Research only · No orders · Public market scans")