from __future__ import annotations

import time
from typing import Any

import requests


API_ROOT = "https://api.coindcx.com"
PUBLIC_ROOT = "https://public.coindcx.com"
TIMEOUT_SECONDS = 12
INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
               "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
               **{f'{minutes}m': minutes * 60_000 for minutes in (2, 3, 45, 120, 180)}}


def normalize_candles(
    payload: Any, interval: str, count: int = 120, now_ms: int | None = None,
    include_open: bool = False,
) -> list[dict[str, Any]]:
    """Return recent, completed bars in chronological order (API times are milliseconds)."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    duration = INTERVAL_MS[interval]
    if isinstance(payload, dict):
        if payload.get("s", "ok") != "ok":
            raise ValueError("Exchange rejected the candle request")
        payload = payload.get("data")
    if not isinstance(payload, list):
        raise ValueError("Unexpected candle response")
    try:
        bars = {int(bar["time"]): bar for bar in payload}
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Candle response has invalid timestamps") from error
    completed = [bars[t] for t in sorted(bars) if t >= 0 and
                 (t <= now_ms if include_open else t + duration <= now_ms)][-count:]
    if completed and now_ms - int(completed[-1]["time"]) > 2 * duration:
        raise ValueError("Candle feed is stale; no signal generated")
    return completed


def aggregate_candles(candles: list[dict[str, Any]], source_interval: str, interval: str,
                      now_ms: int | None = None) -> list[dict[str, Any]]:
    source_duration, duration = INTERVAL_MS[source_interval], INTERVAL_MS[interval]
    if duration % source_duration or duration < source_duration:
        raise ValueError('Chart interval must be a multiple of the source interval')
    groups: dict[int, list[dict[str, Any]]] = {}
    for candle in candles:
        timestamp = int(candle["time"])
        bucket = timestamp // duration * duration
        groups.setdefault(bucket, []).append(candle)
    result = []
    for bucket, bars in sorted(groups.items()):
        bars = sorted(bars, key=lambda bar: int(bar["time"]))
        expected = duration // source_duration
        if now_ms is not None and bucket <= now_ms < bucket + duration:
            expected = (now_ms - bucket) // source_duration + 1
        if [int(bar["time"]) for bar in bars] != [bucket + index * source_duration for index in range(expected)]:
            continue
        result.append({
            "time": bucket, "open": float(bars[0]["open"]),
            "high": max(float(bar["high"]) for bar in bars),
            "low": min(float(bar["low"]) for bar in bars),
            "close": float(bars[-1]["close"]),
            "volume": sum(float(bar["volume"]) for bar in bars),
        })
    return result


def aggregate_four_hour_candles(candles: list[dict[str, Any]], now_ms: int | None = None) -> list[dict[str, Any]]:
    return aggregate_candles(candles, '1h', '4h', now_ms)


class CoinDCXClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "MaxTradeResearch/0.1"})

    def _get(self, path: str, params: dict[str, Any] | None = None, root: str = API_ROOT) -> Any:
        response = self.session.get(f"{root}{path}", params=params, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()

    def spot_markets(self) -> list[dict[str, Any]]:
        return self._get("/exchange/v1/markets_details")

    def spot_tickers(self) -> list[dict[str, Any]]:
        return self._get("/exchange/ticker")

    def spot_candles(self, pair: str, interval: str, count: int = 120,
                     include_open: bool = False) -> list[dict[str, Any]]:
        if interval in ('2m', '3m', '45m', '120m', '180m'):
            return self.custom_candles(pair, interval, count, include_open)
        if interval in ('5m', '30m'):
            source = '1m' if interval == '5m' else '15m'
            now_ms = int(time.time() * 1000)
            source_count = min(count * (INTERVAL_MS[interval] // INTERVAL_MS[source]) + 5, 499)
            candles = self.spot_candles(pair, source, count=source_count, include_open=include_open)
            bars = aggregate_candles(candles, source, interval, now_ms if include_open else None)
            return normalize_candles(bars, interval, count, now_ms, include_open=include_open)
        if interval == "4h":
            now_ms = int(time.time() * 1000)
            hourly = self.spot_candles(pair, "1h", count=count * 4 + 4, include_open=include_open)
            bars = aggregate_four_hour_candles(hourly, now_ms=now_ms if include_open else None)
            return normalize_candles(bars, "4h", count, now_ms, include_open=include_open)
        end_time = int(time.time() * 1000)
        interval_ms = INTERVAL_MS[interval]
        payload = self._get(
            "/market_data/candles",
            {
                "pair": pair,
                "interval": interval,
                "startTime": end_time - (count + 1) * interval_ms,
                "endTime": end_time,
                "limit": min(count + 1, 500),
            },
        )
        return normalize_candles(payload, interval, count, end_time, include_open=include_open)

    def futures_instruments(self) -> list[str]:
        return self._get(
            "/exchange/v1/derivatives/futures/data/active_instruments",
            {"margin_currency_short_name[]": "USDT"},
        )

    def futures_tickers(self) -> dict[str, dict[str, Any]]:
        data = self._get("/market_data/v3/current_prices/futures/rt", root=PUBLIC_ROOT)
        if not isinstance(data, dict) or not isinstance(data.get("prices"), dict):
            raise ValueError("Unexpected futures ticker response")
        if int(time.time() * 1000) - float(data["ts"]) > 300_000:
            raise ValueError("Futures ticker feed is stale")
        return data["prices"]

    def futures_candles(self, pair: str, interval: str, count: int = 120,
                        include_open: bool = False) -> list[dict[str, Any]]:
        if interval in ('2m', '3m', '45m', '120m', '180m'):
            return self.custom_candles(pair, interval, count, include_open, futures=True)
        end_time = int(time.time())
        interval_minutes = INTERVAL_MS[interval] // 60_000
        payload = self._get(
            "/market_data/candlesticks",
            {
                "pair": pair,
                "from": end_time - (count + 1) * interval_minutes * 60,
                "to": end_time,
                "resolution": '1D' if interval == '1d' else str(interval_minutes),
                "pcode": "f",
            },
            root=PUBLIC_ROOT,
        )
        return normalize_candles(payload, interval, count, end_time * 1000, include_open=include_open)

    def custom_candles(self, pair: str, interval: str, count: int, include_open: bool,
                       futures: bool = False) -> list[dict[str, Any]]:
        duration = INTERVAL_MS[interval]
        source = next(value for value in ('1h', '15m', '5m', '1m')
                      if duration % INTERVAL_MS[value] == 0)
        now_ms = int(time.time() * 1000)
        ratio = duration // INTERVAL_MS[source]
        method = self.futures_candles if futures else self.spot_candles
        candles = method(pair, source, count=min(count * ratio + ratio, 499), include_open=include_open)
        bars = aggregate_candles(candles, source, interval, now_ms if include_open else None)
        return normalize_candles(bars, interval, count, now_ms, include_open=include_open)