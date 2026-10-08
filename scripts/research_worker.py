from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import sqlite3
from threading import Event

from maxtrade.historical import ingest
from maxtrade.historical_features import historical_context
from maxtrade.quality import forward_validation


def research_once(database: Path, ledger: Path, state: Path, now: datetime,
                  training_registry: Path | None = None) -> dict:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Research requires timezone-aware time')
    end = int(now.timestamp()) // 86400 * 86400
    report = {'generated': now.isoformat(), 'mode': 'SHADOW ONLY',
              'execution_enabled': False, 'model_trained': False,
              'markets': {}, 'failures': 0,
              'training_enabled': training_registry is not None,
              'limitations': ['No automatic policy promotion or Azure model weight updates',
                              'Recent Coinbase USD coverage does not certify CoinDCX paper execution',
                              'Previously observed holdout is not new untouched evaluation data']}
    for product in ('BTC-USD', 'ETH-USD'):
        market = {}
        for interval, days in (('1d', 1200), ('1h', 30)):
            try:
                start = end - days * 86400
                saved = ingest(product, interval, start, end, database)
                market[interval] = {key: saved[key] for key in
                                    ('status', 'coverage_pct', 'missing_bars', 'start', 'end')}
                if saved['status'] != 'COMPLETE':
                    report['failures'] += 1
            except Exception:
                market[interval] = {'status': 'UNAVAILABLE'}
                report['failures'] += 1
        try:
            context = historical_context(database, product.split('-')[0], datetime.now(timezone.utc))
            market['daily_context'] = {'status': context['status'], 'regime': context['regime']}
        except Exception:
            market['daily_context'] = {'status': 'UNAVAILABLE'}
            report['failures'] += 1
        report['markets'][product] = market
        if training_registry is not None:
            try:
                from maxtrade.training import train_shadow
                market['training'] = train_shadow(database, training_registry, product,
                                                   end - 1200 * 86400, end,
                                                   datetime.now(timezone.utc))
            except Exception as error:
                market['training'] = {'status': 'UNAVAILABLE', 'error_type': type(error).__name__,
                                      'execution_enabled': False, 'promotion_allowed': False}
                report['failures'] += 1
    report['model_trained'] = any(market.get('training', {}).get('status') == 'TRAINED SHADOW'
                                  for market in report['markets'].values())
    try:
        with sqlite3.connect(f'{ledger.resolve().as_uri()}?mode=ro', uri=True, timeout=5) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute('SELECT p.*, s.interval, s.results_json FROM predictions p '
                                      'JOIN scans s ON s.id=p.scan_id ORDER BY p.created_at DESC').fetchall()
        records = []
        for row in rows:
            record = dict(row)
            evidence = json.loads(record.pop('results_json'))
            record['quality'] = next((item.get('Quality') for item in evidence
                                      if item.get('Pair') == record['pair']
                                      and item.get('Signal') == record['action']), None)
            records.append(record)
        report['forward_validation'] = forward_validation(records)
        report['learning_status'] = ('FORWARD MONITORING' if report['forward_validation']
                                     else 'WAITING FOR MATURE COST-AWARE SAMPLES')
    except Exception:
        report['learning_status'] = 'UNAVAILABLE'
        report['failures'] += 1
    state.parent.mkdir(parents=True, exist_ok=True)
    temporary = state.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, allow_nan=False))
    temporary.chmod(0o600)
    temporary.replace(state)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description='Public history refresh and shadow learning; no orders')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--training-registry', type=Path,
                        help='Enable shadow-only statistical training; never changes paper policy')
    arguments = parser.parse_args()
    arguments.database.parent.mkdir(parents=True, exist_ok=True)
    lock = arguments.database.with_suffix('.research.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Another research service owns this database') from None
    while True:
        try:
            report = research_once(arguments.database, arguments.ledger, arguments.state,
                                   datetime.now(timezone.utc), arguments.training_registry)
            print(f"Research cycle completed: failures={report['failures']}; {report['learning_status']}",
                  flush=True)
        except Exception:
            print('Research cycle failed; retrying without changing paper policy', flush=True)
            if not arguments.watch:
                raise SystemExit(1) from None
        if not arguments.watch:
            return
        Event().wait(21600)


if __name__ == '__main__':
    main()