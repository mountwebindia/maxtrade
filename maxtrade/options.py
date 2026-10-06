from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from time import time
from typing import Any, Callable

import requests

from maxtrade.coindcx import aggregate_four_hour_candles, normalize_candles
from maxtrade.signals import analyze_candles


def finite_number(value: Any) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError("Non-finite options market data")
    return number


class DeribitClient:
    def __init__(self) -> None:
        self.session = requests.Session()

    def _get(self, method: str, **params: Any) -> Any:
        response = self.session.get(
            f"https://www.deribit.com/api/v2/public/{method}", params=params, timeout=12
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "error" in payload or "result" not in payload:
            raise ValueError("Deribit returned an invalid response or API error")
        return payload["result"]

    def instruments(self, currency: str) -> list[dict[str, Any]]:
        result = self._get("get_instruments", currency=currency, kind="option", expired="false")
        if not isinstance(result, list):
            raise ValueError("Invalid options instrument list")
        return result

    def summaries(self, currency: str) -> list[dict[str, Any]]:
        result = self._get("get_book_summary_by_currency", currency=currency, kind="option")
        if not isinstance(result, list):
            raise ValueError("Invalid options summary list")
        return result

    def ticker(self, instrument: str) -> dict[str, Any]:
        result = self._get("ticker", instrument_name=instrument)
        if not isinstance(result, dict) or result.get("instrument_name") != instrument:
            raise ValueError("Invalid or mismatched options ticker")
        return result

    def underlying_candles(self, currency: str, interval: str) -> list[dict[str, Any]]:
        now_ms = int(time() * 1000)
        count = 500 if interval == "4h" else 130
        payload = self._get(
            "get_tradingview_chart_data", instrument_name=f"{currency}-PERPETUAL",
            resolution="60", start_timestamp=now_ms - count * 3600000, end_timestamp=now_ms,
        )
        fields = ["ticks", "open", "high", "low", "close", "volume"]
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise ValueError("Underlying candle history unavailable")
        arrays = [payload.get(field) for field in fields]
        if not all(isinstance(values, list) for values in arrays) or len({len(values) for values in arrays}) != 1:
            raise ValueError("Invalid underlying candle arrays")
        candles = [dict(zip(["time", *fields[1:]], values)) for values in zip(*arrays)]
        hourly = normalize_candles(candles, "1h", count=count, now_ms=now_ms)
        if interval == "4h":
            return normalize_candles(aggregate_four_hour_candles(hourly), "4h", now_ms=now_ms)
        return hourly


def scan_options(client: DeribitClient, currency: str, interval: str, limit: int,
                 progress: Callable[[int, int, str], None] | None = None,
                 now_ms: int | None = None) -> list[dict[str, Any]]:
    if currency not in {"BTC", "ETH"} or interval not in {"1h", "4h"} or not 1 <= limit <= 30:
        raise ValueError("Unsupported options scan settings")
    supplied_time = now_ms
    now_ms = int(time() * 1000) if now_ms is None else now_ms
    instruments = {}
    for instrument in client.instruments(currency):
        days = (finite_number(instrument["expiration_timestamp"]) - now_ms) / 86400000
        if (instrument.get("is_active") is True and instrument.get("state") == "open"
                and instrument.get("kind") == "option" and instrument.get("base_currency") == currency
                and instrument.get("quote_currency") == currency
                and instrument.get("option_type") in {"call", "put"} and 7 <= days <= 45):
            instruments[instrument["instrument_name"]] = instrument
    summaries = {row["instrument_name"]: row for row in client.summaries(currency)
                 if row.get("instrument_name") in instruments}
    selected = sorted(summaries, key=lambda name: finite_number(summaries[name].get("volume_usd", 0)), reverse=True)[:limit]
    if not selected:
        raise ValueError("No open 7-45 day options contracts matched the Deribit feed")
    trend = analyze_candles(client.underlying_candles(currency, interval), allow_short=True)
    results = []
    for name in selected:
        instrument = instruments[name]
        row = {
            "Market": name, "Source": "Deribit", "Premium currency": currency,
            "Type": instrument["option_type"].upper(), "Strike USD": finite_number(instrument["strike"]),
            "Expiry UTC": datetime.fromtimestamp(instrument["expiration_timestamp"] / 1000, timezone.utc).isoformat(),
            "Days to expiry": round((instrument["expiration_timestamp"] - now_ms) / 86400000, 2),
            "Signal": "DATA ERROR", "Price": None, "Entry": None, "Stop": None, "Target": None,
            "Underlying trend": trend.action, "RSI": trend.rsi,
        }
        try:
            ticker = client.ticker(name)
            quote_now = int(time() * 1000) if supplied_time is None else supplied_time
            age = quote_now - finite_number(ticker["timestamp"])
            if age < -30000 or age > 300000 or ticker.get("state") != "open":
                raise ValueError("Stale options quote or closed contract")
            bid, ask = finite_number(ticker["best_bid_price"]), finite_number(ticker["best_ask_price"])
            if not 0 < bid <= ask:
                raise ValueError("Missing, zero, or crossed two-sided quote")
            if finite_number(ticker["best_bid_amount"]) <= 0 or finite_number(ticker["best_ask_amount"]) <= 0:
                raise ValueError("No size available at bid or ask")
            spread = (ask - bid) / ((ask + bid) / 2) * 100
            greeks = {key: finite_number(ticker["greeks"][key]) for key in ["delta", "gamma", "theta", "vega"]}
            iv = finite_number(ticker["mark_iv"])
            mark = finite_number(ticker["mark_price"])
            interest = finite_number(ticker["open_interest"])
            volume = finite_number(ticker["stats"]["volume"])
            if iv <= 0 or mark <= 0 or interest < 0 or volume < 0 or not -1 <= greeks["delta"] <= 1:
                raise ValueError("Invalid options valuation or liquidity data")
            aligned = ((trend.action == "LONG" and instrument["option_type"] == "call" and greeks["delta"] > 0)
                       or (trend.action == "SHORT" and instrument["option_type"] == "put" and greeks["delta"] < 0))
            eligible = spread <= 10 and interest >= 10 and volume > 0 and 0.25 <= abs(greeks["delta"]) <= 0.75
            row.update({
                "Price": mark, "Bid": bid, "Ask": ask, "Spread %": round(spread, 2),
                "IV %": iv, "Delta": greeks["delta"], "Gamma": greeks["gamma"],
                "Theta": greeks["theta"], "Vega": greeks["vega"], "Open interest": interest,
                "24h volume": volume, "Quote UTC": datetime.fromtimestamp(ticker["timestamp"] / 1000, timezone.utc).isoformat(),
                "Signal": f"WATCH {row['Type']}" if aligned and eligible else "NO TRADE",
                "Reason": "Trend-aligned research candidate; not a valuation or execution signal."
                          if aligned and eligible else "Underlying trend or spread/delta/liquidity filters do not qualify.",
            })
        except (requests.RequestException, KeyError, TypeError, ValueError) as error:
            row["Reason"] = str(error)
        results.append(row)
        if progress:
            progress(len(results), len(selected), name)
    return results