from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math

import requests

from maxtrade.accuracy import daily_accuracy
from maxtrade.settings import TelegramConfig, telegram_config


def save_setup_alert(history, report: dict, now: datetime) -> bool:
    direction = report.get('technical_bias')
    if direction not in {'LONG', 'SHORT'} or report.get('errors') or report.get('product') != 'Spot':
        return False
    hourly = next((item for item in report.get('evidence', []) if item.get('interval') == '1h'), None)
    if not hourly or hourly.get('completed') is not True or hourly.get('event_time_kind') != 'candle_close':
        return False
    try:
        closed = datetime.fromisoformat(hourly['event_time'])
        expires = datetime.fromisoformat(hourly['expires_at'])
        if closed.tzinfo is None or expires.tzinfo is None or now.tzinfo is None:
            return False
        if not closed <= now < min(expires, closed + timedelta(hours=1)):
            return False
        levels = [float(hourly['values'][field]) for field in ('entry', 'stop', 'target')]
        if not all(math.isfinite(value) and value > 0 for value in levels):
            return False
        entry, stop, target = levels
        if not (stop < entry < target if direction == 'LONG' else target < entry < stop):
            return False
        symbol = report['symbol']
        closed = closed.astimezone(timezone.utc)
        expires = min(expires, closed + timedelta(hours=1)).astimezone(timezone.utc)
        fingerprint = 'setup-v1:' + json.dumps([report['product'], symbol, direction, closed.isoformat()])
        blockers = '; '.join(dict.fromkeys(report.get('blockers', []))) or 'None recorded; not approval'
        quality = report.get('quality') or {}
        shadow = '; '.join(dict.fromkeys(quality.get('blockers', []))) or 'None recorded; not approval'
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    action = 'BUY' if direction == 'LONG' else 'SELL'
    message = (f'{action} setup | Spot | 1h | Research only\n'
               f'Reason: {hourly.get("reason") or "Decision evidence unavailable"}\n'
               f'Entry: {entry:.8g} | SL: {stop:.8g} | TP: {target:.8g}\n'
               f'Closed candle: {closed:%Y-%m-%d %H:%M} UTC\n'
               f'Valid until: {expires:%Y-%m-%d %H:%M} UTC | Not live\n'
               f'PAPER decision: {report.get("decision", "UNAVAILABLE")}\n'
               f'PAPER blockers: {blockers}\n'
               f'Shadow quality blockers: {shadow} | Shadow only\n'
               'Setup is not PAPER approval. Spot SELL is research only; no short order.')
    history.save_alert(fingerprint, now.astimezone(timezone.utc).isoformat(), symbol, message,
                       expires_at=expires.isoformat())
    return True


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
        if alert['fingerprint'].startswith('setup-v1:'):
            try:
                identity = json.loads(alert['fingerprint'][len('setup-v1:'):])
                closed = datetime.fromisoformat(identity[3])
                if closed.tzinfo is None or not closed <= now < closed + timedelta(hours=1):
                    continue
            except (ValueError, TypeError, IndexError, KeyError):
                continue
        error = None
        try:
            heading = 'MaxTrade | Research setup' if alert['fingerprint'].startswith('setup-v1:') else 'MaxTrade | Sirf PAPER simulation'
            send_message(configuration, f"{heading}\n{alert['symbol']}\n{alert['created_at']}\n{alert['message']}\nReal order nahi bheja gaya.")
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
    message = f'Roz ka report {day} UTC | {phase}\n'
    if paper:
        message += f"Band trades: {paper['Closed']} | Jeete: {paper['Wins']} | Net win rate: {paper['Win rate %']:.2f}%\nNet P&L: {paper['Net P&L (USDT)']:.2f} USDT\n"
    else:
        message += 'Band paper trades: 0 | Net win rate: N/A\n'
    if accuracy:
        value = accuracy['Target accuracy %']
        rate = 'N/A' if value is None else f'{value:.2f}%'
        message += f"Technical target accuracy: {rate} | Check hue: {accuracy['Scored']} | Pending: {accuracy['Pending']} | Data gaps: {accuracy['Data gaps']}\n"
    else:
        message += 'Technical target accuracy: N/A | Check hue: 0\n'
    message += 'Pending aur data gaps count nahi hue; 24h outcomes baad mein resolve ho sakte hain. Signal accuracy ka matlab net profit nahi hai.'
    history.save_alert(f'daily-summary:{day}:{days_ago}', now.astimezone(timezone.utc).isoformat(), 'DAILY REPORT', message)


def render_telegram_settings() -> None:
    import sqlite3
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
            send_message(configuration, 'MaxTrade PAPER alerts connect ho gaye. Koi real order nahi bheja gaya.')
            st.success('Telegram test delivered.')
        except ValueError as error:
            st.warning(str(error))
    try:
        status = ScanHistory().notification_status()
    except (OSError, sqlite3.Error):
        st.warning('Notification history unavailable: database access failed. Stored alerts have not been replaced or reset.')
        return
    if status:
        st.dataframe(status, hide_index=True, width='stretch')