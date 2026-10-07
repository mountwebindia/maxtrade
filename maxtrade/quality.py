from __future__ import annotations

import json
from math import isfinite, sqrt

from maxtrade.coindcx import INTERVAL_MS
from maxtrade.signals import _atr, analyze_candles


QUALITY_VERSION = 'trend-breakout-volume-shadow-v1'
HORIZON_MS = 24 * 3600000
MIN_SAMPLES = 30


def shadow_quality(candles: list[dict], interval: str, allow_short: bool = False,
                   higher: list[dict] | None = None, higher_interval: str | None = None) -> dict:
    duration = INTERVAL_MS[interval]
    timestamps = [int(bar['time']) for bar in candles]
    if len(candles) < 50 or any(timestamp % duration for timestamp in timestamps) or any(
            current - previous != duration for previous, current in zip(timestamps, timestamps[1:])):
        raise ValueError('Shadow quality requires aligned contiguous completed candles')
    signal = analyze_candles(candles, allow_short)
    for bar in candles:
        opening, high, low, close = [float(bar[key]) for key in ('open', 'high', 'low', 'close')]
        volume = float(bar['volume'])
        if not all(isfinite(value) and value > 0 for value in (opening, high, low, close)) or not low <= min(opening, close) <= max(opening, close) <= high or not isfinite(volume) or volume < 0:
            raise ValueError('Invalid OHLCV for shadow quality')
    closes = [float(bar['close']) for bar in candles[-21:]]
    travel = sum(abs(current - previous) for previous, current in zip(closes, closes[1:]))
    efficiency = abs(closes[-1] - closes[0]) / travel if travel else 0.0
    atr_pct = _atr(candles) / closes[-1] * 100
    regime = ('HIGH VOLATILITY' if atr_pct > 5 else 'CHOP' if efficiency < 0.25 else
              'UPTREND' if closes[-1] > signal.ema_fast > signal.ema_slow else
              'DOWNTREND' if closes[-1] < signal.ema_fast < signal.ema_slow else 'MIXED')
    prior = candles[-21:-1]
    average_volume = sum(float(bar['volume']) for bar in prior) / 20
    volume_ratio = float(candles[-1]['volume']) / average_volume if average_volume > 0 else None
    long = signal.action == 'LONG'
    breakout = closes[-1] > max(float(bar['high']) for bar in prior) if long else closes[-1] < min(float(bar['low']) for bar in prior)
    blockers = []
    if signal.action not in {'LONG', 'SHORT'}:
        blockers.append('No baseline directional setup')
    if regime != ('UPTREND' if long else 'DOWNTREND'):
        blockers.append('Regime not directionally aligned')
    if not breakout:
        blockers.append('No completed-close prior-20-bar breakout')
    if volume_ratio is None or volume_ratio < 1.2:
        blockers.append('Relative volume below 1.2 or unavailable')
    higher_action = None
    if higher and higher_interval and INTERVAL_MS[higher_interval] > duration:
        decision_ms = timestamps[-1] + duration
        completed = [bar for bar in higher if int(bar['time']) + INTERVAL_MS[higher_interval] <= decision_ms]
        higher_times = [int(bar['time']) for bar in completed]
        if len(completed) >= 50 and decision_ms - (higher_times[-1] + INTERVAL_MS[higher_interval]) < INTERVAL_MS[higher_interval] and all(timestamp % INTERVAL_MS[higher_interval] == 0 for timestamp in higher_times) and all(current - previous == INTERVAL_MS[higher_interval] for previous, current in zip(higher_times, higher_times[1:])):
            higher_action = analyze_candles(completed, allow_short).action
    if higher_action != signal.action or higher_action not in {'LONG', 'SHORT'}:
        blockers.append('Higher-timeframe setup missing or conflicting')
    return {'version': QUALITY_VERSION, 'interval': interval, 'regime': regime,
            'efficiency_20': efficiency, 'atr_pct': atr_pct, 'volume_ratio_20': volume_ratio,
            'higher_interval': higher_interval, 'higher_action': higher_action,
            'baseline_action': signal.action, 'candidate_action': signal.action if not blockers else 'NO TRADE',
            'blockers': blockers, 'mode': 'SHADOW ONLY'}


