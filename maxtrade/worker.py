from __future__ import annotations

import argparse
import hashlib
import json
import logging
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from maxtrade.coindcx import CoinDCXClient
from maxtrade.history import DEFAULT_PATH, ScanHistory
from maxtrade.paper import PaperLedger
from maxtrade.research import run_coordinated_research


def run_once(path: Path, symbols: list[str]) -> int:
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
            try:
                ledger.reconcile(symbol, client.spot_candles(symbol, "1h"), datetime.now(timezone.utc))
            except Exception:
                errors += 1
                logging.exception("Paper reconciliation failed for %s; research will continue", symbol)
            report = run_coordinated_research(client, "Spot", symbol)
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
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(1 if run_once(arguments.database, arguments.symbols) else 0)


if __name__ == "__main__":
    main()