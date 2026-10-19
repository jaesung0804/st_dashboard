"""Adversarial, offline regressions for the entire public financial bundle."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from export_financial_metrics import (publication_batch, publication_evidence,
    require_public_bundle, overlay, load_metrics)
from financial_database import publish_summaries, SNAPSHOT, CHECKPOINT
from research_backend_client import Client, BackendError


def summary(market='us', failed=0):
    return {'schema': 1, 'market': market, 'storage': 'oracle', 'presentation_only': True,
            'generated_at': '2026-10-09T00:00:00+00:00', 'source_snapshot': 'source-fixture',
            'companies': {'X': {'latest': {'period_end': '2025-12-31', 'metrics': {'margin': .2}}}},
            'record_versions': {'X': {'version': 3, 'sha256': 'a' * 64}},
            'collection': {'attempted': 10 + failed, 'saved': 10, 'failed': failed,
                           'failures': [{'ticker': f'F{i}'} for i in range(failed)], 'mode': 'refresh'}}


def marked(bundle, role='public', selected=None):
    batch = publication_batch(bundle, selected or list(bundle))
    for value in bundle.values():
        value['publication'] = publication_evidence(value, role, batch)
    return bundle


@pytest.mark.parametrize('field,value', [
    ('companies', {'X': {'latest': {'metrics': {'margin': 999}}}}),
    ('record_versions', {'X': {'version': 999, 'sha256': 'b' * 64}}),
    ('generated_at', '2026-10-10T00:00:00+00:00'),
    ('source_snapshot', 'different-source'), ('market', 'kr')])
def test_marker_cannot_be_reused_after_payload_market_or_clock_changes(field, value):
    bundle = marked({'us': summary()})
    bundle['us'][field] = value
    with pytest.raises(ValueError):
        require_public_bundle(bundle)


def test_role_flip_and_passing_half_of_failed_batch_are_both_blocked():
    bundle = marked({'kr': summary('kr', 77), 'us': summary()}, 'checkpoint')
    us = bundle['us']
    us['publication']['role'] = 'public'
    with pytest.raises(ValueError): require_public_bundle({'us': us})
    # Even recomputing the role digest cannot erase the failed sibling assessment.
    us['publication'] = publication_evidence(us, 'public', us['publication']['batch'])
    with pytest.raises(ValueError): require_public_bundle({'us': us})
    passing = marked({'us': summary()}, 'checkpoint')
    passing['us']['publication']['role'] = 'public'
    with pytest.raises(ValueError): require_public_bundle(passing)


def test_missing_sibling_and_mixed_batch_markers_are_blocked():
    bundle = marked({'kr': summary('kr'), 'us': summary()})
    with pytest.raises(ValueError): require_public_bundle({'us': bundle['us']})
    replacement = summary()
    replacement['generated_at'] = '2026-10-10T00:00:00+00:00'
    replacement = marked({'us': replacement})['us']
    with pytest.raises(ValueError): require_public_bundle({'kr': bundle['kr'], 'us': replacement})
    bundle['us']['publication'] = copy.deepcopy(bundle['kr']['publication'])
    with pytest.raises(ValueError): require_public_bundle(bundle)


def test_independently_certified_single_market_batches_cannot_be_combined():
    kr = marked({'kr': summary('kr')})['kr']
    us = summary(); us['generated_at'] = '2026-10-10T00:00:00+00:00'
    us = marked({'us': us})['us']
    with pytest.raises(ValueError, match='one complete'):
        require_public_bundle({'kr': kr, 'us': us})


@pytest.mark.parametrize('field', ['generated_at', 'source_snapshot'])
def test_selected_markets_require_the_same_observation_and_source_batch(field):
    bundle = {'kr': summary('kr'), 'us': summary()}
    bundle['kr'][field] = '2026-10-10T00:00:00+00:00' if field == 'generated_at' else 'other-source'
    with pytest.raises(ValueError): publication_batch(bundle, ['kr', 'us'])


@pytest.mark.parametrize('stamp', ['2026-10-09', '2026-10-09T00:00:00', None, 'invalid'])
def test_missing_or_naive_observation_clock_is_not_certified(stamp):
    value = summary(); value['generated_at'] = stamp
    with pytest.raises(ValueError): marked({'us': value})


@pytest.mark.parametrize('role', ['checkpoint', 'legacy', 'tampered', 'public'])
def test_direct_overlay_cannot_skip_bundle_validation(role):
    bundle = marked({'us': summary()}, 'checkpoint' if role == 'checkpoint' else 'public')
    if role == 'legacy': bundle['us'].pop('publication')
    if role == 'tampered': bundle['us']['companies']['X']['latest']['metrics']['margin'] = 2
    if role == 'public':
        assert overlay({'rows': []}, {'snapshot_id': 'fixture', 'markets': bundle})['rows'] == []
    else:
        with pytest.raises(ValueError): overlay({'rows': []}, {'snapshot_id': 'fixture', 'markets': bundle})


def test_loader_rejects_individually_valid_but_inconsistent_market_bundle(tmp_path):
    bundle = marked({'kr': summary('kr'), 'us': summary()})
    other = summary(); other['source_snapshot'] = 'different-source'
    bundle['us'] = marked({'us': other})['us']
    payloads = {m + '.json': json.dumps(s).encode() for m, s in bundle.items()}
    class LoaderClient:
        def json(self, *a): return {'snapshot_id': 'fixture'}
        def snapshot_entries(self, *a):
            return [{'relative_path': p, 'sha256': p, 'byte_size': len(v)} for p, v in payloads.items()]
        def download(self, sha, path, size): path.write_bytes(payloads[sha])
    with pytest.raises(ValueError): load_metrics(LoaderClient(), tmp_path)


def fake_transport(root, bundle, old=None, conflict=None):
    """Use the actual Client.push without constructing a configured/credentialed client."""
    root.mkdir(exist_ok=True)
    for market, value in bundle.items():
        (root / (market + '.json')).write_text(json.dumps(value))
    client = Client.__new__(Client)
    client.calls, client.uploads = [], []
    client.published = old
    if old:
        client._save_receipt(root, SNAPSHOT, old)
    def request(method, path, body=None):
        client.calls.append((method, path))
        if method == 'GET': return {'snapshot_id': 'concurrent' if conflict == 'head' else old}
        if method == 'POST' and path == '/snapshots': return {'snapshot_id': 'new-fixture'}
        if '/commit?' in path:
            if conflict == 'commit': raise BackendError(409, 'fixture changed head')
            client.published = 'new-fixture'
        return {}
    def upload(path):
        raw = path.read_bytes(); client.uploads.append(raw)
        return {'sha256': hashlib.sha256(raw).hexdigest(), 'byte_size': len(raw), 'changed': True}
    client.json, client.upload = request, upload
    client.snapshot_entries = lambda _: [
        {'relative_path': m + '.json', 'sha256': 'old-' + m, 'byte_size': 1} for m in bundle] if old else []
    return client


@pytest.mark.parametrize('kind', ['legacy', 'failed', 'checkpoint', 'role_flip', 'tampered'])
def test_generic_push_rejects_ineligible_payload_before_upload_or_head_change(tmp_path, kind):
    bundle = marked({'us': summary(failed=77 if kind == 'failed' else 0)},
                    'checkpoint' if kind in ('checkpoint', 'role_flip') else 'public')
    if kind == 'legacy': bundle['us'].pop('publication')
    if kind == 'role_flip': bundle['us']['publication']['role'] = 'public'
    if kind == 'tampered': bundle['us']['companies'] = {}
    client = fake_transport(tmp_path, bundle, old='good-head')
    with pytest.raises(ValueError): client.push(SNAPSHOT, tmp_path, ['us.json'])
    assert client.uploads == [] and client.published == 'good-head'
    assert client.calls == [('GET', '/snapshot-heads/' + SNAPSHOT)]


def test_generic_push_requires_all_inherited_markets_and_checks_market_filename(tmp_path):
    bundle = marked({'kr': summary('kr'), 'us': summary()})
    client = fake_transport(tmp_path, bundle, old='good-head')
    with pytest.raises(ValueError): client.push(SNAPSHOT, tmp_path, ['us.json'])
    wrong = copy.deepcopy(bundle['us']); (tmp_path / 'kr.json').write_text(json.dumps(wrong))
    with pytest.raises(ValueError): client.push(SNAPSHOT, tmp_path, ['kr.json', 'us.json'])
    assert client.uploads == [] and client.published == 'good-head'


def test_generic_push_commits_verified_bundle_and_retains_cas(tmp_path):
    bundle = marked({'us': summary()})
    client = fake_transport(tmp_path, bundle)
    assert client.push(SNAPSHOT, tmp_path, ['us.json'])['snapshot_id'] == 'new-fixture'
    assert client.published == 'new-fixture' and len(client.uploads) == 1


@pytest.mark.parametrize('conflict', ['head', 'commit'])
def test_actual_push_cas_conflict_does_not_retry_or_replace_good_head(tmp_path, conflict):
    client = fake_transport(tmp_path, marked({'us': summary()}), old='good-head', conflict=conflict)
    with pytest.raises(BackendError) as error: client.push(SNAPSHOT, tmp_path, ['us.json'])
    assert error.value.status == 409 and client.published == 'good-head'
    assert len([p for _, p in client.calls if '/commit?' in p]) == (1 if conflict == 'commit' else 0)
    assert json.loads(client._receipt(tmp_path, SNAPSHOT).read_text())['snapshot_id'] == 'good-head'


def test_changed_payload_between_validation_and_staging_never_commits(tmp_path):
    client = fake_transport(tmp_path, marked({'kr': summary('kr'), 'us': summary()}), old='good-head')
    original_upload = client.upload
    def change_next_file(path):
        receipt = original_upload(path)
        (tmp_path / 'us.json').write_text('{}')
        return receipt
    client.upload = change_next_file
    with pytest.raises(ValueError, match='changed after'):
        client.push(SNAPSHOT, tmp_path, ['kr.json', 'us.json'])
    assert client.published == 'good-head'
    assert not any(method == 'POST' for method, _ in client.calls)


def test_upload_receipt_must_match_the_validated_financial_payload(tmp_path):
    client = fake_transport(tmp_path, marked({'us': summary()}), old='good-head')
    original_upload = client.upload
    def mismatched_receipt(path):
        return {**original_upload(path), 'sha256': 'b' * 64}
    client.upload = mismatched_receipt
    with pytest.raises(ValueError, match='receipt'):
        client.push(SNAPSHOT, tmp_path, ['us.json'])
    assert client.published == 'good-head'
    assert not any(method == 'POST' for method, _ in client.calls)


def test_single_market_refresh_rebinds_verified_inherited_market_without_changing_values(tmp_path):
    public, checkpoint = tmp_path / 'public', tmp_path / 'checkpoint'
    public.mkdir(); checkpoint.mkdir()
    old = marked({'kr': summary('kr'), 'us': summary()})
    for market, value in old.items(): (public / (market + '.json')).write_text(json.dumps(value))
    new_us = summary(); new_us['generated_at'] = '2026-10-10T00:00:00+00:00'
    (public / 'us.json').write_text(json.dumps(new_us))
    class Publisher:
        def __init__(self): self.calls = []
        def push(self, name, root, paths):
            self.calls.append((name, paths))
            return {'snapshot_id': name + '-fixture'}
    client = Publisher()
    publish_summaries(client, public, checkpoint, ['us'])
    output = {m: json.loads((public / (m + '.json')).read_text()) for m in old}
    require_public_bundle(output)
    assert {k: v for k, v in output['kr'].items() if k != 'publication'} == {
        k: v for k, v in old['kr'].items() if k != 'publication'}
    assert output['kr']['publication']['batch']['selected_markets'] == ['us']
    assert client.calls == [(CHECKPOINT, ['us.json']), (SNAPSHOT, ['kr.json', 'us.json'])]


def test_first_attempt_for_new_market_preserves_all_failed_checkpoint(monkeypatch):
    from argparse import Namespace
    import financial_database as db
    old = marked({'us': summary()})['us']
    class Backend:
        def __init__(self): self.pushes = []; self.checkpoint = None
        def json(self, method, path, body=None):
            assert method == 'GET'
            if path == '/ready': return {'database': 'oracle'}
            if path == '/snapshot-heads/' + SNAPSHOT: return {'snapshot_id': 'good-us-only'}
            if path == '/snapshot-heads/' + CHECKPOINT: return {'snapshot_id': None}
            pytest.fail('Unexpected request')
        def pull(self, name, root):
            assert name == SNAPSHOT
            (root / 'us.json').write_text(json.dumps(old))
        def push(self, name, root, paths):
            self.pushes.append(name)
            assert name == CHECKPOINT
            self.checkpoint = json.loads((root / 'kr.json').read_text())
            return {'snapshot_id': 'failed-checkpoint'}
    backend = Backend()
    def restore(client, root, markets, bootstrap, repair):
        path = root / db.LISTINGS['kr']; path.parent.mkdir(parents=True)
        path.write_text('ticker,name\nUNKNOWN,Fixture\n')
        (root / 'data/raw/opendart_corp_codes.csv').write_text('ticker,corp_code\n')
        return root, {'snapshot_id': 'fixture-source'}
    monkeypatch.setenv('RESEARCH_STORAGE', 'backend')
    monkeypatch.setattr(db, 'Client', lambda **kw: backend)
    monkeypatch.setattr(db, 'source_index', lambda _: {})
    monkeypatch.setattr(db, 'restore_inputs', restore)
    monkeypatch.setattr(db, 'utc_now', lambda: '2026-10-10T00:00:00+00:00')
    with pytest.raises(ValueError, match='checkpoint retained'):
        db.run(Namespace(command='refresh', market='kr', limit=1, tickers=None, workers=1), backend)
    assert backend.pushes == [CHECKPOINT]
    assert backend.checkpoint['companies'] == backend.checkpoint['record_versions'] == {}
    assert backend.checkpoint['collection']['failures'][0]['reason_code'] == 'missing_corporate_code'
    assert backend.checkpoint['publication']['batch']['eligible'] is False
