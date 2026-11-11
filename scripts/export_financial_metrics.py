"""Join saved DB-derived ratios into the presentation, preserving model evidence."""
from __future__ import annotations

import copy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path

from research_backend_client import BackendError

SNAPSHOT = 'financial-metrics-current'
QUALITY_POLICY = 'financial-collection-v2'


def _count_quality(collection):
    if not isinstance(collection, dict):
        raise ValueError('Financial collection quality is unverified')
    counts = {key: collection.get(key) for key in ('attempted', 'saved', 'failed')}
    if any(type(value) is not int or value < 0 for value in counts.values()):
        raise ValueError('Invalid financial collection counts')
    attempted, saved, failed = (counts[k] for k in ('attempted', 'saved', 'failed'))
    if attempted != saved + failed:
        raise ValueError('Inconsistent financial collection counts')
    reason = ('no_successful_writes' if saved == 0 else
              'failure_threshold_exceeded' if failed * 10 > max(30, saved) else 'within_failure_budget')
    return {'schema': 1, 'policy': QUALITY_POLICY, **counts,
            'eligible': reason == 'within_failure_budget', 'reason_code': reason}


def collection_quality(collection):
    """Recompute the existing failure budget; missing/legacy evidence is not a pass."""
    quality = _count_quality(collection)
    failures = collection.get('failures')
    if (not isinstance(failures, list) or len(failures) != quality['failed']
            or collection.get('mode') not in ('bootstrap', 'refresh', 'repair')):
        raise ValueError('Incomplete financial collection quality evidence')
    return quality


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def summary_binding(summary, market):
    if (market not in ('kr', 'us') or summary.get('market') != market
            or summary.get('schema') != 1 or summary.get('storage') != 'oracle'
            or summary.get('presentation_only') is not True
            or not isinstance(summary.get('companies'), dict)
            or not isinstance(summary.get('record_versions'), dict)
            or not isinstance(summary.get('source_snapshot'), str) or not summary['source_snapshot']):
        raise ValueError('Invalid financial payload or market binding')
    stamp = summary.get('generated_at')
    if not isinstance(stamp, str) or datetime.fromisoformat(stamp).utcoffset() is None:
        raise ValueError('Financial publication requires an explicit observation timestamp')
    return {'market': market, 'generated_at': stamp, 'source_snapshot': summary['source_snapshot'],
            'payload_sha256': _digest({k: v for k, v in summary.items() if k != 'publication'})}


def publication_batch(summaries, selected_markets):
    if not summaries or not set(selected_markets) <= set(summaries) or not selected_markets:
        raise ValueError('Incomplete financial publication scope')
    members = {m: {'binding': summary_binding(s, m), 'quality': collection_quality(s.get('collection'))}
               for m, s in sorted(summaries.items())}
    if len({(members[m]['binding']['generated_at'], members[m]['binding']['source_snapshot'])
            for m in selected_markets}) != 1:
        raise ValueError('Selected financial markets have different observation/source batches')
    return {'markets': members, 'selected_markets': sorted(set(selected_markets)),
            'eligible': all(member['quality']['eligible'] for member in members.values())}


def publication_evidence(summary, role, batch):
    if role not in ('public', 'checkpoint'):
        raise ValueError('Invalid financial publication role')
    evidence = {'schema': 2, 'role': role, 'binding': summary_binding(summary, summary.get('market')),
                'quality': collection_quality(summary.get('collection')), 'batch': batch}
    return {**evidence, 'evidence_sha256': _digest(evidence)}


def require_public_summary(summary, market=None):
    quality = collection_quality(summary.get('collection'))
    binding = summary_binding(summary, market or summary.get('market'))
    evidence = summary.get('publication') or {}
    batch = evidence.get('batch') or {}
    members = batch.get('markets') or {}
    if (not quality['eligible'] or not members or not set(members) <= {'kr', 'us'}
            or batch.get('eligible') is not True or not batch.get('selected_markets')
            or not set(batch['selected_markets']) <= set(members)
            or len({(members[m]['binding']['generated_at'], members[m]['binding']['source_snapshot'])
                    for m in batch['selected_markets']}) != 1
            or members.get(binding['market']) != {'binding': binding, 'quality': quality}
            or any(member.get('quality') != _count_quality(member.get('quality'))
                   or member['quality']['eligible'] is not True for member in members.values())
            or evidence != publication_evidence(summary, 'public', batch)):
        raise ValueError('Financial summary is not verified for public publication')


def require_public_bundle(summaries):
    if not summaries:
        raise ValueError('Financial snapshot contains no verified market summaries')
    common_batch = None
    for market, summary in summaries.items():
        require_public_summary(summary, market)
        batch = summary['publication']['batch']
        if set(batch['markets']) != set(summaries) or (common_batch is not None and batch != common_batch):
            raise ValueError('Financial snapshot must have one complete publication batch')
        common_batch = batch
        for other, member in batch['markets'].items():
            if other not in summaries or member != {
                    'binding': summary_binding(summaries[other], other),
                    'quality': collection_quality(summaries[other].get('collection'))}:
                raise ValueError('Financial publication batch is incomplete or has changed')


def load_metrics(client, destination):
    head = client.json('GET', '/snapshot-heads/' + SNAPSHOT)['snapshot_id']
    if not head:
        return None
    files = {e['relative_path']: e for e in client.snapshot_entries(head)}
    if not set(files) <= {'kr.json', 'us.json'}:
        raise ValueError('Unexpected files in public financial snapshot')
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
    require_public_bundle(result['markets'])
    return result


def overlay(report, saved, today=None):
    require_public_bundle(saved['markets'])
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
