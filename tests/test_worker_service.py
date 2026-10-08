import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from maxtrade.worker import worker_configuration, run_once
from scripts.install_worker import service_definition
from scripts.monitor_worker import monitor_once, worker_health


class WorkerServiceTests(unittest.TestCase):
    def test_shadow_training_is_opt_in_and_failure_isolated(self):
        from datetime import datetime, timezone
        from scripts.research_worker import research_once
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('scripts.research_worker.ingest', return_value={
                    'status': 'COMPLETE', 'coverage_pct': 100, 'missing_bars': 0,
                    'start': 0, 'end': 0}), \
                    patch('scripts.research_worker.historical_context', return_value={
                        'status': 'AVAILABLE', 'regime': 'UPTREND'}), \
                    patch('maxtrade.training.train_shadow') as train:
                now = datetime.now(timezone.utc)
                disabled = research_once(root / 'history', root / 'missing', root / 'state', now)
                train.assert_not_called()
                self.assertFalse(disabled['training_enabled'])
                train.side_effect = [ValueError('failed'), {'status': 'TRAINED SHADOW'}]
                enabled = research_once(root / 'history', root / 'missing', root / 'state',
                                        now, root / 'models.sqlite3')
                self.assertEqual(train.call_count, 2)
                self.assertTrue(enabled['model_trained'])
                self.assertFalse(enabled['execution_enabled'])
                self.assertEqual(enabled['markets']['BTC-USD']['training']['status'], 'UNAVAILABLE')
                self.assertFalse((root / 'missing').exists())

    def test_worker_saves_frozen_prediction_quality(self):
        from maxtrade.history import ScanHistory
        quality = {'version': 'test-quality', 'mode': 'SHADOW ONLY'}
        report = {'product': 'Spot', 'symbol': 'B-BTC_USDT', 'technical_bias': 'LONG',
                  'errors': [], 'created_at': '2026-10-07T10:00:00+00:00', 'blockers': [],
                  'quality': quality, 'decision': 'NO TRADE',
                  'evidence': [{'interval': '1h', 'event_time': '2026-10-07T10:00:00+00:00',
                                'values': {'stop': 90, 'target': 110}}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ledger.sqlite3'
            with patch('maxtrade.worker.CoinDCXClient'), \
                    patch('maxtrade.paper.PaperLedger.reconcile'), \
                    patch('maxtrade.worker.run_coordinated_research', return_value=report), \
                    patch('maxtrade.worker.update_outcomes', return_value=[]), \
                    patch('maxtrade.worker.save_daily_summary'), \
                    patch('maxtrade.worker.telegram_config', return_value=None):
                self.assertEqual(run_once(path, ['B-BTC_USDT']), 0)
            predictions = ScanHistory(path).predictions()
            self.assertEqual(predictions[0]['quality'], quality)
            self.assertEqual(ScanHistory(path).recent_research()[0]['report']['agents'][-1]['agent'], 'challenger')

    def test_monitor_reports_research_failure(self):
        import json
        from datetime import datetime, timezone
        from maxtrade.history import ScanHistory
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / 'ledger.sqlite3'
            ScanHistory(database).save_worker_status(now, 'watch', 0)
            secrets = root / 'secrets.toml'
            secrets.write_text('TELEGRAM_BOT_TOKEN = "123:test_token"\nTELEGRAM_CHAT_ID = "123"\n')
            secrets.chmod(0o600)
            research = root / 'research.json'
            research.write_text(json.dumps({'generated': now.isoformat(), 'failures': 1}))
            with patch.dict(os.environ, {}, clear=True), patch('scripts.monitor_worker.send_message'):
                status = monitor_once(database, secrets, root / 'state.json', now, research)
            self.assertIn('Research refresh', status)

    def test_research_refresh_is_closed_utc_shadow_only_and_isolates_failures(self):
        from datetime import datetime, timezone
        from scripts.research_worker import research_once
        now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('scripts.research_worker.ingest') as ingest, \
                    patch('scripts.research_worker.historical_context', return_value={
                        'status': 'AVAILABLE', 'regime': 'UPTREND'}):
                ingest.side_effect = [ValueError('provider failed')] + [
                    {'status': 'COMPLETE', 'coverage_pct': 100, 'missing_bars': 0,
                     'start': 0, 'end': 0} for index in range(3)]
                report = research_once(root / 'history.sqlite3', root / 'missing.sqlite3',
                                       root / 'status.json', now)
            self.assertEqual(ingest.call_count, 4)
            for call in ingest.call_args_list:
                self.assertEqual(call.args[3], int(now.timestamp()) // 86400 * 86400)
            self.assertFalse(report['execution_enabled'])
            self.assertFalse(report['model_trained'])
            self.assertEqual(report['markets']['BTC-USD']['1d']['status'], 'UNAVAILABLE')
            self.assertEqual(report['markets']['ETH-USD']['1h']['status'], 'COMPLETE')
            self.assertFalse((root / 'missing.sqlite3').exists())
            self.assertEqual((root / 'status.json').stat().st_mode & 0o077, 0)

    def test_monitor_stale_failure_and_recovery_notifications_are_deduplicated(self):
        from datetime import datetime, timedelta, timezone
        from maxtrade.history import ScanHistory
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'paper.sqlite3'
            state = Path(directory) / 'monitor.json'
            secrets = Path(directory) / 'secrets.toml'
            secrets.write_text('TELEGRAM_BOT_TOKEN = "123:test_token"\nTELEGRAM_CHAT_ID = "123"\n')
            secrets.chmod(0o600)
            history = ScanHistory(database)
            now = datetime.now(timezone.utc)
            history.save_worker_status(now - timedelta(minutes=36), 'watch', 0)
            with patch.dict(os.environ, {}, clear=True), patch('scripts.monitor_worker.send_message') as send:
                self.assertIn('stale', monitor_once(database, secrets, state, now))
                monitor_once(database, secrets, state, now)
                self.assertEqual(send.call_count, 1)
                history.save_worker_status(now, 'watch', 1)
                self.assertIn('failures', monitor_once(database, secrets, state, now))
                history.save_worker_status(now, 'watch', 0)
                self.assertEqual(monitor_once(database, secrets, state, now), 'healthy')
                self.assertEqual(send.call_count, 3)

    def test_monitor_missing_database_does_not_create_it(self):
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'missing.sqlite3'
            self.assertIn('unavailable', worker_health(database, datetime.now(timezone.utc)))
            self.assertFalse(database.exists())

    def test_monitor_retries_delivery_without_acknowledging_failure(self):
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'missing.sqlite3'
            state = Path(directory) / 'monitor.json'
            secrets = Path(directory) / 'secrets.toml'
            secrets.write_text('TELEGRAM_BOT_TOKEN = "123:test_token"\nTELEGRAM_CHAT_ID = "123"\n')
            secrets.chmod(0o600)
            with patch.dict(os.environ, {}, clear=True), patch('scripts.monitor_worker.send_message', side_effect=ValueError):
                with self.assertRaises(ValueError):
                    monitor_once(database, secrets, state, datetime.now(timezone.utc))
            self.assertFalse(state.exists())

    def test_configuration_blocker_preserves_reconciliation_without_submitting(self):
        from maxtrade.paper import PaperLedger
        from maxtrade.history import ScanHistory
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'paper.sqlite3'
            ledger = PaperLedger(path)
            ledger.set_automation(True)
            report = {'technical_bias': 'NO TRADE', 'errors': [], 'evidence': [],
                      'created_at': '2026-10-07T10:00:00+00:00', 'blockers': []}
            decision = {'approved': True, 'decision': 'BUY', 'blockers': []}
            with patch('maxtrade.worker.CoinDCXClient') as client, \
                    patch('maxtrade.worker.run_coordinated_research', return_value=report), \
                    patch('maxtrade.worker.coordinate', return_value=decision), \
                    patch('maxtrade.worker.update_outcomes', return_value=[]), \
                    patch('maxtrade.worker.save_daily_summary'), \
                    patch('maxtrade.worker.telegram_config', return_value=None), \
                    patch.object(PaperLedger, 'reconcile') as reconcile, \
                    patch.object(PaperLedger, 'submit') as submit:
                client.return_value.spot_candles.return_value = []
                run_once(path, ['B-BTC_USDT'], entry_blocker='Private integrations unavailable')
                reconcile.assert_called_once()
                submit.assert_not_called()
            saved = ScanHistory(path).recent_research()[0]['report']
            self.assertEqual(saved['decision'], 'NO TRADE')
            self.assertFalse(saved['risk']['approved'])
            self.assertIn('Private integrations unavailable', saved['blockers'])

    def test_service_is_supervised_and_requires_private_integrations(self):
        definition = service_definition(Path("/tmp/maxtrade"))
        self.assertTrue(definition["KeepAlive"])
        self.assertTrue(definition["RunAtLoad"])
        self.assertIn("--require-integrations", definition["ProgramArguments"])
        self.assertEqual(definition["EnvironmentVariables"]["MAXTRADE_DATABASE"],
                         "/tmp/maxtrade/data/scan_history.sqlite3")
        self.assertNotIn("TOKEN", repr(definition))

    def test_private_configuration_required_and_redacted(self):
        with patch.dict(os.environ, {}, clear=True), tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secrets.toml"
            with self.assertRaisesRegex(ValueError, "missing or invalid"):
                worker_configuration(path, True)
            with path.open("w") as output:
                output.write('AZURE_OPENAI_API_KEY = "test-private"\n'
                             'TELEGRAM_BOT_TOKEN = "123:test_token"\nTELEGRAM_CHAT_ID = "123"\n')
            path.chmod(0o600)
            azure, telegram = worker_configuration(path, True)
            self.assertEqual(azure.deployment, "gpt-6-astra-2")
            self.assertNotIn("test-private", repr(azure))
            self.assertNotIn("test_token", repr(telegram))
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "owner-only"):
                worker_configuration(path, True)
            with self.assertRaisesRegex(ValueError, "waiting"):
                worker_configuration(None, True)