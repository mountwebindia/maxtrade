from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from math import isfinite
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


DEFAULT_PATH = Path(os.environ.get("MAXTRADE_DATABASE", str(Path(__file__).resolve().parent.parent / "data" / "scan_history.sqlite3"))).expanduser()


class ScanHistory:
    """Local research snapshots and modeled outcomes, without exchange orders or account data."""

    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    requested_limit INTEGER NOT NULL,
                    scanned_at TEXT NOT NULL,
                    results_json TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    report_json TEXT NOT NULL
                )
            """)
            connection.execute("CREATE TABLE IF NOT EXISTS research_alerts (id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL, symbol TEXT NOT NULL, message TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS telegram_deliveries (alert_id INTEGER NOT NULL, destination TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, attempted_at TEXT, sent_at TEXT, error TEXT, PRIMARY KEY(alert_id,destination))")
            connection.execute("CREATE TABLE IF NOT EXISTS worker_status (id INTEGER PRIMARY KEY CHECK(id=1), finished_at TEXT NOT NULL, mode TEXT NOT NULL, failures INTEGER NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS predictions (fingerprint TEXT PRIMARY KEY, scan_id INTEGER NOT NULL, created_at TEXT NOT NULL, product TEXT NOT NULL, pair TEXT NOT NULL, action TEXT NOT NULL, start_ms INTEGER NOT NULL, stop REAL NOT NULL, target REAL NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING', result_json TEXT)")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def save(self, product: str, interval: str, requested_limit: int,
             scanned_at: str, results: list[dict[str, Any]]) -> int:
        payload = json.dumps(results, allow_nan=False)
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "INSERT INTO scans (product, interval, requested_limit, scanned_at, results_json) VALUES (?, ?, ?, ?, ?)",
                (product, interval, requested_limit, scanned_at, payload),
            )
            scan_id = int(cursor.lastrowid)
            if product in {"Spot", "Futures"} and interval in {"1h", "4h"}:
                duration = {"1h": 3600000, "4h": 14400000}[interval]
                for result in results:
                    if result.get("Signal") not in {"LONG", "SHORT"} or not result.get("Pair") or "Signal candle time" not in result:
                        continue
                    try:
                        timestamp = datetime.fromisoformat(scanned_at)
                        if timestamp.tzinfo is None:
                            continue
                        scanned_ms = int(timestamp.timestamp() * 1000)
                        candle_ms = int(result["Signal candle time"])
                        stop, target = float(result["Stop"]), float(result["Target"])
                        if not all(isfinite(value) and value > 0 for value in (stop, target)):
                            continue
                        if not candle_ms + duration <= scanned_ms < candle_ms + 2 * duration:
                            continue
                        start_ms = (scanned_ms // duration + 1) * duration
                        fingerprint = hashlib.sha256(json.dumps([product, result["Pair"], interval, candle_ms, result["Signal"]]).encode()).hexdigest()
                        created_at = timestamp.astimezone(timezone.utc).isoformat()
                    except (KeyError, TypeError, ValueError, OverflowError):
                        continue
                    connection.execute("INSERT OR IGNORE INTO predictions (fingerprint,scan_id,created_at,product,pair,action,start_ms,stop,target) VALUES (?,?,?,?,?,?,?,?,?)",
                                       (fingerprint, scan_id, created_at, product, result["Pair"], result["Signal"], start_ms, stop, target))
            return scan_id

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT * FROM scans ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        summaries = []
        for row in rows:
            summary = dict(row)
            results = json.loads(summary.pop("results_json"))
            actions = [result.get("Signal") for result in results]
            summary.update(markets=len(results), longs=actions.count("LONG"),
                           shorts=actions.count("SHORT"), errors=actions.count("DATA ERROR"))
            summaries.append(summary)
        return summaries

    def load(self, scan_id: int) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if row is None:
            return None
        snapshot = dict(row)
        snapshot["results"] = json.loads(snapshot.pop("results_json"))
        return snapshot

    def save_research(self, report: dict[str, Any]) -> int:
        payload = json.dumps(report, allow_nan=False)
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute("INSERT INTO research_reports (created_at, report_json) VALUES (?, ?)",
                                        (report["created_at"], payload))
            return int(cursor.lastrowid)

    def recent_research(self, limit: int = 20) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT id, report_json FROM research_reports ORDER BY id DESC LIMIT ?",
                                      (limit,)).fetchall()
        return [{"id": row["id"], "report": json.loads(row["report_json"])} for row in rows]

    def save_alert(self, fingerprint: str, created_at: str, symbol: str, message: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("INSERT OR IGNORE INTO research_alerts (fingerprint,created_at,symbol,message) VALUES (?,?,?,?)",
                               (fingerprint, created_at, symbol, message))

    def recent_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            return [dict(row) for row in connection.execute("SELECT created_at,symbol,message FROM research_alerts ORDER BY id DESC LIMIT ?", (limit,))]

    def predictions(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM predictions ORDER BY created_at DESC")]

    def claim_notifications(self, destination: str, now: datetime, limit: int = 20) -> list[dict[str, Any]]:
        from datetime import timedelta
        current = now.astimezone(timezone.utc).isoformat()
        cutoff = (now - timedelta(hours=24)).astimezone(timezone.utc).isoformat()
        retry = (now - timedelta(minutes=5)).astimezone(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute('''SELECT a.* FROM research_alerts a
                LEFT JOIN telegram_deliveries d ON d.alert_id=a.id AND d.destination=?
                WHERE a.created_at>=? AND d.sent_at IS NULL
                AND (d.attempted_at IS NULL OR d.attempted_at<=?)
                ORDER BY a.id LIMIT ?''', (destination, cutoff, retry, limit)).fetchall()
            for row in rows:
                connection.execute('''INSERT INTO telegram_deliveries (alert_id,destination,attempts,attempted_at)
                    VALUES (?,?,1,?) ON CONFLICT(alert_id,destination) DO UPDATE
                    SET attempts=attempts+1,attempted_at=excluded.attempted_at''', (row['id'], destination, current))
            return [dict(row) for row in rows]

    def finish_notification(self, alert_id: int, destination: str, now: datetime, error: str | None = None) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute('UPDATE telegram_deliveries SET sent_at=?,error=? WHERE alert_id=? AND destination=?',
                               (None if error else now.astimezone(timezone.utc).isoformat(), error, alert_id, destination))

    def notification_status(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            return [dict(row) for row in connection.execute('''SELECT a.symbol,d.attempts,d.attempted_at,d.sent_at,d.error
                FROM telegram_deliveries d JOIN research_alerts a ON a.id=d.alert_id
                ORDER BY d.attempted_at DESC LIMIT 20''')]

    def save_worker_status(self, now: datetime, mode: str, failures: int) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute('INSERT OR REPLACE INTO worker_status VALUES (1,?,?,?)',
                               (now.astimezone(timezone.utc).isoformat(), mode, failures))

    def worker_status(self) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute('SELECT * FROM worker_status WHERE id=1').fetchone()
            return dict(row) if row else None

    def daily_backup(self, now: datetime) -> Path:
        directory = self.path.parent / 'backups'
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"paper-{now.astimezone(timezone.utc).date().isoformat()}.sqlite3"
        if destination.exists():
            return destination
        temporary = destination.with_suffix('.tmp')
        with closing(self._connect()) as source, closing(sqlite3.connect(temporary)) as backup:
            source.backup(backup)
            if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Backup integrity check failed')
        temporary.replace(destination)
        return destination

    def save_outcome(self, fingerprint: str, outcome: dict[str, Any]) -> None:
        payload = json.dumps(outcome, allow_nan=False)
        with closing(self._connect()) as connection, connection:
            connection.execute("UPDATE predictions SET status=?, result_json=? WHERE fingerprint=? AND status IN ('PENDING','DATA GAP')",
                               (outcome["status"], payload, fingerprint))
