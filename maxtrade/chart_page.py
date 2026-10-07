from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

import requests
import pandas as pd
import streamlit as st

from maxtrade.charts import candle_figure, chart_analysis
from maxtrade.backtest import ReplaySettings, replay
from maxtrade.coindcx import CoinDCXClient, INTERVAL_MS, normalize_candles
from maxtrade.options import DeribitClient, scan_options, render_option_chain
from maxtrade.presentation import display_number, signal_card
from maxtrade.scanner import QUOTE_CURRENCIES
from maxtrade.research import render_market_research
from maxtrade.auth import require_chart_login


def render_chart_page() -> None:
    product = st.radio("Chart market type", ["Spot", "Futures", "Options"], horizontal=True,
                       label_visibility="collapsed", key="chart_product", width="stretch")
    controls = st.columns(2)
    interval = controls[0].selectbox("Chart timeframe", ["1h", "4h"], key="chart_interval")
    if product == "Options":
        market = controls[1].selectbox("Chart underlying", ["BTC", "ETH"], key="chart_underlying")
        pair = market
        st.caption(f"Deribit · {market}-PERPETUAL · USD underlying, not option premium")
    else:
        if st.button("Browse markets", icon=":material/search:", key="chart_browse"):
            client = CoinDCXClient()
            try:
                with st.spinner("Loading active markets…"):
                    if product == "Spot":
                        catalog = {row["coindcx_name"]: row["pair"] for row in client.spot_markets()
                                   if row.get("status") == "active" and row.get("base_currency_short_name") in QUOTE_CURRENCIES
                                   and row.get("coindcx_name") and row.get("pair")}
                    else:
                        catalog = {name: name for name in client.futures_instruments()}
                    if not catalog:
                        raise ValueError("No active markets available")
                    st.session_state[f"chart_catalog_{product}"] = catalog
            except (requests.RequestException, KeyError, TypeError, ValueError) as error:
                st.error(f"Market discovery unavailable: {error}")
            finally:
                client.session.close()
        default_catalog = {"BTCUSDT": "B-BTC_USDT", "ETHUSDT": "B-ETH_USDT"} if product == "Spot" else {
            "B-BTC_USDT": "B-BTC_USDT", "B-ETH_USDT": "B-ETH_USDT"}
        catalog = st.session_state.get(f"chart_catalog_{product}", default_catalog)
        names = sorted(catalog)
        preferred = "BTCUSDT" if product == "Spot" else "B-BTC_USDT"
        market = controls[1].selectbox("Chart market", names, index=names.index(preferred) if preferred in names else 0,
                          key=f"chart_market_{product}")
        pair = catalog[market]
        st.caption(f"CoinDCX · {product.lower()} · {market}")
    toolbar = st.columns(3)
    toolbar[0].selectbox("Chart style", ["Candles", "Line", "Area"], key="chart_style")
    toolbar[1].selectbox("Chart theme", ["Dark", "Light"], key="chart_theme")
    toolbar[2].selectbox("Visible candles", [80, 40, 120], key="chart_visible")
    st.multiselect("Indicators", ["EMA 20", "EMA 50", "EMA 200", "Bollinger Bands", "VWAP (UTC day)",
                                  "Support / resistance", "Volume", "RSI 14", "MACD"],
                   default=["EMA 20", "EMA 50", "Volume", "RSI 14"], key="chart_indicators")
    st.toggle("Log price scale", key="chart_log")
    live = st.toggle("Live updates · 10s", value=True, key="chart_live")
    st.fragment(run_every="10s" if live else None)(render_chart_snapshot)(product, interval, pair, live)
    render_market_research(product, pair)


