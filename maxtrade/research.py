from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import os
import sqlite3
from pathlib import Path
from typing import Any

from requests import RequestException

from maxtrade.charts import chart_analysis
from maxtrade.coindcx import INTERVAL_MS, normalize_candles
from maxtrade.research_sources import fetch_derivatives, fetch_news


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


def run_coordinated_research(client: Any, product: str, symbol: str) -> dict[str, Any]:
    from maxtrade.paper import coordinate

    report = run_market_research(client, product, symbol)
    report["schema_version"] = 2
    report["agent"] = "research-coordinator"
    from maxtrade.historical_features import historical_context

    historical_database = Path(os.environ.get("MAXTRADE_HISTORICAL_DATABASE",
                                              str(Path(__file__).resolve().parent.parent / "data" / "historical.sqlite3")))
    try:
        report["historical_shadow"] = historical_context(historical_database, symbol, datetime.now(timezone.utc))
    except (OSError, sqlite3.Error, KeyError, IndexError, TypeError, ValueError, OverflowError) as error:
        report["historical_shadow"] = {"agent": "historical-shadow", "status": "UNAVAILABLE",
                                       "mode": "SHADOW ONLY", "execution_enabled": False, "reason": str(error)}
    for name, fetcher in (("sentiment", lambda: fetch_sentiment(client.session)),
                          ("news", lambda: fetch_news(client.session)),
                          ("derivatives", lambda: fetch_derivatives(client.session, symbol))):
        report[name] = None
        try:
            report[name] = fetcher()
            report["expires_at"] = min(report["expires_at"], report[name]["expires_at"])
        except (RequestException, KeyError, IndexError, TypeError, ValueError, OverflowError) as error:
            report[f"{name}_error"] = str(error)
    risk = coordinate(report, datetime.now(timezone.utc))
    report.update(risk=risk, blockers=risk["blockers"], decision=risk["decision"],
                  reason="Evidence-backed research; manual event review and paper account risk approval required.")
    return report


def render_market_research(product: str, symbol: str) -> None:
    import json
    import sqlite3

    import streamlit as st

    from maxtrade.coindcx import CoinDCXClient
    from maxtrade.history import ScanHistory
    from maxtrade.options import DeribitClient
    from maxtrade.paper import PaperLedger, coordinate

    with st.expander("Market research", icon=":material/radar:", expanded=True):
        if st.button("Run market research", icon=":material/radar:", key="research_run", width="stretch"):
            st.session_state.pop("research_report", None)
            client = DeribitClient() if product == "Options" else CoinDCXClient()
            try:
                with st.spinner("Checking 1h and 4h evidence..."):
                    report = run_coordinated_research(client, product, symbol)
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
            news = report.get("news")
            if news:
                st.write("News · CoinDesk")
                st.caption(news["scope"])
                for item in news["items"][:8]:
                    st.link_button(item["title"], item["url"], icon=":material/open_in_new:")
                    st.caption(item["event_time"])
            elif report.get("news_error"):
                st.warning(f"News unavailable: {report['news_error']}")
            derivatives = report.get("derivatives")
            if derivatives:
                st.write(f"Derivatives · {derivatives['instrument']}")
                st.dataframe([derivatives["values"]], hide_index=True, width="stretch")
                st.caption(derivatives["scope"])
                st.caption(f"OI: {derivatives['open_interest_unit']} · funding: {derivatives['funding_unit']}")
            elif report.get("derivatives_error"):
                st.warning(f"Derivatives unavailable: {report['derivatives_error']}")
            historical = report.get("historical_shadow")
            if historical:
                st.write(f"Historical shadow · {historical['status']}")
                if historical["status"] == "AVAILABLE":
                    st.caption(f"{historical['dataset']['product']} · Coinbase USD · daily · {historical['regime']}")
                    st.dataframe([historical["prior_same_regime_five_day_outcomes"]], hide_index=True, width="stretch")
                    st.caption("Descriptive historical samples, not predicted probabilities or paper-policy validation.")
                else:
                    st.caption(historical["reason"])
            st.write("Paper risk review")
            reviewed = st.checkbox("News and event risks reviewed", value=False, key=f"review_{report['created_at']}")
            try:
                ledger = PaperLedger()
                now = datetime.now(timezone.utc)
                risk = coordinate(report, now, reviewed, ledger.account(now))
                st.write(f"Paper decision: {risk['decision']}")
                if st.button("Queue paper entry", icon=":material/add_chart:", disabled=not risk["approved"], key="paper_submit"):
                    ledger.submit(report, datetime.now(timezone.utc), reviewed)
                    st.success("Simulated entry queued; no exchange order sent.")
            except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
                st.warning(f"Paper risk unavailable: {error}")
                risk = {"blockers": report["blockers"]}
            for blocker in risk["blockers"]:
                st.write(f"- {blocker}")
            st.download_button("Research JSON", data=json.dumps(report, indent=2, allow_nan=False),
                               file_name="maxtrade_research.json", mime="application/json",
                               icon=":material/download:", key="research_download")
        else:
            st.caption("No matching research report.")


