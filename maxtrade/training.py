from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from maxtrade.historical_features import causal_features, forward_outcomes, load_dataset


VERSION = 'daily-logistic-shadow-v2'
FEATURES = ['return_1', 'return_20', 'rsi14_sma', 'atr14_pct',
            'volatility20', 'volume_ratio20', 'trend20_50', 'trend50_200']


def prediction_benchmark(records: list[dict]) -> dict:
    if not records:
        return {'samples': 0, 'brier': None, 'baseline_brier': None,
            'brier_skill': None, 'comparison': 'NO SCORED SAMPLES',
            'reliability': [], 'by_regime': {},
                'threshold': 0.5, 'selected_samples': 0, 'selected_mean_net_return_pct': None,
                'all_mean_net_return_pct': None, 'execution_enabled': False}
    probabilities = np.array([record['probability'] for record in records], dtype=float)
    baselines = np.array([record['baseline'] for record in records], dtype=float)
    returns = np.array([record['net_return'] for record in records], dtype=float)
    if (not all(np.isfinite(values).all() for values in (probabilities, baselines, returns))
            or ((probabilities < 0) | (probabilities > 1)).any()
            or ((baselines < 0) | (baselines > 1)).any()):
        raise ValueError('Benchmark requires finite outcomes and probabilities between zero and one')
    labels = returns > 0
    brier = float(brier_score_loss(labels, probabilities))
    baseline_brier = float(brier_score_loss(labels, baselines))
    reliability = []
    for bin_index in range(5):
        lower, upper = bin_index / 5, (bin_index + 1) / 5
        mask = (probabilities >= lower) & ((probabilities < upper) if bin_index < 4 else (probabilities <= upper))
        reliability.append({'lower': lower, 'upper': upper, 'samples': int(mask.sum()),
                            'mean_probability': float(probabilities[mask].mean()) if mask.any() else None,
                            'positive_rate': float(labels[mask].mean()) if mask.any() else None})
    by_regime = {}
    for regime in sorted({record['regime'] for record in records}):
        mask = np.array([record['regime'] == regime for record in records])
        by_regime[regime] = {'samples': int(mask.sum()),
                             'brier': float(brier_score_loss(labels[mask], probabilities[mask])),
                             'baseline_brier': float(brier_score_loss(labels[mask], baselines[mask]))}
    selected = probabilities >= 0.5
    return {'samples': len(records), 'brier': brier, 'baseline_brier': baseline_brier,
            'brier_skill': 1 - brier / baseline_brier if baseline_brier > 0 else None,
            'comparison': 'LOWER BRIER THAN BASELINE' if brier < baseline_brier else 'NO BRIER IMPROVEMENT',
            'reliability': reliability, 'by_regime': by_regime, 'threshold': 0.5,
            'selected_samples': int(selected.sum()),
            'selected_mean_net_return_pct': float(returns[selected].mean() * 100) if selected.any() else None,
            'all_mean_net_return_pct': float(returns.mean() * 100), 'execution_enabled': False}


def fit_candidate(bars: list[dict], now: datetime) -> tuple[dict, dict]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Training requires timezone-aware time')
    if len(bars) < 1200 or bars[-1]['time'] + 86400000 > now.timestamp() * 1000:
        raise ValueError('At least 1200 completed contiguous daily candles required')
    frame = causal_features(bars)
    frame['trend20_50'] = frame['ema20'] / frame['ema50'] - 1
    frame['trend50_200'] = frame['ema50'] / frame['ema200'] - 1
    outcomes = forward_outcomes(bars, horizon=5)
    returns = outcomes['net_return'].shift(-1)
    maturity = outcomes['matures_at_ms'].shift(-1)
    eligible = [index for index in range(199, len(bars), 5)
                if frame.iloc[index]['ready'] and np.isfinite(returns.iloc[index])
                and maturity.iloc[index] <= now.timestamp() * 1000]
    if len(eligible) < 180:
        raise ValueError('Insufficient mature nonoverlapping training samples')
    matrix = frame[FEATURES].to_numpy(dtype=float)
    labels = (returns > 0).astype(int).to_numpy()
    development = int(len(eligible) * .6)
    boundaries = [development + (len(eligible) - development) * offset // 3
                  for offset in range(4)]
    evaluations = []
    benchmark_records = []
    for beginning, ending in zip(boundaries[:-1], boundaries[1:]):
        test = eligible[beginning:ending]
        cutoff = frame.iloc[test[0]]['available_at_ms']
        train = [index for index in eligible[:beginning] if maturity.iloc[index] < cutoff]
        if len(set(labels[train])) != 2:
            raise ValueError('Training requires both outcome classes')
        model = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=1000,
                                                                    random_state=0))
        model.fit(matrix[train], labels[train])
        probabilities = model.predict_proba(matrix[test])[:, 1]
        baseline = float(labels[train].mean())
        benchmark_records.extend({'probability': float(probability), 'baseline': baseline,
                      'net_return': float(returns.iloc[index]),
                      'regime': str(frame.iloc[index]['regime'])}
                     for index, probability in zip(test, probabilities))
        evaluations.append({'train_samples': len(train), 'test_samples': len(test),
                            'train_last_maturity_ms': int(max(maturity.iloc[train])),
                            'test_start_ms': int(cutoff),
                            'brier': float(brier_score_loss(labels[test], probabilities)),
                            'baseline_brier': float(brier_score_loss(labels[test],
                                                                     [baseline] * len(test)))})
    model.fit(matrix[eligible], labels[eligible])
    scaler = model.named_steps['standardscaler']
    classifier = model.named_steps['logisticregression']
    weights = {'features': FEATURES, 'mean': scaler.mean_.tolist(),
               'scale': scaler.scale_.tolist(), 'coefficient': classifier.coef_[0].tolist(),
               'intercept': float(classifier.intercept_[0])}
    forecast = float(model.predict_proba(matrix[-1:])[0, 1])
    return {'version': VERSION, 'mode': 'SHADOW ONLY', 'execution_enabled': False,
            'training_samples': len(eligible), 'walk_forward': evaluations,
            'out_of_sample_benchmark': prediction_benchmark(benchmark_records),
            'probability_positive_net_return': forecast,
            'calibrated_probability': False, 'weights': weights,
            'feature_closed_at_ms': int(frame.iloc[-1]['available_at_ms']),
            'limitations': ['Fixed model and hyperparameters; no automatic strategy promotion',
                            'Retrospective walk-forward is not untouched prospective evidence',
                            'Reliability bins are diagnostics, not proof of calibrated probabilities',
                            'Fixed 0.5 threshold is not optimized; mean outcomes are not portfolio returns',
                            'Coinbase USD daily outcomes do not certify CoinDCX hourly trades',
                            'Five-day long outcome, one-day entry delay, 10bps fees and 5bps slippage per side',
                            'No Azure model weight updates or guaranteed improvement']}, {
                                'returns': returns, 'maturity': maturity}


