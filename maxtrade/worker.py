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
from maxtrade.history import DEFAULT_PATH, ScanHistory
from maxtrade.paper import PaperLedger, coordinate
from maxtrade.research import run_coordinated_research
from maxtrade.settings import AzureOpenAIConfig, azure_openai_config


def run_once(path: Path, symbols: list[str], ai_config: AzureOpenAIConfig | None = None) -> int:
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
            fingerprint = hashlib.sha256(json.dumps([symbol, report["technical_bias"], report["blockers"],
                [(item["interval"], item["event_time"]) for item in report["evidence"]]], sort_keys=True).encode()).hexdigest()
            history.save_alert(fingerprint, report["created_at"], symbol,
                               f"{report['technical_bias']} | {report['decision']} | " + "; ".join(report["blockers"]))
            logging.info("Research saved for %s: %s", symbol, report["decision"])
        except Exception:
            errors += 1
            logging.exception("Worker failed for %s; no trade submitted", symbol)
        finally:
            client.session.close()
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
            failures = run_once(arguments.database, arguments.symbols)
            logging.info("Worker cycle completed: failures=%s", failures)
            Event().wait(900)
    except KeyboardInterrupt:
        logging.info("Worker stopped")


if __name__ == "__main__":
    main()