from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
import json

import pandas as pd
import requests
import streamlit as st

from maxtrade.coindcx import CoinDCXClient


HOUR_MS = 3600000


def modeled_return(entry: float, exit_price: float, long: bool) -> dict:
    fee, slippage = 0.001, 0.0005
    direction = 1 if long else -1
    entry_fill = entry * (1 + direction * slippage)
    exit_fill = exit_price * (1 - direction * slippage)
    gross = direction * (exit_price / entry - 1) * 100
    net = (direction * (exit_fill - entry_fill) - fee * (entry_fill + exit_fill)) / entry_fill * 100
    return {'gross_return_pct': gross, 'net_return_pct': net,
            'entry_fill': entry_fill, 'exit_fill': exit_fill,
            'fee_bps_per_side': 10, 'slippage_bps_per_side': 5,
            'cost_model': 'fixed-notional-v1', 'funding_included': False}


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
            return {"status": "LOSS" if stopped else "WIN", "entry": entry,
                    "exit": exit_price, **modeled_return(entry, exit_price, long),
                    "exit_bar_utc": datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat()}
    if now_ms >= end and entry is not None:
        exit_price = float(completed[end - HOUR_MS]['close'])
        return {'status': 'EXPIRED', 'entry': entry, 'exit': exit_price,
                **modeled_return(entry, exit_price, long),
                'exit_bar_utc': datetime.fromtimestamp((end - HOUR_MS) / 1000, timezone.utc).isoformat()}
    return {"status": "EXPIRED" if now_ms >= end else "PENDING", "entry": entry}


def daily_accuracy(records: list[dict]) -> list[dict]:
    days = {}
    for record in records:
        day = record["created_at"][:10]
        row = days.setdefault(day, {"Date UTC": day, "Predictions": 0, "Wins": 0, "Losses": 0,
                                   "Expired": 0, "Pending": 0, "Data gaps": 0, "Invalid entries": 0,
                                   'Net samples': 0, 'Net wins': 0, '_net_total': 0.0})
        row["Predictions"] += 1
        column = {"WIN": "Wins", "LOSS": "Losses", "EXPIRED": "Expired", "PENDING": "Pending",
                  "DATA GAP": "Data gaps", "INVALID ENTRY": "Invalid entries"}[record["status"]]
        row[column] += 1
        result = json.loads(record.get('result_json') or '{}')
        net = result.get('net_return_pct')
        if record['status'] in {'WIN', 'LOSS', 'EXPIRED'} and result.get('cost_model') == 'fixed-notional-v1' and isinstance(net, (int, float)) and not isinstance(net, bool) and isfinite(net):
            row['Net samples'] += 1
            row['Net wins'] += int(net > 0)
            row['_net_total'] += net
    for row in days.values():
        resolved = row["Wins"] + row["Losses"] + row["Expired"]
        row["Scored"] = resolved
        row["Target accuracy %"] = round(row["Wins"] / resolved * 100, 2) if resolved else None
        samples = row['Net samples']
        row['Net win rate %'] = round(row['Net wins'] / samples * 100, 2) if samples else None
        total = row.pop('_net_total')
        row['Mean net outcome %'] = round(total / samples, 4) if samples else None
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
            outcome = evaluate_prediction(record, cache[key], now_ms)
            outcome['evaluated_at_ms'] = now_ms
            history.save_outcome(record["fingerprint"], outcome)
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
    from maxtrade.quality import forward_validation, prior_confidence
    st.markdown('#### Shadow quality evaluation')
    folds = forward_validation(records)
    if folds:
        st.dataframe(folds, hide_index=True, width='stretch')
        st.download_button('Download forward validation CSV', pd.DataFrame(folds).to_csv(index=False).encode(),
                           file_name='maxtrade-shadow-forward-validation.csv', mime='text/csv', icon=':material/download:')
    else:
        st.info('No matured cost-aware versioned shadow samples yet.')
    confidence_rows = []
    for record in records:
        if record['created_at'][:10] != selected_date:
            continue
        decision_ms = int(datetime.fromisoformat(record['created_at']).timestamp() * 1000)
        estimate = prior_confidence(records, record, decision_ms)
        quality = record.get('quality') or {}
        confidence_rows.append({'Pair': record['pair'], 'Timeframe': record.get('interval'),
                                'Regime': quality.get('regime'), 'Candidate': quality.get('candidate_action'),
                                'Blockers': '; '.join(quality.get('blockers', [])), **estimate})
    st.dataframe(confidence_rows, hide_index=True, width='stretch')
    st.caption('Shadow only: no PAPER policy changes. Frozen candidate rules, 30-day forward folds, crossing 24-hour horizons purged, non-overlapping samples per market/timeframe. Means are signal statistics, not portfolio performance. Confidence uses only comparable outcomes whose full horizon ended before the decision; 30 samples minimum and Wilson uncertainty. Brier scores use estimates frozen at fold start. This is forward monitoring, not trained-model walk-forward certification.')
    st.caption("Modeled signals, not executed trades. New outcomes include 10 bps fees and 5 bps slippage per side on entry notional; funding and borrowing are excluded. Expiry exits use the final 24-hour close. Legacy outcomes without this cost model are excluded from net statistics. Signals can overlap, so these averages are not portfolio returns or drawdown. Outcomes update each worker cycle or on request; cloud storage may be lost on restart. Small samples do not establish future accuracy.")