import tempfile
import unittest
from pathlib import Path

from maxtrade.history import ScanHistory
from maxtrade.accuracy import evaluate_prediction, daily_accuracy


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

    def test_forward_predictions_deduplicate_and_preserve_final_outcomes(self):
        history = ScanHistory(self.path)
        from datetime import datetime
        candle = int(datetime.fromisoformat("2026-10-05T17:00:00+00:00").timestamp() * 1000)
        rows = [{"Pair": "B-BTC_USDT", "Signal": "LONG", "Signal candle time": candle,
                 "Stop": 90, "Target": 120}]
        for timestamp in ["2026-10-05T18:01:00+00:00", "2026-10-05T18:05:00+00:00"]:
            history.save("Spot", "1h", 5, timestamp, rows)
        record = history.predictions()[0]
        self.assertEqual(len(history.predictions()), 1)
        self.assertEqual(record["start_ms"], candle + 2 * 3600000)
        history.save_outcome(record["fingerprint"], {"status": "WIN"})
        history.save_outcome(record["fingerprint"], {"status": "LOSS"})
        self.assertEqual(ScanHistory(self.path).predictions()[0]["status"], "WIN")

    def test_outcomes_ties_gaps_expiry_short_and_unscored_denominator(self):
        hour = 3600000
        prediction = {"start_ms": 0, "action": "LONG", "stop": 90, "target": 120}
        bar = {"time": 0, "open": 100, "close": 100, "low": 80, "high": 130}
        self.assertEqual(evaluate_prediction(prediction, [bar], hour)["status"], "LOSS")
        bar["low"] = 95
        self.assertEqual(evaluate_prediction(prediction, [bar], hour)["status"], "WIN")
        self.assertEqual(evaluate_prediction(prediction, [bar], 0)["status"], "PENDING")
        self.assertEqual(evaluate_prediction(prediction, [], hour)["status"], "DATA GAP")
        bars = [{"time": index * hour, "open": 100, "close": 100, "low": 95, "high": 105} for index in range(24)]
        self.assertEqual(evaluate_prediction(prediction, bars, 24 * hour)["status"], "EXPIRED")
        short = {"start_ms": 0, "action": "SHORT", "stop": 110, "target": 80}
        self.assertEqual(evaluate_prediction(short, [{**bar, "low": 70, "high": 105}], hour)["status"], "WIN")
        self.assertEqual(evaluate_prediction(prediction, [{**bar, "open": 125, "high": 130}], hour)["status"], "INVALID ENTRY")
        summary = daily_accuracy([{"created_at": "2026-10-05", "status": state} for state in
                                  ["WIN", "LOSS", "EXPIRED", "PENDING", "DATA GAP", "INVALID ENTRY"]])[0]
        self.assertEqual(summary["Target accuracy %"], 33.33)
        self.assertEqual(summary["Scored"], 3)