def _net(record: dict) -> float | None:
    result = json.loads(record.get('result_json') or '{}')
    value = result.get('net_return_pct')
    if record['status'] in {'WIN', 'LOSS', 'EXPIRED'} and result.get('cost_model') == 'fixed-notional-v1' and isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value):
        return float(value)
    return None


def _key(record: dict) -> tuple:
    quality = record.get('quality') or {}
    return (record.get('product'), record.get('pair'), record.get('interval'), record.get('action'),
            quality.get('version'), quality.get('regime'), quality.get('candidate_action'))


def prior_confidence(records: list[dict], current: dict, as_of_ms: int) -> dict:
    quality = current.get('quality') or {}
    if quality.get('version') != QUALITY_VERSION or not quality.get('regime'):
        return {'available': False, 'samples': 0, 'reason': 'Comparable versioned evidence unavailable'}
    selected = []
    next_available = 0
    for record in sorted(records, key=lambda item: item['start_ms']):
        start = record['start_ms']
        value = _net(record)
        result = json.loads(record.get('result_json') or '{}')
        evaluated = result.get('evaluated_at_ms')
        if _key(record) != _key(current) or value is None or start < next_available or start + HORIZON_MS > as_of_ms or not isinstance(evaluated, int) or isinstance(evaluated, bool) or not start <= evaluated <= as_of_ms:
            continue
        selected.append(value)
        next_available = start + HORIZON_MS
    count = len(selected)
    if count < MIN_SAMPLES:
        return {'available': False, 'samples': count, 'reason': f'At least {MIN_SAMPLES} prior non-overlapping outcomes required'}
    rate = sum(value > 0 for value in selected) / count
    z_score = 1.96
    denominator = 1 + z_score ** 2 / count
    center = (rate + z_score ** 2 / (2 * count)) / denominator
    width = z_score * sqrt(rate * (1 - rate) / count + z_score ** 2 / (4 * count ** 2)) / denominator
    return {'available': True, 'samples': count, 'net_win_probability': rate,
            'wilson_95_low': center - width, 'wilson_95_high': center + width,
            'as_of_ms': as_of_ms, 'method': 'prior-comparable-nonoverlapping-v1',
            'limitation': 'Empirical frequency, not certified model probability; regime drift remains possible'}


def forward_validation(records: list[dict], fold_days: int = 30) -> list[dict]:
    if fold_days < 1:
        raise ValueError('Fold length must be positive')
    eligible = sorted((record for record in records if _net(record) is not None and
                       (record.get('quality') or {}).get('version') == QUALITY_VERSION),
                      key=lambda record: record['start_ms'])
    if not eligible:
        return []
    width = fold_days * HORIZON_MS
    origin = eligible[0]['start_ms'] // HORIZON_MS * HORIZON_MS
    folds = {}
    next_available = {}
    for record in eligible:
        series = (record['product'], record['pair'], record.get('interval'))
        if record['start_ms'] < next_available.get(series, 0):
            continue
        next_available[series] = record['start_ms'] + HORIZON_MS
        bucket = (record['start_ms'] - origin) // width
        start, end = origin + bucket * width, origin + (bucket + 1) * width
        if record['start_ms'] + HORIZON_MS > end:
            continue
        row = folds.setdefault(bucket, {'Fold start ms': start, 'Fold end ms': end,
                                       'Baseline samples': 0, 'Candidate samples': 0,
                                       '_baseline': [], '_candidate': [], '_brier': []})
        value = _net(record)
        row['Baseline samples'] += 1
        row['_baseline'].append(value)
        if record['quality'].get('candidate_action') == record['action']:
            row['Candidate samples'] += 1
            row['_candidate'].append(value)
        estimate = prior_confidence(records, record, start)
        if estimate['available']:
            row['_brier'].append((estimate['net_win_probability'] - int(value > 0)) ** 2)
    for row in folds.values():
        for field, label in (('_baseline', 'Baseline'), ('_candidate', 'Candidate')):
            values = row.pop(field)
            row[f'{label} mean net %'] = sum(values) / len(values) if values else None
            row[f'{label} net win %'] = sum(value > 0 for value in values) / len(values) * 100 if values else None
        scores = row.pop('_brier')
        row['Calibration samples'] = len(scores)
        row['Brier score'] = sum(scores) / len(scores) if scores else None
    return list(folds.values())