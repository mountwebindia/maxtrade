from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from math import isfinite
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree


NEWS_URL = "https://www.coindesk.com/arc/outboundfeeds/rss/"
DERIBIT_URL = "https://www.deribit.com/api/v2/public/"


def news_evidence(content: bytes, now: datetime, source: str = NEWS_URL) -> dict[str, Any]:
    if now.utcoffset() is None or len(content) > 2_000_000:
        raise ValueError("Invalid news time or oversized feed")
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError("Feed declarations are not permitted")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise ValueError("Invalid news XML") from error
    items = []
    seen = set()
    for item in root.findall("./channel/item")[:100]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = item.findtext("pubDate")
        if not title or len(title) > 500 or not published or link in seen:
            continue
        parsed = urlparse(link)
        if parsed.scheme != "https" or parsed.hostname not in {"coindesk.com", "www.coindesk.com"}:
            continue
        try:
            event = parsedate_to_datetime(published)
        except (TypeError, ValueError, OverflowError):
            continue
        if event.utcoffset() is None or not now - timedelta(hours=24) <= event <= now:
            continue
        seen.add(link)
        items.append({"title": title, "url": link, "event_time": event.astimezone(timezone.utc).isoformat()})
    if not items:
        raise ValueError("No valid news items from the past 24 hours")
    return {"agent": "news-events", "source": source, "retrieved_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=30)).isoformat(), "items": items[:20],
            "scope": "Single-provider crypto headlines; not exhaustive macro/event coverage",
            "event_clearance": False}


def fetch_news(session: Any, now: datetime | None = None) -> dict[str, Any]:
    response = session.get(NEWS_URL, timeout=12, stream=True)
    try:
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_content(chunk_size=16384):
            content.extend(chunk)
            if len(content) > 2_000_000:
                raise ValueError("News feed exceeds size limit")
        return news_evidence(bytes(content), now or datetime.now(timezone.utc))
    finally:
        response.close()


def derivatives_evidence(payload: dict[str, Any], currency: str, now: datetime) -> dict[str, Any]:
    if currency not in {"BTC", "ETH"} or now.utcoffset() is None or payload.get("error"):
        raise ValueError("Unsupported currency or invalid derivatives response")
    item = payload["result"]
    if item["instrument_name"] != f"{currency}-PERPETUAL" or item.get("state") != "open":
        raise ValueError("Unexpected instrument or closed derivatives book")
    event = datetime.fromtimestamp(int(item["timestamp"]) / 1000, timezone.utc)
    if not now - timedelta(minutes=2) <= event <= now:
        raise ValueError("Stale/future derivatives book")
    values = {name: float(item[name]) for name in
              ("best_bid_price", "best_ask_price", "mark_price", "open_interest", "funding_8h")}
    if not all(isfinite(value) for value in values.values()):
        raise ValueError("Non-finite derivatives data")
    bid, ask = values["best_bid_price"], values["best_ask_price"]
    if not 0 < bid <= ask or values["mark_price"] <= 0 or values["open_interest"] <= 0:
        raise ValueError("Missing/crossed derivatives quotes or open interest")
    spread = (ask - bid) / ((ask + bid) / 2) * 100
    return {"agent": "derivatives", "source": DERIBIT_URL + "get_order_book", "venue": "Deribit",
            "instrument": item["instrument_name"], "event_time": event.isoformat(),
            "retrieved_at": now.isoformat(), "expires_at": (event + timedelta(minutes=2)).isoformat(),
            "values": dict(values, spread_percent=spread), "price_unit": "USD",
            "open_interest_unit": "USD for inverse perpetual", "funding_unit": "8-hour decimal rate",
            "liquid": spread <= 0.1,
            "scope": "Deribit inverse perpetual context, not CoinDCX execution liquidity or option premiums"}


def fetch_derivatives(session: Any, symbol: str, now: datetime | None = None) -> dict[str, Any]:
    currency = symbol.removeprefix("B-").split("_")[0]
    if currency not in {"BTC", "ETH"}:
        raise ValueError("Verified derivatives coverage currently supports BTC and ETH only")
    response = session.get(DERIBIT_URL + "get_order_book",
                           params={"instrument_name": f"{currency}-PERPETUAL", "depth": 1}, timeout=12)
    response.raise_for_status()
    return derivatives_evidence(response.json(), currency, now or datetime.now(timezone.utc))