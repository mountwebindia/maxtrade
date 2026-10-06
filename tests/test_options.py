import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from maxtrade.options import DeribitClient, scan_options, option_chain


class OptionsTests(unittest.TestCase):
    def setUp(self):
        self.now = 1791260000000
        self.client = Mock()
        self.instrument = {
            "instrument_name": "BTC-TEST-C", "kind": "option", "state": "open",
            "is_active": True, "base_currency": "BTC", "quote_currency": "BTC",
            "option_type": "call", "strike": 85000, "expiration_timestamp": self.now + 14 * 86400000,
        }
        self.client.instruments.return_value = [self.instrument]
        self.client.summaries.return_value = [{"instrument_name": "BTC-TEST-C", "volume_usd": 10000}]
        self.ticker = {
            "timestamp": self.now, "state": "open", "best_bid_price": .02, "best_ask_price": .021,
            "best_bid_amount": 2, "best_ask_amount": 3, "mark_price": .0205, "mark_iv": 40,
            "open_interest": 100, "stats": {"volume": 20},
            "greeks": {"delta": .5, "gamma": .001, "theta": -10, "vega": 30},
        }
        self.client.ticker.return_value = self.ticker
        self.trend = patch("maxtrade.options.analyze_candles", return_value=SimpleNamespace(action="LONG", rsi=60))
        self.trend.start()
        self.addCleanup(self.trend.stop)

    def scan(self):
        return scan_options(self.client, "BTC", "1h", 5, now_ms=self.now)

    def test_watch_call_is_research_only_with_real_quote_and_greeks(self):
        row = self.scan()[0]
        self.assertEqual(row["Signal"], "WATCH CALL")
        self.assertEqual(row["Source"], "Deribit")
        self.assertEqual(row["Delta"], .5)
        self.assertIsNone(row["Entry"])
        self.assertIsNone(row["Stop"])
        self.assertIsNone(row["Target"])

    def test_stale_missing_crossed_or_invalid_quotes_fail_closed(self):
        for change in [{"timestamp": self.now - 300001}, {"timestamp": self.now + 30001},
                       {"best_bid_price": None}, {"best_bid_price": .03},
                       {"greeks": {}}, {"mark_iv": float("nan")}, {"state": "halted"}]:
            with self.subTest(change=change):
                self.client.ticker.return_value = {**self.ticker, **change}
                self.assertEqual(self.scan()[0]["Signal"], "DATA ERROR")

    def test_wide_spread_or_low_interest_is_not_a_candidate(self):
        for change in [{"best_ask_price": .04}, {"open_interest": 1}, {"stats": {"volume": 0}}]:
            with self.subTest(change=change):
                self.client.ticker.return_value = {**self.ticker, **change}
                self.assertEqual(self.scan()[0]["Signal"], "NO TRADE")

    def test_expiring_and_closed_instruments_are_excluded(self):
        for change in [{"expiration_timestamp": self.now + 86400000}, {"state": "halted"}]:
            with self.subTest(change=change):
                self.client.instruments.return_value = [{**self.instrument, **change}]
                with self.assertRaises(ValueError):
                    self.scan()

    def test_rpc_error_is_not_success(self):
        client = DeribitClient()
        self.addCleanup(client.session.close)
        with patch.object(client.session, "get") as request:
            request.return_value.json.return_value = {"error": {"message": "rate limited"}}
            with self.assertRaises(ValueError):
                client.instruments("BTC")

    def test_chain_pairs_call_put_and_blanks_stale_quotes(self):
        self.client.instruments.return_value = [self.instrument, dict(self.instrument,
            instrument_name='BTC-TEST-P', option_type='put')]
        quote = {'instrument_name': 'BTC-TEST-C', 'creation_timestamp': self.now,
                 'bid_price': .02, 'ask_price': .021, 'mark_price': .0205, 'mark_iv': 40,
                 'open_interest': 100, 'volume': 20}
        self.client.summaries.return_value = [quote, dict(quote, instrument_name='BTC-TEST-P',
                                                          creation_timestamp=self.now - 300001)]
        rows = option_chain(self.client, 'BTC', self.now)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['Strike USD'], 85000)
        self.assertEqual(rows[0]['CALL bid'], .02)
        self.assertIsNone(rows[0]['PUT bid'])
        self.assertEqual(rows[0]['PUT quote status'], 'STALE')
        with self.assertRaises(ValueError):
            option_chain(self.client, 'GOLD', self.now)