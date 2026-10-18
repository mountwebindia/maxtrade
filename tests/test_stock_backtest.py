from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

import pandas as pd

from maxtrade.signals import TradeSignal
from maxtrade.stock_backtest import load_stock_csv, stock_analyses, stock_signal_label, stock_replay, stock_schedule, stock_report, StockReplaySettings
from maxtrade.stock_data import INDIA, MARKET_INTERVALS, MarketDataError, fetch_market_history, market_analyses, normalize_market_history, search_market_symbols
from maxtrade.stock_page import cached_market_data, cached_market_search
from io import BytesIO
import json
from streamlit.testing.v1 import AppTest


class StockReplayTests(unittest.TestCase):
    def csv(self):
        sessions = stock_schedule('2026-01-01', '2026-06-30').index[:80]
        rows = ['date,open,high,low,close,volume']
        rows.extend(f'{session.date()},100,104,96,100,1000' for session in sessions)
        return ('\n'.join(rows) + '\n').encode()

    def dataset(self, content=None):
        return load_stock_csv(content or self.csv(), 'RELIANCE', 'Synthetic test fixture',
                              datetime(2026, 10, 17, tzinfo=timezone.utc))

    def signals(self, dataset):
        idle = TradeSignal('NO TRADE', None, None, None, 50, 100, 100, '')
        long = TradeSignal('LONG', 100, 95, 110, 55, 101, 99, '')
        return [idle, long] + [long] * (len(dataset.bars) - 51)

    def test_calendar_skips_weekends_and_holidays(self):
        sessions = stock_schedule('2026-10-15', '2026-10-20').index
        self.assertEqual([str(value.date()) for value in sessions],
                         ['2026-10-15', '2026-10-16', '2026-10-19'])
        self.assertEqual(len(self.dataset().bars), 80)
        with self.assertRaisesRegex(ValueError, 'coverage'):
            stock_schedule('1900-01-01', '1900-06-01')

    def test_import_rejects_gaps_duplicates_and_invalid_prices(self):
        lines = self.csv().decode().splitlines()
        for changed in [lines[:10] + lines[11:], lines[:10] + [lines[9]] + lines[10:],
                        [line.replace('100,104,96,100', '100,99,96,100') for line in lines],
                        [line.replace(',1000', ',nan') for line in lines]]:
            with self.subTest(changed=changed[10]):
                with self.assertRaises(ValueError):
                    self.dataset('\n'.join(changed).encode())

    def test_import_rejects_unfinished_and_future_sessions(self):
        content = self.csv()
        latest = self.dataset().bars[-1].date
        with self.assertRaisesRegex(ValueError, 'not completed'):
            load_stock_csv(content, 'TCS', 'Fixture', datetime.combine(latest, datetime.min.time(), timezone.utc))
        with self.assertRaisesRegex(ValueError, 'Future'):
            load_stock_csv(content, 'TCS', 'Fixture', datetime(2026, 1, 1, tzinfo=timezone.utc))

    def test_next_open_whole_shares_costs_and_reconciliation(self):
        dataset = self.dataset()
        with patch('maxtrade.stock_backtest.stock_analyses', return_value=self.signals(dataset)):
            free = stock_replay(dataset, StockReplaySettings(fee_bps=0, slippage_bps=0, fixed_fee=0))
            paid = stock_replay(dataset)
        self.assertEqual(len(paid['trades']), 1)
        trade = paid['trades'].iloc[0]
        self.assertEqual(trade['EntryBar'], 51)
        self.assertEqual(trade['ExitBar'], len(dataset.bars) - 1)
        self.assertEqual(trade['Size'], int(trade['Size']))
        self.assertLess(paid['equity']['Strategy'].iloc[-1], free['equity']['Strategy'].iloc[-1])
        self.assertAlmostEqual(paid['equity']['Strategy'].iloc[-1] - 100000,
                               paid['trades']['Net PnL'].sum())
        self.assertEqual(paid['equity'].index[-1].hour, 3)
        self.assertEqual(paid['equity'].index[-1].minute, 45)
        self.assertIsNone(paid['metrics']['Strategy']['sharpe'])

    def test_holdout_has_no_earlier_trades_and_retains_warmup(self):
        dataset = self.dataset()
        with patch('maxtrade.stock_backtest.stock_analyses', return_value=self.signals(dataset)):
            result = stock_replay(dataset, evaluation_start=dataset.bars[64].date)
        self.assertEqual(result['warmup_sessions'], 64)
        self.assertEqual(result['trades'].iloc[0]['EntryBar'], 65)
        self.assertEqual(len(result['equity']), 16)
        self.assertEqual(result['dataset_sha256'], dataset.checksum)

    def test_gap_stop_and_drawdown_halt(self):
        lines = self.csv().decode().splitlines()
        lines[53] = lines[53].replace('100,104,96,100', '90,92,85,88')
        dataset = self.dataset('\n'.join(lines).encode())
        with patch('maxtrade.stock_backtest.stock_analyses', return_value=self.signals(dataset)):
            result = stock_replay(dataset, StockReplaySettings(risk_pct=5, allocation_pct=95,
                                  fee_bps=0, slippage_bps=0, fixed_fee=0, drawdown_halt_pct=2))
        self.assertEqual(result['trades'].iloc[0]['ExitPrice'], 90)
        self.assertTrue(result['entry_halted'])
        self.assertLess(result['trades'].iloc[0]['Net PnL'], -5000)

    def test_ambiguous_bar_uses_stop_not_optimistic_target(self):
        lines = self.csv().decode().splitlines()
        lines[52] = lines[52].replace('100,104,96,100', '100,115,94,100')
        dataset = self.dataset('\n'.join(lines).encode())
        with patch('maxtrade.stock_backtest.stock_analyses', return_value=self.signals(dataset)):
            result = stock_replay(dataset)
        self.assertEqual(result['trades'].iloc[0]['ExitPrice'], 95)
        self.assertEqual(result['trades'].iloc[0]['EntryBar'], result['trades'].iloc[0]['ExitBar'])

    def test_final_valuation_never_uses_final_intraday_extremes(self):
        lines = self.csv().decode().splitlines()
        lines[-1] = lines[-1].replace('100,104,96,100', '100,120,90,92')
        dataset = self.dataset('\n'.join(lines).encode())
        with patch('maxtrade.stock_backtest.stock_analyses', return_value=self.signals(dataset)):
            result = stock_replay(dataset)
        self.assertEqual(result['trades'].iloc[0]['ExitPrice'], 100)
        self.assertEqual(result['trades'].iloc[0]['ExitBar'], 79)
        self.assertAlmostEqual(result['equity']['Strategy'].iloc[-1] - 100000,
                               result['trades']['Net PnL'].sum())

    def test_settings_fail_closed(self):
        for settings in [StockReplaySettings(risk_pct=6), StockReplaySettings(cash=float('nan')),
                         StockReplaySettings(fixed_fee=-1), StockReplaySettings(drawdown_halt_pct=0)]:
            with self.assertRaises(ValueError):
                stock_replay(self.dataset(), settings)

    def test_direction_labels_and_causal_analysis(self):
        for action, cash, options in [('LONG', 'BUY', 'CALL BUY'), ('SHORT', 'SELL', 'PUT BUY'),
                                      ('NO TRADE', 'WAIT', 'WAIT'), ('UNKNOWN', 'WAIT', 'WAIT')]:
            self.assertEqual(stock_signal_label(action), cash)
            self.assertEqual(stock_signal_label(action, options=True), options)
        dataset = self.dataset()
        with patch('maxtrade.stock_backtest.analyze_candles') as analyzer:
            stock_analyses(dataset, allow_short=True)
            self.assertEqual(len(analyzer.call_args_list), len(dataset.bars) - 49)
            for end, call in enumerate(analyzer.call_args_list, start=50):
                self.assertTrue(call.kwargs['allow_short'])
                self.assertEqual(len(call.args[0]), end)
                self.assertEqual(call.args[0][-1]['date'], dataset.bars[end - 1].date)
            analyzer.reset_mock()
            stock_analyses(dataset)
            self.assertTrue(all(not call.kwargs['allow_short'] for call in analyzer.call_args_list))

    def test_bearish_display_cannot_open_short_replay_orders(self):
        dataset = self.dataset()
        bearish = TradeSignal('SHORT', 100, 105, 90, 40, 101, 103, 'Bearish fixture')
        with patch('maxtrade.stock_backtest.stock_analyses', return_value=[bearish] * (len(dataset.bars) - 49)):
            result = stock_replay(dataset)
        self.assertTrue(result['trades'].empty)
        self.assertEqual(result['equity']['Strategy'].iloc[-1], 100000)

    def test_signal_views_keep_option_levels_underlying_only(self):
        dataset = self.dataset()
        signals = self.signals(dataset)
        app = AppTest.from_string('from maxtrade.stock_page import render_stock_research\nrender_stock_research()', default_timeout=60)
        with patch('streamlit.file_uploader', return_value=BytesIO(self.csv())), patch('maxtrade.stock_page.cached_analyses', return_value=signals):
            app.run()
            app.text_input(key='stock_source').set_value('Synthetic fixture')
            app.checkbox(key='stock_adjusted').check().run()
            self.assertIn('BUY', [metric.value for metric in app.metric])
            app.radio(key='stock_signal_view').set_value('CALL BUY / PUT BUY').run()
            self.assertIn('CALL BUY', [metric.value for metric in app.metric])
            signals[-1] = TradeSignal('SHORT', 100, 105, 90, 40, 101, 103, 'Bearish fixture')
            app.run()
            self.assertIn('PUT BUY', [metric.value for metric in app.metric])
            self.assertIn('Underlying stop (INR)', [metric.label for metric in app.metric])
            self.assertTrue(any('premium Entry/SL/TP are unavailable' in warning.value for warning in app.warning))
            history = app.dataframe[0].value
            self.assertFalse(history['Execution enabled'].any())
            self.assertFalse(history['Option contract available'].any())
            self.assertEqual(history.iloc[0]['Underlying stop INR'], 105)
            app.radio(key='stock_signal_view').set_value('BUY / SELL').run()
            self.assertIn('SELL', [metric.value for metric in app.metric])
            signals[-1] = TradeSignal('NO TRADE', None, None, None, 50, 100, 100, '')
            app.run()
            self.assertIn('WAIT', [metric.value for metric in app.metric])
            self.assertFalse(app.exception)

    def test_report_is_strict_auditable_json(self):
        dataset = self.dataset()
        with patch('maxtrade.stock_backtest.stock_analyses', return_value=self.signals(dataset)):
            result = stock_replay(dataset)
        report = json.loads(stock_report(dataset, result))
        self.assertFalse(report['execution_enabled'])
        self.assertEqual(report['dataset_sha256'], dataset.checksum)
        self.assertEqual(report['dataset']['currency'], 'INR')
        self.assertEqual(report['trades'][0]['Size'], int(report['trades'][0]['Size']))
        self.assertEqual(len(report['equity']), len(result['equity']))

    def test_workspace_replay_and_invalidation(self):
        app = AppTest.from_string('from maxtrade.stock_page import render_stock_research\nrender_stock_research()', default_timeout=60)
        with patch('streamlit.file_uploader', return_value=BytesIO(self.csv())):
            app.run()
            app.text_input(key='stock_source').set_value('Synthetic fixture')
            app.checkbox(key='stock_adjusted').check().run()
            self.assertFalse(app.exception)
            app.button(key='stock_run').click().run()
            self.assertFalse(app.exception)
            self.assertIn('stock_replay_result', app.session_state)
            self.assertEqual(len(app.get('download_button')), 4)
            app.number_input(key='stock_cash').set_value(110000.0).run()
            self.assertNotIn('stock_replay_result', app.session_state)
            app.button(key='stock_run').click().run()
            app.text_input(key='stock_symbol').set_value('INVALID/SYMBOL').run()
            self.assertTrue(app.error)
            self.assertNotIn('stock_replay_result', app.session_state)


