from html import escape
from math import isfinite
from typing import Any
from datetime import datetime, timezone


def render_decision_styles() -> None:
    import streamlit as st

    st.html('''<style>
        .market-decision {padding:.25rem 0 .5rem 44px;min-width:0;}
        .decision-line {display:flex;align-items:baseline;justify-content:space-between;gap:.75rem;flex-wrap:wrap;}
        .decision-line strong {font-size:.8125rem;color:#202631;}
        .decision-line span, .decision-meta {font-size:.6875rem !important;color:#596575;}
        .decision-reason {margin:.3rem 0 .5rem;font-size:.8125rem;overflow-wrap:anywhere;}
        .decision-levels {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.5rem;border-top:1px solid #dce3eb;border-bottom:1px solid #dce3eb;padding:.5rem 0;}
        .decision-levels div {min-width:0;}
        .decision-levels span {display:block;color:#596575;font-size:.6875rem;}
        .decision-levels strong {display:block;font-size:.8125rem;font-variant-numeric:tabular-nums;overflow-wrap:anywhere;}
        .decision-meta {margin:.35rem 0 0;overflow-wrap:anywhere;}
        .decision-quality {margin-top:.5rem;font-size:.75rem;}
        .decision-quality p, .decision-quality li {font-size:.75rem !important;line-height:1.5;}
        .decision-quality summary {cursor:pointer;min-height:36px;display:list-item;}
        .decision-quality ul {padding-left:1.2rem;overflow-wrap:anywhere;}
        .snapshot-status {display:flex;justify-content:space-between;gap:.5rem;flex-wrap:wrap;padding:.5rem 0;border-bottom:1px solid #e5e8ee;color:#596575;font-size:.75rem;}
        .signal-card {min-width:0;}
        .signal-card .card-top {align-items:flex-start;gap:.5rem;flex-wrap:wrap;}
        .signal-card .card-top strong {overflow-wrap:anywhere;min-width:0;}
        .signal-card .card-price {display:flex;align-items:baseline;justify-content:space-between;gap:.5rem;flex-wrap:wrap;}
        .signal-card .card-price small {margin:0;font-size:.6875rem;}
        @media(max-width:640px) {
            .market-decision {padding-left:0;}
            .decision-quality summary {min-height:44px;line-height:1.5;}
            .decision-levels {gap:.35rem;}
            .snapshot-status {font-size:.6875rem;}
        }
        </style>''')


