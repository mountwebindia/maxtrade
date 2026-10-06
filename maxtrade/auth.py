from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections.abc import Mapping
from typing import Any

import streamlit as st


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
    session = st.session_state.get("authenticated_session")
    if session_valid(config):
        if st.button("Sign out", icon=":material/logout:", key="auth_logout"):
            clear_session()
            st.rerun()
        return
    if session:
        clear_session()
    with st.form("login_form"):
        st.subheader("Sign in")
        st.text_input("Username", key="login_username", max_chars=128)
        st.text_input("Password", type="password", key="login_password", max_chars=1024)
        submitted = st.form_submit_button("Sign in", icon=":material/login:", type="primary", width="stretch")
    if submitted:
        username = st.session_state.pop("login_username", "")
        password = st.session_state.pop("login_password", "")
        if now < st.session_state.get("login_retry_at", 0):
            st.error("Too many attempts. Try again in a minute.")
        elif verify_login(username, password, config):
            clear_session()
            st.session_state["authenticated_session"] = {"config": fingerprint, "expires_at": now + 3600}
            st.rerun()
        else:
            attempts = st.session_state.get("login_attempts", 0) + 1
            st.session_state["login_attempts"] = attempts
            if attempts >= 5:
                st.session_state["login_retry_at"] = now + 60
                st.session_state["login_attempts"] = 0
            st.error("Invalid username or password.")
    st.stop()