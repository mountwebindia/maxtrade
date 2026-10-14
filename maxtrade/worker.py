from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import sqlite3
import tomllib
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from maxtrade.coindcx import CoinDCXClient
from maxtrade.accuracy import update_outcomes
from maxtrade.history import DEFAULT_PATH, ScanHistory
from maxtrade.paper import PaperLedger, coordinate
from maxtrade.notifications import deliver_notifications, save_daily_summary, save_setup_alert
from maxtrade.research import run_coordinated_research
from maxtrade.settings import (AzureOpenAIConfig, TelegramConfig, azure_openai_config,
                               gemini_shadow_config, telegram_config)


def run_once(path: Path, symbols: list[str], ai_config: AzureOpenAIConfig | None = None,
             notification_config: TelegramConfig | None = None, continuous: bool = False,
             entry_blocker: str | None = None, shadow_secrets_file: Path | None = None) -> int:
    if not 1 <= len(symbols) <= 5 or any(symbol not in {"B-BTC_USDT", "B-ETH_USDT"} for symbol in symbols):
        raise ValueError("Worker supports up to five BTC/ETH USDT spot selections")
    history = ScanHistory(path)
    ledger = PaperLedger(path)
    with closing(history._connect()) as connection, connection:
        connection.execute("CREATE TABLE IF NOT EXISTS worker_runs (slot TEXT PRIMARY KEY, started_at TEXT NOT NULL)")
    errors = 0
    for symbol in dict.fromkeys(symbols):
        now = datetime.now(timezone.utc)
        slot = f"{symbol}:{int(now.timestamp())//900}"
        with closing(history._connect()) as connection, connection:
            cursor = connection.execute("INSERT OR IGNORE INTO worker_runs VALUES (?,?)", (slot, now.isoformat()))
            claimed = cursor.rowcount == 1
        if not claimed:
            continue
        client = CoinDCXClient()
        try:
            reconciled = True
            try:
                ledger.reconcile(symbol, client.spot_candles(symbol, "1h"), datetime.now(timezone.utc))
            except Exception:
                reconciled = False
                errors += 1
                logging.exception("Paper reconciliation failed for %s; research will continue", symbol)
            report = run_coordinated_research(client, "Spot", symbol)
            report['ai_mode'] = ledger.ai_mode()
            from maxtrade.research import manager_reports
            report['performance_shadow'] = dict(ledger.performance(), mode='SHADOW ONLY')
            account_time = datetime.now(timezone.utc)
            account = ledger.account(account_time)
            report['paper_account'] = dict(account, as_of=account_time.isoformat(), mode='PAPER ONLY')
            preflight = coordinate(report, account_time, account=account, autonomous=ledger.automation_enabled())
            report.update(decision=preflight['decision'], blockers=preflight['blockers'], risk=preflight)
            report['agents'] = manager_reports(report)
            shadow_evidence = None
            shadow_error = False
            if ledger.gemini_shadow_enabled():
                from maxtrade.ai_review import encoded_evidence
                try:
                    shadow_evidence = encoded_evidence(report)
                except ValueError:
                    shadow_error = True
            if report['ai_mode'] == 'Azure-assisted':
                from maxtrade.ai_review import routed_review
                try:
                    configuration = ai_config or azure_openai_config()
                    if configuration is None:
                        raise ValueError('Azure OpenAI is not configured')
                    report['agents'] = manager_reports(report, review_pending=True)
                    if shadow_evidence is not None:
                        shadow_evidence = encoded_evidence(report)
                    report['ai_review'] = routed_review(configuration, report)
                except ValueError as error:
                    report['ai_error'] = str(error)
                    errors += 1
            if ledger.automation_enabled():
                decision_time = datetime.now(timezone.utc)
                decision = coordinate(report, decision_time, account=ledger.account(decision_time), autonomous=True)
                report.update(decision=decision['decision'], blockers=decision['blockers'], risk=decision,
                              paper_policy='autonomous-paper-v1', reason='Automated paper-only policy; no human review; no real orders.')
                if entry_blocker:
                    report['decision'] = 'NO TRADE'
                    report['blockers'].append(entry_blocker)
                    decision.update(approved=False, decision='NO TRADE')
                if not reconciled:
                    report['decision'] = 'NO TRADE'
                    report['blockers'].append('Paper reconciliation failed')
                elif decision['approved'] and not entry_blocker:
                    try:
                        report['paper_position_id'] = ledger.submit(report, datetime.now(timezone.utc), autonomous=True)
                    except sqlite3.IntegrityError:
                        report['decision'] = 'NO TRADE'
                        report['blockers'].append('Paper decision already recorded')
            report['agents'] = manager_reports(report)
            if ledger.gemini_shadow_enabled():
                from maxtrade.gemini_ai import review_shadow
                from maxtrade.settings import load_gemini_settings
                try:
                    configuration = gemini_shadow_config(secrets={
                        **worker_private_secrets(shadow_secrets_file), **load_gemini_settings()})
                    if configuration is None:
                        report['gemini_shadow'] = {'status': 'DISABLED', 'shadow_only': True,
                                                  'execution_enabled': False}
                    elif shadow_error or shadow_evidence is None:
                        raise ValueError('Shadow evidence unavailable')
                    else:
                        shadow = review_shadow(configuration, shadow_evidence)
                        shadow['evidence_sha256'] = hashlib.sha256(shadow_evidence.encode()).hexdigest()
                        primary = report.get('ai_review', {})
                        shadow['comparison'] = {'primary_provider': primary.get('provider'),
                                                'primary_verdict': primary.get('verdict'),
                                                'same_verdict': (shadow['verdict'] == primary['verdict']
                                                                 if primary.get('verdict') else None)}
                        report['gemini_shadow'] = shadow
                except Exception:
                    report['gemini_shadow'] = {'status': 'UNAVAILABLE', 'shadow_only': True,
                                              'execution_enabled': False,
                                              'error': 'Gemini shadow failed; verify private configuration, quota and response.'}
            history.save_research(report)
            if report['technical_bias'] in {'LONG', 'SHORT'} and not report['errors']:
                hourly = next(item for item in report['evidence'] if item['interval'] == '1h')
                history.save('Spot', '1h', 1, report['created_at'], [{
                    'Market': symbol, 'Pair': symbol, 'Source': 'Autonomous technical research',
                    'Signal': report['technical_bias'],
                    'Signal candle time': int(datetime.fromisoformat(hourly['event_time']).timestamp() * 1000) - 3600000,
                    'Stop': hourly['values']['stop'], 'Target': hourly['values']['target'],
                    'Quality': report.get('quality'),
                }])
            save_setup_alert(history, report, datetime.now(timezone.utc))
            logging.info("Research saved for %s: %s", symbol, report["decision"])
        except Exception:
            errors += 1
            logging.exception("Worker failed for %s; no trade submitted", symbol)
        finally:
            client.session.close()
    client = CoinDCXClient()
    try:
        outcome_errors = update_outcomes(history, client, int(datetime.now(timezone.utc).timestamp() * 1000))
        errors += len(outcome_errors)
        for error in outcome_errors:
            logging.warning("Accuracy update: %s", error)
    finally:
        client.session.close()
    try:
        for position in ledger.positions():
            if position['state'] not in {'OPEN', 'CLOSED', 'CANCELLED'}:
                continue
            history.save_alert(f"paper-position:{position['id']}:{position['state']}",
                               position['closed_at'] or position['opened_at'] or position['submitted_at'],
                               position['symbol'], f"Paper #{position['id']} {position['state']} - "
                               + {'OPEN': 'position khul gayi', 'CLOSED': 'position band ho gayi', 'CANCELLED': 'entry cancel ho gayi'}[position['state']] + ' | '
                               + f"Entry: {position['entry']} | Stop: {position['stop']} | Target: {position['target']} | "
                               f"Exit: {position['exit']} | Net P&L: {position['pnl']} USDT | Karan (risk engine): {position['reason']}")
        configuration = notification_config or telegram_config()
        save_daily_summary(history, ledger, datetime.now(timezone.utc))
        save_daily_summary(history, ledger, datetime.now(timezone.utc), days_ago=2)
        if configuration:
            errors += deliver_notifications(history, configuration, datetime.now(timezone.utc))
    except ValueError:
        errors += 1
        logging.warning('Telegram configuration invalid; saved records retained')
    finished = datetime.now(timezone.utc)
    history.save_worker_status(finished, 'watch' if continuous else 'one-shot', errors)
    try:
        history.daily_backup(finished)
    except (OSError, sqlite3.Error, ValueError):
        errors += 1
        logging.warning('Daily backup failed; primary records retained')
        history.save_worker_status(finished, 'watch' if continuous else 'one-shot', errors)
    return errors


