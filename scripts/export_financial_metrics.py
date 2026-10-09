"""Join saved DB-derived ratios into the presentation, preserving model evidence."""
from __future__ import annotations

import copy
from datetime import date
import json
from pathlib import Path

from research_backend_client import BackendError

SNAPSHOT = 'financial-metrics-current'


def load_metrics(client, destination):
    head = client.json('GET', '/snapshot-heads/' + SNAPSHOT)['snapshot_id']
    if not head:
        return None
    files = {e['relative_path']: e for e in client.snapshot_entries(head)}
    result = {'snapshot_id': head, 'markets': {}}
    for market in ('kr', 'us'):
        name = market + '.json'
        if name not in files:
            continue
        entry = files[name]
        if entry['byte_size'] > 24 * 1024**2:
            raise ValueError('Financial metric summary exceeds public build budget')
        path = Path(destination) / name
        client.download(entry['sha256'], path, entry['byte_size'])
        summary = json.loads(path.read_text(encoding='utf-8'))
        if summary['market'] != market or summary['storage'] != 'oracle':
            raise ValueError('Invalid financial metric source')
        result['markets'][market] = summary
    return result


def overlay(report, saved, today=None):
    today = today or date.today().isoformat()
    output = copy.deepcopy(report)
    coverage = {}
    for row in output['rows']:
        summary = saved['markets'].get(row['market'])
        if summary is None:
            continue
        financials = summary['companies'].get(row['ticker'])
        if not financials or not financials.get('latest'):
            continue
        financials = copy.deepcopy(financials)
        latest = financials['latest']
        previous = (row.get('financials') or {}).get('latest')
        if previous and previous.get('period_end') and (
            not latest.get('period_end') or latest['period_end'] < previous['period_end']
        ):
            continue  # Migration must not hide a newer, already verified public ratio.
        age = (date.fromisoformat(today) - date.fromisoformat(latest['period_end'])).days if latest.get('period_end') else None
        financials.update(age_days=age, stale=age is not None and age > 240)
        row['financials'] = financials
        # Frozen experimental opinions remain in the archive and original fields.
        row['financial_opinion'] = {'status': 'withheld', 'label': '지표 확인', 'evidence': [],
            'reasons': ['현재 재무지표를 제공합니다. 기존 모델의 과거 재무 의견과는 기준 시점이 다릅니다.'],
            'asof': summary['generated_at'], 'valuation': 'not_assessed'}
        coverage[row['market']] = coverage.get(row['market'], 0) + 1
    output['financial_data'] = {'snapshot_id': saved['snapshot_id'], 'storage': 'oracle',
        'coverage': coverage, 'updated_at': {m: s['generated_at'] for m, s in saved['markets'].items()},
        'collection': {m: {k: s['collection'][k] for k in ('attempted', 'saved', 'failed', 'mode')}
                       for m, s in saved['markets'].items()}}
    return output
