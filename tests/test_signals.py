import unittest

from maxtrade.signals import analyze_candles
from maxtrade.charts import candle_figure, chart_analysis


def make_candles(closes: list[float]) -> list[dict[str, float]]:
    return [
        {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 10}
        for close in closes
    ]


class SignalTests(unittest.TestCase):
    def test_shadow_higher_confirmation_excludes_future_bars(self):
        from maxtrade.quality import shadow_quality
        candles = make_candles([100 + index * 0.1 + (index % 4) * 0.5 for index in range(280)])
        for index, candle in enumerate(candles):
            candle['time'] = index * 3600000
        higher = make_candles([100 + index * 0.1 + (index % 4) * 0.5 for index in range(70)])
        for index, candle in enumerate(higher):
            candle['time'] = index * 4 * 3600000
        quality = shadow_quality(candles, '1h', higher=higher, higher_interval='4h')
        self.assertEqual(quality['higher_action'], 'LONG')
        future = {'time': 280 * 3600000, 'open': 1, 'close': 1, 'high': 2, 'low': 0.5, 'volume': 10}
        self.assertEqual(quality, shadow_quality(candles, '1h', higher=higher + [future], higher_interval='4h'))
        self.assertIn('No completed-close prior-20-bar breakout', quality['blockers'])
        self.assertIn('Relative volume below 1.2 or unavailable', quality['blockers'])

    def test_shadow_quality_blocks_chop_and_missing_confirmation(self):
        from maxtrade.quality import shadow_quality
        candles = make_candles([100] * 80)
        for index, candle in enumerate(candles):
            candle['time'] = index * 3600000
        quality = shadow_quality(candles, '1h')
        self.assertEqual(quality['regime'], 'CHOP')
        self.assertEqual(quality['candidate_action'], 'NO TRADE')
        self.assertIn('Higher-timeframe setup missing or conflicting', quality['blockers'])
        candles[-1]['time'] += 3600000
        with self.assertRaisesRegex(ValueError, 'contiguous'):
            shadow_quality(candles, '1h')

    def test_chart_matches_scanner_without_future_candles(self):
        candles = make_candles([100 + index * .1 + (index % 4) * .5 for index in range(90)])
        for index, candle in enumerate(candles):
            candle["time"] = index * 3600000
        analyses = chart_analysis(candles, "1h", True)
        self.assertEqual(analyses[-1], analyze_candles(candles, allow_short=True))
        self.assertEqual(analyses[:21], chart_analysis(candles[:70], "1h", True))
        figure = candle_figure(candles, analyses, "1h")
        self.assertEqual(figure.data[0].type, "candlestick")
        self.assertEqual(len(figure.data[0].x), len(candles))
        self.assertEqual(len(figure.layout.shapes), 3)
        self.assertIn("Volume", [trace.name for trace in figure.data])
        self.assertFalse(any(trace.mode == "markers" for trace in figure.data if trace.type == "scatter"))
        options_figure = candle_figure(candles, analyses, "1h", options=True)
        self.assertEqual(len(options_figure.layout.shapes), 3)
        self.assertNotIn("CALL", [trace.name for trace in options_figure.data])
        clean = candle_figure(candles, analyses, "1h", chart_type="Line", indicators=(), theme="Light", logarithmic=True)
        self.assertEqual(len(clean.data), 1)
        self.assertEqual(clean.data[0].type, "scatter")
        self.assertEqual(clean.layout.yaxis.type, "log")
        self.assertFalse(clean.layout.shapes)

    def test_extended_indicators_exclude_forming_candle(self):
        candles = make_candles([100 + index * .1 for index in range(240)])
        for index, candle in enumerate(candles):
            candle["time"] = index * 3600000
        analyses = chart_analysis(candles[:-1], "1h", True)
        indicators = ("EMA 200", "Bollinger Bands", "VWAP (UTC day)", "Support / resistance", "MACD")
        original = candle_figure(candles, analyses, "1h", indicators=indicators)
        candles[-1].update(close=500, high=501, volume=10000)
        changed = candle_figure(candles, analyses, "1h", indicators=indicators)
        for before, after in zip(original.data[1:], changed.data[1:]):
            self.assertEqual(tuple(before.x), tuple(after.x))
            self.assertEqual(len(before.x), 239)
            for first, second in zip(before.y, after.y):
                self.assertTrue(first == second or (first != first and second != second))
        traces = {trace.name: trace for trace in original.data}
        self.assertAlmostEqual(traces["Support (prior 20)"].y[20], 99)
        self.assertAlmostEqual(traces["Resistance (prior 20)"].y[20], 102.9)
        self.assertTrue(all(value != value for value in traces["EMA 200"].y[:199]))
        self.assertTrue(all(value != value for value in traces["VWAP (UTC day)"].y[:24]))
        self.assertAlmostEqual(traces["VWAP (UTC day)"].y[24], 102.4)

    def test_chart_rejects_gaps_and_invalid_open(self):
        candles = make_candles([100] * 60)
        for index, candle in enumerate(candles):
            candle["time"] = index * 3600000
        candles[-1]["time"] += 3600000
        with self.assertRaisesRegex(ValueError, "gaps"):
            chart_analysis(candles, "1h", True)
        candles[-1]["time"] -= 3600000
        candles[-1]["open"] = 200
        with self.assertRaisesRegex(ValueError, "open/high/low/close"):
            chart_analysis(candles, "1h", True)

    def test_rejects_nonfinite_and_invalid_prices(self) -> None:
        for value in [float("nan"), float("inf"), -1, 0]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                analyze_candles(make_candles([value] * 80))

    def test_requires_at_least_fifty_candles(self) -> None:
        with self.assertRaisesRegex(ValueError, "50 candles"):
            analyze_candles(make_candles([100 + index for index in range(49)]))

    def test_bullish_trend_returns_long_with_risk_levels(self) -> None:
        closes = [100 + index * 0.1 + (index % 4) * 0.5 for index in range(80)]
        result = analyze_candles(make_candles(closes))
        self.assertEqual(result.action, "LONG")
        self.assertLess(result.stop, result.entry)
        self.assertGreater(result.target, result.entry)

    def test_short_requires_derivatives_permission(self) -> None:
        closes = [120 - index * 0.1 - (index % 4) * 0.5 for index in range(80)]
        candles = make_candles(closes)
        spot_result = analyze_candles(candles)
        futures_result = analyze_candles(candles, allow_short=True)
        self.assertEqual(spot_result.action, "NO TRADE")
        self.assertEqual(futures_result.action, "SHORT")
        self.assertGreater(futures_result.stop, futures_result.entry)
        self.assertLess(futures_result.target, futures_result.entry)


if __name__ == "__main__":
    unittest.main()