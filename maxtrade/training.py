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


VERSION = 'daily-logistic-shadow-v1'
FEATURES = ['return_1', 'return_20', 'rsi14_sma', 'atr14_pct',
            'volatility20', 'volume_ratio20', 'trend20_50', 'trend50_200']


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
            'probability_positive_net_return': forecast,
            'calibrated_probability': False, 'weights': weights,
            'feature_closed_at_ms': int(frame.iloc[-1]['available_at_ms']),
            'limitations': ['Fixed model and hyperparameters; no automatic strategy promotion',
                            'Retrospective walk-forward is not untouched prospective evidence',
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
                                    'WHERE product=? AND net_return IS NOT NULL ORDER BY feature_close',
                                    (product,)).fetchall()
        report = {**report, 'weights': 'Stored in immutable model registry',
                  'prospective_samples': len(scored),
                  'prospective_brier': float(brier_score_loss(
                      [net > 0 for probability, net in scored],
                      [probability for probability, net in scored])) if scored else None,
                  'status': 'TRAINED SHADOW', 'promotion_allowed': False}
    registry.chmod(0o600)
    return report