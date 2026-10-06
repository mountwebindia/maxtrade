from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from maxtrade.accuracy import daily_accuracy
from maxtrade.settings import TelegramConfig, telegram_config


def send_message(configuration: TelegramConfig, message: str) -> None:
    try:
        response = requests.post(
            f'https://api.telegram.org/bot{configuration.token}/sendMessage',
            json={'chat_id': configuration.chat_id, 'text': message[:4000],
                  'link_preview_options': {'is_disabled': True}}, timeout=15, allow_redirects=False,
        )
        if response.status_code != 200 or response.json().get('ok') is not True:
            raise ValueError('Telegram rejected delivery; check bot access and chat ID privately.')
    except (requests.RequestException, TypeError, AttributeError, ValueError):
        raise ValueError('Telegram delivery failed; check configuration, bot access and network.') from None


def deliver_notifications(history, configuration: TelegramConfig, now: datetime) -> int:
    failures = 0
    for alert in history.claim_notifications(configuration.chat_id, now):
        error = None
        try:
            send_message(configuration, f"MaxTrade | PAPER ONLY\n{alert['symbol']}\n{alert['created_at']}\n{alert['message']}")
        except ValueError as failure:
            error = str(failure)
            failures += 1
        history.finish_notification(alert['id'], configuration.chat_id, now, error)
    return failures


def save_daily_summary(history, ledger, now: datetime, days_ago: int = 1) -> None:
    day = (now.astimezone(timezone.utc) - timedelta(days=days_ago)).date().isoformat()
    performance = ledger.performance()
    paper = next((row for row in performance['daily'] if row['Date (UTC)'] == day), None)
    accuracy = next((row for row in daily_accuracy(history.predictions()) if row['Date UTC'] == day), None)
    phase = 'provisional' if days_ago == 1 else '48h update'
    message = f'Daily report {day} UTC | {phase}\n'
    if paper:
        message += f"Closed: {paper['Closed']} | Wins: {paper['Wins']} | Net win rate: {paper['Win rate %']:.2f}%\nNet P&L: {paper['Net P&L (USDT)']:.2f} USDT\n"
    else:
        message += 'Closed paper trades: 0 | Net win rate: N/A\n'
    if accuracy:
        value = accuracy['Target accuracy %']
        rate = 'N/A' if value is None else f'{value:.2f}%'
        message += f"Technical target accuracy: {rate} | Scored: {accuracy['Scored']} | Pending: {accuracy['Pending']} | Gaps: {accuracy['Data gaps']}\n"
    else:
        message += 'Technical target accuracy: N/A | Scored: 0\n'
    message += 'Pending/gaps excluded; 24h signal outcomes can resolve later. Gross signal accuracy is not net profitability.'
    history.save_alert(f'daily-summary:{day}:{days_ago}', now.astimezone(timezone.utc).isoformat(), 'DAILY REPORT', message)


def render_telegram_settings() -> None:
    import streamlit as st
    from maxtrade.history import ScanHistory

    st.markdown('#### Telegram alerts')
    try:
        secrets = st.secrets.to_dict()
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        secrets = {}
    try:
        configuration = telegram_config(secrets=secrets)
    except ValueError as error:
        st.warning(str(error))
        configuration = None
    st.info('Configured · delivery unverified' if configuration else 'Not configured')
    if st.button('Test Telegram delivery', icon=':material/send:', disabled=configuration is None, key='telegram_test'):
        try:
            send_message(configuration, 'MaxTrade paper alerts connected. No real order submitted.')
            st.success('Telegram test delivered.')
        except ValueError as error:
            st.warning(str(error))
    status = ScanHistory().notification_status()
    if status:
        st.dataframe(status, hide_index=True, width='stretch')