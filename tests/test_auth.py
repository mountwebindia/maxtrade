import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from maxtrade.auth import login_config, verify_login


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        salt = bytes.fromhex("0123456789abcdef0123456789abcdef")
        cls.password = "test-only-long-password"
        digest = hashlib.pbkdf2_hmac("sha256", cls.password.encode(), salt, 600000).hex()
        cls.config = ("test-user", f"pbkdf2_sha256$600000${salt.hex()}${digest}")

    def test_missing_and_malformed_config_fail_closed(self):
        with patch.dict("os.environ", {}, clear=True):
            for secrets in ({}, {"MAXTRADE_USERNAME": "user", "MAXTRADE_PASSWORD_HASH": "bad"}):
                with self.assertRaises(ValueError):
                    login_config(secrets)

    def test_wrong_username_or_password_denied(self):
        self.assertTrue(verify_login("test-user", self.password, self.config))
        self.assertFalse(verify_login("wrong-user", self.password, self.config))
        self.assertFalse(verify_login("test-user", "wrong-password", self.config))

    def test_gate_login_logout_and_expiry(self):
        with patch("maxtrade.auth.login_config", return_value=self.config), \
                patch("maxtrade.auth.time.time", return_value=1000), \
                patch("maxtrade.chart_page.CoinDCXClient") as client:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20)
            app.session_state["navigation"] = "Chart"
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.tabs), 0)
            client.assert_not_called()
            app.text_input(key="login_username").set_value("test-user")
            app.text_input(key="login_password").set_value("incorrect")
            app.button[0].click().run()
            self.assertEqual(len(app.tabs), 0)
            app.text_input(key="login_username").set_value("test-user")
            app.text_input(key="login_password").set_value(self.password)
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.tabs), 4)
            self.assertEqual(app.sidebar.button(key="auth_logout").label, "Sign out")
            self.assertNotIn("login_password", app.session_state)
            app.session_state["research_report"] = {"private": "snapshot"}
            app.button(key="auth_logout").click().run()
            self.assertEqual(len(app.tabs), 0)
            self.assertEqual(len(app.sidebar.button), 0)
            self.assertNotIn("research_report", app.session_state)
            app.text_input(key="login_username").set_value("test-user")
            app.text_input(key="login_password").set_value(self.password)
            app.button[0].click().run()
            with patch("maxtrade.auth.time.time", return_value=4601):
                app.run()
            self.assertEqual(len(app.tabs), 0)