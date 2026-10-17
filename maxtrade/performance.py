from math import isfinite, sqrt

import pandas as pd

from maxtrade.coindcx import INTERVAL_MS


def replay_performance(result: dict, candles: list[dict], interval: str) -> dict:
    duration = INTERVAL_MS[interval]
    equity = result['equity']['Equity'].iloc[50:].copy()
    prices = pd.DataFrame(candles).iloc[50:]
    expected = pd.to_datetime(prices['time'], unit='ms', utc=True)
    if (len(equity) < 2 or not equity.index.equals(pd.DatetimeIndex(expected))
            or not equity.index.to_series().diff().iloc[1:].eq(pd.Timedelta(milliseconds=duration)).all()
            or not all(isfinite(value) and value > 0 for value in equity)):
        raise ValueError('Performance requires positive, finite, contiguous UTC replay equity')
    if not all(isfinite(value) and value > 0 for value in prices[['open', 'close']].to_numpy().flat):
        raise ValueError('Benchmark requires positive finite prices')
    settings = result['settings']
    cost = (settings.fee_bps + settings.slippage_bps) / 10000
    entry = float(prices['open'].iloc[1])
    units = settings.cash / (entry * (1 + cost))
    benchmark = prices['close'].astype(float).copy()
    benchmark.iloc[0] = settings.cash
    benchmark.iloc[1:] = units * prices['close'].iloc[1:]
    benchmark.iloc[-1] = units * float(prices['open'].iloc[-1]) * (1 - cost)
    funding_per_bar = units * entry * settings.funding_bps_8h / 10000 * duration / (8 * 3600000)
    benchmark -= pd.Series([max(0, index - 1) * funding_per_bar
                            for index in range(len(benchmark))], index=benchmark.index)
    if not all(isfinite(value) and value > 0 for value in benchmark):
        raise ValueError('Benchmark equity is nonpositive or non-finite under these cost assumptions')
    benchmark.index = equity.index
    curves = pd.DataFrame({'Strategy': equity, 'Buy and hold': benchmark})
    valuations = list(equity.index + pd.Timedelta(milliseconds=duration))
    valuations[-1] = equity.index[-1]
    curves.index = pd.DatetimeIndex(valuations)
    curves = curves.loc[~curves.index.duplicated(keep='last')]
    curves.index.name = 'Valuation UTC'
    daily_equity = curves.loc[curves.index == curves.index.normalize()]
    daily = daily_equity.pct_change(fill_method=None).iloc[1:]
    monthly_equity = daily_equity.loc[daily_equity.index.day == 1]
    monthly = monthly_equity.pct_change(fill_method=None).iloc[1:] * 100
    monthly.index = (monthly.index - pd.Timedelta(days=1)).strftime('%Y-%m')
    metrics = {}
    for name in curves:
        values = curves[name]
        returns = daily[name]
        sharpe = sortino = None
        if len(returns) >= 30 and all(isfinite(value) for value in returns):
            volatility = float(returns.std(ddof=1))
            downside = float(returns.clip(upper=0).pow(2).mean()) ** 0.5
            if volatility > 1e-12:
                sharpe = float(returns.mean()) / volatility * sqrt(365)
            if downside > 1e-12:
                sortino = float(returns.mean()) / downside * sqrt(365)
        metrics[name] = {
            'net_return_pct': float((values.iloc[-1] / settings.cash - 1) * 100),
            'max_drawdown_pct': float((1 - values / values.cummax()).max() * 100),
            'sharpe': sharpe, 'sortino': sortino,
        }
    pnl = result['trades']['Net PnL']
    if not all(isfinite(value) for value in pnl):
        raise ValueError('Trade outcomes must be finite')
    losses = float(-pnl[pnl < 0].sum())
    return {
        'equity': curves, 'daily_returns': daily, 'monthly_returns_pct': monthly,
        'metrics': metrics, 'complete_days': len(daily), 'warmup_bars': 50,
        'trade_count': len(pnl),
        'expectancy': float(pnl.mean()) if len(pnl) else None,
        'profit_factor': float(pnl[pnl > 0].sum()) / losses if losses else None,
        'excess_return_pp': metrics['Strategy']['net_return_pct'] - metrics['Buy and hold']['net_return_pct'],
    }