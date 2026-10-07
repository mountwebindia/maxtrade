import unittest
from unittest.mock import patch

import requests

from maxtrade.coindcx import CoinDCXClient, aggregate_four_hour_candles, normalize_candles
from maxtrade.scanner import scan_futures, scan_spot


class CandleTests(unittest.TestCase):
    def test_custom_source_intervals_have_complete_history_and_preserve_gaps(self):
        from maxtrade.coindcx import INTERVAL_MS
        for interval, source in [('2m', '1m'), ('3m', '1m'), ('45m', '15m'), ('120m', '1h'), ('180m', '1h')]:
            duration = INTERVAL_MS[source]
            now_ms = duration * 499
            bars = [{'time': index * duration, 'open': 100, 'high': 102, 'low': 99,
                     'close': 101, 'volume': 10} for index in range(499)]
            client = CoinDCXClient()
            with self.subTest(interval=interval), patch('maxtrade.coindcx.time.time', return_value=now_ms / 1000), patch.object(client, '_get', return_value=bars):
                result = client.spot_candles('B-BTC_USDT', interval)
                self.assertGreaterEqual(len(result), 50)
                self.assertTrue(all(result[index]['time'] - result[index - 1]['time'] == INTERVAL_MS[interval]
                                    for index in range(1, len(result))))
            client.session.close()

    def test_minute_intervals_use_real_feed_resolutions_and_closed_bars(self):
        from maxtrade.coindcx import INTERVAL_MS
        for interval in ('1m', '5m', '15m', '30m', '1h', '4h', '1d'):
            duration = INTERVAL_MS[interval]
            with self.subTest(interval=interval):
                rows = [{'time': index * duration} for index in range(4)]
                self.assertEqual(len(normalize_candles(rows, interval, now_ms=duration * 3 + duration // 2)), 3)
                client = CoinDCXClient()
                with patch.object(client, '_get', return_value=[]) as request:
                    client.futures_candles('B-BTC_USDT', interval)
                    self.assertEqual(request.call_args.args[1]['resolution'], '1D' if interval == '1d' else str(duration // 60_000))
                client.session.close()

    def test_minute_aggregation_never_fills_missing_source_bars(self):
        from maxtrade.coindcx import aggregate_candles
        bars = [{'time': index * 60000, 'open': 100, 'high': 102, 'low': 99,
                 'close': 101, 'volume': 10} for index in range(11)]
        self.assertEqual(len(aggregate_candles(bars, '1m', '5m')), 2)
        self.assertEqual(len(aggregate_candles(bars[:2] + bars[3:], '1m', '5m')), 1)

    def test_live_display_retains_forming_candle_but_rejects_future_bars(self):
        rows = [{"time": index * 3_600_000} for index in range(4)]
        result = normalize_candles(rows, "1h", now_ms=9_000_000, include_open=True)
        self.assertEqual([row["time"] for row in result], [0, 3_600_000, 7_200_000])
        self.assertEqual(len(normalize_candles(rows, "1h", now_ms=9_000_000)), 2)

    def test_forming_four_hour_group_requires_contiguous_hours(self):
        bars = [{"time": index * 3_600_000, "open": 100, "high": 102, "low": 99,
                 "close": 101, "volume": 10} for index in range(7)]
        self.assertEqual(len(aggregate_four_hour_candles(bars)), 1)
        live = aggregate_four_hour_candles(bars, now_ms=23_400_000)
        self.assertEqual(len(live), 2)
        self.assertEqual(live[-1]["volume"], 30)
        self.assertEqual(len(aggregate_four_hour_candles(bars[:5] + bars[6:], now_ms=23_400_000)), 1)

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
        candles = [{"time": index * 3600000, "close": 100, "high": 101, "low": 99} for index in range(80)]
        updates = []
        with patch.object(client, "spot_markets", return_value=markets), patch.object(client, "spot_tickers", return_value=tickers), patch.object(client, "spot_candles", side_effect=[requests.Timeout("timed out"), candles]):
            result = scan_spot(client, "1h", 2, progress=lambda *args: updates.append(args))
        self.assertEqual([row["Signal"] for row in result], ["DATA ERROR", "NO TRADE"])
        self.assertEqual(updates, [(1, 2, "BAD"), (2, 2, "GOOD")])

    def test_futures_uses_batch_tickers_and_active_instruments(self):
        client = CoinDCXClient()
        candles = [{"time": index * 3600000, "close": 100, "high": 101, "low": 99} for index in range(80)]
        prices = {"ACTIVE": {"mp": "100", "v": "200"}, "DELISTED": {"mp": "100", "v": "9999"}}
        with patch.object(client, "futures_instruments", return_value=["ACTIVE"]), patch.object(client, "futures_tickers", return_value=prices), patch.object(client, "futures_candles", return_value=candles):
            result = scan_futures(client, "1h", 5)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["Market"], "ACTIVE")
        self.assertEqual(result[0]["Price"], 100)
        self.assertEqual(result[0]["24h volume"], 200)

    def test_gold_filter_uses_active_token_identity_and_preserves_provenance(self):
        from unittest.mock import Mock
        client = Mock()
        client.spot_markets.return_value = [
            {"coindcx_name": name + "USDT", "status": "active", "base_currency_short_name": "USDT",
             "target_currency_short_name": name, "pair": "B-" + name + "_USDT"}
            for name in ["BTC", "PAXG", "XAUT"]]
        client.spot_tickers.return_value = [{"market": name + "USDT", "last_price": 100, "volume": 10}
                                           for name in ["BTC", "PAXG", "XAUT"]]
        candles = [{"time": index * 3600000, "close": 100, "high": 101, "low": 99} for index in range(80)]
        client.spot_candles.return_value = candles
        rows = scan_spot(client, "1h", 5, gold_only=True)
        self.assertEqual({row["Pair"] for row in rows}, {"B-PAXG_USDT", "B-XAUT_USDT"})
        self.assertTrue(all(row["Signal candle time"] == 79 * 3600000 for row in rows))
        client.futures_instruments.return_value = ["B-BTC_USDT", "B-PAXG_USDT", "B-PAXGFAKE_USDT"]
        client.futures_tickers.return_value = {name: {"mp": 100, "v": 10} for name in client.futures_instruments.return_value}
        client.futures_candles.return_value = candles
        self.assertEqual([row["Pair"] for row in scan_futures(client, "1h", 5, gold_only=True)], ["B-PAXG_USDT"])
