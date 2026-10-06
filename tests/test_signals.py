import unittest

from maxtrade.signals import analyze_candles
from maxtrade.charts import candle_figure, chart_analysis


def make_candles(closes: list[float]) -> list[dict[str, float]]:
    return [
        {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 10}
        for close in closes
    ]


class SignalTests(unittest.TestCase):
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
        self.assertEqual(len(figure.layout.shapes), 6)
        options_figure = candle_figure(candles, analyses, "1h", options=True)
        self.assertEqual(len(options_figure.layout.shapes), 3)
        self.assertIn("CALL", [trace.name for trace in options_figure.data])

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