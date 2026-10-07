from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from math import isfinite
from typing import Any

import plotly.graph_objects as go
import pandas as pd
from plotly.subplots import make_subplots

from maxtrade.coindcx import INTERVAL_MS
from maxtrade.signals import TradeSignal, analyze_candles


def chart_analysis(candles: list[dict[str, Any]], interval: str, allow_short: bool) -> list[TradeSignal]:
    duration = INTERVAL_MS[interval]
    previous_time = None
    for candle in candles:
        timestamp = int(candle["time"])
        if previous_time is not None and timestamp - previous_time != duration:
            raise ValueError("Candle history has gaps or unordered timestamps; no chart signal generated")
        previous_time = timestamp
        prices = [float(candle[field]) for field in ("open", "high", "low", "close")]
        if not all(isfinite(price) and price > 0 for price in prices):
            raise ValueError("Chart prices must be positive and finite")
        opening, high, low, close = prices
        if not low <= min(opening, close) <= max(opening, close) <= high:
            raise ValueError("Invalid candle open/high/low/close")
    if len(candles) < 50:
        raise ValueError("At least 50 completed candles are required")
    return [analyze_candles(candles[:end], allow_short=allow_short) for end in range(50, len(candles) + 1)]


def signal_records(candles: list[dict[str, Any]], analyses: list[TradeSignal], interval: str) -> list[dict[str, Any]]:
    records = []
    for index, signal in enumerate(analyses):
        if not index or signal.action not in {'LONG', 'SHORT'} or signal.action == analyses[index - 1].action:
            continue
        candle = candles[49 + index]
        records.append({'Candle time': datetime.fromtimestamp(candle['time'] / 1000, timezone.utc),
                        'Available at': datetime.fromtimestamp((candle['time'] + INTERVAL_MS[interval]) / 1000, timezone.utc),
                        'Signal': 'BUY' if signal.action == 'LONG' else 'SELL',
                        'Reference entry': signal.entry, 'Stop': signal.stop, 'Target': signal.target,
                        'RSI': signal.rsi, 'Reason': signal.reason,
                        'Marker price': candle['low'] if signal.action == 'LONG' else candle['high']})
    return records


