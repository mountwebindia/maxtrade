import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from maxtrade.historical import audit_gaps, four_hour_coverage, ingest, parse_candles, quality_report
from maxtrade.historical_features import causal_features, evaluate, forward_outcomes, historical_context, load_dataset


class HistoricalTests(unittest.TestCase):
    def test_gap_audit_is_fresh_and_preserves_original_history(self):
        session = Mock()
        row = [0, 99, 102, 100, 101, 10]
        session.get.return_value = Mock(status_code=200, content=json.dumps([row]).encode())
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "history.sqlite3"
            original = ingest("BTC-USD", "1h", 0, 7200, database, session)
            session.get.return_value = Mock(status_code=200, content=json.dumps([row, [3600, 99, 102, 100, 101, 10]]).encode())
            audit = audit_gaps("BTC-USD", "1h", 0, 7200, database, session)
            self.assertEqual(audit["status"], "RECOVERABLE")
            self.assertEqual(len(audit["checks"][0]["recovered_bars"]), 1)
            self.assertFalse(audit["original_dataset_modified"])
            self.assertEqual(ingest("BTC-USD", "1h", 0, 7200, database, session)["pages"], original["pages"])
            session.get.return_value = Mock(status_code=200, content=json.dumps([row]).encode())
            self.assertEqual(audit_gaps("BTC-USD", "1h", 0, 7200, database, session)["status"], "GAPS_REMAIN")
            revised = [0, 99, 102, 100, 100, 10]
            session.get.return_value = Mock(status_code=200, content=json.dumps([revised]).encode())
            self.assertEqual(audit_gaps("BTC-USD", "1h", 0, 7200, database, session)["status"], "REVISIONS_DETECTED")
            self.assertEqual(session.get.call_count, 4)
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM historical_candles").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT count(*) FROM historical_gap_audits").fetchone()[0], 3)
                connection.execute("UPDATE historical_pages SET raw=?", (b"[]",))
            with self.assertRaisesRegex(ValueError, "checksum"):
                audit_gaps("BTC-USD", "1h", 0, 7200, database, session)

    def test_shadow_context_rejects_future_retrieval_and_staleness(self):
        now = datetime.now(timezone.utc)
        end = int(now.timestamp()) // 86400 * 86400
        start = end - 240 * 86400
        session = Mock()
        session.get.return_value = Mock(status_code=200, content=json.dumps([
            [start + index * 86400, 99 + index, 102 + index, 100 + index, 101 + index, 10]
            for index in range(240)]).encode())
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "history.sqlite3"
            ingest("BTC-USD", "1d", start, end, database, session)
            context = historical_context(database, "B-BTC_USDT", now + timedelta(minutes=1))
            self.assertEqual(context["regime"], "UPTREND")
            self.assertFalse(context["execution_enabled"])
            self.assertIsNone(context["calibrated_probability"])
            self.assertEqual(context["prior_same_regime_five_day_outcomes"]["trades"], 8)
            with self.assertRaisesRegex(ValueError, "retrieved before"):
                historical_context(database, "BTC", datetime.fromtimestamp(end, timezone.utc))
            with self.assertRaisesRegex(ValueError, "stale"):
                historical_context(database, "BTC", now + timedelta(days=3))

    def test_four_hour_coverage_requires_every_hour_without_gap_fill(self):
        bars = [{"time": index * 3600000, "open": 100, "high": 102,
                 "low": 99, "close": 101, "volume": 10} for index in range(8)]
        self.assertEqual(four_hour_coverage(bars, 0, 28800)["observed_bars"], 2)
        report = four_hour_coverage(bars[:2] + bars[3:], 0, 28800)
        self.assertEqual(report["missing_ranges_seconds"], [[0, 14400]])
        self.assertEqual(report["coverage_pct"], 50)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            four_hour_coverage(bars + bars[:1], 0, 28800)
        with self.assertRaisesRegex(ValueError, "aligned"):
            four_hour_coverage(bars, 3600, 28800)

    def test_parser_order_dedup_bounds_and_validation(self):
        row = [86400, 99, 102, 100, 101, 10]
        bars = parse_candles([row, [0, 99, 102, 100, 101, 10], row], 86400, 172800, 86400)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["time"], 86400000)
        for bad in [[86401, 99, 102, 100, 101, 10], [86400, 99, 102, 100, 101, -1],
                    [86400, 99, 102, 200, 101, 10], [86400, 99, 102, 100, float("nan"), 10]]:
            with self.assertRaises(ValueError):
                parse_candles([bad], 86400, 172800, 86400)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_candles([row, [86400, 99, 102, 100, 100, 10]], 86400, 172800, 86400)

    def test_report_counts_leading_internal_and_trailing_gaps(self):
        report = quality_report([{"time": 86400000}, {"time": 259200000}], 0, 432000, 86400)
        self.assertEqual(report["missing_ranges_seconds"], [[0, 86400], [172800, 259200], [345600, 432000]])
        self.assertEqual(report["coverage_pct"], 40)
        self.assertEqual(report["status"], "INCOMPLETE")

    def test_ingestion_paginates_and_resumes_without_network(self):
        session = Mock()
        session.get.side_effect = [Mock(status_code=200, content=json.dumps([
            [index * 86400, 99, 102, 100, 101, 10] for index in range(301)]).encode()),
            Mock(status_code=200, content=b'[[25920000,99,102,100,101,10]]')]
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "history.sqlite3"
            report = ingest("BTC-USD", "1d", 0, 301 * 86400, database, session)
            self.assertEqual(report["status"], "COMPLETE")
            self.assertEqual(len(report["pages"]), 2)
            self.assertEqual(session.get.call_count, 2)
            again = ingest("BTC-USD", "1d", 0, 301 * 86400, database, session)
            self.assertEqual(again["pages"], report["pages"])
            self.assertEqual(session.get.call_count, 2)
            bars, manifest = load_dataset(database, "BTC-USD", "1d", 0, 301 * 86400)
            self.assertEqual(len(bars), 301)
            self.assertEqual(len(manifest["dataset_sha256"]), 64)

    def test_features_are_causal_and_outcomes_are_separate(self):
        bars = [{"time": index * 86400000, "open": 100 + index, "high": 102 + index,
                 "low": 99 + index, "close": 101 + index, "volume": 10} for index in range(240)]
        features = causal_features(bars)
        prefix = causal_features(bars[:220])
        self.assertTrue(features.iloc[:220].equals(prefix))
        self.assertFalse(features.iloc[198]["ready"])
        self.assertEqual(features.iloc[199]["regime"], "UPTREND")
        self.assertNotIn("net_return", features.columns)
        labels = forward_outcomes(bars, horizon=5, fee_bps=0, slippage_bps=0)
        self.assertAlmostEqual(labels.iloc[0]["net_return"], 106 / 101 - 1)
        self.assertEqual(labels.iloc[0]["matures_at_ms"], 6 * 86400000)
        self.assertTrue(labels.tail(5)["net_return"].isna().all())
        self.assertLess(forward_outcomes(bars).iloc[0]["net_return"], labels.iloc[0]["net_return"])
        with self.assertRaisesRegex(ValueError, "contiguous"):
            causal_features(bars[:100] + bars[101:])

    def test_provider_error_and_invalid_range(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "history.sqlite3"
            session = Mock()
            session.get.return_value = Mock(status_code=429)
            with self.assertRaisesRegex(ValueError, "429"):
                ingest("ETH-USD", "1d", 0, 86400, database, session)
            with self.assertRaisesRegex(ValueError, "aligned"):
                ingest("BTC-USD", "1d", 1, 86400, database, session)

    def test_evaluation_purges_boundaries_and_excludes_future_windows(self):
        bars = [{"time": index * 86400000, "open": 100 + index, "high": 102 + index,
                 "low": 99 + index, "close": 101 + index, "volume": 10} for index in range(1000)]
        report = evaluate(bars, {"dataset_sha256": "fixture"})
        self.assertEqual(len(report["windows"]), 5)
        self.assertEqual(report["windows"][0]["eligible_decisions"], 45)
        self.assertEqual(report["windows"][0]["purged_or_unmatured"], 5)
        self.assertEqual(report["windows"][0]["policies"]["UPTREND"]["trades"], 9)
        mutated = [dict(bar) for bar in bars]
        for bar in mutated[800:]:
            for field in ("open", "high", "low", "close"):
                bar[field] *= 2
        self.assertEqual(report["windows"][:4], evaluate(mutated, {})["windows"][:4])
        self.assertFalse(report["execution_enabled"])
        self.assertFalse(report["model_trained"])

    def test_interrupted_download_resumes_and_corruption_fails_closed(self):
        session = Mock()
        page = json.dumps([[index * 86400, 99, 102, 100, 101, 10] for index in range(301)]).encode()
        session.get.side_effect = [Mock(status_code=200, content=page), Mock(status_code=429)]
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "history.sqlite3"
            with self.assertRaisesRegex(ValueError, "429"):
                ingest("BTC-USD", "1d", 0, 301 * 86400, database, session)
            session.get.side_effect = [Mock(status_code=200, content=b'[[25920000,99,102,100,101,10]]')]
            report = ingest("BTC-USD", "1d", 0, 301 * 86400, database, session)
            self.assertEqual(report["status"], "COMPLETE")
            self.assertEqual(session.get.call_count, 3)
            with sqlite3.connect(database) as connection:
                connection.execute("UPDATE historical_pages SET raw=? WHERE start=0", (b"[]",))
            with self.assertRaisesRegex(ValueError, "checksum"):
                load_dataset(database, "BTC-USD", "1d", 0, 301 * 86400)
            with self.assertRaisesRegex(ValueError, "checksum"):
                ingest("BTC-USD", "1d", 0, 301 * 86400, database, session)

    def test_missing_history_cannot_enter_evaluation(self):
        session = Mock()
        session.get.return_value = Mock(status_code=200, content=b'[[0,99,102,100,101,10]]')
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "history.sqlite3"
            self.assertEqual(ingest("ETH-USD", "1d", 0, 172800, database, session)["status"], "INCOMPLETE")
            with self.assertRaisesRegex(ValueError, "coverage"):
                load_dataset(database, "ETH-USD", "1d", 0, 172800)