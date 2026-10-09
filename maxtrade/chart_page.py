from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import json
from urllib.parse import urlencode
import sqlite3

import requests
import pandas as pd
import streamlit as st

from maxtrade.charts import candle_figure, chart_analysis, signal_records
from maxtrade.backtest import ReplaySettings, replay
from maxtrade.coindcx import CoinDCXClient, INTERVAL_MS, normalize_candles
from maxtrade.options import DeribitClient, scan_options, render_option_chain
from maxtrade.presentation import display_number, signal_card
from maxtrade.scanner import QUOTE_CURRENCIES
from maxtrade.research import render_market_research
from maxtrade.auth import require_chart_login


def chart_workspace_url(product: str, pair: str, interval: str, preferences: dict) -> str:
    return '?' + urlencode({'view': 'chart', 'product': product, 'pair': pair, 'interval': interval,
                            'layout': json.dumps(preferences)})


def render_chart_page(workspace: bool = False) -> None:
    st.markdown('''<style>
        .block-container:has(.st-key-chart_market_header) > [data-testid="stVerticalBlock"] {gap:.45rem;}
        .block-container:has(.st-key-chart_market_header) .desk-header {display:none;}
        .block-container:has(.st-key-chart_market_header) [data-testid="stElementContainer"]:has(.desk-header) {display:none;}
        .st-key-chart_market_header [data-testid="stVerticalBlock"], .st-key-chart_toolbar [data-testid="stVerticalBlock"] {gap:0;}
        .st-key-chart_market_header button, .st-key-chart_toolbar button {min-height:36px !important;}
        .st-key-chart_market_header [data-baseweb="select"] > div {min-height:36px;}
        </style>''', unsafe_allow_html=True)
    if workspace:
        st.markdown('<style>.block-container {max-width: none; padding: 1rem;} .desk-header {display:none;} header[data-testid="stHeader"] {display:none;}</style>', unsafe_allow_html=True)
    product, interval, pair, live = render_chart_controls(workspace)
    st.fragment(run_every='10s' if live else None)(render_chart_snapshot)(product, interval, pair, live, workspace)
    if not workspace:
        render_market_research(product, pair)


