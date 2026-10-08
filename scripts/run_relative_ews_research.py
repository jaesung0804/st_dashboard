"""Explicit research training; archived v1 models and forecasts are never modified."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from importlib.metadata import version as package_version

import numpy as np
import pandas as pd
from scipy.special import expit, logit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ai_stock_assistant import monthly_ews as live, relative_ews as relative
from ai_stock_assistant.data import price_quality
from research_backend_client import Client, safe_path
from unpack_dashboard_state import restore_state

FOLDS = ['2024-10-01', '2025-04-01', '2025-10-01', '2026-04-01']


def restore_inputs(root):
    client = Client(project='investment')
    sid = client.json('GET', '/snapshot-heads/pipeline-state')['snapshot_id']
    entries = {e['relative_path']: e for e in client.snapshot_entries(sid)}
    packed = root / 'packed'

    def download(path):
        e = entries[path]
        client.download(e['sha256'], safe_path(packed, path), e['byte_size'])

    download('state-manifest.json')
    manifest = json.loads((packed / 'state-manifest.json').read_text())
    selected = {p: info for p, info in manifest.items() if
                p in ['data/raw/' + f for f in [*live.PRICE_FILES.values(), *live.LISTING_FILES.values()]]
                or p == 'data/dashboard_research'}
    needed = {p for p, info in selected.items() if info['type'] == 'file'}
    needed.update(part for info in selected.values() for part in info.get('parts', []))
    for path in sorted(needed):
        download(path)
    (packed / 'state-manifest.json').write_text(json.dumps(selected))
    restored = root / 'restored'
    restore_state(packed, restored)
    return restored, {'snapshot_id': sid, 'files': selected}


def weights(frame):
    value = 1 / frame.groupby('date').date.transform('size').to_numpy(float)
    return value / value.mean()


def fit_bundle(train, cal):
    import lightgbm as lgb
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    if len(train) > 180_000:
        train = train.sample(180_000, random_state=804).sort_values(['date', 'ticker'])
    x = train[relative.FEATURES]
    w = weights(train)
    params = dict(n_estimators=180, learning_rate=.035, num_leaves=15, max_depth=5,
                  min_child_samples=150, reg_lambda=10, colsample_bytree=.85, max_bin=63,
                  random_state=804, n_jobs=2, verbosity=-1, deterministic=True, force_col_wise=True)
    bundle, card = {'heads': {}, 'quantiles': {}}, {'heads': {}, 'features': relative.FEATURES,
        'train_rows': len(train), 'train_first': str(train.date.min().date()),
        'train_last': str(train.date.max().date()), 'train_label_end': str(train.end_down.max().date()),
        'cal_first': str(cal.date.min().date()), 'cal_last': str(cal.date.max().date()),
        'cal_label_end': str(cal.end_down.max().date()),
        'train_baseline_log_return': float(np.log1p(train.future_return).median())}
    for head in ('up', 'down'):
        y = train['y_' + head].astype(int)
        if y.nunique() != 2 or cal['y_' + head].nunique() != 2:
            raise ValueError('Both classes required for training and calibration')
        linear = make_pipeline(SimpleImputer(strategy='median', keep_empty_features=True), StandardScaler(),
                               LogisticRegression(C=.1, max_iter=600, random_state=804))
        linear.fit(x, y, logisticregression__sample_weight=w)
        tree = lgb.LGBMClassifier(**params).fit(x, y, sample_weight=w)
        raw = .5 * linear.predict_proba(cal[relative.FEATURES])[:, 1] + .5 * tree.predict_proba(cal[relative.FEATURES])[:, 1]
        platt = LogisticRegression(C=1, max_iter=300).fit(logit(np.clip(raw, 1e-6, 1-1e-6))[:, None],
                                                       cal['y_' + head].astype(int), sample_weight=weights(cal))
        slope, bias = float(platt.coef_[0, 0]), float(platt.intercept_[0])
        accepted = slope > 0
        calibration = {'coef': slope if accepted else 1., 'intercept': bias if accepted else 0., 'weight': 1. if accepted else 0.}
        bundle['heads'][head] = {'linear': linear, 'tree': tree, 'calibration': calibration}
        card['heads'][head] = {'calibration': calibration, 'calibration_accepted': accepted,
            'original_calibration_slope': slope,
            'train_prevalence': float(np.average(y, weights=w)),
            'coefficients_per_sd': dict(zip(relative.FEATURES, linear[-1].coef_[0].tolist())),
            'intercept': float(linear[-1].intercept_[0]),
            'calibration_diagnostics_not_test': live.metrics(cal['y_' + head], live.calibrated(raw, calibration))}
    for q in (.1, .5, .9):
        model = lgb.LGBMRegressor(**params, objective='quantile', alpha=q)
        model.fit(x, np.log1p(train.future_return), sample_weight=w)
        bundle['quantiles'][q] = model
    return bundle, card


def predict(bundle, frame):
    result = {}
    for head, model in bundle['heads'].items():
        x = frame[relative.FEATURES]
        raw = .5 * model['linear'].predict_proba(x)[:, 1] + .5 * model['tree'].predict_proba(x)[:, 1]
        result[head] = live.calibrated(raw, model['calibration'])
    raw = np.column_stack([bundle['quantiles'][q].predict(frame[relative.FEATURES]) for q in (.1, .5, .9)])
    # Monotonic rearrangement is explicit; report how often independently fitted
    # quantiles cross rather than returning an impossible downside interval.
    result['crossed'] = (np.diff(raw, axis=1) < 0).any(axis=1)
    ordered = np.expm1(np.sort(raw, axis=1))
    result.update(q10=ordered[:, 0], q50=ordered[:, 1], q90=ordered[:, 2])
    return result


def evaluate(frame, prediction, card):
    result = {'rows': len(frame), 'first': str(frame.date.min().date()), 'last': str(frame.date.max().date())}
    for head in ('down', 'up'):
        y, p = frame['y_' + head], prediction[head]
        result[head] = {'pooled': live.metrics(y, p), 'within_date': live.dated_metrics(frame.date, y, p),
                        'training_constant_brier': float(np.mean((y - card['heads'][head]['train_prevalence']) ** 2))}
    actual = frame.future_return.to_numpy()
    baseline = np.expm1(card['train_baseline_log_return'])
    result['return_forecast'] = {'median_absolute_error': float(np.median(np.abs(actual - prediction['q50']))),
        'constant_median_absolute_error': float(np.median(np.abs(actual - baseline))),
        'q10_observed_fraction': float(np.mean(actual <= prediction['q10'])),
        'q90_observed_fraction': float(np.mean(actual <= prediction['q90'])),
        'quantile_crossing_fraction': float(prediction['crossed'].mean())}
    return result


def finite(value):
    return float(value) if np.isfinite(value) else None


def run_market(restored, output, market):
    import joblib
    path = restored / 'data/raw' / live.PRICE_FILES[market]
    prices = live.read_prices(path)
    prices, quality = price_quality.prepare(prices, market)
    print(f'{market}: preparing causal feature panel ({len(prices)} prices)', flush=True)
    panel = relative.attach_targets(live.feature_panel(prices, market, training=True), prices)
    dest = output / market
    dest.mkdir(parents=True)
    # Input features/targets are retained outside Git to reproduce this exact run.
    panel.to_csv(dest / 'research_panel.csv.gz', index=False)
    diagnostics = {'quality': quality, 'folds': [], 'feature_panel_sha256': live.digest(dest / 'research_panel.csv.gz'),
        'matured_dates': int(panel.loc[panel.end_down.notna(), 'date'].nunique()),
        'relative_label_dates': int(panel.loc[panel.y_down.notna(), 'date'].nunique()),
        'coverage_rejected_dates': int(panel.loc[panel.benchmark_coverage.lt(.95), 'date'].nunique())}
    for boundary in FOLDS:
        train, cal = relative.purged_split(panel, boundary)
        end = pd.Timestamp(boundary) + pd.DateOffset(months=3)
        test = panel.loc[(panel.date >= boundary) & (panel.date < end) & panel.y_down.notna() & panel.y_up.notna()]
        if test.empty:
            raise ValueError(f'No independent holdout outcomes at {boundary}')
        bundle, card = fit_bundle(train, cal)
        predicted = predict(bundle, test)
        diagnostics['folds'].append({'boundary': boundary, 'training': card, 'holdout': evaluate(test, predicted, card)})
        # Exact artifacts, not a model retrospectively described as a live one.
        joblib.dump(bundle, dest / f'research-{boundary}.joblib')
        heldout = test[['date', 'ticker', 'future_return', 'benchmark_median', 'y_up', 'y_down']].copy()
        for key, values in predicted.items():
            heldout['prediction_' + key] = values
        heldout.to_csv(dest / f'holdout-{boundary}.csv.gz', index=False)
        print(f'{market} {boundary}: down date AUC={diagnostics["folds"][-1]["holdout"]["down"]["within_date"]["mean_date_auc"]:.3f}', flush=True)
    signal = prices.date.max()
    boundary = signal.to_period('M').start_time
    train, cal = relative.purged_split(panel, boundary)
    bundle, card = fit_bundle(train, cal)
    joblib.dump(bundle, dest / 'latest-research.joblib')
    latest = live.feature_panel(prices.loc[prices.date <= signal], market, training=False, signal_date=str(signal.date()))
    prediction = predict(bundle, latest)
    listing = pd.read_csv(restored / 'data/raw' / live.LISTING_FILES[market], dtype={'ticker': str}).fillna('')
    names = listing.set_index('ticker')['name'].to_dict() if 'name' in listing else {}
    events_path = restored / f'data/dashboard_research/accounting/{market}/filing_events.csv.gz'
    events = pd.read_csv(events_path, dtype={'ticker': str, 'filing_id': str},
                         parse_dates=['available_at', 'filed', 'period_end']) if events_path.exists() else pd.DataFrame()
    rows = []
    for i, item in enumerate(latest.itertuples()):
        rows.append({'market': market, 'ticker': item.ticker, 'name': names.get(item.ticker, item.ticker),
            'asof': str(signal.date()), 'up': finite(prediction['up'][i]), 'down': finite(prediction['down'][i]),
            'q10': finite(prediction['q10'][i]), 'q50': finite(prediction['q50'][i]), 'q90': finite(prediction['q90'][i]),
            'market_trend_60': finite(item.market_ret60), 'relative_trend_60': finite(item.relative60),
            'opinion': relative.fundamental_opinion(events, item.ticker, signal),
            'scenarios': {str(fx): relative.attractiveness(prediction['q50'][i], prediction['q10'][i], market, fx_change=fx)
                          for fx in (-.05, 0., .05)}})
    diagnostics['latest'] = {'asof': str(signal.date()), 'training': card, 'scored': len(rows),
        'financial_events_sha256': live.digest(events_path) if events_path.exists() else None,
        'financial_covered': sum(bool(r['opinion']['evidence']) for r in rows),
        'financial_opinions': pd.Series([r['opinion']['status'] for r in rows]).value_counts().to_dict(),
        'feature_hash': hashlib.sha256(pd.util.hash_pandas_object(latest[['ticker', *relative.FEATURES]], index=False).values.tobytes()).hexdigest()}
    live.write_json(dest / 'diagnostics.json', diagnostics)
    return rows, diagnostics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='Fresh ignored output directory')
    parser.add_argument('--snapshot-name', required=True, help='New immutable research snapshot name')
    args = parser.parse_args()
    root = args.root.resolve()
    allowed = (Path.cwd() / '.research-backend/artifacts').resolve()
    if not root.is_relative_to(allowed) or root == allowed or root.exists():
        raise ValueError('Use a new ignored research output directory; restore an existing result before continuing it')
    client = Client(project='investment')
    if client.json('GET', '/snapshot-heads/' + args.snapshot_name)['snapshot_id']:
        raise ValueError('Research snapshot already exists; restore it instead of replacing it')
    restored, source = restore_inputs(root)
    output = root / 'result'
    output.mkdir(parents=True)
    report = {'version': relative.VERSION, 'created_at': live.utc_now(), 'code_commit': os.getenv('GITHUB_SHA', 'local'),
        'packages': {name: package_version(name) for name in ('numpy', 'pandas', 'scipy', 'scikit-learn', 'lightgbm')},
        'kind': 'research-created-after-signal', 'contract': relative.CONTRACT, 'rules': relative.RULES,
        'source': source, 'fx_observation': None, 'fx_status': 'No retained point-in-time USD/KRW series; scenario only',
        'validation_status': 'research_only_no_promotion', 'markets': {}, 'rows': []}
    for market in ('kr', 'us'):
        rows, diag = run_market(restored, output, market)
        report['rows'].extend(rows)
        report['markets'][market] = diag
    live.write_json(output / 'investment_view.json', report)
    from build_investment_preview import build
    build(output, output / 'preview')
    archived = client.push(args.snapshot_name, output, [str(p.relative_to(output)).replace('\\', '/') for p in output.iterdir()])
    print(json.dumps({'archived': archived, 'preview_rows': len(report['rows'])}), flush=True)


if __name__ == '__main__':
    main()
