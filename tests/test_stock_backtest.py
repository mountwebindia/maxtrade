from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from maxtrade.signals import TradeSignal
from maxtrade.stock_backtest import load_stock_csv, stock_analyses, stock_signal_label, stock_replay, stock_schedule, stock_report, StockReplaySettings
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
        app = AppTest.from_string('from maxtrade.stock_page import render_stock_page\nrender_stock_page()', default_timeout=60)
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
        app = AppTest.from_string('from maxtrade.stock_page import render_stock_page\nrender_stock_page()', default_timeout=60)
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


if __name__ == '__main__':
    unittest.main()