def worker_private_secrets(path: Path | None) -> dict:
    secrets = {}
    if path is not None:
        try:
            if path.stat().st_mode & 0o077:
                raise ValueError("Worker secrets file must have owner-only permissions (chmod 600)")
            with path.open("rb") as source:
                secrets = tomllib.load(source)
        except (OSError, tomllib.TOMLDecodeError):
            raise ValueError("Worker private configuration missing or invalid") from None
    return secrets


def worker_configuration(path: Path | None, required: bool) -> tuple[AzureOpenAIConfig | None, TelegramConfig | None]:
    secrets = worker_private_secrets(path)
    azure = azure_openai_config(secrets=secrets)
    telegram = telegram_config(secrets=secrets)
    if required and (azure is None or telegram is None):
        raise ValueError("Worker waiting for private Azure and Telegram configuration")
    return azure, telegram


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded public-data research and paper reconciliation; no real orders")
    parser.add_argument("--database", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--symbols", nargs="+", default=["B-BTC_USDT", "B-ETH_USDT"])
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--secrets-file", type=Path)
    parser.add_argument("--require-integrations", action="store_true")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    arguments.database = arguments.database.expanduser().resolve()
    arguments.database.parent.mkdir(parents=True, exist_ok=True)
    lock = arguments.database.with_suffix(".worker.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Another worker already owns this database") from None
    if not arguments.watch:
        azure, telegram = worker_configuration(arguments.secrets_file, arguments.require_integrations)
        raise SystemExit(1 if run_once(arguments.database, arguments.symbols, azure, telegram,
                          shadow_secrets_file=arguments.secrets_file) else 0)
    try:
        while True:
            try:
                entry_blocker = None
                try:
                    azure, telegram = worker_configuration(arguments.secrets_file, arguments.require_integrations)
                except ValueError as error:
                    logging.warning("Worker configuration: %s; reconciliation continues, new entries blocked", error)
                    azure, telegram = None, None
                    entry_blocker = "Worker private configuration unavailable; new entries blocked"
                if arguments.require_integrations:
                    PaperLedger(arguments.database).set_ai_mode('Azure-assisted')
                failures = run_once(arguments.database, arguments.symbols, azure, telegram, continuous=True,
                                    entry_blocker=entry_blocker, shadow_secrets_file=arguments.secrets_file)
                logging.info("Worker cycle completed: failures=%s", failures)
            except ValueError as error:
                logging.warning("Worker configuration: %s", error)
            except Exception:
                logging.exception('Worker cycle interrupted; retrying at next scheduled cycle')
            Event().wait(900)
    except KeyboardInterrupt:
        logging.info("Worker stopped")


if __name__ == "__main__":
    main()