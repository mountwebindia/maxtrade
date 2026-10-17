from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
from io import BytesIO
from math import floor, isfinite, sqrt
import json
import re
from typing import Any

from backtesting import Backtest, Strategy
import pandas as pd
import pandas_market_calendars as calendars

from maxtrade.signals import TradeSignal, analyze_candles


@dataclass(frozen=True)
class StockBar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class StockDataset:
    symbol: str
    source: str
    bars: tuple[StockBar, ...]
    imported_at: str
    price_basis: str = "split_bonus_adjusted"

    def manifest(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "exchange": "NSE", "currency": "INR",
                "source": self.source, "price_basis": self.price_basis,
                "bars": [{**asdict(bar), "date": bar.date.isoformat()} for bar in self.bars]}

    @property
    def checksum(self) -> str:
        return sha256(json.dumps(self.manifest(), sort_keys=True, allow_nan=False).encode()).hexdigest()


def stock_schedule(start: date, end: date) -> pd.DataFrame:
    calendar = calendars.get_calendar("NSE")
    holiday_years = {holiday.year for holiday in calendar.adhoc_holidays}
    if not set(range(pd.Timestamp(start).year, pd.Timestamp(end).year + 1)).issubset(holiday_years):
        raise ValueError("NSE calendar holiday coverage is unavailable for the requested year; update the calendar dependency")
    return calendar.schedule(start_date=start, end_date=end)


def load_stock_csv(content: bytes, symbol: str, source: str,
                   now: datetime | None = None) -> StockDataset:
    symbol, source = symbol.strip().upper(), source.strip()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9&.-]{0,31}", symbol):
        raise ValueError("Enter one NSE symbol, such as RELIANCE or M&M")
    if not source or len(source) > 200:
        raise ValueError("A data source description of up to 200 characters is required")
    if len(content) > 5_000_000:
        raise ValueError("CSV exceeds the 5 MB research limit")
    frame = pd.read_csv(BytesIO(content), dtype=str)
    frame.columns = frame.columns.str.strip().str.lower()
    required = {"date", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns) or frame.columns.duplicated().any():
        raise ValueError("CSV requires unique date, open, high, low, close, volume columns")
    if not 52 <= len(frame) <= 5000:
        raise ValueError("Import between 52 and 5,000 completed daily sessions")
    if "symbol" in frame and not frame["symbol"].str.strip().str.upper().eq(symbol).all():
        raise ValueError("CSV contains another symbol; multi-stock files are not supported")
    bars = []
    for record in frame.to_dict("records"):
        value = str(record["date"])
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Session dates must use YYYY-MM-DD, not UTC timestamps")
        session = date.fromisoformat(value)
        prices = [float(record[field]) for field in ("open", "high", "low", "close")]
        volume = float(record["volume"])
        if not all(isfinite(price) and price > 0 for price in prices):
            raise ValueError("OHLC prices must be positive and finite")
        opening, high, low, close = prices
        if not low <= min(opening, close) <= max(opening, close) <= high:
            raise ValueError("Invalid daily OHLC range")
        if not isfinite(volume) or volume <= 0:
            raise ValueError("Each equity session must have positive traded volume; no-print bars are not filled")
        if bars and session <= bars[-1].date:
            raise ValueError("Session dates must be unique and ascending")
        bars.append(StockBar(session, opening, high, low, close, volume))
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Import time must have a timezone")
    if bars[-1].date > current.date() or (bars[-1].date - bars[0].date).days > 10_000:
        raise ValueError("Future sessions or overly long date ranges are not supported")
    schedule = stock_schedule(bars[0].date, bars[-1].date)
    expected = [timestamp.date() for timestamp in schedule.index]
    actual = [bar.date for bar in bars]
    if actual != expected:
        raise ValueError("CSV has missing NSE sessions or includes non-trading days; gaps are not filled")
    if pd.Timestamp(current) < schedule["market_close"].iloc[-1]:
        raise ValueError("Latest session is not completed; remove the unfinished daily candle")
    return StockDataset(symbol, source, tuple(bars), current.isoformat())


