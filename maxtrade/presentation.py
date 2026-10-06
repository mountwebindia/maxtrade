from html import escape
from math import isfinite
from typing import Any


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
    style = {"LONG": "long", "SHORT": "short", "DATA ERROR": "error"}.get(action, "neutral")
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
            f'<article class="signal-card"><div class="card-top"><strong>{market}</strong>'
            f'<span class="badge neutral">{escape(action)}</span></div>'
            f'<div class="card-price">{display_number(row.get("Price"))} {currency}</div>'
            f'<p>Deribit · Strike ${display_number(row.get("Strike USD"))} · {display_number(row.get("Days to expiry"), 2)} days</p>'
            f'<p>Expiry {expiry}</p><div class="risk-grid">{details}</div><p>{reason}</p></article>'
        )
    levels = "".join(f'<div><span>{label}</span><strong>{display_number(row.get(label))}</strong></div>' for label in ["Entry", "Stop", "Target"])
    return (
        f'<article class="signal-card"><div class="card-top"><strong>{market}</strong>'
        f'<span class="badge {style}">{escape(action)}</span></div>'
        f'<div class="card-price">{display_number(row.get("Price"))}<small>RSI {display_number(row.get("RSI"), 1)}</small></div>'
        f'<div class="risk-grid">{levels}</div><p>{reason}</p></article>'
    )
