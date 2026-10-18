from dataclasses import asdict
from datetime import datetime, timezone
import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from maxtrade.stock_backtest import (
    StockDataset, StockReplaySettings, load_stock_csv, stock_analyses, stock_replay, stock_report,
    stock_schedule, stock_signal_label,
)
from maxtrade.stock_data import INDEX_SYMBOLS, MARKET_INTERVALS, MarketDataError, fetch_market_history, market_analyses, search_market_symbols


@st.cache_data(show_spinner=False, max_entries=8, hash_funcs={StockDataset: lambda dataset: dataset.checksum})
def cached_analyses(dataset):
    return stock_analyses(dataset, allow_short=True)


@st.cache_data(show_spinner=False, max_entries=8, hash_funcs={StockDataset: lambda dataset: dataset.checksum})
def cached_replay(dataset, settings, evaluation_start):
    return stock_replay(dataset, settings, evaluation_start)


def render_stock_page() -> None:
    workspace = st.radio('Indian markets', ['Market charts', 'CSV research'], horizontal=True,
                         key='stock_workspace')
    if workspace == 'CSV research':
        render_stock_research()
    else:
        render_market_chart()


@st.cache_data(show_spinner=False, ttl=300, max_entries=32)
def cached_market_data(symbol, exchange, period, interval='1d'):
    return fetch_market_history(symbol, exchange, period, interval)


@st.cache_data(show_spinner=False, ttl=3600, max_entries=64)
def cached_market_search(query, exchange):
    return search_market_symbols(query, exchange)


