from __future__ import annotations

from typing import Any, Callable

from requests import RequestException

from maxtrade.coindcx import CoinDCXClient
from maxtrade.signals import analyze_candles


QUOTE_CURRENCIES = {"INR", "USDT", "USDC"}


def _volume(ticker: dict[str, Any]) -> float:
    try:
        return float(ticker.get("volume", 0))
    except (TypeError, ValueError):
        return 0.0


def scan_spot(client: CoinDCXClient, interval: str, limit: int,
              progress: Callable[[int, int, str], None] | None = None,
              gold_only: bool = False) -> list[dict[str, Any]]:
    markets = {
        market.get("coindcx_name"): market
        for market in client.spot_markets()
        if market.get("status") == "active"
        and market.get("base_currency_short_name") in QUOTE_CURRENCIES
        and market.get("coindcx_name")
        and (not gold_only or market.get("target_currency_short_name") in {"PAXG", "XAUT"})
    }
    tickers = {
        ticker.get("market"): ticker
        for ticker in client.spot_tickers()
        if ticker.get("market") in markets
    }

    candidates = sorted(tickers.values(), key=_volume, reverse=True)[:limit]
    if not candidates:
        raise ValueError("No active spot markets matched the ticker feed. Try again later.")
    results = []
    for ticker in candidates:
        market_name = ticker["market"]
        market = markets[market_name]
        try:
            candles = client.spot_candles(market["pair"], interval)
            signal = analyze_candles(candles)
            results.append(
                {
                    "Market": market_name,
                    "Pair": market["pair"],
                    "Signal candle time": int(candles[-1]["time"]),
                    "Price": float(ticker["last_price"]),
                    "24h volume": _volume(ticker),
                    "Signal": signal.action,
                    "RSI": signal.rsi,
                    "Entry": signal.entry,
                    "Stop": signal.stop,
                    "Target": signal.target,
                    "Reason": signal.reason,
                }
            )
        except (RequestException, KeyError, TypeError, ValueError) as error:
            results.append(
                {
                    "Market": market_name,
                    "Price": None,
                    "24h volume": _volume(ticker),
                    "Signal": "DATA ERROR",
                    "RSI": None,
                    "Entry": None,
                    "Stop": None,
                    "Target": None,
                    "Reason": str(error),
                }
            )
        if progress:
            progress(len(results), len(candidates), market_name)
    return results


def scan_futures(client: CoinDCXClient, interval: str, limit: int,
                 progress: Callable[[int, int, str], None] | None = None,
                 gold_only: bool = False) -> list[dict[str, Any]]:
    instruments = client.futures_instruments()
    tickers = client.futures_tickers()
    # Use reported volume only as a discovery heuristic, not a liquidity guarantee.
    selected = sorted(
        (pair for pair in instruments if pair in tickers
         and (not gold_only or pair.split("_")[0] in {"B-PAXG", "B-XAUT"})),
        key=lambda pair: _volume({"volume": tickers[pair].get("v", 0)}),
        reverse=True,
    )[:limit]
    if not selected:
        raise ValueError("No active futures instruments matched the ticker feed. Try again later.")
    results = []
    for pair in selected:
        try:
            market_data = tickers[pair]
            candles = client.futures_candles(pair, interval)
            signal = analyze_candles(candles, allow_short=True)
            results.append(
                {
                    "Market": pair,
                    "Pair": pair,
                    "Signal candle time": int(candles[-1]["time"]),
                    "Price": float(market_data.get("mp", market_data.get("ls", 0))),
                    "24h volume": float(market_data.get("v", 0) or 0),
                    "Signal": signal.action,
                    "RSI": signal.rsi,
                    "Entry": signal.entry,
                    "Stop": signal.stop,
                    "Target": signal.target,
                    "Reason": signal.reason,
                }
            )
        except (RequestException, KeyError, TypeError, ValueError) as error:
            results.append(
                {
                    "Market": pair,
                    "Price": None,
                    "24h volume": None,
                    "Signal": "DATA ERROR",
                    "RSI": None,
                    "Entry": None,
                    "Stop": None,
                    "Target": None,
                    "Reason": str(error),
                }
            )
        if progress:
            progress(len(results), len(selected), pair)
    return results