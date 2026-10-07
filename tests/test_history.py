import tempfile
import unittest
from pathlib import Path

from maxtrade.history import ScanHistory
from maxtrade.accuracy import evaluate_prediction, daily_accuracy, update_outcomes


class HistoryTests(unittest.TestCase):
    def test_rejection_summary_excludes_manual_and_approved_and_deduplicates_reasons(self):
        history = ScanHistory(self.path)
        report = {'created_at': '2026-10-07T01:00:00+00:00', 'symbol': 'B-BTC_USDT',
                  'paper_policy': 'autonomous-paper-v1', 'decision': 'NO TRADE',
                  'blockers': ['stale', 'stale', 'AI veto']}
        history.save_research(report)
        history.save_research(dict(report, decision='BUY'))
        history.save_research(dict(report, paper_policy='manual'))
        rows = history.rejection_summary()
        self.assertEqual({item['Reason'] for item in rows}, {'stale', 'AI veto'})
        self.assertTrue(all(item['Assessments'] == 1 for item in rows))

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

    def test_automatic_outcomes_persist_and_do_not_rescore(self):
        from datetime import datetime
        from unittest.mock import Mock
        history = ScanHistory(self.path)
        candle = int(datetime.fromisoformat("2026-10-05T17:00:00+00:00").timestamp() * 1000)
        history.save("Spot", "1h", 1, "2026-10-05T18:01:00+00:00", [{
            "Pair": "B-BTC_USDT", "Signal": "LONG", "Signal candle time": candle,
            "Stop": 90, "Target": 120}])
        start = history.predictions()[0]["start_ms"]
        client = Mock()
        client.spot_candles.return_value = [{"time": start, "open": 100, "close": 120, "low": 95, "high": 125}]
        self.assertEqual(update_outcomes(history, client, start + 3600000), [])
        self.assertEqual(ScanHistory(self.path).predictions()[0]["status"], "WIN")
        update_outcomes(history, client, start + 7200000)
        client.spot_candles.assert_called_once()

    def test_telegram_retries_persist_and_success_deduplicates(self):
        from datetime import datetime, timedelta, timezone
        from unittest.mock import patch
        from maxtrade.notifications import deliver_notifications
        from maxtrade.settings import telegram_config
        now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
        history = ScanHistory(self.path)
        history.save_alert('signal', now.isoformat(), 'BTC', 'Paper BUY')
        configuration = telegram_config({'TELEGRAM_BOT_TOKEN': '123:test', 'TELEGRAM_CHAT_ID': '-123'}, {})
        with patch('maxtrade.notifications.send_message', side_effect=ValueError('Delivery failed')) as send:
            self.assertEqual(deliver_notifications(history, configuration, now), 1)
            self.assertEqual(deliver_notifications(history, configuration, now), 0)
            send.assert_called_once()
        with patch('maxtrade.notifications.send_message') as send:
            self.assertEqual(deliver_notifications(ScanHistory(self.path), configuration, now + timedelta(minutes=6)), 0)
            deliver_notifications(history, configuration, now + timedelta(minutes=12))
            send.assert_called_once()
        self.assertEqual(history.notification_status()[0]['attempts'], 2)
        self.assertIsNotNone(history.notification_status()[0]['sent_at'])

    def test_telegram_errors_never_expose_token(self):
        import requests
        from unittest.mock import patch
        from maxtrade.notifications import send_message
        from maxtrade.settings import telegram_config
        configuration = telegram_config({'TELEGRAM_BOT_TOKEN': '123:private', 'TELEGRAM_CHAT_ID': '123'}, {})
        self.assertNotIn('private', repr(configuration))
        with patch('maxtrade.notifications.requests.post', side_effect=requests.ConnectionError('123:private')):
            with self.assertRaises(ValueError) as error:
                send_message(configuration, 'Paper only')
        self.assertNotIn('private', str(error.exception))
        with self.assertRaises(ValueError):
            telegram_config({'TELEGRAM_CHAT_ID': '123'}, {})

    def test_daily_backup_can_restore_all_records(self):
        from datetime import datetime, timezone
        import sqlite3
        history = ScanHistory(self.path)
        history.save('Spot', '1h', 1, 'now', [])
        destination = history.daily_backup(datetime(2026, 10, 6, tzinfo=timezone.utc))
        restored = ScanHistory(destination)
        self.assertEqual(len(restored.recent()), 1)
        with sqlite3.connect(destination) as connection:
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_daily_reports_separate_net_win_rate_and_signal_accuracy(self):
        from datetime import datetime, timezone, timedelta
        from unittest.mock import Mock
        from maxtrade.notifications import save_daily_summary
        history = ScanHistory(self.path)
        ledger = Mock()
        ledger.performance.return_value = {'daily': [{'Date (UTC)': '2026-10-05', 'Closed': 2,
            'Wins': 1, 'Win rate %': 50.0, 'Net P&L (USDT)': -2.0}]}
        now = datetime(2026, 10, 6, tzinfo=timezone.utc)
        save_daily_summary(history, ledger, now)
        save_daily_summary(history, ledger, now)
        save_daily_summary(history, ledger, now + timedelta(days=1), days_ago=2)
        alerts = history.recent_alerts()
        self.assertEqual(len(alerts), 2)
        self.assertTrue(any('48h update' in row['message'] for row in alerts))
        self.assertIn('Net win rate: 50.00%', alerts[0]['message'])
        self.assertIn('Net P&L: -2.00 USDT', alerts[0]['message'])
        self.assertIn('Technical target accuracy: N/A', alerts[0]['message'])