def render_chart_controls(workspace: bool = False) -> tuple[str, str, str, bool]:
    if (workspace or 'layout' in st.query_params) and not st.session_state.get('chart_route_loaded'):
        product = st.query_params.get('product', 'Spot')
        interval = st.query_params.get('interval', '1h')
        if product in ('Spot', 'Futures', 'Options'):
            st.session_state['chart_product'] = product
        if interval in INTERVAL_MS:
            st.session_state['chart_interval'] = interval if interval in ('1m', '5m', '15m', '30m', '1h', '4h', '1d') else 'Custom'
            if st.session_state['chart_interval'] == 'Custom':
                st.session_state['chart_custom_minutes'] = INTERVAL_MS[interval] // 60_000
        pair = st.query_params.get('pair', '')
        if product == 'Options' and pair in ('BTC', 'ETH'):
            st.session_state['chart_underlying'] = pair
        elif pair.startswith('B-') and len(pair) <= 64 and all(character.isalnum() or character in '-_' for character in pair):
            name = pair[2:].replace('_', '') if product == 'Spot' else pair
            st.session_state[f'chart_catalog_{product}'] = {name: pair}
            st.session_state[f'chart_market_{product}'] = name
        try:
            preferences = json.loads(st.query_params.get('layout', '{}'))
            choices = {'chart_style': ['Candles', 'Line', 'Area'], 'chart_theme': ['Dark', 'Light'],
                       'chart_visible': [40, 80, 120], 'chart_log': [True, False],
                       'chart_signals': [True, False], 'chart_paper_fills': [True, False]}
            if isinstance(preferences, dict):
                for key, options in choices.items():
                    if preferences.get(key) in options:
                        st.session_state[key] = preferences[key]
                indicators = preferences.get('chart_indicators')
                if isinstance(indicators, list) and all(value in CHART_INDICATORS for value in indicators):
                    st.session_state['chart_indicators'] = indicators
        except (ValueError, TypeError):
            pass
        st.session_state['chart_route_loaded'] = True
    if workspace:
        st.markdown('<style>.block-container {max-width: none; padding: 1rem;} .desk-header {display:none;} header[data-testid="stHeader"] {display:none;}</style>', unsafe_allow_html=True)
    st.markdown('''<style>
        .st-key-chart_interval [role="radiogroup"], .st-key-chart_product [role="radiogroup"] {flex-wrap:wrap !important; gap: .35rem;}
        .st-key-chart_interval [role="radiogroup"] > div, .st-key-chart_product [role="radiogroup"] > div {flex: 0 0 auto !important; min-width: 44px !important;}
        .st-key-chart_interval [data-testid="stRadioOption"], .st-key-chart_product [data-testid="stRadioOption"] {min-height: 44px; padding: 0 .65rem; width: auto !important;}
        .st-key-chart_interval [data-testid="stRadioOption"] p, .st-key-chart_product [data-testid="stRadioOption"] p {white-space: nowrap;}
        .st-key-chart_refresh button {min-height: 40px;}
        </style>''', unsafe_allow_html=True)
    st.markdown('''<style>
        .st-key-chart_interval {overflow:clip;}
        .st-key-chart_interval [role="radiogroup"] {flex-wrap:nowrap !important;overflow-x:auto;gap:.15rem;}
        .st-key-chart_interval [data-testid="stRadioOption"], .st-key-chart_product [data-testid="stRadioOption"] {min-height:36px !important;padding:.15rem .45rem !important;border:0;}
        .st-key-chart_interval [data-testid="stRadioOption"] p, .st-key-chart_product [data-testid="stRadioOption"] p {margin:0;font-size:.75rem;}
        .st-key-chart_toolbar [data-testid="stHorizontalBlock"] {flex-wrap:nowrap !important;}
        .st-key-chart_toolbar [data-testid="stColumn"] {min-width:0 !important;}
        .st-key-chart_toolbar [data-testid="stColumn"]:first-child {flex:8 1 0 !important;}
        .st-key-chart_toolbar [data-testid="stColumn"]:not(:first-child) {flex:1 1 0 !important;}
        </style>''', unsafe_allow_html=True)
    product = st.radio("Chart market type", ["Spot", "Futures", "Options"], horizontal=True,
                       label_visibility="collapsed", key="chart_product", width="stretch")
    with st.container(key='chart_market_header'):
        header = st.columns([7, 1, 3])
    st.markdown('''<style>
        .st-key-chart_market_header [data-testid="stHorizontalBlock"] {flex-wrap:nowrap !important;align-items:center;gap:.4rem;}
        .st-key-chart_market_header [data-testid="stColumn"] {min-width:0 !important;}
        .st-key-chart_market_header [data-testid="stColumn"]:nth-child(1) {flex:7 1 0 !important;}
        .st-key-chart_market_header [data-testid="stColumn"]:nth-child(2) {flex:0 0 44px !important;}
        .st-key-chart_market_header [data-testid="stColumn"]:nth-child(3) {flex:0 0 90px !important;}
        .st-key-chart_product [role="radiogroup"] {border-bottom:1px solid #e5e8ee;}
        .st-key-chart_product [data-testid="stRadioOption"] {border-radius:0 !important;}
        .st-key-chart_product [data-testid="stRadioOption"]:has(input:checked) {border-bottom:2px solid #2864ef !important;}
        </style>''', unsafe_allow_html=True)
    live = header[2].toggle("Live · 10s", value=True, key="chart_live")
    intervals = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', 'Custom']
    controls = header[:2]
    if product == "Options":
        market = controls[0].selectbox("Chart underlying", ["BTC", "ETH"], key="chart_underlying", label_visibility='collapsed')
        pair = market
        st.caption(f"Deribit · {market}-PERPETUAL · USD underlying, not option premium")
    else:
        if controls[1].button("", icon=":material/search:", help='Browse markets', key="chart_browse"):
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
        default_catalog = {"BTCUSDT": "B-BTC_USDT", "ETHUSDT": "B-ETH_USDT"} if product == "Spot" else {
            "B-BTC_USDT": "B-BTC_USDT", "B-ETH_USDT": "B-ETH_USDT"}
        catalog = st.session_state.get(f"chart_catalog_{product}", default_catalog)
        names = sorted(catalog)
        preferred = "BTCUSDT" if product == "Spot" else "B-BTC_USDT"
        market = controls[0].selectbox("Chart market", names, index=names.index(preferred) if preferred in names else 0,
                          key=f"chart_market_{product}", label_visibility='collapsed')
        pair = catalog[market]
    if 'chart_interval' not in st.session_state:
        st.session_state['chart_interval'] = '1h'
    with st.container(key='chart_toolbar'):
        timeframe_controls, display_controls, layout_controls = st.columns([8, 1, 1])
    interval = timeframe_controls.radio("Chart timeframe", intervals, horizontal=True,
                                       label_visibility='collapsed', key="chart_interval", width="stretch")
    if interval == 'Custom':
        minutes = st.selectbox('Custom interval (minutes)', [2, 3, 45, 120, 180], key='chart_custom_minutes')
        interval = f'{minutes}m'
    with display_controls.popover('', icon=':material/tune:', help='Indicators & display', width='stretch'):
        toolbar = st.columns(3)
        toolbar[0].selectbox("Chart style", ["Candles", "Line", "Area"], key="chart_style")
        toolbar[1].selectbox("Chart theme", ["Light", "Dark"], key="chart_theme")
        toolbar[2].selectbox("Visible candles", [80, 40, 120], key="chart_visible")
        st.multiselect("Indicators", CHART_INDICATORS,
                       default=["EMA 20", "EMA 50", "Volume", "RSI 14"], key="chart_indicators")
        toggles = st.columns(3)
        toggles[0].toggle("Log price scale", key="chart_log")
        toggles[1].toggle('BUY / SELL setups', value=True, key='chart_signals')
        toggles[2].toggle('Saved paper fills', value=True, key='chart_paper_fills')
        st.selectbox('Time window', ['Latest candles', 'All loaded candles', 'Custom UTC range'], key='chart_window')
        if st.session_state['chart_window'] == 'Custom UTC range':
            dates = st.columns(2)
            dates[0].text_input('From (UTC)', placeholder='2026-10-07T00:00:00+00:00', key='chart_from')
            dates[1].text_input('To (UTC)', placeholder='2026-10-07T12:00:00+00:00', key='chart_to')
    preferences = {key: st.session_state[key] for key in ('chart_style', 'chart_theme', 'chart_visible',
                   'chart_indicators', 'chart_log', 'chart_signals', 'chart_paper_fills')}
    url = chart_workspace_url(product, pair, interval, preferences)
    if workspace:
        st.query_params.update({'product': product, 'pair': pair, 'interval': interval,
                                'layout': json.dumps(preferences)})
    with layout_controls.popover('', icon=':material/save:', help='Chart layout', width='stretch'):
        tools = st.columns(2)
        tools[0].link_button('Open chart in new tab', url, icon=':material/open_in_new:', width='stretch')
        if st.button('Save layout to URL', icon=':material/bookmark:', key='chart_bookmark'):
            st.query_params.update({'product': product, 'pair': pair, 'interval': interval,
                                    'layout': json.dumps(preferences)})
        tools[1].download_button('Save layout', json.dumps({'url': url, 'preferences': preferences}, indent=2),
                                 file_name='maxtrade-chart-layout.json', mime='application/json',
                                 icon=':material/save:', key='chart_layout_download', width='stretch')
        uploaded = st.file_uploader('Load saved layout', type=['json'], key='chart_layout_upload', max_upload_size=1)
        if uploaded is not None and st.session_state.get('chart_layout_imported') != uploaded.getvalue():
            try:
                document = json.loads(uploaded.getvalue())
                imported = document['preferences']
                if not isinstance(imported, dict):
                    raise ValueError('Invalid chart layout')
                st.query_params['layout'] = json.dumps(imported)
                st.session_state.pop('chart_route_loaded', None)
                st.session_state['chart_layout_imported'] = uploaded.getvalue()
                for key in preferences:
                    st.session_state.pop(key, None)
                st.rerun()
            except (ValueError, KeyError, TypeError):
                st.error('Invalid saved chart layout.')
    return product, interval, pair, live


