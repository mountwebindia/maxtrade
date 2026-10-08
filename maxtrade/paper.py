from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any

from maxtrade.history import DEFAULT_PATH


def coordinate(report: dict[str, Any], now: datetime, reviewed: bool = False,
               account: dict[str, Any] | None = None, autonomous: bool = False) -> dict[str, Any]:
    blockers = []
    if now.utcoffset() is None:
        raise ValueError("Decision time must be timezone-aware")
    for name in ("sentiment", "news", "derivatives"):
        item = report.get(name)
        try:
            if not item or not datetime.fromisoformat(item["retrieved_at"]) <= now < datetime.fromisoformat(item["expires_at"]):
                blockers.append(f"Missing/stale {name} evidence")
        except (KeyError, TypeError, ValueError):
            blockers.append(f"Invalid {name} evidence")
    try:
        evidence = report["evidence"]
        if len(evidence) != 2 or {item["interval"] for item in evidence} != {"1h", "4h"}:
            blockers.append("Missing technical timeframes")
        for item in evidence:
            if not datetime.fromisoformat(item["event_time"]) <= now < datetime.fromisoformat(item["expires_at"]):
                blockers.append("Expired technical evidence")
        if report["errors"] or {item["action"] for item in evidence} != {"LONG"}:
            blockers.append("Technical evidence is not unanimously LONG")
    except (KeyError, TypeError, ValueError):
        blockers.append("Invalid technical evidence")
    if report.get("product") != "Spot" or report.get("symbol") not in {"B-BTC_USDT", "B-ETH_USDT"}:
        blockers.append("Paper fills currently support USDT spot only")
    derivatives = report.get("derivatives")
    if not isinstance(derivatives, dict) or derivatives.get("liquid") is not True:
        blockers.append("Poor derivatives context liquidity")
    if not reviewed and not autonomous:
        blockers.append("Human news/event review required")
    if report.get('ai_mode') == 'Azure-assisted':
        review = report.get('ai_review')
        if (not isinstance(review, dict) or review.get('verdict') != 'CLEAR'
            or review.get('concerns') or review.get('shadow_only')):
            blockers.append('Azure review veto, uncertainty or unavailable')
    if not account:
        blockers.append("Paper account risk state unavailable")
    else:
        try:
            if not all(isfinite(account[name]) for name in ("capital", "equity", "daily_pnl")) or account["capital"] <= 0:
                blockers.append("Invalid paper account values")
            if account["kill_switch"] or account["occupied"] or account["daily_pnl"] <= -account["capital"] * 0.03 or account["equity"] <= 0:
                blockers.append("Paper kill switch, position cap or daily loss limit")
        except (KeyError, TypeError, ValueError):
            blockers.append("Invalid paper account risk state")
    return {"agent": "deterministic-paper-risk", "approved": not blockers,
            "decision": "BUY" if not blockers else "NO TRADE", "blockers": list(dict.fromkeys(blockers)),
            "execution_enabled": False, "mode": "SIMULATION ONLY", "evaluated_at": now.isoformat()}