@dataclass(frozen=True)
class StockReplaySettings:
    cash: float = 100_000
    risk_pct: float = 1
    allocation_pct: float = 25
    fee_bps: float = 12
    slippage_bps: float = 5
    fixed_fee: float = 16
    drawdown_halt_pct: float = 20

    def validate(self) -> None:
        if not all(isfinite(value) for value in asdict(self).values()):
            raise ValueError("Replay settings must be finite")
        if self.cash <= 0 or not 0 < self.risk_pct <= 5 or not 0 < self.allocation_pct <= 95:
            raise ValueError("Use positive INR capital, risk up to 5%, allocation up to 95%")
        if not 0 < self.drawdown_halt_pct <= 50:
            raise ValueError("Drawdown entry halt must be between 0 and 50 percent")
        if not 0 <= self.fee_bps <= 100 or not 0 <= self.slippage_bps <= 100 or not 0 <= self.fixed_fee <= 500:
            raise ValueError("Use costs up to 100 bps per side and fixed fees up to INR 500")


def stock_analyses(dataset: StockDataset, allow_short: bool = False) -> list[TradeSignal]:
    candles = [asdict(bar) for bar in dataset.bars]
    return [analyze_candles(candles[:end], allow_short=allow_short)
            for end in range(50, len(candles) + 1)]


def stock_signal_label(action: str, options: bool = False) -> str:
    labels = {"LONG": "CALL BUY", "SHORT": "PUT BUY"} if options else {"LONG": "BUY", "SHORT": "SELL"}
    return labels.get(action, "WAIT")