CHART_INDICATORS = ['EMA 20', 'EMA 50', 'EMA 200', 'Bollinger Bands', 'VWAP (UTC day)',
                    'Support / resistance', 'Volume', 'RSI 14', 'MACD']


def render_trade_status(product: str, pair: str, latest, stale: bool) -> None:
    from maxtrade.paper import PaperLedger, coordinate
    from maxtrade.history import ScanHistory

    st.subheader("Trade decision")
    candidate = not stale and latest.action in {"LONG", "SHORT"}
    status = f"{latest.action} SETUP" if candidate else "WAIT / NO TRADE"
    if candidate:
        st.info(f"Technical: {status} | {latest.reason}")
    else:
        st.warning(f"Technical: {status} | " + ("Closed candles are stale." if stale else latest.reason))
    if candidate:
        columns = st.columns(3)
        for column, label, value in zip(columns, ("Research entry", "Stop", "Target"),
                                        (latest.entry, latest.stop, latest.target)):
            column.metric(label, display_number(value, 2 if value is not None and abs(value) >= 1 else 8))
        if latest.action == 'LONG':
            st.caption('Setup invalidation: next completed candle fails price > EMA20 > EMA50 or RSI 50-70. Paper stop remains the saved position stop.')
        else:
            st.caption('Setup invalidation: next completed candle fails price < EMA20 < EMA50 or RSI 30-50. SHORT is research only; paper spot entries are BUY-only.')
    st.caption("Completed-candle setup only; not permission to enter. Paper fills require fresh 1h/4h agreement and all risk checks.")
    try:
        ledger = PaperLedger()
        now = datetime.now(timezone.utc)
        account = ledger.account(now)
        enabled = ledger.automation_enabled()
        st.caption(f"Paper automation: {'ENABLED' if enabled else 'PAUSED'} | "
                 f"Kill switch: {'ON' if account['kill_switch'] else 'OFF'} | "
                 f"Position slot: {'OCCUPIED' if account['occupied'] else 'FREE'}")
        history = ScanHistory(ledger.path)
        report = st.session_state.get("research_report")
        if not report or (report.get("product"), report.get("symbol")) != (product, pair):
            report = next((saved['report'] for saved in history.recent_research()
                           if (saved['report'].get('product'), saved['report'].get('symbol')) == (product, pair)
                           and saved['report'].get('paper_policy') == 'autonomous-paper-v1'), None)
        if report and (report.get("product"), report.get("symbol")) == (product, pair):
            evidence = dict(report, ai_mode=ledger.ai_mode())
            risk = coordinate(evidence, now, account=account, autonomous=enabled)
            blockers = list(risk["blockers"])
            if report.get('paper_policy') == 'autonomous-paper-v1':
                blockers.extend(report.get('blockers', []))
            if stale:
                blockers.append("Chart candles are stale")
            if not enabled:
                blockers.append("Paper automation paused")
            blockers = list(dict.fromkeys(blockers))
            st.write(f"Paper: {'BUY ELIGIBLE' if not blockers else 'NO TRADE'}")
            st.caption(f"Full assessment: {report['created_at']} | expires {report['expires_at']}")
            age = (now - datetime.fromisoformat(report['created_at'])).total_seconds()
            st.caption(f'Assessment age: {age / 60:.1f} minutes' if age >= 0 else 'Assessment timestamp is in the future; unverified.')
            checklist = []
            for timeframe in ('1h', '4h'):
                matches = [item for item in report['evidence'] if item['interval'] == timeframe]
                item = matches[0] if len(matches) == 1 else None
                fresh = bool(item and datetime.fromisoformat(item['event_time']) <= now
                             < datetime.fromisoformat(item['expires_at']))
                checklist.append({'Timeframe': timeframe, 'Direction': item['action'] if item else 'MISSING',
                                  'Paper gate': 'PASS' if fresh and item['action'] == 'LONG' else 'BLOCKED',
                                  'Freshness': 'FRESH' if fresh else 'STALE / MISSING',
                                  'Candle closed': item['event_time'] if item else None,
                                  'Expires': item['expires_at'] if item else None})
            st.dataframe(checklist, hide_index=True, width='stretch')
            for blocker in blockers:
                st.write(f"- {blocker}")
        else:
            st.write("Paper: NO TRADE | No matching full research assessment. Run market research below.")
        heartbeat = history.worker_status()
        if not heartbeat:
            st.warning("Worker has not completed a cycle on this database.")
        elif (now - datetime.fromisoformat(heartbeat["finished_at"])).total_seconds() > 1800:
            st.warning(f"Worker inactive: last completed cycle {heartbeat['finished_at']}.")
        else:
            st.caption(f"Last paper cycle {heartbeat['finished_at']} | failures: {heartbeat['failures']}")
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
        st.warning(f"Paper: NO TRADE | Risk status unavailable: {error}")


