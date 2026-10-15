import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from maxtrade.options import DeribitClient, scan_options, option_chain


def chain_preview():
    from unittest.mock import patch
    from maxtrade.options import render_option_chain
    rows = [{'Expiry UTC': expiry, 'Strike USD': strike, 'CALL contract': 'CALL',
             'PUT contract': 'PUT', 'CALL bid': .02, 'PUT bid': .03}
            for expiry, strikes in [('2030-01-01T08:00:00+00:00', [80000, 85000]),
                                   ('2030-01-08T08:00:00+00:00', [90000, 95000])]
            for strike in strikes]
    with patch('maxtrade.options.option_chain', return_value=rows), patch('maxtrade.options.DeribitClient'):
        render_option_chain('BTC', 'preview')


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

    def test_oi_cached_quotes_are_rechecked_without_mutating_snapshot(self):
        from datetime import datetime, timedelta, timezone
        from maxtrade.options import current_oi_quotes
        now = datetime.now(timezone.utc)
        for age, value, expected in [(0, 100, 100), (300, 0, 0), (301, 100, None),
                                     (-31, 100, None), (0, float('nan'), None), (0, -1, None)]:
            with self.subTest(age=age, value=value):
                row = {'CALL quote UTC': (now - timedelta(seconds=age)).isoformat(), 'CALL OI': value}
                result = current_oi_quotes([row], now)[0]
                self.assertEqual(result['CALL OI'], expected)
                self.assertIsNone(result['PUT OI'])
                self.assertIs(row['CALL OI'], value)
        self.assertIsNone(current_oi_quotes([{'CALL quote UTC': '2026-10-14T00:00:00', 'CALL OI': 1}], now)[0]['CALL OI'])

    def test_oi_failure_states_keep_refresh_and_provider_label(self):
        import requests
        import streamlit as st
        from maxtrade.options import StaleOpenInterest
        from streamlit.testing.v1 import AppTest
        response = requests.Response()
        response.status_code = 429
        for error, expected in [(StaleOpenInterest(), 'stale'), (requests.Timeout(), 'timed out'),
                                (requests.HTTPError(response=response), 'rate limited'), (ValueError(), 'invalid')]:
            with self.subTest(expected=expected):
                st.cache_data.clear()
                with patch('maxtrade.options.futures_oi_history', side_effect=error):
                    app = AppTest.from_string('from maxtrade.options import render_oi_profiles\n'
                                              'render_oi_profiles("BTC", "Futures")').run()
                self.assertFalse(app.exception)
                self.assertIn(expected, app.warning[0].value)
                self.assertIn('Binance BTCUSDT', app.caption[0].value)
                self.assertEqual(app.button[0].label, 'Refresh OI')

    def test_oi_display_reports_data_time_and_rejects_cached_expiry(self):
        from datetime import datetime, timedelta, timezone
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        now = datetime.now(timezone.utc)
        for age, plotted in [(0, True), (301, False)]:
            with self.subTest(view='Options', age=age):
                st.cache_data.clear()
                rows = [{'Expiry UTC': '2030-01-01T08:00:00+00:00', 'Strike USD': 85000,
                         'CALL OI': 100, 'CALL quote UTC': (now - timedelta(seconds=age)).isoformat(),
                         'PUT OI': 50, 'PUT quote UTC': (now - timedelta(seconds=301)).isoformat()}]
                with patch('maxtrade.options.option_chain', return_value=rows), patch('maxtrade.options.DeribitClient'):
                    app = AppTest.from_string('from maxtrade.options import render_oi_profiles\n'
                                              'render_oi_profiles("BTC", "Options")').run(timeout=10)
                self.assertFalse(app.exception)
                self.assertEqual(bool(app.get('plotly_chart')), plotted)
                if plotted:
                    self.assertTrue(any('1 current / 1 missing or expired' in item.value for item in app.caption))
                    self.assertTrue(any('Quote times' in item.value for item in app.caption))
                else:
                    self.assertIn('No current OI quotes', app.warning[0].value)
        st.cache_data.clear()
        stale = [{'timestamp': (now - timedelta(hours=3)).timestamp() * 1000, 'quantity': 100, 'notional': 1000}]
        fresh = [{'timestamp': now.timestamp() * 1000, 'quantity': 100, 'notional': 1000}]
        with patch('maxtrade.options.futures_oi_history', side_effect=[stale, fresh]) as request:
            app = AppTest.from_string('from maxtrade.options import render_oi_profiles\n'
                                      'render_oi_profiles("ETH", "Futures")').run(timeout=10)
            self.assertFalse(app.exception)
            self.assertFalse(app.get('plotly_chart'))
            self.assertIn('stale', app.warning[0].value)
            app.button[0].click().run(timeout=10)
            self.assertFalse(app.exception)
            self.assertTrue(app.get('plotly_chart'))
            self.assertTrue(any('Latest sample' in item.value for item in app.caption))
            self.assertEqual(request.call_count, 2)

    def test_chain_auto_load_and_expiry_strike_filters(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_function(chain_preview).run()
        self.assertFalse(app.exception)
        self.assertEqual(list(app.dataframe[0].value['Strike USD']), [80000, 85000])
        app.selectbox[0].select('2030-01-08T08:00:00+00:00').run()
        self.assertFalse(app.exception)
        self.assertEqual(list(app.dataframe[0].value['Strike USD']), [90000, 95000])
        app.select_slider[0].set_range(95000, 95000).run()
        self.assertFalse(app.exception)
        self.assertEqual(list(app.dataframe[0].value['Strike USD']), [95000])

    def test_chain_requires_fresh_quotes_for_signal_comments(self):
        from datetime import datetime, timedelta, timezone
        from streamlit.testing.v1 import AppTest
        now = datetime.now(timezone.utc)
        preview = AppTest.from_function(chain_preview).run(timeout=10)
        snapshot = preview.session_state['preview_chain_snapshot']
        for quote_age, scan_age, expected in [(0, 0, 'WATCH CALL'), (0, 301, 'NOT SCANNED'),
                                              (301, 0, 'UNAVAILABLE')]:
            with self.subTest(quote_age=quote_age, scan_age=scan_age):
                snapshot['fetched'] = now.isoformat()
                for row in snapshot['rows']:
                    row['CALL quote status'] = 'CURRENT'
                    row['CALL quote UTC'] = (now - timedelta(seconds=quote_age)).isoformat()
                app = AppTest.from_string('from maxtrade.options import render_option_chain\n'
                                         'render_option_chain("BTC", "chart")')
                app.session_state['chart_snapshot'] = {'contracts': [{
                    'Market': 'CALL', 'Source': 'Deribit', 'Signal': 'WATCH CALL',
                    'Reason': 'CALL ko watch karein; buy approval nahi.',
                    'Quote UTC': (now - timedelta(seconds=scan_age)).isoformat()}]}
                app.session_state['chart_chain_snapshot'] = snapshot
                app.run()
                self.assertFalse(app.exception)
                self.assertEqual(set(app.dataframe[0].value['CALL signal']), {expected})
                self.assertEqual(set(app.dataframe[0].value['CALL premium levels']),
                                 {'N/A: premium entry/TP/SL strategy not available'})