def render_worker_diagnostics(status: dict | None, reports: list[dict], now: datetime) -> None:
    import streamlit as st

    st.markdown('#### Worker and Azure diagnostics')
    if not status:
        st.warning('Worker: no completed cycle on this database.')
    else:
        st.caption(f"Last cycle: {status['finished_at']} · mode: {status['mode']} · failures: {status['failures']}")
        try:
            age = (now - datetime.fromisoformat(status['finished_at'])).total_seconds()
        except (ValueError, TypeError):
            age = -1
        if age < 0:
            st.warning('Worker: invalid or future-dated heartbeat; health unverified.')
        elif age > 1800:
            st.warning('Worker: stale heartbeat. No completed worker cycle in the last 30 minutes.')
        elif status['failures']:
            st.warning('Worker: recent cycle completed with failures; inspect saved review errors and backend logs.')
        else:
            st.success('Worker: recent cycle completed without reported failures.')
        if status['mode'] != 'watch':
            st.warning('Always-on worker unverified: the last cycle was one-shot, not supervised watch mode.')
        else:
            st.caption('Watch-mode heartbeat records the last completed cycle, not current process liveness.')
    if not reports:
        st.info('Azure: no saved autonomous assessment on this database.')
    for report in reports:
        st.write(f"{report['symbol']} · assessed {report['created_at']}")
        if report.get('ai_error'):
            st.warning(f"Azure unavailable: {report['ai_error']}")
        elif report.get('ai_mode') != 'Azure-assisted':
            st.info('Azure not requested: this assessment used Deterministic mode.')
        elif not report.get('ai_review'):
            st.warning('Azure unavailable: no structured review saved for this assessment.')
        else:
            review = report['ai_review']
            verdict = review['verdict']
            concerns = review['concerns']
            if verdict == 'CLEAR' and not concerns:
                st.success('Azure: CLEAR, no additional concerns. Other evidence and risk gates still apply.')
            else:
                st.warning(f'Azure: {verdict} · new entries blocked by this review.')
            st.text(review['summary'])
            for concern in concerns:
                st.text(f'Concern: {concern}')