def render_chart_snapshot(product: str, interval: str, pair: str, live: bool, workspace: bool = False) -> None:
    require_chart_login()
    selection = (product, interval, pair)
    if st.session_state.get('chart_zoom_selection') != selection:
        st.session_state['chart_zoom_selection'] = selection
        st.session_state['chart_price_zoom'] = 1.0
        st.session_state['chart_price_offset'] = 0.0
    st.session_state.setdefault('chart_price_offset', 0.0)
    st.markdown('<style>.st-key-chart_price_controls [data-testid="stHorizontalBlock"] {flex-wrap:nowrap !important;}'
                '.st-key-chart_price_controls [data-testid="stColumn"] {min-width:0 !important;flex:1 1 0 !important;}'
                '</style>', unsafe_allow_html=True)
    with st.container(key='chart_price_controls'):
        scale_control, up_control, down_control, refresh_control = st.columns(4)
    if up_control.button('', icon=':material/arrow_upward:', help='Pan price range up', key='chart_pan_up', width='stretch'):
        st.session_state['chart_price_offset'] += .3
    if down_control.button('', icon=':material/arrow_downward:', help='Pan price range down', key='chart_pan_down', width='stretch'):
        st.session_state['chart_price_offset'] -= .3
    refresh = refresh_control.button('', icon=':material/refresh:', help='Refresh chart', key='chart_refresh', width='stretch')
    with scale_control.popover('', icon=':material/height:', help='Price scale', width='stretch'):
        zoom_controls = st.columns(3)
    if zoom_controls[0].button('', icon=':material/zoom_in:', help='Zoom in vertically', key='chart_zoom_in',
                               width='stretch', disabled=st.session_state['chart_price_zoom'] >= 8):
        st.session_state['chart_price_zoom'] = min(8.0, st.session_state['chart_price_zoom'] * 1.25)
    if zoom_controls[1].button('', icon=':material/zoom_out:', help='Zoom out vertically', key='chart_zoom_out',
                               width='stretch', disabled=st.session_state['chart_price_zoom'] <= .25):
        st.session_state['chart_price_zoom'] = max(.25, st.session_state['chart_price_zoom'] / 1.25)
    if zoom_controls[2].button('', icon=':material/fit_screen:', help='Reset price scale', key='chart_zoom_reset', width='stretch'):
        st.session_state['chart_price_zoom'] = 1.0
        st.session_state['chart_price_offset'] = 0.0
    previous = st.session_state.get("chart_snapshot")
    refresh_failed = False
    if refresh or live or not previous or previous["selection"] != selection:
        client = DeribitClient() if product == "Options" else CoinDCXClient()
        try:
            with st.spinner("Loading market candles…"):
                if product == "Options":
                    display_candles = client.underlying_candles(pair, interval, include_open=True)
                elif product == "Spot":
                    display_candles = client.spot_candles(pair, interval, include_open=True)
                else:
                    display_candles = client.futures_candles(pair, interval, include_open=True)
                chart_analysis(display_candles, interval, allow_short=product != "Spot")
                candles = normalize_candles(display_candles, interval, count=len(display_candles))
                analyses = chart_analysis(candles, interval, allow_short=product != "Spot")
                st.session_state["chart_snapshot"] = {"selection": selection, "candles": candles, "analyses": analyses,
                                                     "display_candles": display_candles,
                                                     "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                if product == "Options" and not workspace and interval in ('1h', '4h'):
                    try:
                        age = (datetime.now(timezone.utc) - datetime.fromisoformat(previous["contracts_fetched"])).total_seconds() if previous and previous.get("contracts_fetched") else 60
                        if not refresh and previous and previous["selection"] == selection and age < 60:
                            contracts = previous["contracts"]
                            fetched = previous["contracts_fetched"]
                        else:
                            contracts = scan_options(client, pair, interval, 10)
                            fetched = datetime.now(timezone.utc).isoformat(timespec="seconds")
                        st.session_state["chart_snapshot"].update(contracts=contracts, contracts_fetched=fetched)
                    except (requests.RequestException, KeyError, TypeError, ValueError) as error:
                        st.session_state["chart_snapshot"]["contract_error"] = str(error)
        except (requests.RequestException, KeyError, TypeError, ValueError) as error:
            refresh_failed = True
            if previous and previous['selection'] == selection:
                st.warning(f"Refresh unavailable; showing saved snapshot from {previous['fetched']}: {error}")
            else:
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
    display_candles = snapshot["display_candles"]
    forming = int(display_candles[-1]["time"]) > int(candles[-1]["time"])
    last = display_candles[-1]
    change = (float(last['close']) / float(last['open']) - 1) * 100
    quote = display_number(last['close'], 2 if abs(last['close']) >= 1 else 8)
    ohlc = ''.join(f'<div><span>{field.upper()}</span><strong>{display_number(last[field], 2 if abs(last[field]) >= 1 else 8)}</strong></div>'
                   for field in ('open', 'high', 'low'))
    st.html(f'''<style>
        .market-strip {{display:flex;align-items:center;justify-content:space-between;gap:.4rem;border-top:1px solid #dce5e8;border-bottom:1px solid #dce5e8;padding:.35rem 0;flex-wrap:wrap;}}
        .market-quote strong {{font-size:1.2rem;font-variant-numeric:tabular-nums;}}
        .market-quote small {{display:block;color:#64767b;font-size:.7rem;}}
        .market-ohlc {{display:flex;gap:.65rem;flex-wrap:wrap;}}
        .market-ohlc span {{display:block;color:#64767b;font-size:.65rem;}}
        .market-ohlc strong {{font-size:.8rem;font-variant-numeric:tabular-nums;}}
        </style><div class="market-strip"><div class="market-quote"><small>{escape(pair)} / {escape(interval)}</small>
    <strong>{quote}</strong> <span style="color:{'#00896b' if change >= 0 else '#db4056'}">{change:+.2f}%</span>
    <small>{'Saved snapshot' if refresh_failed else 'Live · 10s' if live else 'Paused'} · {'Forming candle' if forming else 'Closed candle'} · Candle change</small></div>
    <div class="market-ohlc">{ohlc}</div></div>''')
    style = st.session_state.get("chart_style", "Candles")
    theme = st.session_state.get("chart_theme", "Dark")
    indicators = tuple(st.session_state.get("chart_indicators", ["EMA 20", "EMA 50", "Volume", "RSI 14"]))
    logarithmic = st.session_state.get("chart_log", False)
    visible = st.session_state.get("chart_visible", 80)
    records = signal_records(candles, chart_analysis(candles, interval, allow_short=True), interval)
    paper_positions = []
    if product == 'Spot' and st.session_state.get('chart_paper_fills', True):
        from maxtrade.paper import PaperLedger
        try:
            paper_positions = [position for position in PaperLedger().positions() if position['symbol'] == pair]
        except (OSError, sqlite3.Error, ValueError):
            st.warning('Saved paper fills unavailable on this database.')
    figure = candle_figure(display_candles, analyses, interval, options=product == "Options",
                           chart_type=style, indicators=indicators, theme=theme,
                           logarithmic=logarithmic, visible_bars=visible,
                           signals=records if st.session_state.get('chart_signals', True) else None,
                           paper_positions=paper_positions,
                           price_zoom=st.session_state['chart_price_zoom'],
                           price_offset=st.session_state['chart_price_offset'])
    figure.update_layout(uirevision=repr((selection, style, indicators, logarithmic, visible)),
                          editrevision="|".join(selection))
    window = st.session_state.get('chart_window', 'Latest candles')
    figure.update_layout(uirevision=repr((selection, style, indicators, logarithmic, visible,
                                          window, st.session_state.get('chart_from'), st.session_state.get('chart_to'))))
    figure.update_yaxes(uirevision=repr((selection, style, logarithmic, visible,
                                        st.session_state['chart_price_zoom'], st.session_state['chart_price_offset'])), row=1, col=1)
    if window == 'All loaded candles':
        figure.update_xaxes(autorange=True)
    elif window == 'Custom UTC range':
        try:
            start = datetime.fromisoformat(st.session_state.get('chart_from', ''))
            end = datetime.fromisoformat(st.session_state.get('chart_to', ''))
            if start.tzinfo is None or end.tzinfo is None or start >= end:
                raise ValueError('Use timezone-aware UTC timestamps with From before To.')
            figure.update_xaxes(range=[start, end], autorange=False)
        except ValueError as error:
            st.warning(f'Custom time range unavailable: {error}')
    if workspace:
        figure.update_layout(height=620)
    st.plotly_chart(figure,
                    width="stretch", config={"displaylogo": False, "scrollZoom": False,
                                              "displayModeBar": True,
                                              "modeBarButtonsToAdd": ["drawline", "drawrect", "drawopenpath", "eraseshape"],
                                              "modeBarButtonsToRemove": ["select2d", "lasso2d", "zoom2d", "zoomIn2d", "zoomOut2d", "autoScale2d"],
                                              "toImageButtonOptions": {"filename": "maxtrade_chart", "scale": 2}},
                    key="candle_chart")
    with st.popover('', icon=':material/download:', help='Chart exports'):
        st.download_button('Save interactive chart', figure.to_html(include_plotlyjs=True, full_html=True),
                       file_name='maxtrade-chart.html', mime='text/html', icon=':material/download:',
                       key='chart_html')
        export = pd.DataFrame(display_candles)
        export['time'] = pd.to_datetime(export['time'], unit='ms', utc=True)
        st.download_button('Candle CSV', export.to_csv(index=False), file_name='maxtrade_candles.csv',
                           mime='text/csv', icon=':material/download:', key='chart_csv')
    if workspace:
        return
    if latest.action not in {'LONG', 'SHORT'} and records and st.session_state.get('chart_signals', True):
        st.caption(f"Previous {records[-1]['Signal']} setup levels · confirmed {records[-1]['Available at']} · historical, not a current entry.")
    elif latest.action not in {'LONG', 'SHORT'} and not any(position['state'] in {'OPEN', 'PENDING'} for position in paper_positions):
        st.caption('No active PAPER position or confirmed setup levels for this market.')
    with st.expander('Trade decision & PAPER risk', icon=':material/shield:'):
        render_trade_status(product, pair, latest, stale)
        st.caption(f"Last closed candle {close_time.isoformat(timespec='minutes')} · fetched {snapshot['fetched']}")
    st.caption('Arrows: completed-candle technical setups, not executed trades. Spot SELL is bearish research, not a short order. Diamonds/crosses: saved paper entry/exit on this database only.')
    with st.expander('Past signal records', expanded=False):
        st.caption('Recomputed from loaded completed candles only; not a contemporaneously saved recommendation. First warm-up setup is excluded. Options labels describe underlying bias, not option premium.')
        if records:
            table = pd.DataFrame(records).drop(columns=['Marker price']).iloc[::-1]
            st.dataframe(table, hide_index=True, width='stretch')
            st.download_button('Signal history CSV', table.to_csv(index=False), file_name='chart-signal-history.csv',
                               mime='text/csv', icon=':material/download:', key='chart_signal_csv')
        else:
            st.info('No new BUY/SELL setup transitions in the loaded completed candles.')
    with st.expander('Closed-candle details', icon=':material/analytics:'):
        st.write(f"Technical direction: {latest.action}")
        st.write(latest.reason)
        st.dataframe([{"Close": candles[-1]["close"], "EMA 20": latest.ema_fast,
                   "EMA 50": latest.ema_slow, "RSI 14": latest.rsi,
                   "Research entry": latest.entry, "Research stop": latest.stop,
                   "Research target": latest.target}], hide_index=True, width="stretch")
    st.caption(f"{interval} completed candle · {close_time.isoformat(timespec='minutes')} · "
               f"{'USD underlying, not option premium' if product == 'Options' else 'Market quote currency'} · "
               "Technical research only, not paper approval or an exchange order.")
    if product == "Options" and interval in ('1h', '4h'):
        render_option_chain(pair, 'chart')
        st.subheader("Contract watchlist")
        st.caption("CALL/PUT bias is underlying direction only. WATCH labels also require current contract liquidity and delta filters; not premium entry/exit signals.")
        if snapshot.get("contract_error"):
            st.warning(f"Contract quotes unavailable: {snapshot['contract_error']}")
        else:
            st.markdown('<div class="signal-grid">' + ''.join(signal_card(row) for row in snapshot.get("contracts", [])) + '</div>',
                        unsafe_allow_html=True)
            st.caption(f"Contract quotes fetched {snapshot['contracts_fetched']} · refreshed at most once per minute automatically. Options may lose their full premium.")
    elif product != 'Options' and interval in ('1h', '4h'):
        render_replay(snapshot, interval, product)
    with st.expander("Research rules"):
        st.write("Chart indicators and setup markers use completed candles. Technical arrows are separate from saved paper fills. Drawings are temporary browser annotations, not orders or saved trading instructions.")
        st.caption("EMA200 needs 200 closed candles. Bollinger Bands use 20 closes and two population standard deviations. Support/resistance are the prior 20-bar low/high, not predictive zones. MACD uses 12/26 EMAs and a 9-period signal. UTC-day VWAP excludes the first loaded day because its opening history may be incomplete; zero volume stays blank. Indicator warm-ups remain blank.")
        if product == "Spot":
            st.caption("Spot is buy-only research. Sell/short setups are available on Futures; no position-aware exit rule exists.")
        st.caption("Price display uses 10-second REST polling, not a tick-by-tick WebSocket stream. Signals use completed candles only; the forming candle never changes a confirmed signal. No automated orders.")


def render_replay(snapshot: dict, interval: str, product: str) -> None:
    with st.expander("Backtest", icon=":material/science:"):
        st.caption(f"Historical simulation · {len(snapshot['candles'])} candles · capital and PnL in market quote currency")
        columns = st.columns(2)
        cash = columns[0].number_input("Starting capital", min_value=100.0, value=10000.0, step=100.0, key="replay_cash")
        risk = columns[1].number_input("Risk per trade (%)", min_value=0.1, max_value=5.0, value=1.0, step=0.1, key="replay_risk")
        allocation = columns[0].number_input("Maximum allocation (%)", min_value=1.0, max_value=95.0, value=25.0, key="replay_allocation")
        fee = columns[1].number_input("Fee per fill (bps)", min_value=0.0, max_value=100.0, value=10.0, key="replay_fee")
        slippage = columns[0].number_input("Slippage per fill (bps)", min_value=0.0, max_value=100.0, value=5.0, key="replay_slippage")
        funding = columns[1].number_input("Funding cost / 8h (bps)", min_value=0.0, max_value=100.0, value=0.0,
                                          disabled=product == "Spot", key="replay_funding")
        settings = ReplaySettings(cash, risk, allocation, fee, slippage, funding if product == "Futures" else 0)
        signature = (snapshot["selection"], tuple((bar["time"], bar["open"], bar["high"], bar["low"], bar["close"]) for bar in snapshot["candles"]), settings)
        if st.button("Run backtest", icon=":material/play_arrow:", width="stretch", key="replay_run"):
            st.session_state.pop("replay_result", None)
            try:
                with st.spinner("Replaying candles…"):
                    result = replay(snapshot["candles"], interval, product == "Futures", settings)
                    st.session_state["replay_result"] = (signature, result)
            except (ValueError, TypeError, KeyError) as error:
                st.error(f"Simulation unavailable: {error}")
        saved = st.session_state.get("replay_result")
        if saved and saved[0] == signature:
            result = saved[1]
            st.markdown('<div class="risk-grid">' + ''.join(
                f'<div><span>{label}</span><strong>{value}</strong></div>' for label, value in [
                    ("Net return", f"{result['return_pct']:.2f}%"),
                    ("Max drawdown", f"{result['drawdown_pct']:.2f}%"),
                    ("Trades", str(len(result["trades"])))]) + '</div>', unsafe_allow_html=True)
            st.line_chart(result["equity"]["Equity"], height=240, width="stretch")
            st.caption(f"Win rate: {display_number(result['win_rate'], 1)}% · Funding estimate: {display_number(result['funding'])}")
            if result["trades"].empty:
                st.info("No completed simulated trades in this snapshot.")
            else:
                ledger = result["trades"][["Tag", "EntryTime", "ExitTime", "Size", "EntryPrice", "ExitPrice",
                                           "Commission", "Funding estimate", "Net PnL"]].rename(columns={"Tag": "Direction", "Commission": "Fees + slippage"})
                st.dataframe(ledger, hide_index=True, width="stretch")
                st.download_button("Trade CSV", ledger.to_csv(index=False), file_name="maxtrade_backtest.csv",
                                   mime="text/csv", icon=":material/download:", key="replay_csv")
            st.warning("Small historical sample. Results are not a profitability forecast or live paper-trading record.")
        elif saved:
            st.caption("Previous simulation does not match this snapshot or these settings.")
        st.caption("Next-candle open entries on new setups; one unleveraged position; fixed signal stop/target. Stop is tested before target on ambiguous bars. Remaining positions are liquidated at the final bar open by the engine.")
        st.caption("Slippage is a per-fill cash cost, not a changed execution price. Funding is an entry-notional, elapsed-time cost for both directions, deducted after replay; it does not affect position sizing. These are estimates, not exchange fee or funding history.")
        st.caption("Risk sizing uses the signal close and stop distance plus fill costs. Gaps can exceed the risk budget. No liquidation model, tax, lot-size rules, or order-book fills; 50 candles are used for warm-up.")