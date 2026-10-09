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
from maxtrade.scanner import QUOTE_CURRENCIES, scan_futures, scan_spot
from maxtrade.settings import credential_status
from maxtrade.auth import sign_out, require_login


st.set_page_config(page_title="MaxTrade | Signal Desk", page_icon="M", layout="wide")
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');
    @import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded');
    :root { --ink: #192b30; --muted: #64767b; --paper: #f3f6f8; --line: #dce5e8; --lime: #137b69; }
    .stApp { background: linear-gradient(180deg, #e8f0f3 0, var(--paper) 240px); color: var(--ink); }
    .stApp, .stApp input, .stApp button, .stApp select { font-family: 'IBM Plex Sans', sans-serif; letter-spacing: 0; }
    .block-container { padding: 3.1rem 1.25rem 2rem; max-width: 1560px; }
    h1, h2, h3 { letter-spacing: 0 !important; }
    h3 { font-size: 1.25rem !important; }
    [data-testid="stTextInput"] input { min-height: 44px; }
    .st-key-login_form { max-width: 440px; margin: .5rem auto; border-radius: 8px; background: #fff; padding: 1.25rem; }
    .st-key-paper_settings { max-width: 640px; border: 0; border-top: 1px solid var(--line); border-radius: 0; padding: .65rem 0; }
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
        .st-key-navigation [role="tab"]:nth-child(1)::before { content: 'radar' / ''; }
        .st-key-navigation [role="tab"]:nth-child(2)::before { content: 'candlestick_chart' / ''; }
        .st-key-navigation [role="tab"]:nth-child(3)::before { content: 'history' / ''; }
        .st-key-navigation [role="tab"]:nth-child(4)::before { content: 'tune' / ''; }
        [role="tab"][aria-selected="true"] { background: #e1f2ed; color: #137b69; }
        [role="tab"]:focus-visible { outline: 2px solid #137b69; outline-offset: 2px; }
        [data-testid="stExpander"] { border-radius: 6px; border-color: var(--line); }
        [data-testid="stExpander"] summary { min-height: 44px; }
        @media (min-width: 641px) {
            [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] { display: none !important; }
        }
        @media (max-width: 640px) {
            .st-key-desktop_logout { display: block; }
            .block-container { padding: 3.8rem .9rem 6rem; }
            .desk-title { font-size: 1.4rem; }
            .signal-grid { grid-template-columns: minmax(0, 1fr); }
            [data-testid="stHorizontalBlock"] { flex-wrap: wrap; gap: .5rem; }
            [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { min-width: min(100%, 140px) !important; flex: 1 1 140px !important; width: auto !important; }
            .st-key-navigation [role="tablist"] { position: fixed; bottom: 0; left: 0; right: 0; z-index: 999; background: #fff; border-top: 1px solid var(--line); padding: .25rem .5rem calc(.25rem + env(safe-area-inset-bottom)); box-shadow: 0 -4px 18px #192b3008; }
            [data-baseweb="tab-highlight"], [data-baseweb="tab-border"], .react-aria-SelectionIndicator { display: none; }
            .st-key-navigation [role="tab"] { min-height: 58px; border-radius: 6px; font-size: .75rem; flex-direction: column; gap: .15rem; }
            [role="tab"] p { font-size: .7rem; margin: 0; }
            [role="tab"][aria-selected="true"] { background: #e1f2ed; color: #137b69; }
            button { min-height: 44px; }
        }
    .stApp { background: linear-gradient(180deg, #edf2f4 0, #f7f9fa 160px); }
    .stApp p, .stApp li, .stApp label, .stApp input { font-size: .8125rem; line-height: 1.5; }
    .stApp h1 { font-size: 1.5rem; }
    .stApp h2 { font-size: 1.125rem; }
    .stApp h3 { font-size: 1rem !important; }
    .stApp h4 { font-size: .875rem; }
    .stApp h1, .stApp h2, .stApp h3, .stApp h4 { padding: .35rem 0; }
    [data-testid="stVerticalBlock"] { gap: .5rem; }
    .desk-header { padding: .25rem 0 .65rem; gap: .5rem; }
    .brand-mark { flex-basis: 32px; height: 32px; border-radius: 6px; }
    .brand-mark .app-icon { font-size: 22px; }
    .desk-title { font-size: 1.25rem; }
    .desk-subtitle { margin-top: .1rem; font-size: .6875rem; }
    .stApp button { min-height: 36px; border-radius: 4px; }
    [data-testid="stButton"] button[kind="primary"] { min-height: 38px; }
    [data-testid="stSelectbox"] [data-baseweb="select"] > div { min-height: 38px; border-radius: 4px; }
    [data-testid="stRadio"] [data-testid="stRadioOption"] { min-height: 36px; padding: .3rem .5rem; border-radius: 4px; }
    [data-testid="stMetric"] { background: transparent !important; border: 0 !important; border-left: 2px solid var(--line) !important; padding: .25rem .65rem !important; border-radius: 0 !important; min-width: 0; }
    [data-testid="stMetricValue"] { font-size: 1.125rem !important; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
    [data-testid="stMetricLabel"] p { font-size: .6875rem; color: var(--muted); white-space: normal; }
    [data-testid="stMetricDelta"] { font-size: .6875rem; }
    [data-testid="stCaptionContainer"] p { font-size: .6875rem; }
    [data-testid="stAlert"] { padding: .5rem .65rem; border-radius: 4px; }
    [data-testid="stAlert"] p { font-size: .75rem; }
    [data-testid="stExpander"] summary { min-height: 38px; padding: .35rem .65rem; }
    [data-testid="stExpander"] summary p { font-size: .8125rem; font-weight: 500; }
    [data-testid="stDataFrame"] { border-radius: 4px; }
    .stApp button:focus-visible, .stApp summary:focus-visible { outline: 2px solid var(--lime); outline-offset: 2px; }
    @media (prefers-reduced-motion: reduce) {
        .stApp *, .stApp *::before, .stApp *::after { animation-duration: .01ms !important; transition-duration: .01ms !important; scroll-behavior: auto !important; }
    }
    .st-key-navigation > [role="tablist"], .st-key-navigation [role="tablist"] { gap: .25rem; }
    [role="tab"] { min-height: 40px; border-radius: 4px; }
    [role="tab"] p { font-size: .8125rem; }
    .signal-grid { grid-template-columns: repeat(auto-fit, minmax(min(100%, 290px), 1fr)); }
    .signal-card { padding: .65rem; border-radius: 6px; }
    .card-price { font-size: 1.125rem; margin: .4rem 0; font-variant-numeric: tabular-nums; }
    .summary { padding: .5rem 0; }
    .summary strong { font-size: 1rem; }
    .stApp [data-testid="stMarkdownContainer"] { overflow-wrap: anywhere; }
    @media (max-width: 640px) {
        .block-container { padding: 3.1rem .65rem 6rem; }
        .desk-title { font-size: 1.125rem; }
        .desk-brand { gap: .5rem; }
        [data-testid="stMetricValue"] { font-size: 1rem !important; }
        .stApp button, [data-testid="stButton"] button[kind="primary"],
        [data-testid="stSelectbox"] [data-baseweb="select"] > div,
        [data-testid="stRadio"] [data-testid="stRadioOption"],
        [data-testid="stExpander"] summary { min-height: 44px; }
        [role="tab"] { min-height: 54px; }
        [role="tab"] p { font-size: .6875rem; }
        [data-testid="stPlotlyChart"] { overflow: hidden; }
    }
    :root { --ink: #202631; --muted: #76808f; --paper: #f7f8fa; --line: #e5e8ee; --lime: #2864ef; }
    html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {max-width:100%;overflow-x:clip;overscroll-behavior-x:none;}
    .block-container, [data-testid="stColumn"], [data-testid="stVerticalBlock"] {min-width:0;}
    .desk-brand > div:last-child {min-width:0;}
    .desk-title, .desk-subtitle {overflow-wrap:anywhere;}
    .brand-mark {font-size:20px;font-weight:700;line-height:1;user-select:none;}
    .block-container:has(.st-key-login_form) {max-width:520px;padding:1.5rem 1rem;min-height:100svh;}
    .block-container:has(.st-key-login_form) .desk-header {margin:0;padding:.75rem 0;}
    .block-container:has(.st-key-login_form) .research-status {display:none;}
    .st-key-login_form {width:100%;max-width:440px;margin:1.5rem auto 0;box-sizing:border-box;border:1px solid var(--line);padding:1.25rem;}
    .st-key-login_form input {font-size:16px;min-height:48px;}
    .st-key-login_form button {min-height:48px;}
    .st-key-login_form [data-testid="stCheckbox"] p {font-size:.8125rem;overflow-wrap:anywhere;}
    @media (max-width:640px) {
        .stApp input, .stApp textarea, [data-baseweb="select"] input {font-size:16px !important;}
        .block-container:has(.st-key-login_form) {padding:calc(.75rem + env(safe-area-inset-top)) 1rem calc(1rem + env(safe-area-inset-bottom));}
        .st-key-login_form {padding:1rem;margin-top:1rem;}
    }
    header[data-testid="stHeader"], [data-testid="stToolbar"], #MainMenu, footer { display: none !important; }
    .stApp { background: #f7f8fa; }
    .block-container { max-width: 1600px; padding-top: .75rem; }
    .desk-header { background: #fff; margin: 0 -1.25rem; padding: .75rem 1.25rem; }
    .brand-mark { background: #2864ef; }
    .research-status { color: #2864ef; background: #edf3ff; }
    .st-key-navigation [role="tablist"] { background: #fff; margin-bottom: .5rem; border-bottom: 1px solid var(--line); }
    .st-key-navigation [role="tab"] { flex: 0 0 auto; min-width: 110px; border-radius: 0; }
    .st-key-navigation [role="tab"][aria-selected="true"] { background: transparent; color: #2864ef; border-bottom: 2px solid #2864ef; }
    [data-testid="stRadioOption"] { background: transparent !important; border: 1px solid var(--line); }
    [data-testid="stRadioOption"]:has(input:checked) { background: #edf3ff !important; color: #2864ef !important; border-color: #2864ef; }
    [data-testid="stButton"] button[kind="primary"] { background: #2864ef; border-color: #2864ef; }
    .signal-card { border: 1px solid #d5dfe5; border-radius: 8px; background:linear-gradient(135deg,#ffffff,#edf2f6); box-shadow:inset 0 1px 0 #fff,0 3px 10px #192b3010; }
    .signal-card.signal-long {background:linear-gradient(135deg,#ffffff,#d4f2e2);border-color:#91ccb0;}
    .signal-card.signal-short {background:linear-gradient(135deg,#ffffff,#fbd8df);border-color:#e9a3b1;}
    .signal-card.signal-error {background:linear-gradient(135deg,#fff,#ffe7cf);border-color:#dcb68d;}
    .desk-header, [data-testid="stMetric"], [data-testid="stSidebar"] {background:linear-gradient(135deg,#fff,#eef3f7);}
    .brand-mark {background:linear-gradient(145deg,#4387fa,#1851c9);box-shadow:inset 0 1px 0 #ffffff80;}
    .st-key-signals_controls [data-testid="stHorizontalBlock"], [class*="_signal_tools"] [data-testid="stHorizontalBlock"] {align-items:flex-end;}
    @media(max-width:640px) {.st-key-signals_controls [data-testid="stColumn"] {flex:1 1 calc(50% - .5rem) !important;min-width:0 !important;}}
    .st-key-navigation [role="tablist"] {position:fixed;bottom:0;left:0;right:0;z-index:999;margin:0;padding:.25rem .5rem calc(.25rem + env(safe-area-inset-bottom));border-top:1px solid var(--line);justify-content:center;}
    .st-key-navigation [role="tab"] {flex:1;max-width:240px;min-width:0;min-height:54px;}
    .block-container {padding-bottom:calc(6rem + env(safe-area-inset-bottom));}
    .st-key-chart_product {position:fixed;top:0;left:58px;right:12px;width:calc(100% - 70px) !important;z-index:998;height:58px;display:flex;align-items:center;background:transparent !important;margin-top:0 !important;}
    .block-container:has(.st-key-chart_product) {padding-top:70px;}
    .block-container:has(.st-key-chart_product) .desk-header {position:fixed;top:0;left:0;right:0;height:58px;box-sizing:border-box;margin:0;z-index:997;}
    .st-key-chart_product [role="radiogroup"] {gap:.25rem;}
    .st-key-chart_product, .st-key-chart_interval { background: #fff; }
    [data-testid="stPlotlyChart"] { border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
    @media (max-width: 640px) {
        .desk-header { margin: 0 -.65rem; padding: .65rem; }
        .block-container { padding-top: .35rem; padding-bottom: calc(6rem + env(safe-area-inset-bottom)); }
        .st-key-navigation [role="tablist"] { margin-bottom: 0; }
        .st-key-chart_product { margin-top: -.25rem; }
        .st-key-navigation [role="tab"] { flex: 1; min-width: 0; }
        .market-ohlc { gap: 1rem; }
    }
    </style>
    <div class="desk-header"><div class="desk-brand"><div class="brand-mark" role="img" aria-label="MaxTrade">M</div></div></div>
    """,
    unsafe_allow_html=True,
)
require_login()

if st.query_params.get('view') == 'chart':
    st.session_state['navigation'] = 'Chart'
    st.query_params.pop('view', None)

def select_page(page: str) -> None:
    st.session_state["navigation"] = page

with st.sidebar:
    st.markdown('<div class="desk-header"><div class="desk-brand"><div class="brand-mark" role="img" aria-label="MaxTrade">M</div></div></div>', unsafe_allow_html=True)
    for page, icon in [("Signals", "radar"), ("Chart", "candlestick_chart"), ("History", "history"), ("Settings", "tune")]:
        st.button(page, icon=f":material/{icon}:", key=f"menu_{page.lower()}",
                  type="primary" if st.session_state.get("navigation", "Signals") == page else "secondary",
                  width="stretch", on_click=select_page, args=(page,))
    if st.button("Sign out", icon=":material/logout:", key="auth_logout", width="stretch"):
        sign_out()
        st.rerun()

signals_tab, chart_tab, history_tab, api_tab = st.tabs(["Signals", "Chart", "History", "Settings"], key="navigation", on_change="rerun")
with signals_tab:
    product = st.radio("Market type", ["Spot", "Futures", "Options"], horizontal=True, label_visibility="collapsed", key="market_type", width="stretch")
    with st.container(key='signals_controls'):
        controls = st.columns([1, 1, 1])
    interval = controls[0].selectbox("Timeframe", ["1h", "4h"])
    limit = controls[1].selectbox("Scan limit", [5, 10, 15, 20, 25, 30], index=1)
    currency = controls[2].selectbox("Underlying", ["BTC", "ETH"]) if product == "Options" else None
    selected_market = None
    if product != 'Options':
        catalog_key = f'signal_catalog_{product}'
        catalog = st.session_state.get(catalog_key, ['BTCUSDT', 'ETHUSDT'] if product == 'Spot' else ['B-BTC_USDT', 'B-ETH_USDT'])
        selected_market = controls[2].selectbox('Coin', ['All markets', *catalog], key=f'signal_coin_{product}')
        if st.button('Refresh coins', icon=':material/refresh:', key='signal_coins_refresh'):
            client = CoinDCXClient()
            try:
                if product == 'Spot':
                    catalog = sorted({item['coindcx_name'] for item in client.spot_markets()
                                      if item.get('status') == 'active' and item.get('base_currency_short_name') in QUOTE_CURRENCIES
                                      and item.get('coindcx_name')})
                else:
                    catalog = sorted(set(client.futures_instruments()))
                st.session_state[catalog_key] = catalog
                st.rerun()
            except Exception as error:
                st.warning(f'Coin list unavailable: {error}')
            finally:
                client.session.close()
        selected_market = None if selected_market == 'All markets' else selected_market
    gold_only = st.selectbox("Asset group", ["All markets", "Gold-backed tokens"], key="scan_asset_group") == "Gold-backed tokens" if product != "Options" else False
    if gold_only:
        st.caption("PAXG / XAUT · gold-backed tokens · active CoinDCX markets only, not gold options")
    if product == "Options":
        st.caption(f"Deribit · 7–45 day expiry · premiums in {currency}")
    else:
        st.caption(f"CoinDCX · {product.lower()} · {interval} candles")
    scan = st.button("Scan markets now", type="primary", width="stretch", icon=":material/radar:")

def show_signals(rows: list[dict], key: str) -> None:
    with st.container(key=f'{key}_signal_tools'):
        tools = st.columns([1, 1])
    view = tools[0].radio("Display", ["List", "Cards", "Table"], horizontal=True, key=f"{key}_view", label_visibility="collapsed", width="stretch")
    selected_filter = tools[1].selectbox("Signal filter", ["All signals", "Candidates", "No trade", "Data errors"], key=f"{key}_filter", label_visibility="collapsed")
    actions = {"Candidates": {"LONG", "SHORT", "WATCH CALL", "WATCH PUT"}, "No trade": {"NO TRADE"}, "Data errors": {"DATA ERROR"}}
    visible = rows if selected_filter == "All signals" else [row for row in rows if row.get("Signal") in actions[selected_filter]]
    if not visible:
        st.info("No matching signals in this snapshot.")
        return
    if view == 'List':
        from maxtrade.presentation import render_market_list
        render_market_list(visible, key)
    elif view == "Cards":
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
                results = scan_spot(client, interval, limit, progress=report_progress, **({"gold_only": True} if gold_only else {}), **({'market': selected_market} if selected_market else {}))
            else:
                results = scan_futures(client, interval, limit, progress=report_progress, **({"gold_only": True} if gold_only else {}), **({'market': selected_market} if selected_market else {}))
            st.session_state["scan_results"] = results
            st.session_state["scan_product"] = product
            st.session_state["scan_interval"] = interval
            st.session_state["scan_limit"] = limit
            st.session_state["scan_currency"] = currency
            st.session_state["scan_gold_only"] = gold_only
            st.session_state['scan_market'] = selected_market
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

 if product == 'Options':
    render_option_chain(currency, 'signals')

 results = st.session_state.get("scan_results", [])
 if results:
    if (
        st.session_state.get("scan_product") != product
        or st.session_state.get("scan_interval") != interval
        or st.session_state.get("scan_limit") != limit
        or st.session_state.get("scan_currency") != currency
        or st.session_state.get("scan_gold_only", False) != gold_only
        or st.session_state.get('scan_market') != selected_market
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

def render_analysis_records() -> None:
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
                st.session_state['records_product'] = snapshot['product']
                st.session_state['records_interval'] = snapshot['interval']
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

with history_tab:
    history_view = st.radio('History view', ['PAPER Positions', 'Records'], horizontal=True,
                           key='history_view', label_visibility='collapsed', width='stretch')
    if history_view == 'PAPER Positions':
        from maxtrade.research import render_paper_positions
        render_paper_positions()
    else:
        render_analysis_records()

with api_tab:
    from maxtrade.research import render_paper_account
    from maxtrade.azure_ai import render_azure_settings
    from maxtrade.notifications import render_telegram_settings

    render_paper_account()
    st.markdown("#### Connections")
    with st.expander("AI research", icon=":material/psychology:"):
        render_azure_settings()
    with st.expander("Telegram alerts", icon=":material/notifications:"):
        render_telegram_settings()
    with st.expander("Exchange configuration", icon=":material/settings_ethernet:"):
        try:
            local_secrets = st.secrets.to_dict()
        except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
            local_secrets = {}
        st.info(credential_status(secrets=local_secrets))
        st.caption("Configuration only. Credentials are not sent to CoinDCX; authentication and order execution are disabled.")
        st.warning("Never share or commit API keys. Withdrawal permissions must remain disabled.")
    if st.button("Sign out", icon=":material/logout:", key="desktop_logout"):
        sign_out()
        st.rerun()

st.caption("Research only · No orders · Public market scans")