def snapshot_status(scanned_at: str, now: datetime | None = None, archived: bool = False) -> str:
    try:
        scanned = datetime.fromisoformat(scanned_at.replace('Z', '+00:00'))
        if scanned.tzinfo is None:
            raise ValueError('Missing timezone')
        age = ((now or datetime.now(timezone.utc)) - scanned).total_seconds()
        if not isfinite(age) or age < 0:
            raise ValueError('Future snapshot')
        minutes = int(age // 60)
        elapsed = 'Under 1 minute old' if minutes == 0 else f'{minutes} min old' if minutes < 60 else f'{minutes // 60}h {minutes % 60}m old'
        label = 'Archived snapshot' if archived else 'Scan snapshot'
        return f'<div class="snapshot-status"><strong>{label} · {elapsed}</strong><span>{scanned.astimezone(timezone.utc):%d %b %Y %H:%M:%S} UTC · Not live</span></div>'
    except (TypeError, ValueError, AttributeError, OverflowError):
        return '<div class="snapshot-status"><strong>Snapshot time unavailable</strong><span>Freshness unverified · Not live</span></div>'


def signal_evidence(row: dict[str, Any]) -> str:
    action = str(row.get('Signal', 'NO TRADE'))
    decision = {'LONG': 'BUY setup', 'SHORT': 'SELL setup', 'NO TRADE': 'WAIT',
                'DATA ERROR': 'DATA UNAVAILABLE', 'WATCH CALL': 'WATCH CALL',
                'WATCH PUT': 'WATCH PUT'}.get(action, 'UNAVAILABLE')
    reason = escape(str(row.get('Reason') or 'Decision evidence unavailable.'))
    evidence = f'<div class="decision-line"><strong>{decision}</strong><span>Research only</span></div><p class="decision-reason">{reason}</p>'
    if row.get('Source') != 'Deribit':
        evidence += '<div class="decision-levels">' + ''.join(
            f'<div><span>{label}</span><strong>{display_number(row.get(field))}</strong></div>'
            for label, field in [('Entry', 'Entry'), ('SL', 'Stop'), ('TP', 'Target')]
        ) + '</div>'
        try:
            entry, stop, target = [float(row[field]) for field in ('Entry', 'Stop', 'Target')]
            valid = all(isfinite(value) and value > 0 for value in (entry, stop, target))
            if valid and ((action == 'LONG' and stop < entry < target) or
                          (action == 'SHORT' and target < entry < stop)):
                evidence += f'<p class="decision-meta">Reward / risk {abs(target - entry) / abs(entry - stop):.2f} · Before costs</p>'
        except (KeyError, TypeError, ValueError):
            pass
        try:
            candle = datetime.fromtimestamp(float(row['Signal candle time']) / 1000, timezone.utc)
            evidence += f'<p class="decision-meta">Closed candle · {candle:%d %b %Y %H:%M} UTC</p>'
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            evidence += '<p class="decision-meta">Closed-candle time unavailable</p>'
    quality = row.get('Quality')
    if isinstance(quality, dict) and isinstance(quality.get('blockers'), list) and quality['blockers']:
        blockers = ''.join(f'<li>{escape(str(blocker))}</li>' for blocker in quality['blockers'])
        evidence += f'<details class="decision-quality"><summary>Shadow quality blockers · {len(quality["blockers"])}</summary><ul>{blockers}</ul><p>Shadow only · Does not change the baseline signal.</p></details>'
    return evidence


def render_market_list(rows: list[dict[str, Any]], key: str) -> None:
    import streamlit as st

    render_decision_styles()
    st.html(f'''<style>
        [class*="st-key-{key}_market_"] {{border-bottom:1px solid #e5e8ee;padding:.35rem 0;}}
        [class*="st-key-{key}_market_"] [data-testid="stHorizontalBlock"] {{flex-wrap:nowrap !important;gap:.5rem;}}
        [class*="st-key-{key}_market_"] [data-testid="stColumn"] {{min-width:0 !important;}}
        [class*="st-key-{key}_market_"] [data-testid="stColumn"]:first-child {{flex:0 0 44px !important;}}
        [class*="st-key-{key}_market_"] [data-testid="stColumn"]:nth-child(2) {{flex:6 1 0 !important;}}
        [class*="st-key-{key}_market_"] [data-testid="stColumn"]:nth-child(3) {{flex:4 1 0 !important;text-align:right;}}
        [class*="st-key-{key}_market_"] p {{overflow-wrap:anywhere;}}
        .st-key-{key}_list_controls [data-testid="stHorizontalBlock"] {{flex-wrap:nowrap !important;align-items:center;gap:.5rem;}}
        .st-key-{key}_list_controls [data-testid="stColumn"] {{min-width:0 !important;}}
        .st-key-{key}_list_controls [data-testid="stColumn"]:first-child {{flex:1 1 0 !important;}}
        .st-key-{key}_list_controls [data-testid="stColumn"]:nth-child(2) {{flex:0 1 170px !important;}}
        .st-key-{key}_list_controls [data-testid="stColumn"]:last-child {{flex:0 0 110px !important;}}
        @media(max-width:640px) {{
            .st-key-{key}_list_controls [data-testid="stHorizontalBlock"] {{flex-wrap:wrap !important;}}
            .st-key-{key}_list_controls [data-testid="stColumn"]:first-child {{flex:1 1 100% !important;}}
            .st-key-{key}_list_controls [data-testid="stColumn"]:nth-child(2) {{flex:1 1 140px !important;}}
        }}
        </style>''')
    with st.container(key=f'{key}_list_controls'):
        controls = st.columns([3, 2, 1])
    query = controls[0].text_input('Search markets', key=f'{key}_search', placeholder='Search markets', label_visibility='collapsed')
    order = controls[1].selectbox('Market order', ['Scan order', 'Setups first', 'Data errors first'], key=f'{key}_order', label_visibility='collapsed')
    only_saved = controls[2].toggle('Starred', key=f'{key}_starred')
    favorites = set(st.session_state.get('market_favorites', []))
    visible = [row for row in rows if query.casefold() in str(row.get('Market', '')).casefold()
               and (not only_saved or row.get('Market') in favorites)]
    if order == 'Setups first':
        visible = sorted(visible, key=lambda row: row.get('Signal') not in {'LONG', 'SHORT', 'WATCH CALL', 'WATCH PUT'})
    elif order == 'Data errors first':
        visible = sorted(visible, key=lambda row: row.get('Signal') != 'DATA ERROR')
    if not visible:
        st.info('No matching markets.' if not only_saved else 'No starred markets match this search.')
    for index, row in enumerate(visible):
        market = str(row.get('Market', 'Unknown'))
        with st.container(key=f'{key}_market_{index}'):
            columns = st.columns([1, 6, 4])
            saved = market in favorites
            if columns[0].button('', icon=':material/star:' if saved else ':material/star_border:',
                                 help='Remove from watchlist' if saved else 'Add to watchlist',
                                 key=f'{key}_favorite_{market}'):
                favorites.discard(market) if saved else favorites.add(market)
                st.session_state['market_favorites'] = sorted(favorites)
                st.rerun()
            columns[1].markdown(f'**{escape(market)}**')
            columns[1].caption(str(row.get('Signal', 'NO TRADE')))
            columns[2].markdown(f'**{display_number(row.get("Price"))}**')
            columns[2].caption(str(row.get('Premium currency', '')) or f'RSI {display_number(row.get("RSI"), 1)}')
            st.html(f'<div class="market-decision">{signal_evidence(row)}</div>')
            with st.expander(f'{market} details'):
                st.html(signal_card(row))
                if row.get('Pair') and row.get('Source') != 'Deribit':
                    from maxtrade.chart_page import chart_workspace_url
                    product = st.session_state.get('scan_product' if key == 'live' else 'records_product', 'Spot')
                    interval = st.session_state.get('scan_interval' if key == 'live' else 'records_interval', '1h')
                    st.link_button('Chart', chart_workspace_url(product, row['Pair'], interval, {}),
                                   icon=':material/candlestick_chart:')
    st.caption('Saved stars last for this browser session. Prices are from the scan snapshot, not streaming quotes.')


def display_number(value: Any, decimals: int | None = None) -> str:
    try:
        number = float(value)
        if not isfinite(number):
            return "—"
        return f"{number:,.{decimals}f}" if decimals is not None else f"{number:,.8f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "—"


def signal_card(row: dict[str, Any]) -> str:
    action = str(row.get("Signal", "NO TRADE"))
    style = {"LONG": "long", "WATCH CALL": "long", "SHORT": "short", "WATCH PUT": "short", "DATA ERROR": "error"}.get(action, "neutral")
    market = escape(str(row.get("Market", "Unknown")))
    reason = escape(str(row.get("Reason", "")))
    if row.get("Source") == "Deribit":
        currency = escape(str(row.get("Premium currency", "")))
        details = "".join(
            f'<div><span>{label}</span><strong>{display_number(row.get(label), None if label in {"Bid", "Ask"} else 2)}</strong></div>'
            for label in ["Bid", "Ask", "Spread %", "IV %", "Delta", "Open interest"]
        )
        expiry = escape(str(row.get("Expiry UTC", "")))
        return (
            f'<article class="signal-card signal-{style}"><div class="card-top"><strong>{market}</strong>'
            f'<span class="badge {style}">{escape(action)}</span></div>'
            f'<div class="card-price">{display_number(row.get("Price"))} {currency}</div>'
            f'<p>Deribit · Strike ${display_number(row.get("Strike USD"))} · {display_number(row.get("Days to expiry"), 2)} days</p>'
            f'<p>{reason}</p><p>{"CALL ko watch karein; buy approval nahi." if action == "WATCH CALL" else "PUT ko watch karein; buy approval nahi." if action == "WATCH PUT" else "Abhi entry nahi; qualifying setup ka wait karein."}</p>'
            f'<div class="risk-grid">{details}</div><details><summary>Contract details</summary>'
            f'<p>Expiry {expiry}</p><p>Options can lose their full premium.</p></details></article>'
        )
    return (
        f'<article class="signal-card signal-{style}"><div class="card-top"><strong>{market}</strong>'
        f'<span class="badge {style}">{escape(action)}</span></div>'
        f'<div class="card-price">{display_number(row.get("Price"))}<small>RSI {display_number(row.get("RSI"), 1)}</small></div>'
        f'{signal_evidence(row)}</article>'
    )
