import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from maxtrade.worker import worker_configuration
from scripts.install_worker import service_definition


class WorkerServiceTests(unittest.TestCase):
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