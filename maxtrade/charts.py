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
                  interval: str, options: bool = False, chart_type: str = "Candles",
                  indicators: tuple[str, ...] = ("EMA 20", "EMA 50", "Volume", "RSI 14"),
                  theme: str = "Dark", logarithmic: bool = False, visible_bars: int = 80) -> go.Figure:
    dates = [datetime.fromtimestamp(int(candle["time"]) / 1000, timezone.utc) for candle in candles]
    indicator_dates = dates[49:49 + len(analyses)]
    dark = theme == "Dark"
    background, foreground, grid = ("#16181d", "#d8dde5", "#292d35") if dark else ("#ffffff", "#242933", "#edf0f4")
    rising, falling = "#00b890", "#f45164"
    panels = [name for name in ("Volume", "RSI 14") if name in indicators]
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
    for row, panel in enumerate(panels, start=2):
        if panel == "Volume":
            figure.add_trace(go.Bar(x=dates, y=[bar.get("volume", 0) for bar in candles], name="Volume",
                                   marker_color=[rising if bar["close"] >= bar["open"] else falling for bar in candles],
                                   opacity=.65), row=row, col=1)
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