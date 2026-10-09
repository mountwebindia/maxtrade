from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from threading import Event
import tomllib

from maxtrade.notifications import send_message
from maxtrade.settings import telegram_config


def worker_health(database: Path, now: datetime) -> str:
    try:
        with sqlite3.connect(f'{database.resolve().as_uri()}?mode=ro', uri=True, timeout=5) as connection:
            row = connection.execute('SELECT finished_at, mode, failures FROM worker_status WHERE id=1').fetchone()
        if row is None:
            return 'Worker heartbeat missing'
        age = (now - datetime.fromisoformat(row[0])).total_seconds()
        if not 0 <= age < 2100:
            return 'Worker heartbeat stale or invalid'
        if row[1] != 'watch' or row[2] != 0:
            return 'Worker cycle reported failures or watch service inactive'
        return 'healthy'
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return 'Worker database or heartbeat unavailable'


def monitor_once(database: Path, secrets: Path, state: Path, now: datetime,
                 research_state: Path | None = None) -> str:
    status = worker_health(database, now)
    if research_state is not None:
        try:
            research = json.loads(research_state.read_text())
            age = (now - datetime.fromisoformat(research['generated'])).total_seconds()
            if not 0 <= age < 28800 or research['failures'] != 0:
                status = 'Research refresh stale or reported failures; ' + status
        except (OSError, ValueError, KeyError, TypeError):
            status = 'Research heartbeat unavailable; ' + status
    previous = None
    try:
        previous = json.loads(state.read_text()).get('status')
    except (OSError, ValueError, AttributeError):
        pass
    if status != previous:
        if secrets.stat().st_mode & 0o077:
            raise ValueError('Monitor configuration requires owner-only permissions')
        with secrets.open('rb') as source:
            configuration = telegram_config(secrets=tomllib.load(source))
        if configuration is None:
            raise ValueError('Monitor Telegram configuration unavailable')
        message = ('MaxTrade worker monitor | Sirf PAPER simulation\n'
               + ('Worker sahi chal raha hai.' if status == 'healthy' else 'Worker check mein dikkat mili: ' + status)
                   + '\n' + now.isoformat()
               + '\nYeh VM-local monitor hai; poore VM ya network ki outage yahan detect nahi hoti.')
        send_message(configuration, message)
        state.parent.mkdir(parents=True, exist_ok=True)
        temporary = state.with_suffix('.tmp')
        temporary.write_text(json.dumps({'status': status, 'checked_at': now.isoformat()}))
        temporary.chmod(0o600)
        temporary.replace(state)
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description='VM-local worker heartbeat alerts; no trading actions')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--secrets-file', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--research-state', type=Path)
    parser.add_argument('--watch', action='store_true')
    arguments = parser.parse_args()
    while True:
        try:
            status = monitor_once(arguments.database, arguments.secrets_file, arguments.state,
                                  datetime.now(timezone.utc), arguments.research_state)
            print('Monitor: ' + status, flush=True)
        except Exception:
            print('Monitor check or notification failed; private details suppressed.', flush=True)
            if not arguments.watch:
                raise SystemExit(1) from None
        if not arguments.watch:
            return
        Event().wait(60)


if __name__ == '__main__':
    main()