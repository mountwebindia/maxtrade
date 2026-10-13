import unittest
from unittest.mock import patch

from maxtrade.backtest import ReplaySettings, replay
from maxtrade.charts import candle_figure, signal_records, chart_analysis
from maxtrade.signals import TradeSignal


class ReplayTests(unittest.TestCase):
    def test_daily_pivots_require_complete_previous_utc_day(self):
        candles = [{'time': index * 3600000, 'open': 100, 'high': 110, 'low': 90,
                    'close': 105, 'volume': 10} for index in range(73)]
        analyses = chart_analysis(candles, '1h', False)
        figure = candle_figure(candles, analyses, '1h', indicators=('Daily pivots',))
        levels = {trace.name: trace.y for trace in figure.data if trace.name.startswith('Pivot')}
        self.assertEqual(len(levels), 7)
        self.assertTrue(all(value != value for value in levels['Pivot P'][:24]))
        self.assertAlmostEqual(levels['Pivot P'][24], 305 / 3)
        self.assertAlmostEqual(levels['Pivot R1'][24], 2 * 305 / 3 - 90)
        self.assertAlmostEqual(levels['Pivot S3'][24], 90 - 2 * (110 - 305 / 3))
        labels = {annotation.name: annotation for annotation in figure.layout.annotations
                  if annotation.name.startswith('Pivot')}
        self.assertEqual(set(labels), set(levels))
        self.assertEqual(labels['Pivot P'].text, 'P 101.67')
        for name, annotation in labels.items():
            self.assertAlmostEqual(annotation.y, levels[name][-1])
        logarithmic = candle_figure(candles, analyses, '1h', indicators=('Daily pivots',), logarithmic=True)
        from math import log10
        self.assertAlmostEqual(next(annotation.y for annotation in logarithmic.layout.annotations
                                    if annotation.name == 'Pivot P'), log10(305 / 3))
        candles[-1].update(high=1000, close=999)
        changed = candle_figure(candles, analyses[:-1], '1h', indicators=('Daily pivots',))
        self.assertAlmostEqual(next(trace for trace in changed.data if trace.name == 'Pivot P').y[48], 305 / 3)
        self.assertEqual(labels['Pivot P'].text, next(annotation for annotation in changed.layout.annotations
                                                     if annotation.name == 'Pivot P').text)
        partial = candles[1:]
        partial_figure = candle_figure(partial, chart_analysis(partial, '1h', False), '1h', indicators=('Daily pivots',))
        self.assertTrue(all(value != value for value in next(trace for trace in partial_figure.data
                                                            if trace.name == 'Pivot P').y[:47]))

    def test_futures_oi_validation(self):
        from maxtrade.options import futures_oi_history
        with patch('maxtrade.options.requests.get') as request, patch('maxtrade.options.time', return_value=7200):
            request.return_value.json.return_value = [
                {'symbol': 'BTCUSDT', 'timestamp': 3600000, 'sumOpenInterest': '10', 'sumOpenInterestValue': '1000'}]
            self.assertEqual(futures_oi_history('BTC')[0]['quantity'], 10)
            request.return_value.json.return_value[0]['sumOpenInterest'] = '-1'
            with self.assertRaises(ValueError):
                futures_oi_history('BTC')
            with self.assertRaises(ValueError):
                futures_oi_history('SOL')

    def test_oi_views_and_provider_failure(self):
        from streamlit.testing.v1 import AppTest
        chain = [{'Expiry UTC': '2026-11-01T08:00:00+00:00', 'Strike USD': 100,
                  'CALL OI': 10, 'PUT OI': 20}]
        history = [{'timestamp': 3600000, 'quantity': 10, 'notional': 1000}]
        with patch('maxtrade.options.option_chain', return_value=chain), \
                patch('maxtrade.options.futures_oi_history', return_value=history):
            app = AppTest.from_string("from maxtrade.options import render_oi_profiles\nrender_oi_profiles('BTC', 'Options')").run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get('plotly_chart')), 1)
            app.session_state['chart_oi_view_Options'] = 'Futures over time'
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get('plotly_chart')), 1)
            self.assertTrue(any('not CoinDCX' in caption.value for caption in app.caption))
        with patch('maxtrade.options.futures_oi_history', side_effect=ValueError('Unavailable')):
            app = AppTest.from_string("from maxtrade.options import render_oi_profiles\nrender_oi_profiles('ETH', 'Futures')").run()
            self.assertFalse(app.exception)
            self.assertTrue(any('unavailable' in warning.value for warning in app.warning))

    def candles(self):
        return [{"time": index * 3600000, "open": 100, "high": 101, "low": 99,
                 "close": 100, "volume": 10} for index in range(60)]

    def signals(self, action="LONG"):
        idle = TradeSignal("NO TRADE", None, None, None, 50, 100, 100, "")
        signal = TradeSignal(action, 100, 95 if action == "LONG" else 105,
                             110 if action == "LONG" else 90, 55, 100, 100, "")
        return [idle] + [signal] * 10

    def test_next_open_costs_and_funding(self):
        candles = self.candles()
        candles[51].update(open=102, high=103, close=102)
        with patch("maxtrade.backtest.chart_analysis", return_value=self.signals()):
            free = replay(candles, "1h", True, ReplaySettings(fee_bps=0, slippage_bps=0))
            paid = replay(candles, "1h", True, ReplaySettings(funding_bps_8h=10))
        self.assertEqual(len(free["trades"]), 1)
        self.assertEqual(free["trades"].iloc[0]["EntryBar"], 51)
        self.assertAlmostEqual(free["trades"].iloc[0]["EntryPrice"], 102)
        self.assertLess(paid["return_pct"], free["return_pct"])
        self.assertGreater(paid["funding"], 0)
        self.assertAlmostEqual(paid["equity"]["Equity"].iloc[-1] - 10000,
                               paid["trades"]["Net PnL"].sum())

    def test_stop_precedes_target_on_ambiguous_bar(self):
        candles = self.candles()
        candles[51].update(high=112, low=94)
        with patch("maxtrade.backtest.chart_analysis", return_value=self.signals()):
            result = replay(candles, "1h", False, ReplaySettings(fee_bps=0, slippage_bps=0))
        self.assertAlmostEqual(result["trades"].iloc[0]["ExitPrice"], 95)
        self.assertGreater(result["drawdown_pct"], 0)

    def test_no_trades_and_invalid_settings(self):
        result = replay(self.candles(), "1h", False)
        self.assertEqual(len(result["trades"]), 0)
        self.assertIsNone(result["win_rate"])
        self.assertEqual(result["return_pct"], 0)
        for settings in [ReplaySettings(cash=-1), ReplaySettings(risk_pct=6),
                         ReplaySettings(fee_bps=float("nan")), ReplaySettings(funding_bps_8h=1)]:
            with self.assertRaises(ValueError):
                replay(self.candles(), "1h", False, settings)

    def test_replay_rejects_gaps_duplicates_and_unaligned_times(self):
        for timestamp in (52 * 3600000, 50 * 3600000, 51 * 3600000 + 1, True):
            with self.subTest(timestamp=timestamp):
                candles = self.candles()
                candles[51]['time'] = timestamp
                with patch('maxtrade.backtest.chart_analysis') as analysis:
                    with self.assertRaisesRegex(ValueError, 'contiguous aligned'):
                        replay(candles, '1h', False)
                    analysis.assert_not_called()

    def test_short_and_allocation_cap(self):
        with patch("maxtrade.backtest.chart_analysis", return_value=self.signals("SHORT")):
            result = replay(self.candles(), "1h", True, ReplaySettings(fee_bps=0, slippage_bps=0))
        trade = result["trades"].iloc[0]
        self.assertLess(trade["Size"], 0)
        self.assertLessEqual(abs(trade["Size"]) * trade["EntryPrice"], 2500)

    def test_chart_has_no_signal_markers_for_either_market(self):
        for options in (False, True):
            with self.subTest(options=options):
                figure = candle_figure(self.candles(), self.signals(), "1h", options=options)
                markers = [trace for trace in figure.data if trace.type == "scatter" and trace.mode == "markers"]
                self.assertEqual(markers, [])
                self.assertFalse({"BUY", "SELL", "CALL", "PUT"}.intersection(
                    trace.name for trace in figure.data))

    def test_signal_markers_only_new_confirmed_setups_and_saved_fills(self):
        candles = self.candles()
        signals = self.signals()
        records = signal_records(candles, signals, '1h')
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['Signal'], 'BUY')
        self.assertEqual(records[0]['Available at'].timestamp(), 51 * 3600)
        forming = dict(candles[-1], time=60 * 3600000, close=999, high=1000)
        figure = candle_figure(candles + [forming], signals, '1h', signals=records,
                               paper_positions=[{'id': 1, 'opened_at': '1970-01-03T03:00:00+00:00',
                                                 'closed_at': None, 'entry': 102, 'state': 'OPEN', 'pnl': None}])
        marker = next(trace for trace in figure.data if trace.name == 'BUY setup')
        self.assertEqual(marker.mode, 'markers+text')
        self.assertEqual(list(marker.text), ['BUY'])
        self.assertEqual(marker.textposition, 'bottom center')
        self.assertEqual(figure.layout.hovermode, 'closest')
        self.assertIn('Reason: %{customdata[5]}', marker.hovertemplate)
        self.assertTrue(all(len(line) <= 38 for line in marker.customdata[0][5].split('<br>')))
        self.assertEqual(len(figure.layout.annotations), 5)
        self.assertEqual(len({annotation.name for annotation in figure.layout.annotations}), 5)
        self.assertEqual(len(marker.x), 1)
        self.assertLess(marker.x[0], records[0]['Available at'])
        self.assertTrue(any(trace.name == 'Paper entry' for trace in figure.data))
        self.assertEqual(signal_records(candles, signals[:1], '1h'), [])
        self.assertEqual(signal_records(candles, self.signals('SHORT'), '1h')[0]['Signal'], 'SELL')