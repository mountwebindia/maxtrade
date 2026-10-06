from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from requests import RequestException

from maxtrade.charts import chart_analysis
from maxtrade.coindcx import INTERVAL_MS, normalize_candles


def sentiment_evidence(payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Research time must be timezone-aware")
    if payload.get("metadata", {}).get("error"):
        raise ValueError("Sentiment provider returned an error")
    item = payload["data"][0]
    value = int(item["value"])
    event_time = datetime.fromtimestamp(int(item["timestamp"]), timezone.utc)
    expires = event_time + timedelta(hours=24)
    if not 0 <= value <= 100 or not event_time <= now < expires:
        raise ValueError("Sentiment value or timestamp is invalid/stale")
    classification = item["value_classification"]
    if classification not in {"Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"}:
        raise ValueError("Unknown sentiment classification")
    return {"agent": "market-sentiment", "source": "https://api.alternative.me/fng/",
            "attribution": "Alternative.me", "attribution_url": "https://alternative.me/crypto/fear-and-greed-index/",
            "scope": "Bitcoin-focused crypto market index; not asset-specific or an execution signal",
            "value": value, "classification": classification, "event_time": event_time.isoformat(),
            "retrieved_at": now.astimezone(timezone.utc).isoformat(), "expires_at": expires.isoformat()}


def fetch_sentiment(session: Any, now: datetime | None = None) -> dict[str, Any]:
    response = session.get("https://api.alternative.me/fng/", params={"limit": 1}, timeout=12)
    response.raise_for_status()
    return sentiment_evidence(response.json(), now or datetime.now(timezone.utc))


@dataclass(frozen=True)
class MarketEvidence:
    source: str
    venue: str
    product: str
    symbol: str
    interval: str
    price_unit: str
    event_time: str
    retrieved_at: str
    expires_at: str
    action: str
    values: dict[str, float | None]
    reason: str


def market_evidence(candles: list[dict[str, Any]], product: str, symbol: str,
                    interval: str, now: datetime) -> MarketEvidence:
    if product not in {"Spot", "Futures", "Options"}:
        raise ValueError("Unsupported research product")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Research time must be timezone-aware")
    now = now.astimezone(timezone.utc)
    duration = INTERVAL_MS[interval]
    closed = normalize_candles(candles, interval, count=len(candles), now_ms=int(now.timestamp() * 1000))
    signal = chart_analysis(closed, interval, allow_short=product != "Spot")[-1]
    close_time = datetime.fromtimestamp((int(closed[-1]["time"]) + duration) / 1000, timezone.utc)
    expiry = close_time + timedelta(milliseconds=duration)
    if now >= expiry:
        raise ValueError("Latest completed candle is stale")
    venue = "Deribit" if product == "Options" else "CoinDCX"
    source = ("https://www.deribit.com/api/v2/public/get_tradingview_chart_data" if product == "Options" else
              "https://public.coindcx.com/market_data/candlesticks" if product == "Futures" else
              "https://api.coindcx.com/market_data/candles")
    unit = "USD underlying" if product == "Options" else symbol.rsplit("_", 1)[-1]
    return MarketEvidence(source, venue, product, symbol, interval, unit, close_time.isoformat(),
                          now.isoformat(), expiry.isoformat(), signal.action,
                          {"close": float(closed[-1]["close"]), "rsi": signal.rsi,
                           "ema20": signal.ema_fast, "ema50": signal.ema_slow,
                           "entry": signal.entry if product != "Options" else None,
                           "stop": signal.stop if product != "Options" else None,
                           "target": signal.target if product != "Options" else None}, signal.reason)


def run_market_research(client: Any, product: str, symbol: str,
                        now: datetime | None = None) -> dict[str, Any]:
    if product not in {"Spot", "Futures", "Options"}:
        raise ValueError("Unsupported research product")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Research time must be timezone-aware")
    evidence = []
    errors = []
    for interval in ("1h", "4h"):
        try:
            if product == "Options":
                candles = client.underlying_candles(symbol, interval)
            elif product == "Futures":
                candles = client.futures_candles(symbol, interval)
            else:
                candles = client.spot_candles(symbol, interval)
            evidence.append(market_evidence(candles, product, symbol, interval, now))
        except (RequestException, KeyError, TypeError, ValueError) as error:
            errors.append({"interval": interval, "reason": str(error)})
    directions = {item.action for item in evidence}
    aligned = len(evidence) == 2 and len(directions) == 1 and "NO TRADE" not in directions
    direction = evidence[0].action if aligned else "NO TRADE"
    if product == "Options":
        direction = {"LONG": "CALL BIAS", "SHORT": "PUT BIAS", "NO TRADE": "NO TRADE"}[direction]
    return {"schema_version": 1, "agent": "market-technical", "product": product, "symbol": symbol,
            "created_at": now.astimezone(timezone.utc).isoformat(),
            "expires_at": min((item.expires_at for item in evidence), default=now.isoformat()),
            "technical_bias": direction, "decision": "NO TRADE", "execution_enabled": False,
            "status": "DATA ERROR" if errors else "ALIGNED" if aligned else "NOT ALIGNED",
            "evidence": [asdict(item) for item in evidence], "errors": errors,
            "blockers": ["News and event research not connected", "Fear/greed research not connected",
                         "Derivatives/liquidity research not complete", "Portfolio risk checks not connected",
                         "Paper execution validation not complete"],
            "reason": "Technical evidence only; coordinated research and independent risk approval are incomplete."}


def render_market_research(product: str, symbol: str) -> None:
    import json
    import sqlite3

    import streamlit as st

    from maxtrade.coindcx import CoinDCXClient
    from maxtrade.history import ScanHistory
    from maxtrade.options import DeribitClient

    with st.expander("Market research"):
        if st.button("Run market research", icon=":material/radar:", key="research_run", width="stretch"):
            st.session_state.pop("research_report", None)
            client = DeribitClient() if product == "Options" else CoinDCXClient()
            try:
                with st.spinner("Checking 1h and 4h evidence..."):
                    report = run_market_research(client, product, symbol)
                    report["sentiment"] = None
                    try:
                        report["sentiment"] = fetch_sentiment(client.session)
                        report["expires_at"] = min(report["expires_at"], report["sentiment"]["expires_at"])
                        report["blockers"].remove("Fear/greed research not connected")
                    except (RequestException, KeyError, IndexError, TypeError, ValueError, OverflowError) as error:
                        report["sentiment_error"] = str(error)
                st.session_state["research_report"] = report
                try:
                    ScanHistory().save_research(report)
                except (OSError, sqlite3.Error, ValueError) as error:
                    st.warning(f"Report not saved: {error}")
            finally:
                client.session.close()
        report = st.session_state.get("research_report")
        if report and (report["product"], report["symbol"]) == (product, symbol):
            expired = datetime.now(timezone.utc) >= datetime.fromisoformat(report["expires_at"])
            st.caption(f"{report['agent']} · {report['status']} · {'EXPIRED' if expired else 'Snapshot'}")
            st.write(f"Technical bias: {report['technical_bias']}")
            st.write(f"Coordinated decision: {report['decision']}")
            st.caption(f"Created {report['created_at']} · expires {report['expires_at']}")
            rows = [{"Timeframe": item["interval"], "Direction": item["action"],
                     "Close": item["values"]["close"], "RSI": item["values"]["rsi"],
                     "Price unit": item["price_unit"], "Candle closed": item["event_time"],
                     "Expires": item["expires_at"], "Source": item["source"]} for item in report["evidence"]]
            if rows:
                st.dataframe(rows, hide_index=True, width="stretch")
            for error in report["errors"]:
                st.warning(f"{error['interval']}: {error['reason']}")
            sentiment = report.get("sentiment")
            if sentiment:
                st.write(f"Fear & greed: {sentiment['value']}/100 · {sentiment['classification']}")
                st.caption(f"Bitcoin-focused market context · as of {sentiment['event_time']} · expires {sentiment['expires_at']}")
                st.markdown("Source: [Alternative.me](https://alternative.me/crypto/fear-and-greed-index/)")
            elif report.get("sentiment_error"):
                st.warning(f"Sentiment unavailable: {report['sentiment_error']}")
            st.write("Pending checks")
            for blocker in report["blockers"]:
                st.write(f"- {blocker}")
            st.download_button("Research JSON", data=json.dumps(report, indent=2, allow_nan=False),
                               file_name="maxtrade_research.json", mime="application/json",
                               icon=":material/download:", key="research_download")
        else:
            st.caption("No matching research report.")