def candle_figure(candles: list[dict[str, Any]], analyses: list[TradeSignal],
                  interval: str, options: bool = False, chart_type: str = "Candles",
                  indicators: tuple[str, ...] = ("EMA 20", "EMA 50", "Volume", "RSI 14"),
                  theme: str = "Dark", logarithmic: bool = False, visible_bars: int = 80,
                  signals: list[dict[str, Any]] | None = None,
                  paper_positions: list[dict[str, Any]] | None = None) -> go.Figure:
    dates = [datetime.fromtimestamp(int(candle["time"]) / 1000, timezone.utc) for candle in candles]
    indicator_dates = dates[49:49 + len(analyses)]
    dark = theme == "Dark"
    background, foreground, grid = ("#16181d", "#d8dde5", "#292d35") if dark else ("#ffffff", "#242933", "#edf0f4")
    rising, falling = "#00b890", "#f45164"
    confirmed = pd.DataFrame(candles[:49 + len(analyses)])
    confirmed_dates = dates[:len(confirmed)]
    closes = confirmed["close"].astype(float)
    panels = [name for name in ("Volume", "RSI 14", "MACD") if name in indicators]
    heights = [1 - .17 * len(panels)] + [.17] * len(panels)
    figure = make_subplots(rows=1 + len(panels), cols=1, shared_xaxes=True,
                           row_heights=heights, vertical_spacing=.025)
    if chart_type == "Candles":
        figure.add_trace(go.Candlestick(
            x=dates, open=[bar["open"] for bar in candles], high=[bar["high"] for bar in candles],
            low=[bar["low"] for bar in candles], close=[bar["close"] for bar in candles],
            increasing={"line": {"color": rising}, "fillcolor": rising},
            decreasing={"line": {"color": falling}, "fillcolor": falling}, name="OHLC",
        ), row=1, col=1)
    else:
        figure.add_trace(go.Scatter(x=dates, y=[bar["close"] for bar in candles], name="Price",
                                   mode="lines", line={"color": "#4c91ff", "width": 1.8},
                                   fill="tozeroy" if chart_type == "Area" else None), row=1, col=1)
    for name, attribute, color in [("EMA 20", "ema_fast", "#d19a22"), ("EMA 50", "ema_slow", "#327ba5")]:
        if name in indicators:
            figure.add_trace(go.Scatter(x=indicator_dates, y=[getattr(signal, attribute) for signal in analyses],
                                       name=name, mode="lines", line={"color": color, "width": 1.3}), row=1, col=1)
    overlays = []
    if "EMA 200" in indicators:
        overlays.append(("EMA 200", closes.ewm(span=200, adjust=False, min_periods=200).mean(), "#e77caa"))
    if "Bollinger Bands" in indicators:
        middle = closes.rolling(20).mean()
        deviation = closes.rolling(20).std(ddof=0)
        overlays.extend([("BB upper", middle + 2 * deviation, "#8295ac"),
                         ("BB middle", middle, "#8295ac"),
                         ("BB lower", middle - 2 * deviation, "#8295ac")])
    if "VWAP (UTC day)" in indicators:
        volume = confirmed.get("volume", pd.Series(0., index=confirmed.index)).astype(float)
        sessions = pd.to_datetime(confirmed["time"], unit="ms", utc=True).dt.floor("D")
        typical = (confirmed["high"] + confirmed["low"] + closes) / 3
        cumulative_volume = volume.groupby(sessions).cumsum()
        vwap = (typical * volume).groupby(sessions).cumsum() / cumulative_volume.where(cumulative_volume > 0)
        vwap = vwap.where(sessions != sessions.iloc[0])
        overlays.append(("VWAP (UTC day)", vwap, "#e5ac46"))
    if "Support / resistance" in indicators:
        overlays.extend([("Support (prior 20)", confirmed["low"].shift(1).rolling(20).min(), "#00b890"),
                         ("Resistance (prior 20)", confirmed["high"].shift(1).rolling(20).max(), "#f45164")])
    for name, values, color in overlays:
        figure.add_trace(go.Scatter(x=confirmed_dates, y=values, name=name, mode="lines",
                                   line={"color": color, "width": 1.2}, connectgaps=False), row=1, col=1)
    for action, color, symbol in [('BUY', rising, 'triangle-up'), ('SELL', falling, 'triangle-down')]:
        selected = [record for record in signals or [] if record['Signal'] == action]
        if selected:
            label = ('CALL bias' if action == 'BUY' else 'PUT bias') if options else action
            figure.add_trace(go.Scatter(x=[record['Candle time'] for record in selected],
                                       y=[record['Marker price'] for record in selected], name=f'{label} setup',
                                       mode='markers+text', text=[label] * len(selected),
                                       textposition='bottom center' if action == 'BUY' else 'top center',
                                       marker={'symbol': symbol, 'size': 12, 'color': color},
                                       customdata=[[str(record['Available at']), record['Reference entry'], record['Stop'],
                                                    record['Target'], record['RSI'], escape(record['Reason'])] for record in selected],
                                       hovertemplate=label + ' technical setup<br>Available %{customdata[0]}'
                                       '<br>Reference %{customdata[1]}<br>Stop %{customdata[2]}<br>Target %{customdata[3]}'
                                       '<br>RSI %{customdata[4]:.1f}<br>%{customdata[5]}<extra>Not a paper fill</extra>'), row=1, col=1)
    for field, price_field, label in [('opened_at', 'entry', 'Paper entry'), ('closed_at', 'exit', 'Paper exit')]:
        selected = [position for position in paper_positions or [] if position.get(field) and position.get(price_field)
                    and dates[0] <= datetime.fromisoformat(position[field]) <= dates[-1] + pd.Timedelta(milliseconds=INTERVAL_MS[interval])]
        if selected:
            figure.add_trace(go.Scatter(x=[datetime.fromisoformat(position[field]) for position in selected],
                                       y=[position[price_field] for position in selected], name=label, mode='markers',
                                       marker={'symbol': 'diamond' if field == 'opened_at' else 'x', 'size': 13,
                                               'color': '#327ba5' if field == 'opened_at' else '#d19a22'},
                                       customdata=[[position['id'], position['state'], position.get('pnl')] for position in selected],
                                       hovertemplate=label + ' #%{customdata[0]}<br>%{x}<br>Price %{y}'
                                       '<br>%{customdata[1]}<br>Net P&L %{customdata[2]}<extra>Saved simulation</extra>'), row=1, col=1)
    for row, panel in enumerate(panels, start=2):
        if panel == "Volume":
            figure.add_trace(go.Bar(x=dates, y=[bar.get("volume", 0) for bar in candles], name="Volume",
                                   marker_color=[rising if bar["close"] >= bar["open"] else falling for bar in candles],
                                   opacity=.65), row=row, col=1)
        elif panel == "MACD":
            macd = closes.ewm(span=12, adjust=False, min_periods=12).mean() - closes.ewm(span=26, adjust=False, min_periods=26).mean()
            macd_signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
            histogram = macd - macd_signal
            figure.add_trace(go.Bar(x=confirmed_dates, y=histogram, name="MACD histogram",
                                   marker_color=[rising if value >= 0 else falling for value in histogram]), row=row, col=1)
            for name, values, color in [("MACD", macd, "#4c91ff"), ("MACD signal", macd_signal, "#e5ac46")]:
                figure.add_trace(go.Scatter(x=confirmed_dates, y=values, name=name, mode="lines",
                                           line={"color": color, "width": 1.2}), row=row, col=1)
        else:
            figure.add_trace(go.Scatter(x=indicator_dates, y=[signal.rsi for signal in analyses], name="RSI 14",
                                       mode="lines", line={"color": "#b08bdf", "width": 1.3}), row=row, col=1)
            for level in [30, 50, 70]:
                figure.add_hline(y=level, line_width=1, line_dash="dot", line_color=grid, row=row, col=1)
            figure.update_yaxes(range=[0, 100], row=row, col=1)
        figure.update_yaxes(title_text=panel, row=row, col=1)
    figure.update_layout(height=640, margin={"l": 8, "r": 16, "t": 36, "b": 90},
                         paper_bgcolor=background, plot_bgcolor=background,
                         font={"family": "IBM Plex Sans, sans-serif", "color": foreground, "size": 11},
                         legend={"orientation": "h", "y": -.12, "yanchor": "top", "x": 0}, dragmode="pan",
                         hovermode="x", newshape={"line": {"color": "#e5ac46", "width": 2}},
                         modebar={"bgcolor": background, "color": foreground, "activecolor": "#4c91ff"})
    figure.update_xaxes(showgrid=True, gridcolor=grid, rangeslider_visible=False,
                        showspikes=True, spikemode="across", spikesnap="cursor", spikedash="dot",
                        range=[dates[max(0, len(dates) - visible_bars)], dates[-1]])
    figure.update_yaxes(gridcolor=grid, fixedrange=False, side="right", showspikes=True)
    figure.update_yaxes(type="log" if logarithmic else "linear", row=1, col=1)
    return figure