class MarketChartTests(unittest.TestCase):
    def setUp(self):
        cached_market_data.clear()
        cached_market_search.clear()

    def frame(self):
        return pd.DataFrame({'Open': 100., 'High': 104., 'Low': 96., 'Close': 100., 'Volume': 0.},
                            index=pd.date_range('2026-06-29', periods=80, freq='B', tz='Asia/Kolkata'))

    def dataset(self, symbol='^NSEI', exchange='NSE', is_index=True):
        return normalize_market_history(self.frame(), symbol, exchange, is_index,
                                        datetime(2026, 10, 17, tzinfo=timezone.utc))

    def app(self):
        return AppTest.from_string('from maxtrade.stock_page import render_stock_page\nrender_stock_page()', default_timeout=30)

    def test_index_zero_volume_and_causal_signal_prefixes(self):
        dataset = self.dataset()
        self.assertEqual(len(dataset.bars), 80)
        self.assertEqual(dataset.bars[-1].volume, 0)
        self.assertTrue(dataset.is_index)
        with patch('maxtrade.stock_data.analyze_candles') as analyze:
            market_analyses(dataset)
        self.assertEqual([len(call.args[0]) for call in analyze.call_args_list], list(range(50, 81)))
        self.assertTrue(all(call.kwargs['allow_short'] for call in analyze.call_args_list))

    def test_unfinished_and_future_candles_excluded(self):
        frame = self.frame()
        latest = frame.index[-1]
        before = normalize_market_history(frame, '^NSEI', 'NSE', True,
                                          latest.replace(hour=15, minute=44).to_pydatetime())
        after = normalize_market_history(frame, '^NSEI', 'NSE', True,
                                         latest.replace(hour=15, minute=45).to_pydatetime())
        self.assertEqual(len(before.bars), 79)
        self.assertEqual(len(after.bars), 80)
        self.assertTrue(after.stale(datetime(2026, 11, 1, tzinfo=timezone.utc)))
        with self.assertRaisesRegex(MarketDataError, 'timezone'):
            normalize_market_history(frame, '^NSEI', 'NSE', True, datetime(2026, 10, 17))

    def test_invalid_sparse_and_unsorted_history_rejected(self):
        frame = self.frame()
        invalid = frame.copy()
        invalid.iloc[-1, invalid.columns.get_loc('High')] = 99
        missing = frame.copy()
        missing.iloc[-1, missing.columns.get_loc('Close')] = float('nan')
        for changed in (frame.iloc[:1], frame.iloc[::-1], frame.iloc[[0] + list(range(80))], invalid, missing,
                        frame.tz_localize(None), frame.drop(columns=['High']), frame.iloc[:0]):
            with self.subTest(rows=len(changed)):
                with self.assertRaises(MarketDataError):
                    normalize_market_history(changed, '^NSEI', 'NSE', True)

    def test_exchange_filtered_company_search_and_fallback(self):
        quotes = [{'symbol': 'HDB', 'quoteType': 'EQUITY'},
                  {'symbol': 'HDFCBANK.NS', 'shortname': 'HDFC BANK', 'quoteType': 'EQUITY'},
                  {'symbol': 'HDFCBANK.BO', 'shortname': 'HDFC BANK', 'quoteType': 'EQUITY'},
                  {'symbol': 'FUND.BO', 'quoteType': 'MUTUALFUND'}]
        with patch('maxtrade.stock_data.yf.Search') as search:
            search.return_value.quotes = quotes
            self.assertEqual(search_market_symbols('HDFC Bank', 'NSE'), [('HDFCBANK.NS', 'HDFC BANK')])
            self.assertEqual(search_market_symbols('HDFC Bank', 'BSE'), [('HDFCBANK.BO', 'HDFC BANK')])
            search.return_value.quotes = []
            self.assertEqual(search_market_symbols('M&M', 'NSE'), [('M&M.NS', 'M&M.NS')])
            self.assertEqual(search_market_symbols('RELIANCE.NS', 'BSE'), [])
            search.side_effect = TimeoutError()
            with self.assertRaisesRegex(MarketDataError, 'temporarily unavailable'):
                search_market_symbols('TCS', 'NSE')

    def test_provider_identity_and_errors_fail_closed(self):
        metadata = {'currency': 'INR', 'exchangeName': 'NSI', 'instrumentType': 'INDEX'}
        with patch('maxtrade.stock_data.yf.Ticker') as ticker:
            ticker.return_value.history.return_value = self.frame()
            ticker.return_value.get_history_metadata.return_value = metadata
            self.assertEqual(fetch_market_history('^NSEI', 'NSE').symbol, '^NSEI')
            ticker.return_value.get_history_metadata.return_value = {**metadata, 'currency': 'USD'}
            with self.assertRaisesRegex(MarketDataError, 'verified'):
                fetch_market_history('^NSEI', 'NSE')
            ticker.return_value.history.side_effect = TimeoutError()
            with self.assertRaisesRegex(MarketDataError, 'temporarily unavailable'):
                fetch_market_history('^NSEI', 'NSE')
            for symbol, exchange in [('^NSEI', 'BSE'), ('RELIANCE.BO', 'NSE'), ('AAPL', 'NSE')]:
                with self.assertRaises(MarketDataError):
                    fetch_market_history(symbol, exchange)

    def test_default_chart_without_csv_and_index_switch(self):
        with patch('maxtrade.stock_page.fetch_market_history',
                   side_effect=lambda symbol, venue, period, interval: self.dataset(symbol, venue)) as fetch:
            app = self.app().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get('plotly_chart')), 1)
            self.assertFalse(app.get('file_uploader'))
            self.assertIn('Last close (points)', [metric.label for metric in app.metric])
            app.selectbox(key='market_index').set_value('NIFTY BANK').run()
            self.assertEqual(fetch.call_args.args[:2], ('^NSEBANK', 'NSE'))
            app.selectbox(key='market_index').set_value('SENSEX').run()
            self.assertEqual(fetch.call_args.args[:2], ('^BSESN', 'BSE'))
            self.assertEqual(app.subheader[0].value, 'SENSEX')
            app.radio(key='stock_workspace').set_value('CSV research').run()
            self.assertTrue(app.get('file_uploader'))
            self.assertFalse(app.get('plotly_chart'))
            self.assertFalse(app.exception)

    def test_intraday_completion_timestamps_and_freshness(self):
        frame = pd.DataFrame({'Open': 100., 'High': 104., 'Low': 96., 'Close': 100., 'Volume': 0.},
                             index=pd.date_range('2026-10-16 09:15', periods=75, freq='5min', tz=INDIA))
        before = normalize_market_history(frame, '^NSEI', 'NSE', True,
                                         datetime(2026, 10, 16, 15, 44, tzinfo=INDIA), interval='5m')
        after = normalize_market_history(frame, '^NSEI', 'NSE', True,
                                        datetime(2026, 10, 16, 15, 45, tzinfo=INDIA), interval='5m')
        self.assertEqual((len(before.bars), len(after.bars)), (74, 75))
        self.assertEqual(after.bars[-1].date.isoformat(), '2026-10-16T15:25:00+05:30')
        self.assertFalse(after.stale(datetime(2026, 10, 17, 12, tzinfo=INDIA)))
        self.assertTrue(after.stale(datetime(2026, 10, 19, 12, tzinfo=INDIA)))
        for invalid in (frame.set_axis(frame.index + pd.Timedelta(minutes=1)), frame.iloc[::-1]):
            with self.assertRaises(MarketDataError):
                normalize_market_history(invalid, '^NSEI', 'NSE', True, interval='5m')

    def test_half_hour_provider_aggregation_and_hourly_close(self):
        sessions = pd.bdate_range('2026-10-05', periods=7)
        index = pd.DatetimeIndex([datetime.combine(session.date(), datetime.min.time(), INDIA)
                                  + timedelta(hours=9, minutes=15 + offset * 15)
                                  for session in sessions for offset in range(25)])
        frame = pd.DataFrame({'Open': 100., 'High': 104., 'Low': 96., 'Close': 101., 'Volume': 2.},
                             index=index)
        metadata = {'currency': 'INR', 'exchangeName': 'NSI', 'instrumentType': 'INDEX'}
        with patch('maxtrade.stock_data.yf.Ticker') as ticker:
            ticker.return_value.history.return_value = frame
            ticker.return_value.get_history_metadata.return_value = metadata
            dataset = fetch_market_history('^NSEI', 'NSE', '1mo', '30m')
            self.assertEqual(ticker.return_value.history.call_args.kwargs['interval'], '15m')
            self.assertEqual(len(dataset.bars), 91)
            self.assertEqual(dataset.bars[0].date.time().isoformat(), '09:15:00')
            self.assertEqual(dataset.bars[0].volume, 4.)
            self.assertEqual(dataset.bars[-1].volume, 2.)
            for interval, period in [('1m', '1y'), ('bad', '6mo')]:
                calls = ticker.call_count
                with self.assertRaises(MarketDataError):
                    fetch_market_history('^NSEI', 'NSE', period, interval)
                self.assertEqual(ticker.call_count, calls)
        hourly_index = pd.DatetimeIndex([datetime.combine(session.date(), datetime.min.time(), INDIA)
                                         + timedelta(hours=9 + offset, minutes=15)
                                         for session in pd.bdate_range('2026-10-05', periods=10)
                                         for offset in range(7)])
        hourly = frame.iloc[:0].reindex(hourly_index).fillna(100.)
        before = normalize_market_history(hourly, '^NSEI', 'NSE', True,
                                         datetime(2026, 10, 16, 15, 44, tzinfo=INDIA), interval='1h')
        after = normalize_market_history(hourly, '^NSEI', 'NSE', True,
                                        datetime(2026, 10, 16, 15, 45, tzinfo=INDIA), interval='1h')
        self.assertEqual((len(before.bars), len(after.bars)), (69, 70))
        self.assertFalse(after.stale(datetime(2026, 10, 17, 12, tzinfo=INDIA)))

    def test_timeframe_selection_ranges_exports_and_cache_isolation(self):
        with patch('maxtrade.stock_page.fetch_market_history', return_value=self.dataset()) as fetch:
            app = self.app().run()
            self.assertEqual(app.selectbox(key='market_interval').value, '1d')
            for interval in ('1m', '5m', '15m', '30m', '1h', '1d'):
                app.selectbox(key='market_interval').set_value(interval).run()
                self.assertFalse(app.exception)
                self.assertEqual(app.selectbox(key='market_period').options,
                                 [{'5d': '5 days', '1mo': '1 month', '60d': '60 days', '3mo': '3 months',
                                   '6mo': '6 months', '1y': '1 year', '2y': '2 years'}[period]
                                  for period in MARKET_INTERVALS[interval][1]])
                self.assertTrue(any(call.args == ('^NSEI', 'NSE', MARKET_INTERVALS[interval][1][0], interval)
                                    for call in fetch.call_args_list))
                self.assertEqual(set(app.dataframe[0].value['Timeframe']), {interval})
                self.assertEqual(len(app.get('plotly_chart')), 1)
            calls = fetch.call_count
            app.selectbox(key='market_interval').set_value('1m').run()
            self.assertEqual(fetch.call_count, calls)

    def test_provider_error_refresh_and_symbol_failure_remove_old_chart(self):
        with patch('maxtrade.stock_page.fetch_market_history', side_effect=[MarketDataError('Rate-limited'), self.dataset(), MarketDataError('Unavailable')]):
            app = self.app().run()
            self.assertTrue(app.error)
            self.assertFalse(app.get('plotly_chart'))
            app.button(key='market_refresh').click().run()
            self.assertEqual(len(app.get('plotly_chart')), 1)
            app.selectbox(key='market_index').set_value('NIFTY BANK').run()
            self.assertTrue(app.error)
            self.assertFalse(app.get('plotly_chart'))
            self.assertFalse(app.metric)
            self.assertFalse(app.exception)

    def test_options_are_underlying_only_and_stale_levels_unavailable(self):
        signal = TradeSignal('SHORT', 100, 105, 90, 45, 99, 101, 'Bearish fixture')
        with patch('maxtrade.stock_page.fetch_market_history', return_value=self.dataset()), patch('maxtrade.stock_page.market_analyses', return_value=[signal] * 31):
            app = self.app().run()
            app.radio(key='market_signal_view').set_value('CALL BUY / PUT BUY').run()
            self.assertIn('PUT BUY', [metric.value for metric in app.metric])
            self.assertIn('Underlying stop (points)', [metric.label for metric in app.metric])
            self.assertTrue(any('Not an option-buy approval' in warning.value for warning in app.warning))
            self.assertFalse(app.dataframe[0].value['Execution enabled'].any())
            with patch('maxtrade.stock_data.MarketDataset.stale', return_value=True):
                app.run()
            self.assertIn('UNAVAILABLE', [metric.value for metric in app.metric])
            self.assertNotIn('PUT BUY', [metric.value for metric in app.metric])
            self.assertEqual([metric.value for metric in app.metric if 'Underlying' in metric.label], ['N/A'] * 3)
            self.assertFalse(app.exception)


if __name__ == '__main__':
    unittest.main()