def stock_replay(dataset: StockDataset, settings: StockReplaySettings = StockReplaySettings(),
                 evaluation_start: date | None = None) -> dict[str, Any]:
    settings.validate()
    if len(dataset.bars) < 52:
        raise ValueError("At least 52 completed sessions are required")
    first = 49 if evaluation_start is None else next(
        (index for index, bar in enumerate(dataset.bars) if bar.date >= evaluation_start), len(dataset.bars))
    if first < 49 or first > len(dataset.bars) - 3:
        raise ValueError("Evaluation needs 49 prior warm-up sessions and at least three evaluation sessions")
    analyses = stock_analyses(dataset)
    frame = pd.DataFrame([asdict(bar) for bar in dataset.bars]).rename(columns={
        "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    frame.index = pd.to_datetime(frame.pop("date"))
    frame.loc[frame.index[-1], ["High", "Low", "Close"]] = frame["Open"].iloc[-1]
    cost = (settings.fee_bps + settings.slippage_bps) / 10_000

    class DailyStockStrategy(Strategy):
        def init(self):
            self.peak_equity = settings.cash
            self.entry_halted = False

        def next(self):
            index = len(self.data) - 1
            self.peak_equity = max(self.peak_equity, self.equity)
            if self.equity <= self.peak_equity * (1 - settings.drawdown_halt_pct / 100):
                self.entry_halted = True
            if index < first or index >= len(dataset.bars) - 1 or self.entry_halted or self.position or self.orders:
                return
            signal = analyses[index - 49]
            if signal.action != "LONG" or (index > first and analyses[index - 50].action == "LONG"):
                return
            if signal.entry is None or signal.stop is None or signal.target is None:
                return
            if not 0 < signal.stop < signal.entry < signal.target:
                return
            risk_budget = self.equity * settings.risk_pct / 100 - 2 * settings.fixed_fee
            risk_size = floor(risk_budget / (signal.entry - signal.stop + 2 * signal.entry * cost))
            allocation_size = floor((self.equity * settings.allocation_pct / 100 - settings.fixed_fee)
                                    / (signal.entry * (1 + cost)))
            quantity = min(risk_size, allocation_size)
            if quantity > 0:
                self.buy(size=quantity, sl=signal.stop, tp=signal.target, tag="LONG")

    stats = Backtest(frame, DailyStockStrategy, cash=settings.cash,
                     commission=(settings.fixed_fee, cost), margin=1, trade_on_close=False,
                     exclusive_orders=True, finalize_trades=True).run()
    trades = stats["_trades"].copy()
    trades["Net PnL"] = trades["PnL"]
    strategy = stats["_equity_curve"]["Equity"].iloc[first:].copy()
    entry_index = first + 1
    entry_price = dataset.bars[entry_index].open
    quantity = max(0, floor((settings.cash - settings.fixed_fee) / (entry_price * (1 + cost))))
    benchmark = pd.Series(settings.cash, index=strategy.index, dtype=float)
    if quantity:
        balance = settings.cash - quantity * entry_price * (1 + cost) - settings.fixed_fee
        benchmark.iloc[1:] = [balance + quantity * bar.close for bar in dataset.bars[entry_index:]]
        final_price = dataset.bars[-1].open
        benchmark.iloc[-1] = balance + quantity * final_price * (1 - cost) - settings.fixed_fee
    equity = pd.DataFrame({"Strategy": strategy, "Buy and hold": benchmark})
    schedule = stock_schedule(dataset.bars[first].date, dataset.bars[-1].date)
    valuation_times = list(schedule["market_close"])
    valuation_times[-1] = schedule["market_open"].iloc[-1]
    equity.index = pd.DatetimeIndex(valuation_times, name="Valuation UTC")
    metrics = {}
    for name in equity:
        values = equity[name]
        returns = values.iloc[:-1].pct_change().dropna()
        deviation = float(returns.std(ddof=1)) if len(returns) > 1 else 0
        downside = float((returns.clip(upper=0).pow(2).mean()) ** .5) if len(returns) else 0
        metrics[name] = {
            "net_return_pct": float((values.iloc[-1] / settings.cash - 1) * 100),
            "max_drawdown_pct": float(((1 - values / values.cummax()) * 100).max()),
            "sharpe": float(returns.mean() / deviation * sqrt(252)) if len(returns) >= 30 and deviation > 1e-12 else None,
            "sortino": float(returns.mean() / downside * sqrt(252)) if len(returns) >= 30 and downside > 1e-12 else None,
        }
    net = trades["Net PnL"]
    losses = float(-net[net < 0].sum())
    return {"equity": equity, "trades": trades, "metrics": metrics, "settings": settings,
            "dataset_sha256": dataset.checksum, "evaluation_start": dataset.bars[first].date.isoformat(),
            "warmup_sessions": first, "complete_return_sessions": max(0, len(equity) - 2),
            "benchmark_shares": quantity, "expectancy": float(net.mean()) if len(net) else None,
            "profit_factor": float(net[net > 0].sum()) / losses if losses else None,
            "win_rate": float((net > 0).mean() * 100) if len(net) else None,
            "entry_halted": bool(stats["_strategy"].entry_halted)}


def stock_report(dataset: StockDataset, result: dict[str, Any]) -> str:
    payload = {
        "version": "nse-daily-research-v1", "execution_enabled": False,
        "dataset": dataset.manifest(), "dataset_sha256": dataset.checksum,
        "imported_at": dataset.imported_at, "settings": asdict(result["settings"]),
        "evaluation_start": result["evaluation_start"], "metrics": result["metrics"],
        "warmup_sessions": result["warmup_sessions"],
        "complete_return_sessions": result["complete_return_sessions"],
        "benchmark_shares": result["benchmark_shares"], "entry_halted": result["entry_halted"],
        "expectancy_inr": result["expectancy"], "profit_factor": result["profit_factor"],
        "win_rate_pct": result["win_rate"],
        "trades": json.loads(result["trades"].to_json(orient="records", date_format="iso")),
        "equity": json.loads(result["equity"].reset_index().to_json(orient="records", date_format="iso")),
        "assumptions": [
            "Daily NSE long-only cash-equity research; no live or PAPER orders.",
            "BUY/SELL and CALL BUY/PUT BUY displays are underlying directional research, not trade approval.",
            "SELL is bearish bias, not a delivery short order. No option contract, strike, expiry or premium levels are available.",
            "User attests consistent split/bonus-adjusted OHLC and compatible volume; provider basis not independently verified.",
            "Price returns exclude dividends; rights/demergers require separately corrected data.",
            "NSE calendar supplied by pandas-market-calendars; special sessions may require calendar updates.",
            "Signals use completed-session prefixes; market orders fill next open in whole shares.",
            "Risk and allocation budgets size from signal close, not unknown next open; gaps can exceed budgets.",
            "Fees plus slippage are symmetric per-side commission estimates, not actual spread or statutory reconciliation.",
            "Fixed fee applies per filled order per side, including forced final liquidation.",
            "Stops are not guaranteed; daily ambiguous OHLC paths follow Backtesting.py conservative execution.",
            "Final execution bar uses open-only OHLC; positions liquidate at final session open and that partial-session observation is excluded from ratios.",
            "Buy-and-hold uses the same stock/window/costs, fully allocated whole shares and residual cash.",
            "Sharpe/Sortino use 252 sessions, zero risk-free rate, minimum 30 completed session returns.",
            "Holdout changes evaluation start only; no parameter selection, portfolio or walk-forward optimization.",
        ],
    }
    return json.dumps(payload, indent=2, allow_nan=False)