def render_market_chart() -> None:
    st.caption('Underlying research | Delayed provider data | No PAPER or live orders')
    exchange = st.radio('Exchange', ['Indices', 'NSE', 'BSE'], horizontal=True, key='market_exchange')
    if exchange == 'Indices':
        title = st.selectbox('Index', list(INDEX_SYMBOLS), key='market_index')
        symbol, venue = INDEX_SYMBOLS[title]
    else:
        with st.form('market_search_form'):
            query = st.text_input('Symbol or company', value='RELIANCE', max_chars=80, key='market_query')
            submitted = st.form_submit_button('Search', icon=':material/search:')
        saved = st.session_state.get('market_search_results')
        if submitted or not saved or saved[0] != exchange:
            st.session_state.pop('market_search_results', None)
            try:
                with st.spinner('Searching instruments...'):
                    results = cached_market_search(query, exchange)
                saved = (exchange, results)
                st.session_state['market_search_results'] = saved
            except MarketDataError as error:
                st.error(str(error))
                return
        names = dict(saved[1])
        if not names:
            st.warning('No matching equities on this exchange in the provider catalog.')
            return
        symbol = st.selectbox('Instrument', list(names), format_func=lambda value: f'{names[value]} | {value}',
                              key=f'market_instrument_{exchange}')
        title, venue = names[symbol], exchange
        if venue == 'BSE':
            st.caption('BSE equity history coverage is limited by the provider. Insufficient history blocks signals.')
    interval = st.selectbox('Timeframe', list(MARKET_INTERVALS), index=5,
                            format_func=lambda value: MARKET_INTERVALS[value][0], key='market_interval')
    periods = MARKET_INTERVALS[interval][1]
    if st.session_state.get('market_period') not in periods:
        st.session_state['market_period'] = periods[0]
    controls = st.columns([3, 1], vertical_alignment='bottom')
    period = controls[0].selectbox('History', periods,
                                  format_func=lambda value: {'5d': '5 days', '1mo': '1 month', '60d': '60 days',
                                                            '3mo': '3 months', '6mo': '6 months', '1y': '1 year', '2y': '2 years'}[value],
                                  key='market_period')
    if controls[1].button('', icon=':material/refresh:', help='Refresh market data', key='market_refresh'):
        cached_market_data.clear()
    try:
        with st.spinner(f'Loading {title} {interval} candles...'):
            dataset = cached_market_data(symbol, venue, period, interval)
    except (MarketDataError, ValueError, TypeError, OverflowError) as error:
        st.subheader(title)
        st.error(f'Chart unavailable: {error}')
        return
    analyses = market_analyses(dataset)
    latest = analyses[-1]
    dates = [bar.date for bar in dataset.bars]
    stale = dataset.stale()
    unit = 'points' if dataset.is_index else 'INR'
    st.subheader(title)
    st.caption(f'{symbol} | {venue} | Yahoo Finance | Adjusted {interval} OHLC | {len(dates)} candles')
    st.caption(f'Last completed candle start: {dates[-1]} | Fetched UTC: {dataset.fetched_at:%Y-%m-%d %H:%M:%S} | Not real-time')
    mode = st.radio('Signal view', ['BUY / SELL', 'CALL BUY / PUT BUY'], horizontal=True, key='market_signal_view')
    options = mode == 'CALL BUY / PUT BUY'
    if stale:
        st.warning('Provider history is stale: latest direction and levels are unavailable. Chart and past signals are historical only.')
    if options:
        st.warning('Underlying direction only. Option contract, strike, expiry and premium Entry/SL/TP are unavailable. Not an option-buy approval.')
    metrics = st.columns(3)
    change = (dataset.bars[-1].close / dataset.bars[-2].close - 1) * 100
    metrics[0].metric(f'Last close ({unit})', f'{dataset.bars[-1].close:,.2f}', f'{change:+.2f}%')
    metrics[1].metric(f'{interval} direction', 'UNAVAILABLE' if stale else stock_signal_label(latest.action, options))
    metrics[2].metric('RSI 14', f'{latest.rsi:.1f}')
    levels = st.columns(3)
    for column, label, value in zip(levels, ('Entry', 'Stop', 'Target'), (latest.entry, latest.stop, latest.target)):
        column.metric(f'Underlying {label.lower()} ({unit})', f'{value:,.2f}' if value and not stale else 'N/A')
    if not stale:
        st.caption(latest.reason)
    figure = go.Figure(go.Candlestick(x=dates, open=[bar.open for bar in dataset.bars],
                                    high=[bar.high for bar in dataset.bars], low=[bar.low for bar in dataset.bars],
                                    close=[bar.close for bar in dataset.bars], name=title,
                                    increasing_line_color='#137b69', decreasing_line_color='#b94343'))
    for label, values, color in [('EMA 20', [signal.ema_fast for signal in analyses], '#327ea8'),
                                  ('EMA 50', [signal.ema_slow for signal in analyses], '#a27a16')]:
        figure.add_trace(go.Scatter(x=dates[49:], y=values, name=label, line_color=color))
    for action, color, marker in [('LONG', '#137b69', 'triangle-up'), ('SHORT', '#b94343', 'triangle-down')]:
        transitions = [index for index, signal in enumerate(analyses)
                       if signal.action == action and (index == 0 or analyses[index - 1].action != action)]
        figure.add_trace(go.Scatter(x=[dates[index + 49] for index in transitions],
                                   y=[analyses[index].entry for index in transitions], mode='markers',
                                   name=stock_signal_label(action, options),
                                   marker=dict(color=color, symbol=marker, size=11)))
    figure.update_layout(height=460, margin=dict(l=0, r=0, t=15, b=0), xaxis_rangeslider_visible=False,
                         yaxis_title=unit, legend=dict(orientation='h'),
                         uirevision=f'{symbol}:{interval}:{period}:{mode}', font=dict(family='IBM Plex Sans', size=12),
                         paper_bgcolor='rgba(0,0,0,0)')
    st.plotly_chart(figure, width='stretch', key='market_candles', config={'scrollZoom': True, 'displaylogo': False})
    with st.expander('Signal history'):
        history = pd.DataFrame([{
            'Session': session.isoformat(), 'Signal': stock_signal_label(signal.action, options),
            f'Underlying entry {unit}': signal.entry, f'Underlying stop {unit}': signal.stop,
            f'Underlying target {unit}': signal.target, 'RSI': signal.rsi, 'Reason': signal.reason,
            'Symbol': symbol, 'Exchange': venue, 'Timeframe': interval, 'Source': f'Yahoo Finance adjusted {interval} OHLC',
            'Execution enabled': False, 'Option contract available': False,
        } for session, signal in zip(dates[49:], analyses)])
        st.dataframe(history.iloc[::-1], width='stretch', hide_index=True)
        st.download_button('Signals CSV', history.to_csv(index=False), f'{symbol}-{interval}-signals.csv',
                           'text/csv', icon=':material/download:', key='market_signals_download')


