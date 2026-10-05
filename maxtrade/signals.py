from dataclasses import dataclass
from math import isfinite
from typing import Any


@dataclass(frozen=True)
class TradeSignal:
    action: str
    entry: float | None
    stop: float | None
    target: float | None
    rsi: float
    ema_fast: float
    ema_slow: float
    reason: str


def _ema(values: list[float], period: int) -> float:
    multiplier = 2 / (period + 1)
    value = sum(values[:period]) / period
    for price in values[period:]:
        value = (price - value) * multiplier + value
    return value


def _rsi(values: list[float], period: int = 14) -> float:
    changes = [current - previous for previous, current in zip(values, values[1:])]
    if len(changes) < period:
        raise ValueError("Not enough candle data to calculate RSI")

    gains = [max(change, 0) for change in changes[:period]]
    losses = [max(-change, 0) for change in changes[:period]]
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period

    for change in changes[period:]:
        average_gain = (average_gain * (period - 1) + max(change, 0)) / period
        average_loss = (average_loss * (period - 1) + max(-change, 0)) / period

    if average_loss == 0:
        return 100.0 if average_gain else 50.0
    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def _atr(candles: list[dict[str, Any]], period: int = 14) -> float:
    ranges = []
    for previous, current in zip(candles, candles[1:]):
        high = float(current["high"])
        low = float(current["low"])
        previous_close = float(previous["close"])
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    if len(ranges) < period:
        raise ValueError("Not enough candle data to calculate ATR")
    return sum(ranges[-period:]) / period


def analyze_candles(candles: list[dict[str, Any]], allow_short: bool = False) -> TradeSignal:
    if len(candles) < 50:
        raise ValueError("At least 50 candles are required for a signal")

    closes = [float(candle["close"]) for candle in candles]
    for candle in candles:
        high, low, close = (float(candle[key]) for key in ("high", "low", "close"))
        if not all(isfinite(value) and value > 0 for value in (high, low, close)):
            raise ValueError("Candle prices must be positive and finite")
        if not low <= close <= high:
            raise ValueError("Candle close must be between low and high")
    last_price = closes[-1]
    fast = _ema(closes, 20)
    slow = _ema(closes, 50)
    rsi = _rsi(closes)
    atr = _atr(candles)

    action = "NO TRADE"
    reason = "Trend and momentum are not aligned."
    entry = stop = target = None

    if last_price > fast > slow and 50 <= rsi <= 70:
        action = "LONG"
        entry = last_price
        stop = entry - 1.5 * atr
        target = entry + 3 * atr
        reason = "Price is above the 20 EMA, which is above the 50 EMA; RSI confirms positive momentum."
    elif allow_short and last_price < fast < slow and 30 <= rsi <= 50:
        action = "SHORT"
        entry = last_price
        stop = entry + 1.5 * atr
        target = entry - 3 * atr
        reason = "Price is below the 20 EMA, which is below the 50 EMA; RSI confirms negative momentum."

    return TradeSignal(action, entry, stop, target, rsi, fast, slow, reason)