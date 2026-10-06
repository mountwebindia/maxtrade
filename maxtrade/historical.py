from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from maxtrade.coindcx import aggregate_four_hour_candles

SOURCE = "Coinbase Exchange"
ROOT = "https://api.exchange.coinbase.com"
DOCUMENTATION = "https://docs.cdp.coinbase.com/exchange/reference/exchangerestapi_getproductcandles"
GRANULARITIES = {"1h": 3600, "1d": 86400}


def four_hour_coverage(bars: list[dict[str, Any]], start: int, end: int) -> dict[str, Any]:
    if start < 0 or end <= start or start % 14400 or end % 14400:
        raise ValueError("Four-hour coverage requires aligned UTC boundaries")
    validated = []
    for bar in bars:
        timestamp = bar["time"]
        if isinstance(timestamp, bool) or timestamp % 3600000:
            raise ValueError("Hourly timestamps must be aligned milliseconds")
        validated.extend(parse_candles([[timestamp / 1000, bar["low"], bar["high"],
                                        bar["open"], bar["close"], bar["volume"]]], start, end, 3600))
    timestamps = [bar["time"] for bar in validated]
    if len(timestamps) != len(set(timestamps)):
        raise ValueError("Duplicate hourly candle in four-hour derivation")
    derived = aggregate_four_hour_candles(validated)
    report = quality_report(derived, start, end, 14400)
    report.update(interval="4h", method="UTC-aligned four contiguous completed hourly candles",
                  source_interval="1h", version="four-hour-coverage-v1")
    return report


def parse_candles(payload: Any, start: int, end: int, duration: int) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or len(payload) > 301:
        raise ValueError("Unexpected historical candle response")
    bars = {}
    for row in payload:
        if not isinstance(row, list) or len(row) != 6 or any(isinstance(value, bool) for value in row):
            raise ValueError("Invalid historical candle schema")
        timestamp, low, high, opening, close, volume = map(float, row)
        if (not all(math.isfinite(value) for value in (timestamp, low, high, opening, close, volume))
                or timestamp < 0 or timestamp != int(timestamp) or int(timestamp) % duration
                or min(low, high, opening, close) <= 0 or volume < 0
                or not low <= min(opening, close) <= max(opening, close) <= high):
            raise ValueError("Invalid historical candle values")
        if not start <= timestamp < end:
            continue
        bar = {"time": int(timestamp) * 1000, "open": opening, "high": high,
               "low": low, "close": close, "volume": volume}
        if timestamp in bars and bars[timestamp] != bar:
            raise ValueError("Conflicting historical duplicate")
        bars[timestamp] = bar
    return [bars[timestamp] for timestamp in sorted(bars)]


