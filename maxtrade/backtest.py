from dataclasses import dataclass
from math import isfinite
from typing import Any

import pandas as pd
from backtesting import Strategy
from backtesting.lib import FractionalBacktest

from maxtrade.charts import chart_analysis
from maxtrade.coindcx import INTERVAL_MS


@dataclass(frozen=True)
class ReplaySettings:
    cash: float = 10000
    risk_pct: float = 1
    allocation_pct: float = 25
    fee_bps: float = 10
    slippage_bps: float = 5
    funding_bps_8h: float = 0

    def validate(self) -> None:
        values = [self.cash, self.risk_pct, self.allocation_pct, self.fee_bps,
                  self.slippage_bps, self.funding_bps_8h]
        if not all(isfinite(value) for value in values):
            raise ValueError("Simulation settings must be finite")
        if self.cash <= 0 or not 0 < self.risk_pct <= 5 or not 0 < self.allocation_pct <= 95:
            raise ValueError("Use positive capital, risk up to 5%, and allocation up to 95%")
        if not all(0 <= value <= 100 for value in values[3:]):
            raise ValueError("Cost assumptions must be between 0 and 100 basis points")


def replay(candles: list[dict[str, Any]], interval: str, allow_short: bool,
           settings: ReplaySettings = ReplaySettings()) -> dict[str, Any]:
    settings.validate()
    if not allow_short and settings.funding_bps_8h:
        raise ValueError("Spot simulations cannot include futures funding")
    if interval not in INTERVAL_MS:
        raise ValueError("Unsupported replay interval")
    duration = INTERVAL_MS[interval]
    for index, candle in enumerate(candles):
        timestamp = candle['time']
        if (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
                or not isfinite(timestamp) or timestamp % duration
                or (index and timestamp != candles[index - 1]['time'] + duration)):
            raise ValueError("Replay requires ordered contiguous aligned candles; gaps are not filled")
    analyses = chart_analysis(candles, interval, allow_short)
    if len(candles) < 52:
        raise ValueError("At least 52 completed candles are required for replay")
    frame = pd.DataFrame(candles).rename(columns={
        "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    if "Volume" not in frame:
        frame["Volume"] = float("nan")
    frame.index = pd.to_datetime(frame.pop("time"), unit="ms", utc=True)
    unit = 1e-8
    cost = (settings.fee_bps + settings.slippage_bps) / 10000

    class ReplayStrategy(Strategy):
        def init(self):
            pass

        def next(self):
            index = len(self.data) - 50
            if index <= 0 or len(self.data) >= len(candles) or self.position or self.orders:
                return
            signal = analyses[index]
            if signal.action == "NO TRADE" or signal.action == analyses[index - 1].action:
                return
            if signal.entry is None or signal.stop is None or signal.target is None:
                return
            if min(signal.stop, signal.target) <= 0:
                return
            distance = abs(signal.entry - signal.stop) / signal.entry
            if distance <= 0:
                return
            allocation = min(settings.allocation_pct / 100, settings.risk_pct / 100 / (distance + 2 * cost))
            order = self.buy if signal.action == "LONG" else self.sell
            order(size=allocation, sl=signal.stop * unit, tp=signal.target * unit,
                  tag=signal.action)

    stats = FractionalBacktest(frame, ReplayStrategy, cash=settings.cash,
                              commission=cost, margin=1, trade_on_close=False,
                              exclusive_orders=True, finalize_trades=True,
                              fractional_unit=unit).run()
    trades = stats["_trades"].copy()
    equity = stats["_equity_curve"][["Equity"]].copy()
    funding = pd.Series(0.0, index=equity.index)
    charges = []
    for _, trade in trades.iterrows():
        entry, exit_bar = int(trade["EntryBar"]), int(trade["ExitBar"])
        hourly_cost = abs(trade["Size"]) * trade["EntryPrice"] * settings.funding_bps_8h / 10000
        bar_cost = hourly_cost * INTERVAL_MS[interval] / (8 * 3600000)
        funding.iloc[entry:exit_bar] += bar_cost
        charges.append(bar_cost * (exit_bar - entry))
    trades["Funding estimate"] = charges
    trades["Net PnL"] = trades["PnL"] - trades["Funding estimate"]
    equity["Equity"] -= funding.cumsum()
    equity["Drawdown %"] = (1 - equity["Equity"] / equity["Equity"].cummax()) * 100
    wins = int((trades["Net PnL"] > 0).sum())
    return {"trades": trades, "equity": equity,
            "return_pct": (equity["Equity"].iloc[-1] / settings.cash - 1) * 100,
            "drawdown_pct": float(equity["Drawdown %"].max()),
            "win_rate": wins / len(trades) * 100 if len(trades) else None,
            "funding": float(sum(charges)), "settings": settings}