def train_shadow(database: Path, registry: Path, product: str, start: int,
                 end: int, now: datetime) -> dict:
    if now.tzinfo is None or now.utcoffset() is None or end > now.timestamp():
        raise ValueError('Training cutoff must precede timezone-aware decision time')
    if not 0 <= now.timestamp() - end < 86400:
        raise ValueError('Training requires the latest completed UTC day')
    bars, manifest = load_dataset(database, product, '1d', start, end)
    with sqlite3.connect(f'{database.resolve().as_uri()}?mode=ro', uri=True) as source:
        encoded = source.execute('SELECT report FROM historical_reports WHERE product=? '
                                 'AND interval=? AND start=? AND end=?',
                                 (product, '1d', start, end)).fetchone()[0]
    saved = json.loads(encoded)
    if datetime.fromisoformat(saved['generated']) > now or any(
            datetime.fromisoformat(page['retrieved']) > now for page in saved['pages']):
        raise ValueError('Training evidence was retrieved after the decision')
    identity = hashlib.sha256((VERSION + product + manifest['dataset_sha256']).encode()).hexdigest()
    registry.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(registry, timeout=30) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS models '
                           '(id TEXT PRIMARY KEY, product TEXT, created_at TEXT, report TEXT)')
        connection.execute('CREATE TABLE IF NOT EXISTS forecasts '
                           '(product TEXT, feature_close INTEGER, model_id TEXT, recorded_at TEXT, '
                           'entry_ms INTEGER, maturity_ms INTEGER, probability REAL, net_return REAL, '
                           'PRIMARY KEY(product, feature_close))')
        existing = connection.execute('SELECT report FROM models WHERE id=?', (identity,)).fetchone()
        if existing:
            report = json.loads(existing[0])
            outcomes = forward_outcomes(bars, horizon=5)
            outcome_data = {'returns': outcomes['net_return'].shift(-1),
                            'maturity': outcomes['matures_at_ms'].shift(-1)}
        else:
            report, outcome_data = fit_candidate(bars, now)
            report.update(model_id=identity, product=product, dataset=manifest,
                          trained_at=now.astimezone(timezone.utc).isoformat())
            connection.execute('INSERT INTO models VALUES (?,?,?,?)',
                               (identity, product, report['trained_at'],
                                json.dumps(report, allow_nan=False)))
        for feature_close, maturity_ms in connection.execute(
                'SELECT feature_close,maturity_ms FROM forecasts WHERE product=? '
                'AND net_return IS NULL AND maturity_ms<=?', (product, now.timestamp() * 1000)).fetchall():
            index = next((offset for offset, bar in enumerate(bars)
                          if bar['time'] + 86400000 == feature_close), None)
            if index is not None and np.isfinite(outcome_data['returns'].iloc[index]):
                if int(outcome_data['maturity'].iloc[index]) != maturity_ms:
                    raise ValueError('Forecast maturity mismatch')
                connection.execute('UPDATE forecasts SET net_return=? WHERE product=? AND feature_close=?',
                                   (float(outcome_data['returns'].iloc[index]), product, feature_close))
        feature_close = report['feature_closed_at_ms']
        connection.execute('INSERT OR IGNORE INTO forecasts VALUES (?,?,?,?,?,?,?,NULL)',
                           (product, feature_close, identity, now.isoformat(), feature_close + 86400000,
                            feature_close + 6 * 86400000, report['probability_positive_net_return']))
        scored = connection.execute('SELECT probability,net_return FROM forecasts '
                        'WHERE product=? AND net_return IS NOT NULL AND maturity_ms<=? '
                                    'AND julianday(recorded_at)<=julianday(?) ORDER BY feature_close',
                        (product, now.timestamp() * 1000, now.isoformat())).fetchall()
        report = {**report, 'weights': 'Stored in immutable model registry',
                  'prospective_samples': len(scored),
                  'prospective_brier': float(brier_score_loss(
                      [net > 0 for probability, net in scored],
                      [probability for probability, net in scored])) if scored else None,
                  'prospective_limitations': ['Daily forecasts have overlapping five-day outcomes; samples are not independent',
                                             'Prospective scores do not permit automatic strategy promotion'],
                  'status': 'TRAINED SHADOW', 'promotion_allowed': False}
    registry.chmod(0o600)
    return report