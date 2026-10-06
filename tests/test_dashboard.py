import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from maxtrade.presentation import signal_card
from maxtrade.settings import credential_status


class DashboardTests(unittest.TestCase):
    def test_live_chart_closed_signals_pause_and_hidden_tab(self):
        from time import time
        end = int(time() * 1000) // 3600000 * 3600000
        candles = [{"time": end - (80 - index) * 3600000, "open": 100,
                    "high": 101, "low": 99, "close": 100} for index in range(81)]
        with patch("maxtrade.chart_page.CoinDCXClient") as client:
            client.return_value.spot_candles.return_value = candles
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20)
            app.run()
            client.assert_not_called()
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20)
            app.session_state["navigation"] = "Chart"
            app.run()
            self.assertFalse(app.exception)
            snapshot = app.session_state["chart_snapshot"]
            self.assertEqual(len(snapshot["display_candles"]), 81)
            self.assertEqual(len(snapshot["candles"]), 80)
            self.assertEqual(len(snapshot["analyses"]), 31)
            self.assertTrue(any("Forming candle" in item.value for item in app.caption))
            calls = client.return_value.spot_candles.call_count
            next(widget for widget in app.toggle if widget.key == "chart_live").set_value(False).run()
            app.run()
            self.assertEqual(client.return_value.spot_candles.call_count, calls)
            self.assertTrue(any("Paused snapshot" in item.value for item in app.caption))
            next(button for button in app.button if button.key == "chart_refresh").click().run()
            self.assertEqual(client.return_value.spot_candles.call_count, calls + 1)
            AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()
            self.assertEqual(client.return_value.spot_candles.call_count, calls + 1)

    def test_backtest_results_invalidated_by_settings(self):
        from time import time
        end = int(time() * 1000) // 3600000 * 3600000
        candles = [{"time": end - (80 - index) * 3600000, "open": 100, "high": 101,
                    "low": 99, "close": 100} for index in range(80)]
        with patch("maxtrade.chart_page.CoinDCXClient") as client:
            client.return_value.spot_markets.return_value = [{"status": "active", "base_currency_short_name": "USDT",
                                                              "coindcx_name": "BTCUSDT", "pair": "B-BTC_USDT"}]
            client.return_value.spot_candles.return_value = candles
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20)
            app.session_state["navigation"] = "Chart"
            app.run()
            next(button for button in app.button if button.key == "chart_browse").click().run()
            next(button for button in app.button if button.key == "chart_refresh").click().run()
            next(button for button in app.button if button.key == "replay_run").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("No completed simulated trades" in item.value for item in app.info))
            next(widget for widget in app.number_input if widget.key == "replay_fee").set_value(20.0).run()
            self.assertFalse(app.exception)
            self.assertTrue(any("does not match" in item.value for item in app.caption))

    def test_chart_options_refresh_and_selection_invalidation(self):
        from time import time
        end = int(time() * 1000) // 3600000 * 3600000
        candles = [{"time": end - (80 - index) * 3600000, "open": 100 + index * .1,
                    "high": 102 + index * .1, "low": 98 + index * .1,
                    "close": 100 + index * .1} for index in range(80)]
        with patch("maxtrade.chart_page.DeribitClient") as client, \
                patch("maxtrade.chart_page.scan_options", return_value=[]):
            client.return_value.underlying_candles.return_value = candles
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20)
            app.session_state["navigation"] = "Chart"
            app.run()
            next(widget for widget in app.radio if widget.label == "Chart market type").set_value("Options").run()
            next(button for button in app.button if button.key == "chart_refresh").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get("plotly_chart")), 1)
            self.assertTrue(client.return_value.session.close.called)
            next(widget for widget in app.selectbox if widget.label == "Chart underlying").select("ETH").run()
            self.assertEqual(len(app.get("plotly_chart")), 1)
            self.assertEqual(app.session_state["chart_snapshot"]["selection"], ("Options", "1h", "ETH"))
            self.assertEqual(client.return_value.underlying_candles.call_args.args, ("ETH", "1h"))
            self.assertTrue(client.return_value.underlying_candles.call_args.kwargs["include_open"])
            client.return_value.underlying_candles.side_effect = ValueError("Feed unavailable")
            next(button for button in app.button if button.key == "chart_refresh").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get("plotly_chart")), 0)
            self.assertTrue(any("no signal generated" in error.value for error in app.error))

    def test_options_state_has_enabled_scan_and_underlying_selection(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()
        app.radio[0].set_value("Options").run()
        self.assertFalse(app.exception)
        self.assertFalse(app.button[0].disabled)
        self.assertFalse(app.selectbox[1].disabled)
        self.assertEqual(app.selectbox[1].value, 10)
        underlying = next(widget for widget in app.selectbox if widget.label == "Underlying")
        self.assertEqual(underlying.options, ["BTC", "ETH"])
        self.assertTrue(any("premiums in BTC" in caption.value for caption in app.caption))

    def test_options_scan_uses_deribit_and_hides_stale_currency_results(self):
        rows = [{"Market": "BTC-TEST-C", "Source": "Deribit", "Signal": "WATCH CALL",
                 "Premium currency": "BTC", "Price": .0205, "Bid": .02, "Ask": .021,
                 "Reason": "Research candidate"}]
        with patch("maxtrade.options.scan_options", return_value=rows) as scanner, \
                patch("maxtrade.options.DeribitClient"), patch("maxtrade.history.ScanHistory"):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()
            app.radio[0].set_value("Options").run()
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertEqual(scanner.call_args.args[1:4], ("BTC", "1h", 10))
            self.assertTrue(any("WATCH CALL" in item.value for item in app.markdown))
            next(widget for widget in app.selectbox if widget.label == "Underlying").select("ETH").run()
            self.assertTrue(any("Settings changed" in item.value for item in app.info))
            self.assertFalse(any("WATCH CALL" in item.value for item in app.markdown))

    def test_signal_filters_preserve_snapshot_and_csv_access(self):
        rows = [{"Market": "BTCUSDT", "Signal": "LONG", "Reason": "Trend aligned"},
            {"Market": "ETHUSDT", "Signal": "NO TRADE", "Reason": "No trend"}]
        with patch("maxtrade.scanner.scan_spot", return_value=rows), \
            patch("maxtrade.coindcx.CoinDCXClient"), patch("maxtrade.history.ScanHistory"):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()
            app.button[0].click().run()
            next(widget for widget in app.selectbox if widget.label == "Signal filter").select("Candidates").run()
            cards = next(item.value for item in app.markdown if 'class="signal-grid"' in item.value)
            self.assertIn("BTCUSDT", cards)
            self.assertNotIn("ETHUSDT", cards)
            self.assertIn("<details>", cards)
            self.assertEqual(app.session_state["scan_results"], rows)
            next(widget for widget in app.selectbox if widget.label == "Signal filter").select("Data errors").run()
            self.assertFalse(app.exception)
            self.assertTrue(any("No matching signals" in item.value for item in app.info))
            self.assertTrue(any(button.label == "Download scan CSV" for button in app.get("download_button")))

    def test_options_card_preserves_quote_precision_and_escapes_text(self):
        card = signal_card({"Market": "<script>", "Source": "Deribit", "Signal": "WATCH CALL",
                            "Premium currency": "BTC", "Bid": .02, "Ask": .021,
                            "Days to expiry": "<script>", "Reason": "<script>"})
        self.assertIn("0.021", card)
        self.assertIn("&lt;script&gt;", card)
        self.assertNotIn("<script>", card)
        self.assertNotIn("<span>Stop</span>", card)

    def test_card_escapes_exchange_text_and_shows_missing_values(self):
        html = signal_card({"Market": "<script>alert(1)</script>", "Signal": "DATA ERROR", "Reason": "<b>bad feed</b>", "Price": None})
        self.assertNotIn("<script>", html)
        self.assertNotIn("<b>bad feed</b>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("—", html)

    def test_long_card_contains_risk_levels(self):
        html = signal_card({"Market": "BTCUSDT", "Signal": "LONG", "Price": 100, "Entry": 100, "Stop": 97, "Target": 106, "RSI": 60})
        for text in ["BTCUSDT", "LONG", "Entry", "Stop", "Target", "60.0"]:
            self.assertIn(text, html)

    def test_credentials_missing_partial_and_ready(self):
        self.assertEqual(credential_status({}), "Not configured")
        self.assertEqual(credential_status({"COINDCX_API_KEY": "example"}), "Incomplete configuration")
        self.assertEqual(credential_status({"COINDCX_API_KEY": "example", "COINDCX_API_SECRET": "example"}), "Configured · not connected")
        self.assertEqual(credential_status({"COINDCX_API_KEY": "  ", "COINDCX_API_SECRET": "example"}), "Incomplete configuration")

    def test_secret_status_never_contains_secret_values(self):
        status = credential_status({"COINDCX_API_KEY": "private-key-example", "COINDCX_API_SECRET": "private-secret-example"})
        self.assertNotIn("private", status)

    def test_file_settings_work_without_environment(self):
        self.assertEqual(credential_status({}, {"COINDCX_API_KEY": "example", "COINDCX_API_SECRET": "example"}), "Configured · not connected")
