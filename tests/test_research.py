import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timezone
from dataclasses import replace
from unittest.mock import Mock, patch

from maxtrade.research import fetch_sentiment, market_evidence, run_market_research, sentiment_evidence
from maxtrade.history import ScanHistory


class ResearchTests(unittest.TestCase):
    def bars(self, interval, count=80):
        duration = {"1h": 3600000, "4h": 14400000}[interval]
        end = int(self.now.timestamp() * 1000) // duration * duration
        return [{"time": end - (count - index) * duration, "open": 100,
                 "high": 101, "low": 99, "close": 100} for index in range(count)]

    def setUp(self):
        self.now = datetime(2026, 10, 6, 6, 30, tzinfo=timezone.utc)

    def test_evidence_excludes_forming_candle_and_records_provenance(self):
        bars = self.bars("1h")
        bars.append(dict(bars[-1], time=bars[-1]["time"] + 3600000, close=999))
        item = market_evidence(bars, "Spot", "B-BTC_USDT", "1h", self.now)
        self.assertEqual(item.values["close"], 100)
        self.assertEqual(item.price_unit, "USDT")
        self.assertIn("coindcx.com", item.source)
        self.assertEqual(item.event_time, "2026-10-06T06:00:00+00:00")
        self.assertEqual(item.expires_at, "2026-10-06T07:00:00+00:00")

    def test_missing_evidence_never_authorizes_trade(self):
        client = Mock()
        client.spot_candles.side_effect = [self.bars("1h"), ValueError("unavailable")]
        report = run_market_research(client, "Spot", "B-BTC_USDT", self.now)
        self.assertEqual(report["status"], "DATA ERROR")
        self.assertEqual(report["decision"], "NO TRADE")
        self.assertFalse(report["execution_enabled"])
        self.assertEqual(len(report["errors"]), 1)
        self.assertTrue(report["blockers"])

    def test_stale_gapped_and_naive_evidence_rejected(self):
        bars = self.bars("1h")
        for invalid in (bars[:-1], bars[:20] + bars[21:]):
            with self.assertRaises(ValueError):
                market_evidence(invalid, "Spot", "B-BTC_USDT", "1h", self.now)
        with self.assertRaises(ValueError):
            market_evidence(bars, "Spot", "B-BTC_USDT", "1h", self.now.replace(tzinfo=None))

    def test_options_evidence_never_infers_premium_risk_levels(self):
        item = market_evidence(self.bars("4h"), "Options", "BTC", "4h", self.now)
        self.assertEqual(item.price_unit, "USD underlying")
        self.assertIsNone(item.values["entry"])
        self.assertIsNone(item.values["stop"])
        self.assertIsNone(item.values["target"])

    def test_alignment_and_options_bias_never_open_trade_gate(self):
        for product, actions, bias in (
                ("Futures", ("LONG", "LONG"), "LONG"),
                ("Futures", ("SHORT", "SHORT"), "SHORT"),
                ("Futures", ("LONG", "SHORT"), "NO TRADE"),
                ("Options", ("LONG", "LONG"), "CALL BIAS"),
                ("Options", ("SHORT", "SHORT"), "PUT BIAS")):
            with self.subTest(product=product, actions=actions):
                symbol = "BTC" if product == "Options" else "B-BTC_USDT"
                items = [replace(market_evidence(self.bars(interval), product, symbol, interval, self.now),
                                 action=action) for interval, action in zip(("1h", "4h"), actions)]
                with patch("maxtrade.research.market_evidence", side_effect=items):
                    report = run_market_research(Mock(), product, symbol, self.now)
                self.assertEqual(report["technical_bias"], bias)
                self.assertEqual(report["decision"], "NO TRADE")
                self.assertFalse(report["execution_enabled"])

    def test_report_survives_storage_reopen_without_polluting_scans(self):
        client = Mock()
        client.spot_candles.side_effect = [self.bars("1h"), self.bars("4h")]
        report = run_market_research(client, "Spot", "B-BTC_USDT", self.now)
        with TemporaryDirectory() as folder:
            path = Path(folder) / "reports.sqlite3"
            saved_id = ScanHistory(path).save_research(report)
            restored = ScanHistory(path)
            self.assertEqual(restored.recent_research(), [{"id": saved_id, "report": report}])
            self.assertEqual(restored.recent(), [])

    def test_sentiment_validates_age_range_classification_and_provider_errors(self):
        payload = {"data": [{"value": "73", "value_classification": "Greed",
                             "timestamp": "1791244800"}], "metadata": {"error": None}}
        evidence = sentiment_evidence(payload, self.now)
        self.assertEqual(evidence["value"], 73)
        self.assertEqual(evidence["attribution"], "Alternative.me")
        self.assertEqual(evidence["expires_at"], "2026-10-07T00:00:00+00:00")
        for changed in ({"value": "101"}, {"timestamp": "0"}, {"timestamp": "1791331200"},
                        {"value_classification": "Unknown"}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                sentiment_evidence({"data": [dict(payload["data"][0], **changed)]}, self.now)
        with self.assertRaises(ValueError):
            sentiment_evidence({"metadata": {"error": "unavailable"}}, self.now)
        session = Mock()
        session.get.return_value.json.return_value = payload
        self.assertEqual(fetch_sentiment(session, self.now)["value"], 73)
        session.get.assert_called_once_with("https://api.alternative.me/fng/", params={"limit": 1}, timeout=12)