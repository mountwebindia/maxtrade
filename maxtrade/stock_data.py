from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from math import isfinite
import re
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from maxtrade.signals import TradeSignal, analyze_candles
from maxtrade.stock_backtest import StockBar


INDEX_SYMBOLS = {
    'NIFTY 50': ('^NSEI', 'NSE'),
    'NIFTY BANK': ('^NSEBANK', 'NSE'),
    'SENSEX': ('^BSESN', 'BSE'),
}
INDIA = ZoneInfo('Asia/Kolkata')
MARKET_INTERVALS = {
    '1m': ('1 minute', ('5d',)),
    '5m': ('5 minutes', ('5d', '1mo')),
    '15m': ('15 minutes', ('1mo', '60d')),
    '30m': ('30 minutes', ('1mo', '60d')),
    '1h': ('1 hour', ('3mo', '6mo', '1y')),
    '1d': ('Daily', ('6mo', '1y', '2y')),
}


@dataclass(frozen=True)
class MarketBar:
    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketDataError(ValueError):
    pass


@dataclass(frozen=True)
class MarketDataset:
    symbol: str
    exchange: str
    is_index: bool
    bars: tuple[StockBar | MarketBar, ...]
    fetched_at: datetime
    interval: str = '1d'

    def stale(self, now: datetime | None = None) -> bool:
        current = (now or datetime.now(timezone.utc)).astimezone(INDIA)
        if self.interval == '1d':
            return (current.date() - self.bars[-1].date).days > 7
        reference = current - timedelta(minutes=15)
        if reference.time() < time(9, 15):
            reference = datetime.combine(reference.date() - timedelta(days=1), time(15, 30), INDIA)
        while reference.weekday() >= 5:
            reference = datetime.combine(reference.date() - timedelta(days=1), time(15, 30), INDIA)
        reference = min(reference, datetime.combine(reference.date(), time(15, 30), INDIA))
        duration = timedelta(minutes=60 if self.interval == '1h' else int(self.interval[:-1]))
        latest = self.bars[-1].date
        completed = min(latest + duration, datetime.combine(latest.date(), time(15, 30), INDIA))
        return reference - completed > duration * 2


def search_market_symbols(query: str, exchange: str) -> list[tuple[str, str]]:
    if exchange not in ('NSE', 'BSE'):
        raise MarketDataError('Choose NSE or BSE for an equity search.')
    query = query.strip()
    if not query or len(query) > 80:
        raise MarketDataError('Enter a symbol or company name of up to 80 characters.')
    suffix = '.NS' if exchange == 'NSE' else '.BO'
    try:
        quotes = yf.Search(query, max_results=25, timeout=10).quotes
    except Exception as error:
        raise MarketDataError('Symbol search is temporarily unavailable. Retry later.') from error
    results = {}
    for quote in quotes:
        symbol = str(quote.get('symbol', ''))
        if quote.get('quoteType') == 'EQUITY' and symbol.endswith(suffix):
            results[symbol] = str(quote.get('shortname') or quote.get('longname') or symbol)
    if not results and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9&.-]{0,31}', query):
        symbol = query.upper()
        if not symbol.endswith(('.NS', '.BO')):
            symbol += suffix
        if symbol.endswith(suffix):
            results[symbol] = symbol
    return list(results.items())


