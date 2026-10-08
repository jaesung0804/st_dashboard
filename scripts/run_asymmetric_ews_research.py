"""Compare 5%/10% upside, 30% downside and a continuous full-universe rank head.

Continue the exact saved v2 evidence, not a newly collected/revised price sample.
No production state or live forecast is changed by this explicit experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from importlib.metadata import version as package_version
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ai_stock_assistant import monthly_ews as live, relative_ews as relative, asymmetric_ews as asymmetric
from ai_stock_assistant.data import price_quality
from research_backend_client import Client
from run_relative_ews_research import FOLDS, restore_inputs, fit_bundle, predict

BASE_NAME = 'relative-ews-37740823642-1'
BASE_ID = '93e62daad56d4fd68029815fee073827'


def run_market(base, original, restored, output, market):
    import joblib
    source = base / market / 'research_panel.csv.gz'
    if live.digest(source) != original['markets'][market]['feature_panel_sha256']:
        raise ValueError('Saved feature-panel checksum differs from original evidence')
    panel = asymmetric.relabel(pd.read_csv(source, dtype={'ticker': str}, parse_dates=['date', 'end_up', 'end_down']))
    dest = output / market
    dest.mkdir(parents=True)
    panel[['date', 'ticker', 'y_up5', 'y_up10', 'y_down30', 'future_percentile']].to_csv(dest / 'target_keys.csv.gz', index=False)
    report = {'folds': [], 'base_panel_sha256': live.digest(source), 'target_keys_sha256': live.digest(dest / 'target_keys.csv.gz')}
    for boundary in FOLDS:
        train, cal = relative.purged_split(panel, boundary)
        end = pd.Timestamp(boundary) + pd.DateOffset(months=3)
        test = panel.loc[(panel.date >= boundary) & (panel.date < end) & panel.y_up5.notna() & panel.y_down30.notna()]
        bundle, card = fit_bundle(train, cal, heads=tuple(asymmetric.TARGETS), quantiles=False, ranker=True)
        threshold = asymmetric.warning_threshold(cal.y_down30, predict(bundle, cal)['down30'])
        card['down_warning_threshold'] = threshold
        predicted = predict(bundle, test)
        evaluation = asymmetric.evaluate(test, predicted, card, threshold)
        old = next(f for f in original['markets'][market]['folds'] if f['boundary'] == boundary)
        evaluation['return_forecast'] = old['holdout']['return_forecast']
        # Same dated outcomes for the 20% benchmark; probability semantics stay distinct.
        old_test = pd.read_csv(base / market / f'holdout-{boundary}.csv.gz', dtype={'ticker': str}, parse_dates=['date'])
        matched = test[['date', 'ticker']].merge(old_test[['date', 'ticker', 'prediction_up']], how='left',
            on=['date', 'ticker'], validate='one_to_one')
        if matched.prediction_up.isna().any():
            raise ValueError('The comparator must use exactly matched stock-date outcomes')
        evaluation['selection']['old_up20-top5-all'] = asymmetric.selection_summary(test, matched.prediction_up.to_numpy(),
            predicted['down30'], threshold, .05, filtered=False)
        report['folds'].append({'boundary': boundary, 'training': card, 'holdout': evaluation})
        joblib.dump(bundle, dest / f'research-{boundary}.joblib')
        scored = test[['date', 'ticker', 'future_return', 'excess_return', 'y_up5', 'y_up10', 'y_down30', 'future_percentile']].copy()
        for key, values in predicted.items():
            scored['prediction_' + key] = values
        scored.to_csv(dest / f'holdout-{boundary}.csv.gz', index=False)
        print(json.dumps({'market': market, 'fold': boundary,
            'up5_auc': evaluation['up5']['within_date']['mean_date_auc'],
            'up10_auc': evaluation['up10']['within_date']['mean_date_auc'],
            'down30_auc': evaluation['down30']['within_date']['mean_date_auc'],
            'rank_ic': evaluation['rank']['mean_date_rank_ic']}), flush=True)
    old_latest = original['markets'][market]['latest']
    prices, quality = price_quality.prepare(live.read_prices(restored / 'data/raw' / live.PRICE_FILES[market]), market)
    signal = pd.Timestamp(old_latest['asof'])
    latest = live.feature_panel(prices.loc[prices.date <= signal], market, training=False, signal_date=str(signal.date()))
    feature_hash = hashlib.sha256(pd.util.hash_pandas_object(latest[['ticker', *relative.FEATURES]], index=False).values.tobytes()).hexdigest()
    if feature_hash != old_latest['feature_hash']:
        raise ValueError('Latest features must exactly reproduce the saved comparison date')
    train, cal = relative.purged_split(panel, signal.to_period('M').start_time)
    bundle, card = fit_bundle(train, cal, heads=tuple(asymmetric.TARGETS), quantiles=False, ranker=True)
    threshold = asymmetric.warning_threshold(cal.y_down30, predict(bundle, cal)['down30'])
    card['down_warning_threshold'] = threshold
    joblib.dump(bundle, dest / 'latest-research.joblib')
    predicted = predict(bundle, latest)
    market_scores = asymmetric.market_percentiles(predicted['rank']).to_numpy()
    old_rows = {r['ticker']: r for r in original['rows'] if r['market'] == market}
    rows = []
    for i, item in enumerate(latest.itertuples()):
        row = dict(old_rows[item.ticker])
        row.update(scored=True, up5=float(predicted['up5'][i]), up10=float(predicted['up10'][i]),
            down30=float(predicted['down30'][i]), up=float(predicted['up5'][i]), down=float(predicted['down30'][i]),
            market_score=float(market_scores[i]), expected_percentile=float(predicted['rank'][i]),
            warning=bool(predicted['down30'][i] >= threshold), score_reason=None)
        rows.append(row)
    listing = pd.read_csv(restored / 'data/raw' / live.LISTING_FILES[market], dtype={'ticker': str}).fillna('')
    if listing.ticker.duplicated().any():
        raise ValueError('Ambiguous listing tickers')
    events_path = restored / f'data/dashboard_research/accounting/{market}/filing_events.csv.gz'
    events = pd.read_csv(events_path, dtype={'ticker': str, 'filing_id': str}, parse_dates=['available_at', 'filed', 'period_end'])
    last_dates = prices.loc[prices.date <= signal].groupby('ticker').date.max()
    for item in listing.itertuples():
        if item.ticker in old_rows:
            continue
        last = last_dates.get(item.ticker)
        reason = '기준일 가격 없음' if pd.isna(last) or last != signal else '가격 이력·유동성·가격 연속성 조건 미충족'
        rows.append({'market': market, 'ticker': item.ticker, 'name': getattr(item, 'name', item.ticker),
            'asof': str(signal.date()), 'scored': False, 'score_reason': reason, 'market_score': None,
            'up': None, 'down': None, 'up5': None, 'up10': None, 'down30': None, 'warning': None,
            'expected_percentile': None, 'q10': None, 'q50': None, 'q90': None, 'scenarios': {},
            'market_trend_60': None, 'relative_trend_60': None,
            'opinion': relative.fundamental_opinion(events, item.ticker, signal)})
    scored_rows = [r for r in rows if r['scored']]
    report['latest'] = {'asof': str(signal.date()), 'training': card, 'scored': len(scored_rows), 'listed': len(rows),
        'unscored': len(rows)-len(scored_rows), 'warning_count': sum(r['warning'] for r in scored_rows),
        'financial_covered': sum(bool(r['opinion']['evidence']) for r in rows),
        'financial_opinions': pd.Series([r['opinion']['status'] for r in rows]).value_counts().to_dict(),
        'feature_hash': feature_hash, 'nesting_violation_fraction': float(np.mean(predicted['up5'] > predicted['up10']))}
    live.write_json(dest / 'diagnostics.json', report)
    return rows, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--snapshot-name', required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    allowed = (Path.cwd() / '.research-backend/artifacts').resolve()
    if root.exists() or root == allowed or not root.is_relative_to(allowed):
        raise ValueError('Use a fresh ignored directory; restore any existing experiment first')
    client = Client(project='investment')
    if client.json('GET', '/snapshot-heads/' + args.snapshot_name)['snapshot_id']:
        raise ValueError('Output snapshot exists; restore it instead of replacing it')
    base = root / 'base'
    receipt = client.pull(BASE_NAME, base)
    if receipt['snapshot_id'] != BASE_ID:
        raise ValueError('Base experiment head changed; stop and review')
    original = live.read_json(base / 'investment_view.json')
    restored, source = restore_inputs(root / 'inputs', original['source']['snapshot_id'])
    output = root / 'result'
    output.mkdir(parents=True)
    report = {'version': asymmetric.VERSION, 'created_at': live.utc_now(), 'code_commit': os.getenv('GITHUB_SHA', 'local'),
        'kind': 'research-created-after-signal', 'contract': relative.CONTRACT, 'asymmetric': asymmetric.POLICY,
        'rules': relative.RULES, 'source': source, 'base_snapshot': BASE_ID,
        'packages': {name: package_version(name) for name in ('numpy', 'pandas', 'scipy', 'scikit-learn', 'lightgbm')},
        'validation_status': 'experimental_public_not_validated_for_trading',
        'fx_observation': None, 'fx_status': original['fx_status'], 'markets': {}, 'rows': []}
    for market in ('kr', 'us'):
        rows, diagnostics = run_market(base, original, restored, output, market)
        report['rows'].extend(rows)
        report['markets'][market] = diagnostics
    live.write_json(output / 'investment_view.json', report)
    archived = client.push(args.snapshot_name, output, [p.relative_to(output).as_posix() for p in output.iterdir()])
    print(json.dumps({'archived': archived, 'report_sha256': live.digest(output / 'investment_view.json'),
                      'rows': len(report['rows'])}), flush=True)


if __name__ == '__main__':
    main()
