from __future__ import annotations

import time
from typing import Any

import requests


API_ROOT = "https://api.coindcx.com"
PUBLIC_ROOT = "https://public.coindcx.com"
TIMEOUT_SECONDS = 12
INTERVAL_MS = {"1h": 3_600_000, "4h": 14_400_000}


def normalize_candles(
    payload: Any, interval: str, count: int = 120, now_ms: int | None = None
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
    completed = [bars[t] for t in sorted(bars) if t + duration <= now_ms][-count:]
    if completed and now_ms - int(completed[-1]["time"]) > 2 * duration:
        raise ValueError("Candle feed is stale; no signal generated")
    return completed


def aggregate_four_hour_candles(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[int, list[dict[str, Any]]] = {}
    for candle in candles:
        timestamp = int(candle["time"])
        bucket = timestamp // INTERVAL_MS["4h"] * INTERVAL_MS["4h"]
        groups.setdefault(bucket, []).append(candle)
    result = []
    for bucket, bars in sorted(groups.items()):
        bars = sorted(bars, key=lambda bar: int(bar["time"]))
        if [int(bar["time"]) for bar in bars] != [bucket + i * INTERVAL_MS["1h"] for i in range(4)]:
            continue
        result.append({
            "time": bucket, "open": float(bars[0]["open"]),
            "high": max(float(bar["high"]) for bar in bars),
            "low": min(float(bar["low"]) for bar in bars),
            "close": float(bars[-1]["close"]),
            "volume": sum(float(bar["volume"]) for bar in bars),
        })
    return result


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

    def spot_candles(self, pair: str, interval: str, count: int = 120) -> list[dict[str, Any]]:
        if interval == "4h":
            hourly = self.spot_candles(pair, "1h", count=count * 4 + 4)
            bars = aggregate_four_hour_candles(hourly)
            return normalize_candles(bars, "4h", count)
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
        return normalize_candles(payload, interval, count, end_time)

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

    def futures_candles(self, pair: str, interval: str, count: int = 120) -> list[dict[str, Any]]:
        end_time = int(time.time())
        interval_minutes = {"1h": 60, "4h": 240}[interval]
        payload = self._get(
            "/market_data/candlesticks",
            {
                "pair": pair,
                "from": end_time - (count + 1) * interval_minutes * 60,
                "to": end_time,
                "resolution": str(interval_minutes),
                "pcode": "f",
            },
            root=PUBLIC_ROOT,
        )
        return normalize_candles(payload, interval, count, end_time * 1000)