def render_trade_status(product: str, pair: str, latest, stale: bool) -> None:
    from maxtrade.paper import PaperLedger, coordinate
    from maxtrade.history import ScanHistory

    st.subheader("Trade decision")
    candidate = not stale and latest.action in {"LONG", "SHORT"}
    status = f"{latest.action} SETUP" if candidate else "WAIT / NO TRADE"
    if candidate:
        st.info(f"Technical: {status} | {latest.reason}")
    else:
        st.warning(f"Technical: {status} | " + ("Closed candles are stale." if stale else latest.reason))
    if candidate:
        columns = st.columns(3)
        for column, label, value in zip(columns, ("Research entry", "Stop", "Target"),
                                        (latest.entry, latest.stop, latest.target)):
            column.metric(label, display_number(value))
    st.caption("Completed-candle setup only; not permission to enter. Paper fills require fresh 1h/4h agreement and all risk checks.")
    try:
        ledger = PaperLedger()
        now = datetime.now(timezone.utc)
        account = ledger.account(now)
        enabled = ledger.automation_enabled()
        st.write(f"Paper automation: {'ENABLED' if enabled else 'PAUSED'} | "
                 f"Kill switch: {'ON' if account['kill_switch'] else 'OFF'} | "
                 f"Position slot: {'OCCUPIED' if account['occupied'] else 'FREE'}")
        history = ScanHistory(ledger.path)
        report = st.session_state.get("research_report")
        if not report or (report.get("product"), report.get("symbol")) != (product, pair):
            report = next((saved['report'] for saved in history.recent_research()
                           if (saved['report'].get('product'), saved['report'].get('symbol')) == (product, pair)
                           and saved['report'].get('paper_policy') == 'autonomous-paper-v1'), None)
        if report and (report.get("product"), report.get("symbol")) == (product, pair):
            evidence = dict(report, ai_mode=ledger.ai_mode())
            risk = coordinate(evidence, now, account=account, autonomous=enabled)
            blockers = list(risk["blockers"])
            if report.get('paper_policy') == 'autonomous-paper-v1':
                blockers.extend(report.get('blockers', []))
            if stale:
                blockers.append("Chart candles are stale")
            if not enabled:
                blockers.append("Paper automation paused")
            blockers = list(dict.fromkeys(blockers))
            st.write(f"Paper: {'BUY ELIGIBLE' if not blockers else 'NO TRADE'}")
            st.caption(f"Full assessment: {report['created_at']} | expires {report['expires_at']}")
            if report['evidence']:
                st.dataframe([{'Timeframe': item['interval'], 'Direction': item['action'],
                               'Candle closed': item['event_time'], 'Expires': item['expires_at']}
                              for item in report['evidence']], hide_index=True, width='stretch')
            for blocker in blockers:
                st.write(f"- {blocker}")
        else:
            st.write("Paper: NO TRADE | No matching full research assessment. Run market research below.")
        heartbeat = history.worker_status()
        if not heartbeat:
            st.warning("Worker has not completed a cycle on this database.")
        elif (now - datetime.fromisoformat(heartbeat["finished_at"])).total_seconds() > 1800:
            st.warning(f"Worker inactive: last completed cycle {heartbeat['finished_at']}.")
        else:
            st.caption(f"Last paper cycle {heartbeat['finished_at']} | failures: {heartbeat['failures']}")
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
        st.warning(f"Paper: NO TRADE | Risk status unavailable: {error}")


