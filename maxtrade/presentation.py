from html import escape
from math import isfinite
from typing import Any


def render_market_list(rows: list[dict[str, Any]], key: str) -> None:
    import streamlit as st

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
        .st-key-{key}_list_controls [data-testid="stColumn"]:last-child {{flex:0 0 100px !important;}}
        </style>''')
    with st.container(key=f'{key}_list_controls'):
        controls = st.columns([3, 1])
    query = controls[0].text_input('Search markets', key=f'{key}_search', placeholder='Search markets', label_visibility='collapsed')
    only_saved = controls[1].toggle('Starred', key=f'{key}_starred')
    favorites = set(st.session_state.get('market_favorites', []))
    visible = [row for row in rows if query.casefold() in str(row.get('Market', '')).casefold()
               and (not only_saved or row.get('Market') in favorites)]
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
    levels = "".join(f'<div><span>{label}</span><strong>{display_number(row.get(label))}</strong></div>' for label in ["Entry", "Stop", "Target"])
    return (
        f'<article class="signal-card signal-{style}"><div class="card-top"><strong>{market}</strong>'
        f'<span class="badge {style}">{escape(action)}</span></div>'
        f'<div class="card-price">{display_number(row.get("Price"))}<small>RSI {display_number(row.get("RSI"), 1)}</small></div>'
        f'<div class="risk-grid">{levels}</div><details><summary>Signal details</summary><p>{reason}</p></details></article>'
    )
