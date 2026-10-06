from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from maxtrade.historical import GRANULARITIES, parse_candles, quality_report


FEATURE_VERSION = "daily-causal-v1"
OUTCOME_VERSION = "next-open-close-v1"
POLICY_VERSION = "daily-uptrend-five-day-v1"


def load_dataset(database: Path, product: str, interval: str, start: int,
                 end: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
        saved = connection.execute(
            "SELECT report FROM historical_reports WHERE product=? AND interval=? AND start=? AND end=?",
            (product, interval, start, end)).fetchone()
        if saved is None:
            raise ValueError("No completed ingestion report for this range")
        report = json.loads(saved[0])
        bars = []
        cursor = start
        for page in report["pages"]:
            if page["start"] != cursor or not cursor < page["end"] <= end:
                raise ValueError("Historical page manifest is not contiguous")
            saved_page = connection.execute(
                "SELECT raw, sha256 FROM historical_pages WHERE product=? AND interval=? AND start=? AND end=?",
                (product, interval, page["start"], page["end"])).fetchone()
            if saved_page is None or hashlib.sha256(saved_page[0]).hexdigest() != page["sha256"] or saved_page[1] != page["sha256"]:
                raise ValueError("Historical raw checksum mismatch")
            bars.extend(parse_candles(json.loads(saved_page[0]), cursor, page["end"], GRANULARITIES[interval]))
            cursor = page["end"]
    if cursor != end or quality_report(bars, start, end, GRANULARITIES[interval])["status"] != "COMPLETE":
        raise ValueError("Complete coverage is required for feature research; no gap filling")
    digest = hashlib.sha256(json.dumps(
        {"product": product, "interval": interval, "start": start, "end": end,
         "pages": [(page["start"], page["end"], page["sha256"]) for page in report["pages"]]},
        sort_keys=True).encode()).hexdigest()
    return bars, {"dataset_sha256": digest, "source": report["source"], "product": product,
                  "interval": interval, "start": start, "end": end,
                  "limitations": report["limitations"]}


def validated_frame(bars: list[dict[str, Any]], interval: str) -> pd.DataFrame:
    if interval != "1d":
        raise ValueError("This feature version supports daily research only")
    if not bars:
        raise ValueError("Historical candles are required")
    duration = GRANULARITIES[interval]
    previous = None
    for bar in bars:
        timestamp = bar["time"]
        if isinstance(timestamp, bool) or timestamp % (duration * 1000):
            raise ValueError("Historical timestamps must be aligned milliseconds")
        normalized = parse_candles([[timestamp / 1000, bar["low"], bar["high"],
                                     bar["open"], bar["close"], bar["volume"]]],
                                   timestamp // 1000, timestamp // 1000 + duration, duration)
        if not normalized or (previous is not None and timestamp != previous + duration * 1000):
            raise ValueError("Historical features require ordered contiguous candles")
        previous = timestamp
    return pd.DataFrame(bars).astype({field: float for field in ("open", "high", "low", "close", "volume")})


def causal_features(bars: list[dict[str, Any]], interval: str = "1d") -> pd.DataFrame:
    frame = validated_frame(bars, interval)
    close = frame["close"]
    returns = close.pct_change(fill_method=None)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).rolling(14, min_periods=14).mean()
    rsi = 100 - 100 / (1 + gain / loss.where(loss != 0))
    rsi = rsi.mask((loss == 0) & (gain > 0), 100).mask((gain == 0) & (loss > 0), 0)
    rsi = rsi.mask((gain == 0) & (loss == 0), 50)
    true_range = pd.concat([frame["high"] - frame["low"],
                            (frame["high"] - close.shift()).abs(),
                            (frame["low"] - close.shift()).abs()], axis=1).max(axis=1)
    features = pd.DataFrame({
        "time": frame["time"], "available_at_ms": frame["time"] + GRANULARITIES[interval] * 1000,
        "return_1": returns, "return_20": close.pct_change(20, fill_method=None),
        "ema20": close.ewm(span=20, adjust=False, min_periods=20).mean(),
        "ema50": close.ewm(span=50, adjust=False, min_periods=50).mean(),
        "ema200": close.ewm(span=200, adjust=False, min_periods=200).mean(),
        "rsi14_sma": rsi, "atr14_pct": true_range.rolling(14, min_periods=14).mean() / close,
        "volatility20": returns.rolling(20, min_periods=20).std(ddof=0),
        "volume_ratio20": frame["volume"] / frame["volume"].rolling(20, min_periods=20).mean().replace(0, float("nan")),
    })
    ready = features.drop(columns=["time", "available_at_ms"]).notna().all(axis=1)
    features["ready"] = ready
    features["regime"] = "WARMUP"
    features.loc[ready, "regime"] = "MIXED"
    features.loc[ready & (features["ema20"] > features["ema50"]) &
                 (features["ema50"] > features["ema200"]), "regime"] = "UPTREND"
    features.loc[ready & (features["ema20"] < features["ema50"]) &
                 (features["ema50"] < features["ema200"]), "regime"] = "DOWNTREND"
    return features


def forward_outcomes(bars: list[dict[str, Any]], horizon: int = 5,
                     fee_bps: float = 10, slippage_bps: float = 5) -> pd.DataFrame:
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
        raise ValueError("Outcome horizon must be a positive integer")
    if not all(math.isfinite(value) and 0 <= value < 10000 for value in (fee_bps, slippage_bps)):
        raise ValueError("Invalid outcome costs")
    frame = validated_frame(bars, "1d")
    entry = frame["open"].shift(-1)
    exit_price = frame["close"].shift(-horizon)
    fee, slippage = fee_bps / 10000, slippage_bps / 10000
    return pd.DataFrame({
        "time": frame["time"], "entry_time_ms": frame["time"].shift(-1),
        "matures_at_ms": frame["time"].shift(-horizon) + 86400000,
        "gross_return": exit_price / entry - 1,
        "net_return": exit_price * (1 - slippage) * (1 - fee) / (entry * (1 + slippage) * (1 + fee)) - 1,
    })


def performance(returns: list[float]) -> dict[str, Any]:
    equity = peak = 1.0
    drawdown = 0.0
    for outcome in returns:
        equity *= 1 + outcome
        peak = max(peak, equity)
        drawdown = max(drawdown, 1 - equity / peak)
    wins = sum(value > 0 for value in returns)
    profits = sum(value for value in returns if value > 0)
    losses = -sum(value for value in returns if value < 0)
    return {"trades": len(returns), "net_return_pct": (equity - 1) * 100,
            "win_rate_pct": 100 * wins / len(returns) if returns else None,
            "mean_trade_return_pct": 100 * sum(returns) / len(returns) if returns else None,
            "profit_factor": profits / losses if losses else None,
            "realized_drawdown_pct": drawdown * 100}


def historical_context(database: Path, symbol: str, now: datetime) -> dict[str, Any]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Historical context requires timezone-aware time")
    product = {"B-BTC_USDT": "BTC-USD", "B-ETH_USDT": "ETH-USD",
               "BTC": "BTC-USD", "ETH": "ETH-USD"}.get(symbol)
    if product is None:
        raise ValueError("No historical dataset mapping for this symbol")
    cutoff = int(now.timestamp())
    with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
        candidates = connection.execute(
            "SELECT start, end, report FROM historical_reports WHERE product=? AND interval='1d' AND end<=? ORDER BY end DESC, start ASC LIMIT 20",
            (product, cutoff)).fetchall()
    selected = None
    for start, end, encoded in candidates:
        report = json.loads(encoded)
        if (report["status"] == "COMPLETE" and datetime.fromisoformat(report["generated"]) <= now
                and all(datetime.fromisoformat(page["retrieved"]) <= now for page in report["pages"])):
            selected = (start, end)
            break
    if selected is None:
        raise ValueError("No complete dataset retrieved before this decision")
    bars, manifest = load_dataset(database, product, "1d", *selected)
    if cutoff - manifest["end"] > 2 * 86400:
        raise ValueError("Daily historical context is stale; refresh ingestion")
    features = causal_features(bars)
    latest = features.iloc[-1]
    if not latest["ready"]:
        raise ValueError("Insufficient daily feature warmup")
    labels = forward_outcomes(bars)
    outcomes = []
    next_free = 0
    for index in range(len(bars) - 1):
        if (index < next_free or not features.iloc[index]["ready"]
                or features.iloc[index]["regime"] != latest["regime"]
                or pd.isna(labels.iloc[index]["net_return"])
                or labels.iloc[index]["matures_at_ms"] > latest["available_at_ms"]):
            continue
        outcomes.append(float(labels.iloc[index]["net_return"]))
        next_free = index + 5
    return {"agent": "historical-shadow", "status": "AVAILABLE", "mode": "SHADOW ONLY",
            "dataset": manifest, "feature_version": FEATURE_VERSION, "outcome_version": OUTCOME_VERSION,
            "as_of": now.astimezone(timezone.utc).isoformat(),
            "candle_closed_at_ms": int(latest["available_at_ms"]),
            "regime": latest["regime"],
            "values": {key: float(latest[key]) for key in ("ema20", "ema50", "ema200", "rsi14_sma", "atr14_pct", "volatility20", "volume_ratio20")},
            "prior_same_regime_five_day_outcomes": performance(outcomes),
            "execution_enabled": False, "calibrated_probability": None,
            "limitations": ["Retrospectively downloaded Coinbase USD history, not contemporaneous archived evidence",
                            "Daily context is not CoinDCX hourly execution-policy validation",
                            "Same-regime sample returns are descriptive, not predicted win probabilities",
                            "Five-day next-open long outcomes with 10bps fees and 5bps slippage per side",
                            "Missing hourly coverage remains unresolved; this evidence cannot approve or veto trades"]}


def evaluate(bars: list[dict[str, Any]], manifest: dict[str, Any],
             fee_bps: float = 10, slippage_bps: float = 5) -> dict[str, Any]:
    features = causal_features(bars)
    labels = forward_outcomes(bars, 5, fee_bps, slippage_bps)
    count = len(bars)
    if count < 1000:
        raise ValueError("At least 1,000 contiguous daily candles required for chronological evaluation")
    development_end, test_start = int(count * .6), int(count * .8)
    boundaries = [development_end + (test_start - development_end) * index // 4 for index in range(5)]
    windows = [(f"walk_forward_{index + 1}", boundaries[index], boundaries[index + 1]) for index in range(4)]
    windows.append(("held_out_test", test_start, count))
    results = []
    fee, slippage = fee_bps / 10000, slippage_bps / 10000
    for name, beginning, ending in windows:
        end_time = bars[ending - 1]["time"] + 86400000
        eligible = [index for index in range(beginning, ending)
                    if features.iloc[index]["ready"] and pd.notna(labels.iloc[index]["net_return"])
                    and labels.iloc[index]["matures_at_ms"] <= end_time]
        policies = {}
        for policy in ("UPTREND", "ALWAYS_LONG"):
            outcomes = []
            next_free = 0
            for index in eligible:
                if index < next_free or (policy == "UPTREND" and features.iloc[index]["regime"] != "UPTREND"):
                    continue
                outcomes.append(float(labels.iloc[index]["net_return"]))
                next_free = index + 5
            policies[policy] = performance(outcomes)
        if eligible:
            opening = bars[eligible[0] + 1]["open"]
            closing = bars[ending - 1]["close"]
            buy_hold = closing * (1 - slippage) * (1 - fee) / (opening * (1 + slippage) * (1 + fee)) - 1
            policies["BUY_AND_HOLD"] = {"net_return_pct": buy_hold * 100}
        else:
            policies["BUY_AND_HOLD"] = {"net_return_pct": None}
        results.append({"name": name, "start_ms": bars[beginning]["time"],
                        "end_ms": end_time, "eligible_decisions": len(eligible),
                        "purged_or_unmatured": ending - beginning - len(eligible), "policies": policies})
    return {"dataset": manifest, "feature_version": FEATURE_VERSION, "outcome_version": OUTCOME_VERSION,
            "policy_version": POLICY_VERSION, "fee_bps_per_side": fee_bps,
            "slippage_bps_per_side": slippage_bps, "holding_days": 5,
            "development_bars": development_end, "test_start_index": test_start, "windows": results,
            "execution_enabled": False, "model_trained": False,
            "limitations": ["Fixed research baseline, not the hourly stop/target paper policy",
                            "No fitted model or parameter selection; early 60% is development history only",
                            "Test results are now observed; retuning requires a new untouched holdout",
                            "Long-only, full-notional sequential five-day trades; no leverage or overlapping positions",
                            "Drawdown is closed-trade equity only; intratrade losses and execution liquidity are not modelled",
                            "Immediate next-open entry assumes no data publication or execution delay",
                            "Historical revisions and hindsight design bias remain possible",
                            "No claim of calibrated probabilities, improved accuracy or profitability"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Causal daily shadow research; no trades or AI training")
    parser.add_argument("--product", choices=["BTC-USD", "ETH-USD"], required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--database", type=Path, default=Path("data/historical.sqlite3"))
    arguments = parser.parse_args()
    start, end = [int(datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
                  for value in (arguments.start, arguments.end)]
    bars, manifest = load_dataset(arguments.database, arguments.product, "1d", start, end)
    report = evaluate(bars, manifest)
    encoded = json.dumps(report, allow_nan=False, sort_keys=True)
    identifier = hashlib.sha256(encoded.encode()).hexdigest()
    with sqlite3.connect(arguments.database) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS historical_evaluations (id TEXT PRIMARY KEY, report TEXT NOT NULL)")
        connection.execute("INSERT OR IGNORE INTO historical_evaluations VALUES (?, ?)", (identifier, encoded))
    print(json.dumps({"report_sha256": identifier, **report}, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()