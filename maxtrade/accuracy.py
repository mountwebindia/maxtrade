from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
import json

import pandas as pd
import requests
import streamlit as st

from maxtrade.coindcx import CoinDCXClient


HOUR_MS = 3600000


def evaluate_prediction(prediction: dict, candles: list[dict], now_ms: int) -> dict:
    start = prediction["start_ms"]
    end = start + 24 * HOUR_MS
    completed = {int(bar["time"]): bar for bar in candles
                 if start <= int(bar["time"]) < end and int(bar["time"]) + HOUR_MS <= now_ms}
    stop, target = prediction["stop"], prediction["target"]
    long = prediction["action"] == "LONG"
    entry = None
    for timestamp in range(start, min(end, now_ms // HOUR_MS * HOUR_MS), HOUR_MS):
        if timestamp not in completed:
            return {"status": "DATA GAP"}
        bar = completed[timestamp]
        opening, high, low, close = [float(bar[field]) for field in ("open", "high", "low", "close")]
        if not all(isfinite(value) and value > 0 for value in (opening, high, low, close)) or not low <= min(opening, close) <= max(opening, close) <= high:
            return {"status": "DATA GAP"}
        if entry is None:
            entry = opening
            if not (stop < entry < target if long else target < entry < stop):
                return {"status": "INVALID ENTRY", "entry": entry}
        stopped = low <= stop if long else high >= stop
        won = high >= target if long else low <= target
        if stopped or won:
            exit_price = (min(stop, opening) if long else max(stop, opening)) if stopped else target
            gross = (exit_price / entry - 1) * 100 * (1 if long else -1)
            return {"status": "LOSS" if stopped else "WIN", "entry": entry,
                    "exit": exit_price, "gross_return_pct": gross,
                    "exit_bar_utc": datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat()}
    return {"status": "EXPIRED" if now_ms >= end else "PENDING", "entry": entry}


def daily_accuracy(records: list[dict]) -> list[dict]:
    days = {}
    for record in records:
        day = record["created_at"][:10]
        row = days.setdefault(day, {"Date UTC": day, "Predictions": 0, "Wins": 0, "Losses": 0,
                                   "Expired": 0, "Pending": 0, "Data gaps": 0, "Invalid entries": 0})
        row["Predictions"] += 1
        column = {"WIN": "Wins", "LOSS": "Losses", "EXPIRED": "Expired", "PENDING": "Pending",
                  "DATA GAP": "Data gaps", "INVALID ENTRY": "Invalid entries"}[record["status"]]
        row[column] += 1
    for row in days.values():
        resolved = row["Wins"] + row["Losses"] + row["Expired"]
        row["Scored"] = resolved
        row["Target accuracy %"] = round(row["Wins"] / resolved * 100, 2) if resolved else None
    return sorted(days.values(), key=lambda row: row["Date UTC"], reverse=True)


def update_outcomes(history, client, now_ms: int) -> list[str]:
    cache = {}
    errors = []
    for record in history.predictions():
        if record["status"] not in {"PENDING", "DATA GAP"}:
            continue
        key = (record["product"], record["pair"])
        try:
            if key not in cache:
                fetch = client.spot_candles if record["product"] == "Spot" else client.futures_candles
                cache[key] = fetch(record["pair"], "1h", count=480)
            history.save_outcome(record["fingerprint"], evaluate_prediction(record, cache[key], now_ms))
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            errors.append(f"{record['pair']}: outcome unavailable ({error})")
    return errors


def render_daily_accuracy(history) -> None:
    st.markdown("#### Daily accuracy")
    st.caption("Forward scans and aligned autonomous technical signals · UTC signal date · next timeframe boundary entry · 24-hour target-before-stop · same-bar ties count as losses. Expired predictions count as misses; pending, gaps and invalid entries are unscored. Repeated signals on the same candle count once. Options and NO TRADE are not scored; technical accuracy is separate from paper approval and net win rate.")
    if st.button("Update outcomes", icon=":material/sync:", key="accuracy_update"):
        client = CoinDCXClient()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        try:
            for error in update_outcomes(history, client, now_ms):
                st.warning(error)
        finally:
            client.session.close()
    records = history.predictions()
    if not records:
        st.info("No trackable predictions yet. New LONG/SHORT scans will appear here; older scans without candle provenance cannot be scored.")
        return
    summaries = daily_accuracy(records)
    st.dataframe(summaries, hide_index=True, width="stretch")
    st.download_button("Download daily accuracy CSV", pd.DataFrame(summaries).to_csv(index=False).encode(),
                       file_name="maxtrade-daily-accuracy.csv", mime="text/csv", icon=":material/download:")
    selected_date = st.selectbox("Analysis date (UTC)", [row["Date UTC"] for row in summaries], key="accuracy_date")
    details = [{"Scan": record["scan_id"], "Analyzed UTC": record["created_at"], "Product": record["product"],
                "Pair": record["pair"], "Signal": record["action"], "Status": record["status"],
                "Entry scheduled UTC": datetime.fromtimestamp(record["start_ms"] / 1000, timezone.utc).isoformat(),
                "Stop": record["stop"], "Target": record["target"],
                **(json.loads(record["result_json"]) if record["result_json"] else {})}
               for record in records if record["created_at"][:10] == selected_date]
    st.dataframe(details, hide_index=True, width="stretch")
    st.caption("Gross price outcomes, not executed trades or net profitability. Fees, slippage and funding are excluded. Outcomes update each worker cycle or on request; cloud storage may be lost on restart. A signal's 24-hour evaluation may finish the following day. Small samples do not establish future accuracy.")