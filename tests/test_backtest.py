import unittest
from unittest.mock import patch

from maxtrade.backtest import ReplaySettings, replay
from maxtrade.charts import candle_figure
from maxtrade.signals import TradeSignal


class ReplayTests(unittest.TestCase):
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