def render_paper_account() -> None:
    import sqlite3
    import streamlit as st
    from maxtrade.paper import PaperLedger

    st.markdown("#### Paper account")
    try:
        ledger = PaperLedger()
        account = ledger.account(datetime.now(timezone.utc))
        enabled = ledger.automation_enabled()
        st.write(f"Autonomous paper mode: {'RUNNING POLICY' if enabled else 'PAUSED'}")
        if st.button("Pause paper automation" if enabled else "Start paper automation",
                     icon=":material/pause:" if enabled else ":material/play_arrow:", key="paper_automation"):
            ledger.set_automation(not enabled)
            st.rerun()
        if st.button("Run autonomous research cycle", icon=":material/radar:", key="paper_cycle", disabled=not enabled):
            from maxtrade.worker import run_once
            from maxtrade.settings import azure_openai_config, telegram_config
            configuration = None
            try:
                secrets = st.secrets.to_dict()
            except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
                secrets = {}
            if ledger.ai_mode() == 'Azure-assisted':
                configuration = azure_openai_config(secrets=secrets)
            try:
                notifications = telegram_config(secrets=secrets)
            except ValueError:
                st.warning('Telegram configuration invalid; paper research will continue without delivery.')
                notifications = None
            with st.spinner("Researching BTC and ETH; checking paper risk and positions..."):
                failures = run_once(ledger.path, ["B-BTC_USDT", "B-ETH_USDT"], ai_config=configuration,
                                    notification_config=notifications)
            if failures:
                st.warning(f"Cycle finished with {failures} provider/reconciliation failures. Check worker logs.")
            else:
                st.success("Cycle completed; only eligible paper decisions are queued.")
        st.caption("BTC/ETH USDT spot · no human approval · headline coverage is not comprehensive event clearance · unattended cycles require a running backend worker")
        with st.form("paper_settings"):
            capital = st.number_input("Paper capital (USDT)", min_value=100.0, max_value=1_000_000.0,
                                      value=float(account["capital"]), disabled=bool(ledger.positions()))
            kill = st.checkbox("Paper kill switch", value=bool(account["kill_switch"]))
            if st.form_submit_button("Save paper limits", icon=":material/save:"):
                ledger.settings(capital, kill)
                account = ledger.account(datetime.now(timezone.utc))
                st.success("Paper limits saved.")
        st.caption("USDT spot only · 1% risk · 25% allocation cap · 3% realized daily-loss veto · 10 bps fees per side · 5 bps slippage per side")
        st.metric("Realized paper equity (USDT)", f"{account['equity']:,.2f}")
        performance = ledger.performance()
        metrics = st.columns(3)
        metrics[0].metric("Net paper win rate", f"{performance['win_rate_pct']:.1f}%" if performance['closed'] else "No closed trades")
        metrics[1].metric("Closed paper trades", performance['closed'])
        metrics[2].metric("Net paper P&L (USDT)", f"{performance['net_pnl']:,.2f}")
        averages = st.columns(3)
        for column, label, field in zip(averages, ('Average net win (USDT)', 'Average net loss (USDT)',
                                                 'Net expectancy / trade (USDT)'),
                                        ('average_win', 'average_loss', 'expectancy')):
            value = performance[field]
            column.metric(label, f'{value:,.2f}' if value is not None else 'N/A')
        st.caption(f"Realized max drawdown: {performance['max_drawdown_pct']:.2f}% · profit factor: "
                   + (f"{performance['profit_factor']:.2f}" if performance['profit_factor'] is not None else "N/A (no gross losses)"))
        if performance['daily']:
            import pandas as pd
            daily = pd.DataFrame(performance['daily'])
            st.dataframe(daily, hide_index=True, width="stretch")
            st.download_button("Paper daily results CSV", daily.to_csv(index=False).encode(),
                               file_name="paper_daily.csv", mime="text/csv", icon=":material/download:")
        st.caption("Win = closed trade with positive net P&L after modeled fees/slippage. Breakeven counts as non-win; pending/open/cancelled excluded. Signal target accuracy is separate. No profitability guarantee.")
        positions = ledger.positions()
        if positions:
            columns = ("id", "symbol", "state", "submitted_at", "opened_at", "closed_at", "entry", "stop", "target", "exit", "quantity", "pnl", "reason")
            st.dataframe([{key: row[key] for key in columns} for row in positions], hide_index=True, width="stretch")
            import pandas as pd
            st.download_button('Paper trade records CSV', pd.DataFrame(positions).to_csv(index=False),
                               file_name='paper-trade-records.csv', mime='text/csv', icon=':material/download:',
                               key='paper_records_csv')
        from maxtrade.history import ScanHistory
        status = ScanHistory(ledger.path).worker_status()
        if not enabled:
            st.warning('New paper entries blocked: automation is paused.')
        if account['kill_switch']:
            st.warning('New paper entries blocked: paper kill switch is on.')
        if account['occupied']:
            st.info('New paper entries blocked: an existing pending/open position occupies the single position slot.')
        latest_reports = {}
        for saved in ScanHistory(ledger.path).recent_research():
            report = saved['report']
            if report.get('paper_policy') == 'autonomous-paper-v1':
                latest_reports.setdefault(report['symbol'], report)
        if latest_reports:
            st.write('Latest autonomous decisions')
            st.dataframe([{'Market': symbol, 'Assessed': report['created_at'],
                           'Decision': report['decision'], 'Blockers': '; '.join(report['blockers'])}
                          for symbol, report in latest_reports.items()], hide_index=True, width='stretch')
        render_worker_diagnostics(status, list(latest_reports.values()), datetime.now(timezone.utc))
        rejections = ScanHistory(ledger.path).rejection_summary()
        if rejections:
            with st.expander('Paper rejection history'):
                import pandas as pd
                dates = sorted({item['Date (UTC)'] for item in rejections}, reverse=True)
                selected = st.selectbox('Assessment date (UTC)', dates, key='paper_rejection_date')
                table = pd.DataFrame([item for item in rejections if item['Date (UTC)'] == selected])
                st.dataframe(table, hide_index=True, width='stretch')
                st.caption('Saved autonomous NO TRADE assessments only. One assessment can have multiple reasons; counts are not missed trades.')
                st.download_button('Rejection history CSV', pd.DataFrame(rejections).to_csv(index=False),
                                   file_name='paper-rejections.csv', mime='text/csv', icon=':material/download:',
                                   key='paper_rejection_csv')
        if st.button("Reconcile paper positions", icon=":material/sync:"):
            from maxtrade.coindcx import CoinDCXClient

            client = CoinDCXClient()
            try:
                for symbol in {row["symbol"] for row in positions if row["state"] in {"PENDING", "OPEN"}}:
                    ledger.reconcile(symbol, client.spot_candles(symbol, "1h"), datetime.now(timezone.utc))
                st.rerun()
            finally:
                client.session.close()
        st.caption("Snapshot simulation: fresh evidence at submission, immediately next hourly open, no fresh recommendation at fill. Autonomous mode waives human review, not evidence/risk checks. Completed 1h bars reconstruct fills. Kill switch cancels pending entries, not open positions. Equity excludes unrealized P&L. Local/cloud SQLite needs durable storage and backups.")
    except (OSError, sqlite3.Error, ValueError, RequestException) as error:
        st.warning(f"Paper account unavailable: {error}")