def render_stock_research() -> None:
    st.subheader('NSE equity research')
    st.caption('Daily cash equities | INR | Historical research only | No PAPER or live orders')
    identity = st.columns(2)
    symbol = identity[0].text_input('NSE symbol', value='RELIANCE', key='stock_symbol', max_chars=32)
    source = identity[1].text_input('Data source', key='stock_source', max_chars=200)
    upload = st.file_uploader('Daily OHLCV CSV', type=['csv'], key='stock_csv', max_upload_size=5)
    acknowledged = st.checkbox('Split/bonus-adjusted OHLC and compatible volume verified', key='stock_adjusted')
    if upload is None or not acknowledged:
        st.session_state.pop('stock_replay_result', None)
        return
    try:
        dataset = load_stock_csv(upload.getvalue(), symbol, source)
    except (ValueError, TypeError, OverflowError, pd.errors.ParserError, UnicodeError) as error:
        st.session_state.pop('stock_replay_result', None)
        st.error(f'Import rejected: {error}')
        return
    dates = [bar.date for bar in dataset.bars]
    analyses = cached_analyses(dataset)
    latest = analyses[-1]
    latest_session = dataset.bars[-1].date
    st.caption(f'{dataset.symbol} | {len(dates):,} sessions | {dates[0]} to {latest_session} | {dataset.source}')
    mode = st.radio('Signal view', ['BUY / SELL', 'CALL BUY / PUT BUY'], horizontal=True, key='stock_signal_view')
    options = mode == 'CALL BUY / PUT BUY'
    st.caption('Historical underlying research | Trade approval: unavailable | Execution: disabled')
    if options:
        st.warning('CALL BUY / PUT BUY indicates underlying direction only. Option contract, strike, expiry and premium Entry/SL/TP are unavailable. Not an option-buy approval.')
    else:
        st.caption('SELL is bearish research, not a delivery short order.')
    if (datetime.now(timezone.utc).date() - latest_session).days > 7:
        st.warning('Historical dataset: latest signal is not a current-market signal.')
    metrics = st.columns(4)
    metrics[0].metric('Underlying entry (INR)', f'{latest.entry:,.2f}' if latest.entry else 'N/A')
    metrics[1].metric('Historical direction', stock_signal_label(latest.action, options))
    metrics[2].metric('Underlying stop (INR)', f'{latest.stop:,.2f}' if latest.stop else 'N/A')
    metrics[3].metric('Underlying target (INR)', f'{latest.target:,.2f}' if latest.target else 'N/A')
    closes = stock_schedule(dates[49], latest_session)['market_close']
    st.caption(f'Latest session close: INR {dataset.bars[-1].close:,.2f} | Available after: {closes.iloc[-1].isoformat()} | RSI: {latest.rsi:.1f}' if latest.rsi is not None else f'Available after: {closes.iloc[-1].isoformat()}')
    st.write(latest.reason)
    history = pd.DataFrame([{
        'Session': session.isoformat(), 'Available after UTC': completed.isoformat(),
        'Signal': stock_signal_label(signal.action, options), 'View': mode,
        'Underlying entry INR': signal.entry, 'Underlying stop INR': signal.stop,
        'Underlying target INR': signal.target, 'RSI': signal.rsi, 'Reason': signal.reason,
        'Status': 'HISTORICAL RESEARCH ONLY', 'Execution enabled': False,
        'Option contract available': False, 'Symbol': dataset.symbol,
        'Source': dataset.source, 'Dataset SHA256': dataset.checksum,
    } for session, completed, signal in zip(dates[49:], closes, analyses)])
    figure = go.Figure(go.Candlestick(x=dates, open=[bar.open for bar in dataset.bars],
                                    high=[bar.high for bar in dataset.bars], low=[bar.low for bar in dataset.bars],
                                    close=[bar.close for bar in dataset.bars], name=dataset.symbol,
                                    increasing_line_color='#137b69', decreasing_line_color='#b94343'))
    figure.add_trace(go.Scatter(x=dates[49:], y=[signal.ema_fast for signal in analyses], name='EMA 20', line_color='#327ea8'))
    figure.add_trace(go.Scatter(x=dates[49:], y=[signal.ema_slow for signal in analyses], name='EMA 50', line_color='#a27a16'))
    for action, color, marker in [('LONG', '#137b69', 'triangle-up'), ('SHORT', '#b94343', 'triangle-down')]:
        transitions = [index for index, signal in enumerate(analyses)
                       if signal.action == action and (index == 0 or analyses[index - 1].action != action)]
        figure.add_trace(go.Scatter(
            x=[dates[index + 49] for index in transitions], y=[analyses[index].entry for index in transitions],
            mode='markers', name=stock_signal_label(action, options), marker=dict(color=color, symbol=marker, size=11),
            text=[closes.iloc[index].isoformat() for index in transitions],
            hovertemplate='Underlying INR %{y:.2f}<br>Available after %{text}<extra>%{fullData.name}</extra>'))
    figure.update_layout(height=430, margin=dict(l=0, r=0, t=15, b=0), xaxis_rangeslider_visible=False,
                         yaxis_title='INR', legend=dict(orientation='h'), uirevision=dataset.checksum,
                         font=dict(family='IBM Plex Sans', size=12), paper_bgcolor='rgba(0,0,0,0)')
    st.plotly_chart(figure, width='stretch', key='stock_candles')
    with st.expander('Historical signals'):
        st.dataframe(history.iloc[::-1], width='stretch', hide_index=True)
        st.download_button('Signals CSV', history.to_csv(index=False), f'{dataset.symbol}-signals.csv',
                           'text/csv', icon=':material/download:', key='stock_signals_download')
    st.subheader('Cash-equity replay (long-only)')
    controls = st.columns(3)
    cash = controls[0].number_input('Capital (INR)', min_value=1000.0, value=100000.0, step=1000.0, key='stock_cash')
    risk = controls[1].number_input('Planned risk (%)', min_value=.1, max_value=5.0, value=1.0, step=.1, key='stock_risk')
    allocation = controls[2].number_input('Planned allocation (%)', min_value=1.0, max_value=95.0, value=25.0, key='stock_allocation')
    costs = st.columns(4)
    fee = costs[0].number_input('All-in fee per side (bps)', min_value=0.0, max_value=100.0, value=12.0, key='stock_fee')
    slippage = costs[1].number_input('Slippage estimate (bps)', min_value=0.0, max_value=100.0, value=5.0, key='stock_slippage')
    fixed = costs[2].number_input('Fixed fee per side (INR)', min_value=0.0, max_value=500.0, value=16.0, key='stock_fixed')
    halt = costs[3].number_input('Drawdown entry halt (%)', min_value=1.0, max_value=50.0, value=20.0, key='stock_halt')
    evaluation_start = st.selectbox('Evaluation start session', dates[49:-2], key='stock_evaluation')
    settings = StockReplaySettings(cash, risk, allocation, fee, slippage, fixed, halt)
    signature = (dataset.checksum, json.dumps(asdict(settings), sort_keys=True), evaluation_start.isoformat())
    saved = st.session_state.get('stock_replay_result')
    if saved and saved[0] != signature:
        st.session_state.pop('stock_replay_result', None)
        saved = None
    if st.button('Run NSE replay', icon=':material/play_arrow:', type='primary', key='stock_run'):
        try:
            with st.spinner('Replaying completed NSE sessions...'):
                result = cached_replay(dataset, settings, evaluation_start)
            saved = (signature, result)
            st.session_state['stock_replay_result'] = saved
        except (ValueError, AssertionError, OverflowError) as error:
            st.session_state.pop('stock_replay_result', None)
            st.error(f'Replay rejected: {error}')
            return
    if not saved:
        return
    result = saved[1]
    summary = st.columns(4)
    summary[0].metric('Strategy return', f"{result['metrics']['Strategy']['net_return_pct']:.2f}%")
    summary[1].metric('Buy-and-hold return', f"{result['metrics']['Buy and hold']['net_return_pct']:.2f}%")
    summary[2].metric('Strategy drawdown', f"{result['metrics']['Strategy']['max_drawdown_pct']:.2f}%")
    summary[3].metric('Completed trades', str(len(result['trades'])))
    if result['entry_halted']:
        st.warning('Drawdown limit reached: further strategy entries halted for this replay.')
    st.caption(f"{result['warmup_sessions']} prior sessions | {result['complete_return_sessions']} complete return sessions | Final valuation: final session open")
    st.line_chart(result['equity'], color=['#137b69', '#b94343'], width='stretch')
    st.dataframe(pd.DataFrame(result['metrics']).T, width='stretch')
    st.dataframe(result['trades'], width='stretch', hide_index=True)
    downloads = st.columns(3)
    downloads[0].download_button('Equity CSV', result['equity'].to_csv(), f'{dataset.symbol}-equity.csv',
                                 'text/csv', icon=':material/download:')
    downloads[1].download_button('Trades CSV', result['trades'].to_csv(index=False), f'{dataset.symbol}-trades.csv',
                                 'text/csv', icon=':material/download:')
    report = stock_report(dataset, result)
    downloads[2].download_button('Research JSON', report, f'{dataset.symbol}-research.json',
                                 'application/json', icon=':material/download:')
    with st.expander('Execution and data assumptions'):
        for assumption in json.loads(report)['assumptions']:
            st.write(assumption)