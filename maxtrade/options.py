from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from time import time
from typing import Any, Callable

import requests

from maxtrade.coindcx import INTERVAL_MS, aggregate_candles, aggregate_four_hour_candles, normalize_candles
from maxtrade.signals import analyze_candles


def finite_number(value: Any) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError("Non-finite options market data")
    return number


class DeribitClient:
    def __init__(self) -> None:
        self.session = requests.Session()

    def _get(self, method: str, **params: Any) -> Any:
        response = self.session.get(
            f"https://www.deribit.com/api/v2/public/{method}", params=params, timeout=12
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "error" in payload or "result" not in payload:
            raise ValueError("Deribit returned an invalid response or API error")
        return payload["result"]

    def instruments(self, currency: str) -> list[dict[str, Any]]:
        result = self._get("get_instruments", currency=currency, kind="option", expired="false")
        if not isinstance(result, list):
            raise ValueError("Invalid options instrument list")
        return result

    def summaries(self, currency: str) -> list[dict[str, Any]]:
        result = self._get("get_book_summary_by_currency", currency=currency, kind="option")
        if not isinstance(result, list):
            raise ValueError("Invalid options summary list")
        return result

    def ticker(self, instrument: str) -> dict[str, Any]:
        result = self._get("ticker", instrument_name=instrument)
        if not isinstance(result, dict) or result.get("instrument_name") != instrument:
            raise ValueError("Invalid or mismatched options ticker")
        return result

    def underlying_candles(self, currency: str, interval: str,
                           include_open: bool = False, count: int = 130) -> list[dict[str, Any]]:
        now_ms = int(time() * 1000)
        if interval in ('2m', '3m', '45m', '120m', '180m'):
            source = next(value for value in ('1h', '15m', '5m', '1m')
                          if INTERVAL_MS[interval] % INTERVAL_MS[value] == 0)
            ratio = INTERVAL_MS[interval] // INTERVAL_MS[source]
            candles = self.underlying_candles(currency, source, include_open, count=min(120 * ratio + ratio, 500))
            bars = aggregate_candles(candles, source, interval, now_ms if include_open else None)
            return normalize_candles(bars, interval, now_ms=now_ms, include_open=include_open)
        count = 500 if interval == "4h" else count
        source_interval = "1h" if interval == "4h" else interval
        resolution = "1D" if source_interval == "1d" else str(INTERVAL_MS[source_interval] // 60_000)
        payload = self._get(
            "get_tradingview_chart_data", instrument_name=f"{currency}-PERPETUAL",
            resolution=resolution, start_timestamp=now_ms - count * INTERVAL_MS[source_interval], end_timestamp=now_ms,
        )
        fields = ["ticks", "open", "high", "low", "close", "volume"]
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise ValueError("Underlying candle history unavailable")
        arrays = [payload.get(field) for field in fields]
        if not all(isinstance(values, list) for values in arrays) or len({len(values) for values in arrays}) != 1:
            raise ValueError("Invalid underlying candle arrays")
        candles = [dict(zip(["time", *fields[1:]], values)) for values in zip(*arrays)]
        hourly = normalize_candles(candles, source_interval, count=count, now_ms=now_ms, include_open=include_open)
        if interval == "4h":
            return normalize_candles(aggregate_four_hour_candles(hourly, now_ms=now_ms if include_open else None),
                                     "4h", now_ms=now_ms, include_open=include_open)
        return hourly


def option_chain(client: DeribitClient, currency: str, now_ms: int | None = None) -> list[dict[str, Any]]:
    if currency not in {'BTC', 'ETH'}:
        raise ValueError('Only BTC and ETH option chains are supported')
    now_ms = int(time() * 1000) if now_ms is None else now_ms
    instruments = {item['instrument_name']: item for item in client.instruments(currency)
                   if item.get('is_active') is True and item.get('state') == 'open'
                   and item.get('kind') == 'option' and item.get('base_currency') == currency
                   and item.get('quote_currency') == currency
                   and item.get('option_type') in {'call', 'put'}
                   and finite_number(item['expiration_timestamp']) > now_ms}
    rows = {}
    for quote in client.summaries(currency):
        instrument = instruments.get(quote.get('instrument_name'))
        if not instrument:
            continue
        expiry = datetime.fromtimestamp(instrument['expiration_timestamp'] / 1000, timezone.utc).isoformat()
        strike = finite_number(instrument['strike'])
        if strike <= 0:
            raise ValueError('Invalid option strike')
        row = rows.setdefault((expiry, strike), {'Expiry UTC': expiry, 'Strike USD': strike})
        side = instrument['option_type'].upper()
        row[f'{side} contract'] = instrument['instrument_name']
        age = now_ms - finite_number(quote['creation_timestamp'])
        row[f'{side} quote status'] = 'CURRENT' if -30000 <= age <= 300000 else 'STALE'
        row[f'{side} quote UTC'] = datetime.fromtimestamp(quote['creation_timestamp'] / 1000, timezone.utc).isoformat()
        for field, title in [('bid_price', 'bid'), ('ask_price', 'ask'), ('mark_price', 'mark'),
                             ('mark_iv', 'IV %'), ('open_interest', 'OI'), ('volume', 'volume')]:
            value = quote.get(field)
            number = finite_number(value) if value is not None else None
            if number is not None and number < 0:
                raise ValueError('Invalid negative option quote')
            row[f'{side} {title}'] = number if -30000 <= age <= 300000 else None
    if not rows:
        raise ValueError('No active option chain quotes available')
    return [rows[key] for key in sorted(rows)]


def render_option_chain(currency: str, key: str) -> None:
    import pandas as pd
    import streamlit as st

    st.subheader(f'{currency} option chain')
    st.caption(f'Deribit public quotes · premiums in {currency} · strike in USD · CoinDCX options API not connected')
    snapshot_key = f'{key}_chain_snapshot'
    refresh = st.button('Refresh option chain', icon=':material/refresh:', key=f'{key}_chain_load', width='stretch')
    snapshot = st.session_state.get(snapshot_key)
    if refresh or not snapshot or snapshot['currency'] != currency:
        st.session_state.pop(snapshot_key, None)
        client = DeribitClient()
        try:
            with st.spinner('Loading CALL and PUT quotes...'):
                rows = option_chain(client, currency)
            st.session_state[snapshot_key] = {'currency': currency, 'rows': rows,
                                             'fetched': datetime.now(timezone.utc).isoformat()}
        except (requests.RequestException, KeyError, TypeError, ValueError) as error:
            st.warning(f'Option chain unavailable: {error}')
        finally:
            client.session.close()
    snapshot = st.session_state.get(snapshot_key)
    if not snapshot or snapshot['currency'] != currency:
        return
    expiries = sorted({row['Expiry UTC'] for row in snapshot['rows']})
    expiry_key = f'{key}_chain_expiry'
    if st.session_state.get(expiry_key) not in expiries:
        st.session_state[expiry_key] = expiries[0]
    expiry = st.selectbox('Expiry date (UTC)', expiries,
                         format_func=lambda value: datetime.fromisoformat(value).strftime('%d %b %Y · %H:%M UTC'),
                         key=expiry_key)
    rows = [row for row in snapshot['rows'] if row['Expiry UTC'] == expiry]
    scan_rows = (st.session_state.get('scan_results', [])
                 if key == 'signals' and st.session_state.get('scan_product') == 'Options'
                 and st.session_state.get('scan_currency') == currency
                 else st.session_state.get('chart_snapshot', {}).get('contracts', []) if key == 'chart' else [])
    signals = {row['Market']: row for row in scan_rows if row.get('Source') == 'Deribit'}
    now = datetime.now(timezone.utc)
    def fresh(value: str | None, maximum_age: int) -> bool:
        try:
            age = (now - datetime.fromisoformat(value)).total_seconds()
            return -30 <= age <= maximum_age
        except (TypeError, ValueError):
            return False

    annotated = []
    for original in rows:
        row = dict(original)
        for side in ('CALL', 'PUT'):
            signal = signals.get(row.get(f'{side} contract'))
            current = (row.get(f'{side} quote status') == 'CURRENT'
                       and fresh(snapshot['fetched'], 60)
                       and fresh(row.get(f'{side} quote UTC'), 300)
                       and datetime.fromisoformat(expiry) > now)
            if signal and not fresh(signal.get('Quote UTC'), 300):
                signal = None
            if not current and row.get(f'{side} quote status') == 'CURRENT':
                row[f'{side} quote status'] = 'STALE'
            row[f'{side} signal'] = signal.get('Signal', 'NOT SCANNED') if signal and current else 'NOT SCANNED' if current else 'UNAVAILABLE'
            row[f'{side} comment'] = (signal.get('Reason', '') if signal and current else
                                      'Run a contract scan; buy approval nahi.' if current else 'Quote stale ya unavailable; entry nahi.')
            row[f'{side} premium levels'] = 'N/A: premium entry/TP/SL strategy not available'
        annotated.append(row)
    rows = annotated
    strikes = sorted({row['Strike USD'] for row in rows})
    range_key = f'{key}_chain_strikes'
    selection = (currency, expiry, tuple(strikes))
    if st.session_state.get(f'{key}_chain_selection') != selection:
        st.session_state.pop(range_key, None)
        st.session_state[f'{key}_chain_selection'] = selection
    if len(strikes) > 1:
        lower, upper = st.select_slider('Strike range (USD)', options=strikes,
                                       value=(strikes[0], strikes[-1]), key=range_key)
        rows = [row for row in rows if lower <= row['Strike USD'] <= upper]
    metrics = st.columns(3)
    metrics[0].metric('Strikes', len(rows))
    metrics[1].metric('CALL contracts', sum(bool(row.get('CALL contract')) for row in rows))
    metrics[2].metric('PUT contracts', sum(bool(row.get('PUT contract')) for row in rows))
    columns = ['CALL signal', 'CALL comment', 'CALL premium levels', 'CALL quote status', 'CALL volume', 'CALL OI', 'CALL IV %', 'CALL bid', 'CALL mark', 'CALL ask',
               'Strike USD', 'PUT bid', 'PUT mark', 'PUT ask', 'PUT IV %', 'PUT OI', 'PUT volume', 'PUT quote status', 'PUT signal', 'PUT comment', 'PUT premium levels']
    table = pd.DataFrame(rows).reindex(columns=columns).sort_values('Strike USD')
    styled = table.style.set_properties(subset=['Strike USD'], **{'font-weight': 'bold', 'background-color': '#263238', 'color': '#ffffff'})
    st.dataframe(styled, hide_index=True, width='stretch', height=480,
                 column_config={column: st.column_config.NumberColumn(format='%.6f')
                                for column in columns if column.endswith((' bid', ' ask', ' mark'))})
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(snapshot['fetched'])).total_seconds()
    if age > 60:
        st.warning('Option chain snapshot is older than one minute; refresh quotes.')
    st.caption(f"Fetched {snapshot['fetched']} · stale provider quotes are blank · OI/volume in base currency · not executable prices")
    st.download_button('Option chain CSV', pd.DataFrame(rows).to_csv(index=False),
                       file_name=f'{currency.lower()}-option-chain.csv', mime='text/csv',
                       icon=':material/download:', key=f'{key}_chain_csv')


def scan_options(client: DeribitClient, currency: str, interval: str, limit: int,
                 progress: Callable[[int, int, str], None] | None = None,
                 now_ms: int | None = None) -> list[dict[str, Any]]:
    if currency not in {"BTC", "ETH"} or interval not in {"1h", "4h"} or not 1 <= limit <= 30:
        raise ValueError("Unsupported options scan settings")
    supplied_time = now_ms
    now_ms = int(time() * 1000) if now_ms is None else now_ms
    instruments = {}
    for instrument in client.instruments(currency):
        days = (finite_number(instrument["expiration_timestamp"]) - now_ms) / 86400000
        if (instrument.get("is_active") is True and instrument.get("state") == "open"
                and instrument.get("kind") == "option" and instrument.get("base_currency") == currency
                and instrument.get("quote_currency") == currency
                and instrument.get("option_type") in {"call", "put"} and 7 <= days <= 45):
            instruments[instrument["instrument_name"]] = instrument
    summaries = {row["instrument_name"]: row for row in client.summaries(currency)
                 if row.get("instrument_name") in instruments}
    selected = sorted(summaries, key=lambda name: finite_number(summaries[name].get("volume_usd", 0)), reverse=True)[:limit]
    if not selected:
        raise ValueError("No open 7-45 day options contracts matched the Deribit feed")
    trend = analyze_candles(client.underlying_candles(currency, interval), allow_short=True)
    results = []
    for name in selected:
        instrument = instruments[name]
        row = {
            "Market": name, "Source": "Deribit", "Premium currency": currency,
            "Type": instrument["option_type"].upper(), "Strike USD": finite_number(instrument["strike"]),
            "Expiry UTC": datetime.fromtimestamp(instrument["expiration_timestamp"] / 1000, timezone.utc).isoformat(),
            "Days to expiry": round((instrument["expiration_timestamp"] - now_ms) / 86400000, 2),
            "Signal": "DATA ERROR", "Price": None, "Entry": None, "Stop": None, "Target": None,
            "Underlying trend": trend.action, "RSI": trend.rsi,
        }
        try:
            ticker = client.ticker(name)
            quote_now = int(time() * 1000) if supplied_time is None else supplied_time
            age = quote_now - finite_number(ticker["timestamp"])
            if age < -30000 or age > 300000 or ticker.get("state") != "open":
                raise ValueError("Stale options quote or closed contract")
            bid, ask = finite_number(ticker["best_bid_price"]), finite_number(ticker["best_ask_price"])
            if not 0 < bid <= ask:
                raise ValueError("Missing, zero, or crossed two-sided quote")
            if finite_number(ticker["best_bid_amount"]) <= 0 or finite_number(ticker["best_ask_amount"]) <= 0:
                raise ValueError("No size available at bid or ask")
            spread = (ask - bid) / ((ask + bid) / 2) * 100
            greeks = {key: finite_number(ticker["greeks"][key]) for key in ["delta", "gamma", "theta", "vega"]}
            iv = finite_number(ticker["mark_iv"])
            mark = finite_number(ticker["mark_price"])
            interest = finite_number(ticker["open_interest"])
            volume = finite_number(ticker["stats"]["volume"])
            if iv <= 0 or mark <= 0 or interest < 0 or volume < 0 or not -1 <= greeks["delta"] <= 1:
                raise ValueError("Invalid options valuation or liquidity data")
            aligned = ((trend.action == "LONG" and instrument["option_type"] == "call" and greeks["delta"] > 0)
                       or (trend.action == "SHORT" and instrument["option_type"] == "put" and greeks["delta"] < 0))
            eligible = spread <= 10 and interest >= 10 and volume > 0 and 0.25 <= abs(greeks["delta"]) <= 0.75
            row.update({
                "Price": mark, "Bid": bid, "Ask": ask, "Spread %": round(spread, 2),
                "IV %": iv, "Delta": greeks["delta"], "Gamma": greeks["gamma"],
                "Theta": greeks["theta"], "Vega": greeks["vega"], "Open interest": interest,
                "24h volume": volume, "Quote UTC": datetime.fromtimestamp(ticker["timestamp"] / 1000, timezone.utc).isoformat(),
                "Signal": f"WATCH {row['Type']}" if aligned and eligible else "NO TRADE",
                "Reason": "Trend-aligned research candidate; not a valuation or execution signal."
                          if aligned and eligible else "Underlying trend or spread/delta/liquidity filters do not qualify.",
            })
        except (requests.RequestException, KeyError, TypeError, ValueError) as error:
            row["Reason"] = str(error)
        results.append(row)
        if progress:
            progress(len(results), len(selected), name)
    return results