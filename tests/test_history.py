import tempfile
import unittest
from pathlib import Path

from maxtrade.history import ScanHistory
from maxtrade.accuracy import evaluate_prediction, daily_accuracy, update_outcomes


class HistoryTests(unittest.TestCase):
    def test_confidence_requires_prior_available_nonoverlapping_comparable_outcomes(self):
        import json
        from maxtrade.quality import prior_confidence, forward_validation, QUALITY_VERSION, HORIZON_MS
        current = {'product': 'Spot', 'pair': 'BTC', 'interval': '1h', 'action': 'LONG',
                   'quality': {'version': QUALITY_VERSION, 'regime': 'UPTREND', 'candidate_action': 'LONG'}}
        records = [dict(current, start_ms=index * HORIZON_MS, status='WIN', result_json=json.dumps({
            'cost_model': 'fixed-notional-v1', 'net_return_pct': 1,
            'evaluated_at_ms': (index + 1) * HORIZON_MS})) for index in range(31)]
        self.assertFalse(prior_confidence(records, current, 29 * HORIZON_MS)['available'])
        estimate = prior_confidence(records, current, 30 * HORIZON_MS)
        self.assertEqual(estimate['samples'], 30)
        self.assertTrue(estimate['available'])
        self.assertLess(estimate['wilson_95_low'], 1)
        self.assertAlmostEqual(estimate['wilson_95_high'], 1)
        overlapping = dict(records[0], start_ms=HORIZON_MS // 2)
        different_market = dict(records[0], pair='ETH')
        self.assertEqual(prior_confidence(records + [overlapping, different_market], current, 30 * HORIZON_MS)['samples'], 30)
        delayed = [dict(record, result_json=json.dumps({'cost_model': 'fixed-notional-v1',
                    'net_return_pct': 1, 'evaluated_at_ms': 100 * HORIZON_MS})) for record in records]
        self.assertEqual(prior_confidence(delayed, current, 30 * HORIZON_MS)['samples'], 0)
        folds = forward_validation(records)
        self.assertEqual(folds[0]['Calibration samples'], 0)
        self.assertEqual(folds[1]['Calibration samples'], 1)
        self.assertEqual(folds[1]['Brier score'], 0)
        crossing = dict(records[0], start_ms=30 * HORIZON_MS - HORIZON_MS // 2)
        self.assertEqual(sum(row['Baseline samples'] for row in forward_validation(records + [crossing])), 31)

    def test_prediction_evidence_round_trip_preserves_original_snapshot(self):
        history = ScanHistory(self.path)
        from datetime import datetime
        candle = int(datetime.fromisoformat('2026-10-05T17:00:00+00:00').timestamp() * 1000)
        evidence = {'version': 'test', 'regime': 'CHOP', 'candidate_action': 'NO TRADE'}
        history.save('Spot', '1h', 1, '2026-10-05T18:01:00+00:00', [{'Pair': 'BTC',
                     'Signal': 'LONG', 'Signal candle time': candle, 'Stop': 90, 'Target': 120, 'Quality': evidence}])
        prediction = ScanHistory(self.path).predictions()[0]
        self.assertEqual(prediction['quality'], evidence)
        self.assertEqual(prediction['interval'], '1h')

    def test_net_outcomes_include_costs_expiry_and_keep_legacy_unscored(self):
        import json
        from maxtrade.accuracy import modeled_return
        for long in (True, False):
            result = modeled_return(100, 100, long)
            self.assertLess(result['net_return_pct'], -0.29)
            self.assertEqual(result['gross_return_pct'], 0)
        hour = 3600000
        bars = [{'time': index * hour, 'open': 100, 'close': 100, 'low': 95, 'high': 105}
                for index in range(24)]
        expired = evaluate_prediction({'start_ms': 0, 'action': 'LONG', 'stop': 90, 'target': 120}, bars, 24 * hour)
        self.assertEqual(expired['status'], 'EXPIRED')
        self.assertEqual(expired['exit'], 100)
        self.assertLess(expired['net_return_pct'], 0)
        records = [{'created_at': '2026-10-07', 'status': 'EXPIRED', 'result_json': json.dumps(expired)},
                   {'created_at': '2026-10-07', 'status': 'WIN', 'result_json': None}]
        summary = daily_accuracy(records)[0]
        self.assertEqual(summary['Net samples'], 1)
        self.assertEqual(summary['Net win rate %'], 0)
        self.assertLess(summary['Mean net outcome %'], 0)

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