def render_chart_snapshot(product: str, interval: str, pair: str, live: bool) -> None:
    require_chart_login()
    selection = (product, interval, pair)
    refresh = st.button("Refresh chart", type="primary", icon=":material/refresh:", width="stretch", key="chart_refresh")
    previous = st.session_state.get("chart_snapshot")
    if refresh or live or not previous or previous["selection"] != selection:
        st.session_state.pop("chart_snapshot", None)
        client = DeribitClient() if product == "Options" else CoinDCXClient()
        try:
            with st.spinner("Loading market candles…"):
                if product == "Options":
                    display_candles = client.underlying_candles(pair, interval, include_open=True)
                elif product == "Spot":
                    display_candles = client.spot_candles(pair, interval, include_open=True)
                else:
                    display_candles = client.futures_candles(pair, interval, include_open=True)
                chart_analysis(display_candles, interval, allow_short=product != "Spot")
                candles = normalize_candles(display_candles, interval, count=len(display_candles))
                analyses = chart_analysis(candles, interval, allow_short=product != "Spot")
                st.session_state["chart_snapshot"] = {"selection": selection, "candles": candles, "analyses": analyses,
                                                     "display_candles": display_candles,
                                                     "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                if product == "Options":
                    try:
                        age = (datetime.now(timezone.utc) - datetime.fromisoformat(previous["contracts_fetched"])).total_seconds() if previous and previous.get("contracts_fetched") else 60
                        if not refresh and previous and previous["selection"] == selection and age < 60:
                            contracts = previous["contracts"]
                            fetched = previous["contracts_fetched"]
                        else:
                            contracts = scan_options(client, pair, interval, 10)
                            fetched = datetime.now(timezone.utc).isoformat(timespec="seconds")
                        st.session_state["chart_snapshot"].update(contracts=contracts, contracts_fetched=fetched)
                    except (requests.RequestException, KeyError, TypeError, ValueError) as error:
                        st.session_state["chart_snapshot"]["contract_error"] = str(error)
        except (requests.RequestException, KeyError, TypeError, ValueError) as error:
            st.error(f"Chart unavailable; no signal generated: {error}")
        finally:
            client.session.close()
    snapshot = st.session_state.get("chart_snapshot")
    if not snapshot or snapshot["selection"] != selection:
        st.info("No matching chart snapshot.")
        return
    candles, analyses = snapshot["candles"], snapshot["analyses"]
    latest = analyses[-1]
    close_time = datetime.fromtimestamp((int(candles[-1]["time"]) + INTERVAL_MS[interval]) / 1000, timezone.utc)
    stale = (datetime.now(timezone.utc) - close_time).total_seconds() > INTERVAL_MS[interval] / 1000
    if stale:
        st.warning("Snapshot is outdated. Refresh before assessing a new setup.")
    render_trade_status(product, pair, latest, stale)
    st.caption(f"Last closed candle {close_time.isoformat(timespec='minutes')} · fetched {snapshot['fetched']}")
    display_candles = snapshot["display_candles"]
    forming = int(display_candles[-1]["time"]) > int(candles[-1]["time"])
    st.caption(f"{'Auto-refresh · 10s' if live else 'Paused snapshot'} · Last price {display_number(display_candles[-1]['close'])} · {'Forming candle' if forming else 'No forming candle from feed'}")
    last = display_candles[-1]
    metrics = st.columns(4)
    for column, field in zip(metrics, ("open", "high", "low", "close")):
        column.metric(field.upper(), display_number(last[field]))
    style = st.session_state.get("chart_style", "Candles")
    theme = st.session_state.get("chart_theme", "Dark")
    indicators = tuple(st.session_state.get("chart_indicators", ["EMA 20", "EMA 50", "Volume", "RSI 14"]))
    logarithmic = st.session_state.get("chart_log", False)
    visible = st.session_state.get("chart_visible", 80)
    figure = candle_figure(display_candles, analyses, interval, options=product == "Options",
                           chart_type=style, indicators=indicators, theme=theme,
                           logarithmic=logarithmic, visible_bars=visible)
    figure.update_layout(uirevision=repr((selection, style, indicators, logarithmic, visible)),
                          editrevision="|".join(selection))
    st.plotly_chart(figure,
                    width="stretch", config={"displaylogo": False, "scrollZoom": True,
                                              "displayModeBar": True,
                                              "modeBarButtonsToAdd": ["drawline", "drawrect", "drawopenpath", "eraseshape"],
                                              "modeBarButtonsToRemove": ["select2d", "lasso2d"],
                                              "toImageButtonOptions": {"filename": "maxtrade_chart", "scale": 2}},
                    key="candle_chart")
    st.subheader("Closed-candle details")
    st.write(f"Technical direction: {latest.action}")
    st.write(latest.reason)
    st.dataframe([{"Close": candles[-1]["close"], "EMA 20": latest.ema_fast,
                   "EMA 50": latest.ema_slow, "RSI 14": latest.rsi,
                   "Research entry": latest.entry, "Research stop": latest.stop,
                   "Research target": latest.target}], hide_index=True, width="stretch")
    st.caption(f"{interval} completed candle · {close_time.isoformat(timespec='minutes')} · "
               f"{'USD underlying, not option premium' if product == 'Options' else 'Market quote currency'} · "
               "Technical research only, not paper approval or an exchange order.")
    export = pd.DataFrame(display_candles)
    export["time"] = pd.to_datetime(export["time"], unit="ms", utc=True)
    st.download_button("Candle CSV", export.to_csv(index=False), file_name="maxtrade_candles.csv",
                        mime="text/csv", icon=":material/download:", key="chart_csv")
    if product == "Options":
        render_option_chain(pair, 'chart')
        st.subheader("Contract watchlist")
        st.caption("CALL/PUT bias is underlying direction only. WATCH labels also require current contract liquidity and delta filters; not premium entry/exit signals.")
        if snapshot.get("contract_error"):
            st.warning(f"Contract quotes unavailable: {snapshot['contract_error']}")
        else:
            st.markdown('<div class="signal-grid">' + ''.join(signal_card(row) for row in snapshot.get("contracts", [])) + '</div>',
                        unsafe_allow_html=True)
            st.caption(f"Contract quotes fetched {snapshot['contracts_fetched']} · refreshed at most once per minute automatically. Options may lose their full premium.")
    else:
        render_replay(snapshot, interval, product)
    with st.expander("Research rules"):
        st.write("Chart indicators use completed candles. Signal markers and trade-level overlays are disabled. Drawings are temporary browser annotations, not orders or saved trading instructions.")
        st.caption("EMA200 needs 200 closed candles. Bollinger Bands use 20 closes and two population standard deviations. Support/resistance are the prior 20-bar low/high, not predictive zones. MACD uses 12/26 EMAs and a 9-period signal. UTC-day VWAP excludes the first loaded day because its opening history may be incomplete; zero volume stays blank. Indicator warm-ups remain blank.")
        if product == "Spot":
            st.caption("Spot is buy-only research. Sell/short setups are available on Futures; no position-aware exit rule exists.")
        st.caption("Price display uses 10-second REST polling, not a tick-by-tick WebSocket stream. Signals use completed candles only; the forming candle never changes a confirmed signal. No automated orders.")


def render_replay(snapshot: dict, interval: str, product: str) -> None:
    with st.expander("Backtest", icon=":material/science:"):
        st.caption(f"Historical simulation · {len(snapshot['candles'])} candles · capital and PnL in market quote currency")
        columns = st.columns(2)
        cash = columns[0].number_input("Starting capital", min_value=100.0, value=10000.0, step=100.0, key="replay_cash")
        risk = columns[1].number_input("Risk per trade (%)", min_value=0.1, max_value=5.0, value=1.0, step=0.1, key="replay_risk")
        allocation = columns[0].number_input("Maximum allocation (%)", min_value=1.0, max_value=95.0, value=25.0, key="replay_allocation")
        fee = columns[1].number_input("Fee per fill (bps)", min_value=0.0, max_value=100.0, value=10.0, key="replay_fee")
        slippage = columns[0].number_input("Slippage per fill (bps)", min_value=0.0, max_value=100.0, value=5.0, key="replay_slippage")
        funding = columns[1].number_input("Funding cost / 8h (bps)", min_value=0.0, max_value=100.0, value=0.0,
                                          disabled=product == "Spot", key="replay_funding")
        settings = ReplaySettings(cash, risk, allocation, fee, slippage, funding if product == "Futures" else 0)
        signature = (snapshot["selection"], tuple((bar["time"], bar["open"], bar["high"], bar["low"], bar["close"]) for bar in snapshot["candles"]), settings)
        if st.button("Run backtest", icon=":material/play_arrow:", width="stretch", key="replay_run"):
            st.session_state.pop("replay_result", None)
            try:
                with st.spinner("Replaying candles…"):
                    result = replay(snapshot["candles"], interval, product == "Futures", settings)
                    st.session_state["replay_result"] = (signature, result)
            except (ValueError, TypeError, KeyError) as error:
                st.error(f"Simulation unavailable: {error}")
        saved = st.session_state.get("replay_result")
        if saved and saved[0] == signature:
            result = saved[1]
            st.markdown('<div class="risk-grid">' + ''.join(
                f'<div><span>{label}</span><strong>{value}</strong></div>' for label, value in [
                    ("Net return", f"{result['return_pct']:.2f}%"),
                    ("Max drawdown", f"{result['drawdown_pct']:.2f}%"),
                    ("Trades", str(len(result["trades"])))]) + '</div>', unsafe_allow_html=True)
            st.line_chart(result["equity"]["Equity"], height=240, width="stretch")
            st.caption(f"Win rate: {display_number(result['win_rate'], 1)}% · Funding estimate: {display_number(result['funding'])}")
            if result["trades"].empty:
                st.info("No completed simulated trades in this snapshot.")
            else:
                ledger = result["trades"][["Tag", "EntryTime", "ExitTime", "Size", "EntryPrice", "ExitPrice",
                                           "Commission", "Funding estimate", "Net PnL"]].rename(columns={"Tag": "Direction", "Commission": "Fees + slippage"})
                st.dataframe(ledger, hide_index=True, width="stretch")
                st.download_button("Trade CSV", ledger.to_csv(index=False), file_name="maxtrade_backtest.csv",
                                   mime="text/csv", icon=":material/download:", key="replay_csv")
            st.warning("Small historical sample. Results are not a profitability forecast or live paper-trading record.")
        elif saved:
            st.caption("Previous simulation does not match this snapshot or these settings.")
        st.caption("Next-candle open entries on new setups; one unleveraged position; fixed signal stop/target. Stop is tested before target on ambiguous bars. Remaining positions are liquidated at the final bar open by the engine.")
        st.caption("Slippage is a per-fill cash cost, not a changed execution price. Funding is an entry-notional, elapsed-time cost for both directions, deducted after replay; it does not affect position sizing. These are estimates, not exchange fee or funding history.")
        st.caption("Risk sizing uses the signal close and stop distance plus fill costs. Gaps can exceed the risk budget. No liquidation model, tax, lot-size rules, or order-book fills; 50 candles are used for warm-up.")