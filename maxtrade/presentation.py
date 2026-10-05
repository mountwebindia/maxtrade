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
    levels = "".join(f'<div><span>{label}</span><strong>{display_number(row.get(label))}</strong></div>' for label in ["Entry", "Stop", "Target"])
    return (
        f'<article class="signal-card"><div class="card-top"><strong>{market}</strong>'
        f'<span class="badge {style}">{escape(action)}</span></div>'
        f'<div class="card-price">{display_number(row.get("Price"))}<small>RSI {display_number(row.get("RSI"), 1)}</small></div>'
        f'<div class="risk-grid">{levels}</div><p>{reason}</p></article>'
    )