def quality_report(bars: list[dict[str, Any]], start: int, end: int, duration: int) -> dict[str, Any]:
    timestamps = {bar["time"] // 1000 for bar in bars}
    missing_ranges = []
    gap_start = None
    for timestamp in range(start, end, duration):
        if timestamp not in timestamps and gap_start is None:
            gap_start = timestamp
        elif timestamp in timestamps and gap_start is not None:
            missing_ranges.append([gap_start, timestamp])
            gap_start = None
    if gap_start is not None:
        missing_ranges.append([gap_start, end])
    expected = (end - start) // duration
    return {"expected_bars": expected, "observed_bars": len(timestamps),
            "missing_bars": expected - len(timestamps), "missing_ranges_seconds": missing_ranges,
            "coverage_pct": 100 * len(timestamps) / expected,
            "first_bar_ms": min((bar["time"] for bar in bars), default=None),
            "last_bar_ms": max((bar["time"] for bar in bars), default=None),
            "status": "COMPLETE" if len(timestamps) == expected else "INCOMPLETE"}


def ingest(product: str, interval: str, start: int, end: int, database: Path,
           session: requests.Session | None = None) -> dict[str, Any]:
    duration = GRANULARITIES[interval]
    if product not in {"BTC-USD", "ETH-USD"}:
        raise ValueError("Historical research supports BTC-USD and ETH-USD only")
    if (start < 0 or end <= start or start % duration or end % duration
            or end > int(datetime.now(timezone.utc).timestamp()) // duration * duration
            or end - start > 366 * 11 * 86400):
        raise ValueError("Use aligned, completed historical dates within an eleven-year window")
    database.parent.mkdir(parents=True, exist_ok=True)
    own_session = session is None
    client = session if session is not None else requests.Session()
    try:
        with sqlite3.connect(database) as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS historical_pages (
                    product TEXT, interval TEXT, start INTEGER, end INTEGER,
                    retrieved TEXT NOT NULL, sha256 TEXT NOT NULL, raw BLOB NOT NULL,
                    PRIMARY KEY(product, interval, start, end));
                CREATE TABLE IF NOT EXISTS historical_candles (
                    product TEXT, interval TEXT, time INTEGER, open REAL, high REAL,
                    low REAL, close REAL, volume REAL,
                    PRIMARY KEY(product, interval, time));
                CREATE TABLE IF NOT EXISTS historical_reports (
                    product TEXT, interval TEXT, start INTEGER, end INTEGER,
                    report TEXT NOT NULL, PRIMARY KEY(product, interval, start, end));
            """)
            hashes = []
            bars = []
            for page_start in range(start, end, 300 * duration):
                page_end = min(page_start + 300 * duration, end)
                cached = connection.execute(
                    "SELECT raw, sha256, retrieved FROM historical_pages WHERE product=? AND interval=? AND start=? AND end=?",
                    (product, interval, page_start, page_end)).fetchone()
                if cached:
                    raw, digest, retrieved = cached
                    if hashlib.sha256(raw).hexdigest() != digest:
                        raise ValueError("Historical raw checksum mismatch")
                else:
                    response = client.get(f"{ROOT}/products/{product}/candles", params={
                        "granularity": duration,
                        "start": datetime.fromtimestamp(page_start, timezone.utc).isoformat(),
                        "end": datetime.fromtimestamp(page_end, timezone.utc).isoformat()},
                        timeout=20, allow_redirects=False)
                    if response.status_code != 200:
                        raise ValueError(f"Historical provider failed (HTTP {response.status_code}); rerun to resume")
                    raw = response.content
                    if len(raw) > 200000:
                        raise ValueError("Historical response exceeds size limit")
                    digest = hashlib.sha256(raw).hexdigest()
                    retrieved = datetime.now(timezone.utc).isoformat()
                page_bars = parse_candles(json.loads(raw), page_start, page_end, duration)
                with connection:
                    connection.execute("INSERT OR IGNORE INTO historical_pages VALUES (?, ?, ?, ?, ?, ?, ?)",
                                       (product, interval, page_start, page_end, retrieved, digest, raw))
                    for bar in page_bars:
                        values = tuple(bar[field] for field in ("time", "open", "high", "low", "close", "volume"))
                        existing = connection.execute(
                            "SELECT open, high, low, close, volume FROM historical_candles WHERE product=? AND interval=? AND time=?",
                            (product, interval, bar["time"])).fetchone()
                        if existing and existing != values[1:]:
                            raise ValueError("Historical stored candle conflict; no silent overwrite")
                        connection.execute("INSERT OR IGNORE INTO historical_candles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                           (product, interval, *values))
                bars.extend(page_bars)
                hashes.append({"start": page_start, "end": page_end, "sha256": digest, "retrieved": retrieved})
            report = quality_report(bars, start, end, duration)
            if interval == "1h" and start % 14400 == 0 and end % 14400 == 0:
                report["derived_four_hour"] = four_hour_coverage(bars, start, end)
            report.update(source=SOURCE, product=product, interval=interval, quote_currency="USD",
                          volume_unit=product.split("-")[0], start=start, end=end, documentation=DOCUMENTATION,
                          pages=hashes, generated=datetime.now(timezone.utc).isoformat(),
                          limitations=["Not CoinDCX/USDT prices or execution evidence",
                                       "Missing buckets are not filled; complete coverage is not guaranteed",
                                       "Provider licensing/redistribution review remains required",
                                       "Historical downloads are not contemporaneous decision evidence"])
            connection.execute("INSERT OR REPLACE INTO historical_reports VALUES (?, ?, ?, ?, ?)",
                               (product, interval, start, end, json.dumps(report, allow_nan=False)))
            connection.commit()
            return report
    finally:
        if own_session:
            client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded historical research ingestion; no trades or AI calls")
    parser.add_argument("--product", choices=["BTC-USD", "ETH-USD"], required=True)
    parser.add_argument("--interval", choices=list(GRANULARITIES), default="1d")
    parser.add_argument("--start", required=True, help="UTC date YYYY-MM-DD, inclusive")
    parser.add_argument("--end", required=True, help="UTC date YYYY-MM-DD, exclusive; completed bars only")
    parser.add_argument("--database", type=Path, default=Path("data/historical.sqlite3"))
    arguments = parser.parse_args()
    start, end = [int(datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
                  for value in (arguments.start, arguments.end)]
    print(json.dumps(ingest(arguments.product, arguments.interval, start, end, arguments.database), indent=2))


if __name__ == "__main__":
    main()