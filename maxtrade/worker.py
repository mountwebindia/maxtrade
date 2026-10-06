from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from maxtrade.coindcx import CoinDCXClient
from maxtrade.accuracy import update_outcomes
from maxtrade.history import DEFAULT_PATH, ScanHistory
from maxtrade.paper import PaperLedger, coordinate
from maxtrade.notifications import deliver_notifications, save_daily_summary
from maxtrade.research import run_coordinated_research
from maxtrade.settings import AzureOpenAIConfig, TelegramConfig, azure_openai_config, telegram_config


def run_once(path: Path, symbols: list[str], ai_config: AzureOpenAIConfig | None = None,
             notification_config: TelegramConfig | None = None, continuous: bool = False) -> int:
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
            if report['ai_mode'] == 'Azure-assisted':
                from maxtrade.azure_ai import review_evidence
                try:
                    configuration = ai_config or azure_openai_config()
                    if configuration is None:
                        raise ValueError('Azure OpenAI is not configured')
                    report['ai_review'] = review_evidence(configuration, report)
                except ValueError as error:
                    report['ai_error'] = str(error)
                    errors += 1
            if ledger.automation_enabled():
                decision = coordinate(report, datetime.now(timezone.utc), account=ledger.account(now), autonomous=True)
                report.update(decision=decision['decision'], blockers=decision['blockers'], risk=decision,
                              paper_policy='autonomous-paper-v1', reason='Automated paper-only policy; no human review; no real orders.')
                if not reconciled:
                    report['decision'] = 'NO TRADE'
                    report['blockers'].append('Paper reconciliation failed')
                elif decision['approved']:
                    try:
                        report['paper_position_id'] = ledger.submit(report, datetime.now(timezone.utc), autonomous=True)
                    except sqlite3.IntegrityError:
                        report['decision'] = 'NO TRADE'
                        report['blockers'].append('Paper decision already recorded')
            history.save_research(report)
            if report['technical_bias'] in {'LONG', 'SHORT'} and not report['errors']:
                hourly = next(item for item in report['evidence'] if item['interval'] == '1h')
                history.save('Spot', '1h', 1, report['created_at'], [{
                    'Market': symbol, 'Pair': symbol, 'Source': 'Autonomous technical research',
                    'Signal': report['technical_bias'],
                    'Signal candle time': int(datetime.fromisoformat(hourly['event_time']).timestamp() * 1000) - 3600000,
                    'Stop': hourly['values']['stop'], 'Target': hourly['values']['target'],
                }])
            fingerprint = hashlib.sha256(json.dumps([symbol, report["technical_bias"], report["blockers"],
                [(item["interval"], item["event_time"]) for item in report["evidence"]]], sort_keys=True).encode()).hexdigest()
            history.save_alert(fingerprint, report["created_at"], symbol,
                               f"Technical: {report['technical_bias']} | Paper: {report['decision']} | "
                               + (f"Queued position #{report['paper_position_id']} | " if report.get('paper_position_id') else '')
                               + "; ".join(report["blockers"]))
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
                               position['symbol'], f"Paper #{position['id']} {position['state']} | "
                               f"Entry: {position['entry']} | Stop: {position['stop']} | Target: {position['target']} | "
                               f"Exit: {position['exit']} | Net P&L: {position['pnl']} USDT | {position['reason']}")
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded public-data research and paper reconciliation; no real orders")
    parser.add_argument("--database", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--symbols", nargs="+", default=["B-BTC_USDT", "B-ETH_USDT"])
    parser.add_argument("--watch", action="store_true")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if not arguments.watch:
        raise SystemExit(1 if run_once(arguments.database, arguments.symbols) else 0)
    try:
        while True:
            try:
                failures = run_once(arguments.database, arguments.symbols, continuous=True)
                logging.info("Worker cycle completed: failures=%s", failures)
            except Exception:
                logging.exception('Worker cycle interrupted; retrying at next scheduled cycle')
            Event().wait(900)
    except KeyboardInterrupt:
        logging.info("Worker stopped")


if __name__ == "__main__":
    main()