def normalize_market_history(frame: pd.DataFrame, symbol: str, exchange: str,
                             is_index: bool, now: datetime | None = None,
                             interval: str = '1d') -> MarketDataset:
    current = now or datetime.now(timezone.utc)
    if interval not in MARKET_INTERVALS:
        raise MarketDataError('Unsupported candle timeframe.')
    if current.tzinfo is None:
        raise MarketDataError('Market-data fetch time must include a timezone.')
    if frame.empty:
        raise MarketDataError('No history from the provider for this instrument. Try another symbol or retry later.')
    if not {'Open', 'High', 'Low', 'Close'}.issubset(frame.columns):
        raise MarketDataError('Provider returned incomplete OHLC data.')
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise MarketDataError('Provider session timezone is unavailable.')
    bars = []
    intraday = interval != '1d'
    minutes = 60 if interval == '1h' else int(interval[:-1]) if intraday else 0
    for timestamp, record in frame.iterrows():
        local = timestamp.tz_convert(INDIA).to_pydatetime()
        session = local.date()
        candle_time = local if intraday else session
        if bars and candle_time <= bars[-1].date:
            raise MarketDataError('Provider sessions are duplicated or out of order.')
        if session.weekday() >= 5:
            raise MarketDataError('Non-standard session data is unsupported for market signals.')
        close_time = datetime.combine(session, time(15, 30), INDIA)
        if intraday:
            opening_time = datetime.combine(session, time(9, 15), INDIA)
            offset = (local - opening_time).total_seconds()
            if offset < 0 or local >= close_time or offset % (minutes * 60):
                raise MarketDataError('Provider candle is outside regular hours or misaligned.')
            completed = min(local + timedelta(minutes=minutes), close_time) + timedelta(minutes=15)
        else:
            completed = close_time + timedelta(minutes=15)
        if completed > current:
            continue
        prices = [float(record[field]) for field in ('Open', 'High', 'Low', 'Close')]
        if not all(isfinite(value) and value > 0 for value in prices):
            raise MarketDataError('Provider returned invalid prices; gaps are not filled.')
        opening, high, low, close = prices
        if not low <= min(opening, close) <= max(opening, close) <= high:
            raise MarketDataError('Provider returned an invalid OHLC range.')
        volume = float(record.get('Volume', 0))
        if not isfinite(volume) or volume < 0:
            raise MarketDataError('Provider returned invalid volume.')
        bar_type = MarketBar if intraday else StockBar
        bars.append(bar_type(candle_time, opening, high, low, close, volume))
    if len(bars) < 50:
        raise MarketDataError(f'Only {len(bars)} completed candles available; 50 are required for chart signals. Provider coverage for this instrument is insufficient.')
    return MarketDataset(symbol, exchange, is_index, tuple(bars), current, interval)


def fetch_market_history(symbol: str, exchange: str, period: str = '6mo',
                         interval: str = '1d') -> MarketDataset:
    indices = {value[0]: value[1] for value in INDEX_SYMBOLS.values()}
    is_index = symbol in indices
    suffix = '.NS' if exchange == 'NSE' else '.BO'
    if exchange not in ('NSE', 'BSE') or interval not in MARKET_INTERVALS or period not in MARKET_INTERVALS[interval][1]:
        raise MarketDataError('Unsupported market or history range.')
    if (is_index and indices[symbol] != exchange) or (not is_index and not re.fullmatch(r'[A-Z0-9][A-Z0-9&.-]{0,31}', symbol)) or (not is_index and not symbol.endswith(suffix)):
        raise MarketDataError('Symbol does not belong to the selected exchange.')
    try:
        ticker = yf.Ticker(symbol)
        frame = ticker.history(period=period, interval='15m' if interval == '30m' else interval,
                               auto_adjust=True, timeout=15)
        metadata = ticker.get_history_metadata() if not frame.empty else {}
    except Exception as error:
        raise MarketDataError('Market data is temporarily unavailable or rate-limited. Retry later.') from error
    if frame.empty:
        raise MarketDataError('Market data unavailable. The symbol or timeframe may be unsupported or the provider may be rate-limited. Retry later.')
    expected_exchange = 'NSI' if exchange == 'NSE' else 'BSE'
    if metadata.get('currency') != 'INR' or metadata.get('exchangeName') != expected_exchange or metadata.get('instrumentType') != ('INDEX' if is_index else 'EQUITY'):
        raise MarketDataError('Provider identity, exchange or currency could not be verified.')
    if interval == '30m':
        source = normalize_market_history(frame, symbol, exchange, is_index, interval='15m')
        frame = pd.DataFrame([asdict(bar) for bar in source.bars]).set_index('date')
        frame.columns = [column.title() for column in frame.columns]
        groups = frame.resample('30min', origin='start_day', offset='9h15min')
        counts = groups['Close'].count()
        frame = groups.agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'})
        expected = pd.Series([1 if timestamp.time() == time(15, 15) else 2
                              for timestamp in frame.index], index=frame.index)
        frame = frame.loc[counts == expected]
    return normalize_market_history(frame, symbol, exchange, is_index, interval=interval)


def market_analyses(dataset: MarketDataset) -> list[TradeSignal]:
    candles = [asdict(bar) for bar in dataset.bars]
    return [analyze_candles(candles[:end], allow_short=True)
            for end in range(50, len(candles) + 1)]