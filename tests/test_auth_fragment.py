import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class FragmentAuthTests(unittest.TestCase):
    def test_expired_fragment_returns_to_login_before_fetch(self):
        script = """
import streamlit as st
from maxtrade.chart_page import render_chart_snapshot
from maxtrade.auth import require_login
if st.session_state.get('authenticated_session'):
    render_chart_snapshot('Spot', '1h', 'B-BTC_USDT', True)
else:
    require_login()
"""
        with patch("maxtrade.auth.login_config", return_value=("user", "hash")), \
                patch("maxtrade.auth.time.time", return_value=1000), \
                patch("maxtrade.chart_page.CoinDCXClient") as client:
            app = AppTest.from_string(script)
            app.session_state["authenticated_session"] = {"config": "old", "expires_at": 999}
            app.run()
            self.assertFalse(app.exception)
            client.assert_not_called()
            self.assertEqual(len(app.text_input), 2)