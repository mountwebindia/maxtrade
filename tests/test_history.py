import tempfile
import unittest
from pathlib import Path

from maxtrade.history import ScanHistory


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "nested" / "history.sqlite3"

    def test_round_trip_survives_new_store_and_preserves_nulls(self):
        rows = [{"Market": "BTCUSDT", "Signal": "LONG", "Entry": 100.0},
                {"Market": "BAD", "Signal": "DATA ERROR", "Entry": None}]
        scan_id = ScanHistory(self.path).save("Spot", "1h", 5, "2026-10-05T18:00:00+00:00", rows)
        reopened = ScanHistory(self.path)
        snapshot = reopened.load(scan_id)
        self.assertEqual(snapshot["results"], rows)
        self.assertEqual(snapshot["product"], "Spot")
        self.assertEqual(snapshot["requested_limit"], 5)
        summary = reopened.recent()[0]
        self.assertEqual(summary["markets"], 2)
        self.assertEqual(summary["longs"], 1)
        self.assertEqual(summary["errors"], 1)

    def test_lists_newest_first_and_limits_history(self):
        history = ScanHistory(self.path)
        first = history.save("Spot", "1h", 5, "earlier", [])
        second = history.save("Futures", "4h", 10, "later", [])
        self.assertEqual([row["id"] for row in history.recent()], [second, first])
        self.assertEqual(len(history.recent(limit=1)), 1)

    def test_missing_scan_returns_none(self):
        self.assertIsNone(ScanHistory(self.path).load(999))

    def test_invalid_nonfinite_payload_is_not_saved(self):
        history = ScanHistory(self.path)
        with self.assertRaises(ValueError):
            history.save("Spot", "1h", 5, "now", [{"Price": float("nan")}])
        self.assertEqual(history.recent(), [])
