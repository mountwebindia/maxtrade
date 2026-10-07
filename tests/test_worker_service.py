import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from maxtrade.worker import worker_configuration, run_once
from scripts.install_worker import service_definition


class WorkerServiceTests(unittest.TestCase):
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