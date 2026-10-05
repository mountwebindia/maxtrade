import unittest
from unittest.mock import patch

import requests

from maxtrade.coindcx import CoinDCXClient, aggregate_four_hour_candles, normalize_candles
from maxtrade.scanner import scan_futures, scan_spot


class CandleTests(unittest.TestCase):
    def test_four_hour_aggregation_requires_complete_hourly_groups(self):
        bars = [{"time": i * 3_600_000, "open": 100 + i, "high": 102 + i, "low": 99 + i, "close": 101 + i, "volume": 10} for i in [0, 1, 2, 3, 4, 6, 7]]
        result = aggregate_four_hour_candles(bars[::-1])
        self.assertEqual(result, [{"time": 0, "open": 100, "high": 105, "low": 99, "close": 104, "volume": 40}])

    def test_orders_deduplicates_and_excludes_open_candle(self):
        rows = [{"time": t, "close": 100} for t in [7_200_000, 0, 3_600_000, 0]]
        result = normalize_candles(rows, "1h", count=120, now_ms=9_000_000)
        self.assertEqual([row["time"] for row in result], [0, 3_600_000])

    def test_limits_to_latest_completed_candles(self):
        rows = [{"time": t * 3_600_000} for t in range(5)]
        result = normalize_candles({"s": "ok", "data": rows}, "1h", 2, 18_000_000)
        self.assertEqual([row["time"] for row in result], [10_800_000, 14_400_000])

    def test_rejects_unsuccessful_or_malformed_feed(self):
        for payload in [{"s": "error", "data": []}, {"data": None}, [{"close": 100}]]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                normalize_candles(payload, "1h", now_ms=9_000_000)

    def test_rejects_stale_completed_candles(self):
        with self.assertRaisesRegex(ValueError, "stale"):
            normalize_candles([{"time": 0}], "1h", now_ms=18_000_000)


class ScannerTests(unittest.TestCase):
    def test_empty_discovery_is_an_explicit_error(self):
        client = CoinDCXClient()
        with patch.object(client, "spot_markets", return_value=[]), patch.object(client, "spot_tickers", return_value=[]):
            with self.assertRaisesRegex(ValueError, "No active spot"):
                scan_spot(client, "1h", 5)
        with patch.object(client, "futures_instruments", return_value=[]), patch.object(client, "futures_tickers", return_value={}):
            with self.assertRaisesRegex(ValueError, "No active futures"):
                scan_futures(client, "1h", 5)

    def test_spot_network_error_preserves_other_results(self):
        client = CoinDCXClient()
        markets = [{"coindcx_name": name, "status": "active", "base_currency_short_name": "USDT", "pair": name} for name in ["BAD", "GOOD"]]
        tickers = [{"market": name, "last_price": "100", "volume": "10"} for name in ["BAD", "GOOD"]]
        candles = [{"close": 100, "high": 101, "low": 99} for _ in range(80)]
        updates = []
        with patch.object(client, "spot_markets", return_value=markets), patch.object(client, "spot_tickers", return_value=tickers), patch.object(client, "spot_candles", side_effect=[requests.Timeout("timed out"), candles]):
            result = scan_spot(client, "1h", 2, progress=lambda *args: updates.append(args))
        self.assertEqual([row["Signal"] for row in result], ["DATA ERROR", "NO TRADE"])
        self.assertEqual(updates, [(1, 2, "BAD"), (2, 2, "GOOD")])

    def test_futures_uses_batch_tickers_and_active_instruments(self):
        client = CoinDCXClient()
        candles = [{"close": 100, "high": 101, "low": 99} for _ in range(80)]
        prices = {"ACTIVE": {"mp": "100", "v": "200"}, "DELISTED": {"mp": "100", "v": "9999"}}
        with patch.object(client, "futures_instruments", return_value=["ACTIVE"]), patch.object(client, "futures_tickers", return_value=prices), patch.object(client, "futures_candles", return_value=candles):
            result = scan_futures(client, "1h", 5)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["Market"], "ACTIVE")
        self.assertEqual(result[0]["Price"], 100)
        self.assertEqual(result[0]["24h volume"], 200)
