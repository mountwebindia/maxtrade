from __future__ import annotations

import hashlib
import hmac
import os
import time
from datetime import datetime, timezone
from collections.abc import Mapping
from typing import Any

import streamlit as st
from itsdangerous import BadSignature, URLSafeSerializer
from streamlit_cookies_controller import CookieController


LOGIN_COOKIE = 'maxtrade_login'
REMEMBER_SECONDS = 30 * 24 * 3600


def login_token(config: tuple[str, str], expires_at: float) -> str:
    return URLSafeSerializer(config[1], salt='maxtrade-browser-login-v1').dumps(
        {'user': config[0], 'expires_at': expires_at})


def restore_login(token: str, config: tuple[str, str]) -> dict | None:
    try:
        payload = URLSafeSerializer(config[1], salt='maxtrade-browser-login-v1').loads(token)
        expires_at = float(payload['expires_at'])
        if payload['user'] != config[0] or not time.time() < expires_at <= time.time() + REMEMBER_SECONDS:
            return None
        return {'config': hashlib.sha256(repr(config).encode()).hexdigest(), 'expires_at': expires_at}
    except (BadSignature, ValueError, KeyError, TypeError):
        return None


def sign_out() -> None:
    clear_session()
    st.session_state['login_cookie_logout'] = True


def login_config(secrets: Mapping[str, Any]) -> tuple[str, str]:
    username = str(os.environ.get("MAXTRADE_USERNAME") or secrets.get("MAXTRADE_USERNAME") or "")
    password_hash = str(os.environ.get("MAXTRADE_PASSWORD_HASH") or secrets.get("MAXTRADE_PASSWORD_HASH") or "")
    parts = password_hash.split("$")
    if not username or len(parts) != 4 or parts[:2] != ["pbkdf2_sha256", "600000"]:
        raise ValueError("Login credentials are not configured")
    try:
        salt, digest = bytes.fromhex(parts[2]), bytes.fromhex(parts[3])
    except ValueError as error:
        raise ValueError("Invalid password hash") from error
    if len(salt) != 16 or len(digest) != 32:
        raise ValueError("Invalid password hash")
    return username, password_hash


def verify_login(username: str, password: str, config: tuple[str, str]) -> bool:
    expected_username, password_hash = config
    parts = password_hash.split("$")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(parts[2]), 600000).hex()
    correct_password = hmac.compare_digest(digest, parts[3])
    correct_username = hmac.compare_digest(username.encode(), expected_username.encode())
    return correct_password and correct_username


def clear_session() -> None:
    for key in list(st.session_state):
        del st.session_state[key]


def session_valid(config: tuple[str, str]) -> bool:
    session = st.session_state.get("authenticated_session")
    fingerprint = hashlib.sha256(repr(config).encode()).hexdigest()
    return bool(session and session.get("config") == fingerprint
                and time.time() < session.get("expires_at", 0))


def require_chart_login() -> None:
    try:
        valid = session_valid(login_config(st.secrets))
    except (FileNotFoundError, ValueError):
        valid = False
    if not valid:
        clear_session()
        st.rerun(scope="app")


def require_login() -> None:
    try:
        config = login_config(st.secrets)
    except (FileNotFoundError, ValueError):
        clear_session()
        st.error("Dashboard locked. The owner must configure login credentials in server secrets.")
        st.stop()
    fingerprint = hashlib.sha256(repr(config).encode()).hexdigest()
    now = time.time()
    cookies = CookieController(key='maxtrade_browser_cookies')
    token = st.context.cookies.get(LOGIN_COOKIE) or cookies.get(LOGIN_COOKIE)
    if st.session_state.get('login_cookie_logout'):
        if cookies.get(LOGIN_COOKIE):
            cookies.remove(LOGIN_COOKIE)
        token = None
    pending = st.session_state.get('login_cookie_pending')
    if pending:
        cookies.set(LOGIN_COOKIE, pending, expires=datetime.fromtimestamp(
            st.session_state['authenticated_session']['expires_at'], timezone.utc),
            secure=not st.context.url.startswith('http://localhost') and not st.context.url.startswith('http://127.0.0.1'),
            same_site='strict')
        if token == pending:
            st.session_state.pop('login_cookie_pending', None)
    if not st.session_state.get('authenticated_session') and token:
        restored = restore_login(token, config)
        if restored:
            st.session_state['authenticated_session'] = restored
    session = st.session_state.get("authenticated_session")
    if session_valid(config):
        return
    if session:
        clear_session()
    with st.container(key="login_form"):
        with st.form("login_form"):
            st.subheader("Sign in")
            st.text_input("Username", key="login_username", max_chars=128)
            st.text_input("Password", type="password", key="login_password", max_chars=1024)
            st.checkbox('Keep me signed in for 30 days', value=True, key='login_remember')
            submitted = st.form_submit_button("Sign in", icon=":material/login:", type="primary", width="stretch")
    if submitted:
        username = st.session_state.pop("login_username", "")
        password = st.session_state.pop("login_password", "")
        if now < st.session_state.get("login_retry_at", 0):
            st.error("Too many attempts. Try again in a minute.")
        elif verify_login(username, password, config):
            remember = st.session_state.get('login_remember', False)
            clear_session()
            expires_at = now + (REMEMBER_SECONDS if remember else 8 * 3600)
            st.session_state["authenticated_session"] = {"config": fingerprint, "expires_at": expires_at}
            if remember:
                st.session_state['login_cookie_pending'] = login_token(config, expires_at)
            st.rerun()
        else:
            attempts = st.session_state.get("login_attempts", 0) + 1
            st.session_state["login_attempts"] = attempts
            if attempts >= 5:
                st.session_state["login_retry_at"] = now + 60
                st.session_state["login_attempts"] = 0
            st.error("Invalid username or password.")
    st.stop()