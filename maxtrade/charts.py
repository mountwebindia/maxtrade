from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any

import plotly.graph_objects as go
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


def candle_figure(candles: list[dict[str, Any]], analyses: list[TradeSignal],
                  interval: str, options: bool = False) -> go.Figure:
    dates = [datetime.fromtimestamp(int(candle["time"]) / 1000, timezone.utc) for candle in candles]
    indicator_dates = dates[49:]
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[.75, .25], vertical_spacing=.04)
    figure.add_trace(go.Candlestick(
        x=dates, open=[bar["open"] for bar in candles], high=[bar["high"] for bar in candles],
        low=[bar["low"] for bar in candles], close=[bar["close"] for bar in candles],
        increasing_line_color="#137b69", decreasing_line_color="#bf4545", name="OHLC",
    ), row=1, col=1)
    for name, attribute, color in [("EMA 20", "ema_fast", "#d19a22"), ("EMA 50", "ema_slow", "#327ba5")]:
        figure.add_trace(go.Scatter(x=indicator_dates, y=[getattr(signal, attribute) for signal in analyses],
                                   name=name, mode="lines", line={"color": color, "width": 1.5}), row=1, col=1)
    for action, label, color, symbol in [
        ("LONG", "CALL bias" if options else "BUY / LONG", "#137b69", "triangle-up"),
        ("SHORT", "PUT bias" if options else "SELL / SHORT", "#bf4545", "triangle-down"),
    ]:
        indices = [index for index, signal in enumerate(analyses) if signal.action == action
                   and index > 0 and analyses[index - 1].action != action]
        figure.add_trace(go.Scatter(
            x=[indicator_dates[index] for index in indices], y=[candles[index + 49]["close"] for index in indices],
            mode="markers", name=label, marker={"color": color, "symbol": symbol, "size": 11},
            customdata=[datetime.fromtimestamp((int(candles[index + 49]["time"]) + INTERVAL_MS[interval]) / 1000,
                                               timezone.utc).isoformat() for index in indices],
            hovertemplate=label + "<br>%{y}<br>Available after %{customdata}<extra></extra>",
        ), row=1, col=1)
    figure.add_trace(go.Scatter(x=indicator_dates, y=[signal.rsi for signal in analyses], name="RSI 14",
                               mode="lines", line={"color": "#64767b", "width": 1.5}), row=2, col=1)
    for level in [30, 50, 70]:
        figure.add_hline(y=level, line_width=1, line_dash="dot", line_color="#dce5e8", row=2, col=1)
    latest = analyses[-1]
    if not options and latest.action != "NO TRADE":
        for label, value, color in [("Entry", latest.entry, "#64767b"), ("Stop", latest.stop, "#bf4545"),
                                    ("Target", latest.target, "#137b69")]:
            figure.add_hline(y=value, line_dash="dot", line_color=color, annotation_text=label, row=1, col=1)
    figure.update_layout(height=480, margin={"l": 8, "r": 8, "t": 12, "b": 8},
                         paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#ffffff",
                         font={"family": "IBM Plex Sans, sans-serif", "color": "#192b30", "size": 11},
                         legend={"orientation": "h", "y": 1.12}, dragmode="pan",
                         xaxis_rangeslider_visible=False, hovermode="x unified")
    figure.update_xaxes(showgrid=False)
    figure.update_yaxes(gridcolor="#edf1f3", fixedrange=True)
    figure.update_yaxes(range=[0, 100], title_text="RSI", row=2, col=1)
    return figure