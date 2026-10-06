import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from maxtrade.presentation import signal_card
from maxtrade.settings import credential_status


class DashboardTests(unittest.TestCase):
    def test_options_state_has_enabled_scan_and_underlying_selection(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()
        app.selectbox[0].select("Options").run()
        self.assertFalse(app.exception)
        self.assertFalse(app.button[0].disabled)
        self.assertFalse(app.selectbox[1].disabled)
        self.assertFalse(app.select_slider[0].disabled)
        underlying = next(widget for widget in app.selectbox if widget.label == "Options underlying")
        self.assertEqual(underlying.options, ["BTC", "ETH"])
        self.assertTrue(any("premiums in BTC" in caption.value for caption in app.caption))

    def test_options_scan_uses_deribit_and_hides_stale_currency_results(self):
        rows = [{"Market": "BTC-TEST-C", "Source": "Deribit", "Signal": "WATCH CALL",
                 "Premium currency": "BTC", "Price": .0205, "Bid": .02, "Ask": .021,
                 "Reason": "Research candidate"}]
        with patch("maxtrade.options.scan_options", return_value=rows) as scanner, \
                patch("maxtrade.options.DeribitClient"), patch("maxtrade.history.ScanHistory"):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()
            app.selectbox[0].select("Options").run()
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertEqual(scanner.call_args.args[1:4], ("BTC", "1h", 10))
            self.assertTrue(any("WATCH CALL" in item.value for item in app.markdown))
            next(widget for widget in app.selectbox if widget.label == "Options underlying").select("ETH").run()
            self.assertTrue(any("Settings changed" in item.value for item in app.info))
            self.assertFalse(any("WATCH CALL" in item.value for item in app.markdown))

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
