import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from maxtrade.presentation import signal_card
from maxtrade.settings import azure_openai_config, credential_status


class DashboardTests(unittest.TestCase):
    def test_azure_backend_configuration_is_optional_validated_and_redacted(self):
        self.assertIsNone(azure_openai_config({}, {}))
        values = {"AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
                  "AZURE_OPENAI_DEPLOYMENT": "research-model", "AZURE_OPENAI_API_VERSION": "2024-10-21",
                  "AZURE_OPENAI_API_KEY": "test-private-key"}
        config = azure_openai_config({}, values)
        self.assertIsNotNone(config)
        self.assertEqual(config.endpoint, "https://example.openai.azure.com")
        self.assertNotIn("test-private-key", repr(config))
        self.assertEqual(azure_openai_config({"AZURE_OPENAI_DEPLOYMENT": "override"}, values).deployment, "override")
        for name, invalid in [("AZURE_OPENAI_API_KEY", ""), ("AZURE_OPENAI_ENDPOINT", "http://example.com"),
                              ("AZURE_OPENAI_ENDPOINT", "https://user:password@example.com"),
                              ("AZURE_OPENAI_DEPLOYMENT", "bad/name"), ("AZURE_OPENAI_API_VERSION", "latest")]:
            with self.subTest(name=name, invalid=invalid), self.assertRaises(ValueError):
                azure_openai_config({}, dict(values, **{name: invalid}))

    def setUp(self):
        login = patch("maxtrade.auth.require_login")
        login.start()
        self.addCleanup(login.stop)
        chart_login = patch("maxtrade.chart_page.require_chart_login")
        chart_login.start()
        self.addCleanup(chart_login.stop)

    def test_sidebar_navigation_tracks_tabs(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()
        self.assertEqual([button.label for button in app.sidebar.button],
                         ["Signals", "Chart", "History", "Settings", "Sign out"])
        app.sidebar.button(key="menu_settings").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["navigation"], "Settings")
        app.sidebar.button(key="menu_signals").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["navigation"], "Signals")

    def test_gold_scan_and_daily_accuracy_controls(self):
        with patch("maxtrade.scanner.scan_spot", return_value=[]) as scanner, \
                patch("maxtrade.coindcx.CoinDCXClient"):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()
            app.selectbox(key="scan_asset_group").select("Gold-backed tokens").run()
            next(button for button in app.button if button.label == "Scan markets now").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(scanner.call_args.kwargs["gold_only"])
            self.assertTrue(any("not gold options" in caption.value for caption in app.caption))
            self.assertTrue(any(button.key == "accuracy_update" for button in app.button))

    def test_daily_accuracy_filters_prediction_details_by_date(self):
        def render():
            from unittest.mock import Mock
            from maxtrade.accuracy import render_daily_accuracy
            records = [{"scan_id": index, "created_at": day + "T18:01:00+00:00", "product": "Spot",
                        "pair": "B-PAXG_USDT", "action": "LONG", "status": status,
                        "start_ms": 1791226800000, "stop": 90, "target": 120, "result_json": None}
                       for index, (day, status) in enumerate([("2026-10-05", "WIN"), ("2026-10-06", "LOSS")])]
            history = Mock()
            history.predictions.return_value = records
            render_daily_accuracy(history)

        app = AppTest.from_function(render).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.dataframe[0].value["Target accuracy %"].tolist(), [0.0, 100.0])
        self.assertEqual(app.dataframe[1].value["Status"].tolist(), ["LOSS"])
        app.selectbox(key="accuracy_date").select("2026-10-05").run()
        self.assertFalse(app.exception)
        self.assertEqual(app.dataframe[1].value["Status"].tolist(), ["WIN"])
        self.assertEqual(app.dataframe[1].value["Stop"].tolist(), [90])
        self.assertEqual(app.dataframe[1].value["Target"].tolist(), [120])

    def test_market_research_run_saves_report_and_keeps_trade_gate_closed(self):
        from maxtrade.research import run_market_research
        from unittest.mock import Mock
        from datetime import datetime, timezone
        from time import time

        now = datetime.now(timezone.utc)
        end = int(time() * 1000) // 3600000 * 3600000
        candles = [{"time": end - (80 - index) * 3600000, "open": 100,
                    "high": 101, "low": 99, "close": 100} for index in range(80)]
        source = Mock()
        source.spot_candles.side_effect = [candles, ValueError("4h unavailable")]
        report = run_market_research(source, "Spot", "B-BTC_USDT", now)
        with patch("maxtrade.chart_page.CoinDCXClient") as chart_client, \
                patch("maxtrade.coindcx.CoinDCXClient"), \
                patch("maxtrade.research.run_market_research", return_value=report), \
                patch("maxtrade.research.fetch_sentiment", side_effect=ValueError("stale sentiment")), \
                patch("maxtrade.research.fetch_news", side_effect=ValueError("news unavailable")), \
                patch("maxtrade.research.fetch_derivatives", side_effect=ValueError("derivatives unavailable")), \
                patch("maxtrade.history.ScanHistory") as history:
            chart_client.return_value.spot_candles.return_value = candles
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20)
            app.session_state["navigation"] = "Chart"
            app.run()
            next(button for button in app.button if button.key == "research_run").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["research_report"]["decision"], "NO TRADE")
            history.return_value.save_research.assert_called_once_with(report)
            self.assertTrue(any("4h unavailable" in item.value for item in app.warning))
            self.assertTrue(any("Coordinated decision: NO TRADE" in item.value for item in app.markdown))

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