class PaperLedger:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS paper_settings (id INTEGER PRIMARY KEY CHECK(id=1), capital REAL NOT NULL, kill_switch INTEGER NOT NULL)")
            connection.execute("INSERT OR IGNORE INTO paper_settings VALUES (1,10000,1)")
            connection.execute("CREATE TABLE IF NOT EXISTS paper_automation (id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL)")
            connection.execute("INSERT OR IGNORE INTO paper_automation VALUES (1,0)")
            connection.execute("CREATE TABLE IF NOT EXISTS paper_ai (id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL)")
            connection.execute("INSERT OR IGNORE INTO paper_ai VALUES (1,'Deterministic')")
            connection.execute("""CREATE TABLE IF NOT EXISTS paper_positions (
                id INTEGER PRIMARY KEY, decision_id TEXT UNIQUE NOT NULL, symbol TEXT NOT NULL,
                state TEXT NOT NULL, submitted_at TEXT NOT NULL, report_json TEXT NOT NULL,
                stop REAL NOT NULL, target REAL NOT NULL, quantity REAL, entry REAL, opened_at TEXT,
                exit REAL, closed_at TEXT, pnl REAL, last_bar INTEGER, reason TEXT)""")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _account(self, connection: sqlite3.Connection, now: datetime) -> dict[str, Any]:
        settings = dict(connection.execute("SELECT * FROM paper_settings WHERE id=1").fetchone())
        profit = connection.execute("SELECT COALESCE(SUM(pnl),0) FROM paper_positions WHERE state='CLOSED'").fetchone()[0]
        today = now.astimezone(timezone.utc).date().isoformat()
        daily = connection.execute("SELECT COALESCE(SUM(pnl),0) FROM paper_positions WHERE state='CLOSED' AND substr(closed_at,1,10)=?", (today,)).fetchone()[0]
        occupied = connection.execute("SELECT COUNT(*) FROM paper_positions WHERE state IN ('PENDING','OPEN')").fetchone()[0]
        return dict(settings, equity=settings["capital"] + profit, daily_pnl=daily, occupied=bool(occupied))

    def account(self, now: datetime) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            return self._account(connection, now)

    def automation_enabled(self) -> bool:
        with closing(self._connect()) as connection:
            return bool(connection.execute("SELECT enabled FROM paper_automation WHERE id=1").fetchone()[0])

    def ai_mode(self) -> str:
        with closing(self._connect()) as connection:
            return connection.execute('SELECT mode FROM paper_ai WHERE id=1').fetchone()[0]

    def set_ai_mode(self, mode: str) -> None:
        if mode not in {'Deterministic', 'Azure-assisted'}:
            raise ValueError('Unsupported research mode')
        with closing(self._connect()) as connection, connection:
            connection.execute('UPDATE paper_ai SET mode=? WHERE id=1', (mode,))

    def set_automation(self, enabled: bool) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE paper_automation SET enabled=? WHERE id=1", (int(enabled),))
            connection.execute("UPDATE paper_settings SET kill_switch=? WHERE id=1", (int(not enabled),))
            if not enabled:
                connection.execute("UPDATE paper_positions SET state='CANCELLED',reason='Automation paused' WHERE state='PENDING'")

    def performance(self) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM paper_positions ORDER BY closed_at,id")]
            capital = connection.execute("SELECT capital FROM paper_settings WHERE id=1").fetchone()[0]
        closed = [row for row in rows if row['state'] == 'CLOSED']
        wins = sum(row['pnl'] > 0 for row in closed)
        gains = sum(max(row['pnl'], 0) for row in closed)
        losses = -sum(min(row['pnl'], 0) for row in closed)
        loss_count = sum(row['pnl'] < 0 for row in closed)
        equity = peak = capital
        drawdown = 0.0
        daily = {}
        for row in closed:
            equity += row['pnl']
            peak = max(peak, equity)
            drawdown = max(drawdown, (peak - equity) / peak * 100)
            day = row['closed_at'][:10]
            item = daily.setdefault(day, {'Date (UTC)': day, 'Closed': 0, 'Wins': 0, 'Net P&L (USDT)': 0.0})
            item['Closed'] += 1
            item['Wins'] += int(row['pnl'] > 0)
            item['Net P&L (USDT)'] += row['pnl']
        for item in daily.values():
            item['Win rate %'] = item['Wins'] / item['Closed'] * 100
        return {'closed': len(closed), 'wins': wins, 'win_rate_pct': wins / len(closed) * 100 if closed else None,
                'net_pnl': equity - capital, 'profit_factor': gains / losses if losses else None,
            'average_win': gains / wins if wins else None,
            'average_loss': -losses / loss_count if loss_count else None,
            'expectancy': sum(row['pnl'] for row in closed) / len(closed) if closed else None,
                'max_drawdown_pct': drawdown, 'daily': sorted(daily.values(), key=lambda item: item['Date (UTC)'], reverse=True)}

    def settings(self, capital: float, kill_switch: bool) -> None:
        if not isfinite(capital) or not 100 <= capital <= 1_000_000:
            raise ValueError("Paper capital must be between 100 and 1,000,000 USDT")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0]
            previous = connection.execute("SELECT capital FROM paper_settings WHERE id=1").fetchone()[0]
            if existing and capital != previous:
                raise ValueError("Capital is fixed after the first paper decision")
            connection.execute("UPDATE paper_settings SET capital=?,kill_switch=? WHERE id=1", (capital, int(kill_switch)))
            if kill_switch:
                connection.execute("UPDATE paper_positions SET state='CANCELLED',reason='Kill switch' WHERE state='PENDING'")

    def submit(self, report: dict[str, Any], now: datetime, reviewed: bool = False, autonomous: bool = False) -> int:
        payload = json.dumps(report, allow_nan=False, sort_keys=True)
        identity = json.dumps([report["product"], report["symbol"],
                               [(item["interval"], item["event_time"], item["action"]) for item in report["evidence"]]], sort_keys=True)
        decision_id = hashlib.sha256(identity.encode()).hexdigest()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if autonomous and not connection.execute("SELECT enabled FROM paper_automation WHERE id=1").fetchone()[0]:
                raise ValueError("Autonomous paper mode is paused")
            risk = coordinate(report, now, reviewed, self._account(connection, now), autonomous=autonomous)
            if not risk["approved"]:
                raise ValueError("; ".join(risk["blockers"]))
            levels = next(item["values"] for item in report["evidence"] if item["interval"] == "1h")
            entry, stop, target = (float(levels[name]) for name in ("entry", "stop", "target"))
            if not all(isfinite(value) for value in (entry, stop, target)) or not 0 < stop < entry < target:
                raise ValueError("Invalid paper entry/stop/target")
            cursor = connection.execute("""INSERT INTO paper_positions
                (decision_id,symbol,state,submitted_at,report_json,stop,target)
                VALUES (?,?,'PENDING',?,?,?,?)""", (decision_id, report["symbol"], now.isoformat(), payload, stop, target))
            return int(cursor.lastrowid)

    def positions(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM paper_positions ORDER BY id DESC LIMIT 100")]

    def reconcile(self, symbol: str, candles: list[dict[str, Any]], now: datetime) -> None:
        from maxtrade.charts import chart_analysis
        from maxtrade.coindcx import normalize_candles

        bars = normalize_candles(candles, "1h", count=len(candles), now_ms=int(now.timestamp()*1000))
        chart_analysis(bars, "1h", allow_short=False)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM paper_positions WHERE symbol=? AND state IN ('PENDING','OPEN')", (symbol,)).fetchone()
            if row is None:
                return
            position = dict(row)
            submitted = datetime.fromisoformat(position["submitted_at"])
            eligible = [bar for bar in bars if bar["time"] >= submitted.timestamp()*1000 and
                        (position["last_bar"] is None or bar["time"] > position["last_bar"])]
            if position["last_bar"] is not None and eligible and eligible[0]["time"] != position["last_bar"] + 3600000:
                raise ValueError("Paper monitoring gap: missing bars must be recovered before reconciliation")
            if position["state"] == "PENDING" and eligible:
                bar = eligible[0]
                expected = (int(submitted.timestamp()*1000)//3600000 + 1)*3600000
                opening = datetime.fromtimestamp(bar["time"]/1000, timezone.utc)
                if bar["time"] != expected:
                    connection.execute("UPDATE paper_positions SET state='CANCELLED',reason='Missing immediately next bar' WHERE id=?", (position["id"],))
                    return
                fill = float(bar["open"])*1.0005
                if not position["stop"] < fill < position["target"]:
                    connection.execute("UPDATE paper_positions SET state='CANCELLED',reason='Opening gap invalidates levels' WHERE id=?", (position["id"],))
                    return
                account = self._account(connection, opening)
                if account["kill_switch"] or account["daily_pnl"] <= -account["capital"]*0.03:
                    connection.execute("UPDATE paper_positions SET state='CANCELLED',reason='Risk veto at fill' WHERE id=?", (position["id"],))
                    return
                risk_per_unit = fill - position["stop"]*0.9995 + (fill + position["stop"])*0.001
                quantity = min(account["equity"]*0.01/risk_per_unit, account["equity"]*0.25/(fill*1.001))
                position.update(entry=fill, quantity=quantity, state="OPEN", opened_at=opening.isoformat())
                connection.execute("UPDATE paper_positions SET state='OPEN',entry=?,quantity=?,opened_at=? WHERE id=?", (fill, quantity, opening.isoformat(), position["id"]))
            if position["state"] != "OPEN":
                return
            for bar in eligible:
                exit_price = None
                reason = None
                if float(bar["low"]) <= position["stop"]:
                    exit_price = min(position["stop"], float(bar["open"]))*0.9995
                    reason = "STOP (stop-first if both touched)"
                elif float(bar["high"]) >= position["target"]:
                    exit_price = position["target"]*0.9995
                    reason = "TARGET"
                if exit_price is not None:
                    pnl = position["quantity"]*(exit_price-position["entry"]-(exit_price+position["entry"])*0.001)
                    closed = datetime.fromtimestamp((bar["time"]+3600000)/1000, timezone.utc).isoformat()
                    connection.execute("UPDATE paper_positions SET state='CLOSED',exit=?,closed_at=?,pnl=?,last_bar=?,reason=? WHERE id=?", (exit_price, closed, pnl, bar["time"], reason, position["id"]))
                    break
                connection.execute("UPDATE paper_positions SET last_bar=? WHERE id=?", (bar["time"], position["id"]))