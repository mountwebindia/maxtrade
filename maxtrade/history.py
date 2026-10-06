from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


DEFAULT_PATH = Path(os.environ.get("MAXTRADE_DATABASE", str(Path(__file__).resolve().parent.parent / "data" / "scan_history.sqlite3"))).expanduser()


class ScanHistory:
    """Local research snapshots only: no orders, account data, or trade outcomes."""

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
            return int(cursor.lastrowid)

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
