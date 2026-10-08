"""Public releases must match the immutable reviewed experiment."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import export_investment_experiment as module


def setup_release(tmp_path):
    payload = b'{"rows":[]}'
    digest = hashlib.sha256(payload).hexdigest()
    ref = tmp_path / 'release.json'
    ref.write_text(json.dumps({'snapshot_id': 'a' * 32, 'report_sha256': digest}))
    cache = tmp_path / 'cache'
    report = cache / ('a' * 32) / 'investment_view.json'
    return payload, digest, ref, cache, report


def test_missing_local_cache_preserves_existing_page(tmp_path, monkeypatch):
    monkeypatch.delenv('RESEARCH_STORAGE', raising=False)
    _, _, ref, cache, _ = setup_release(tmp_path)
    site = tmp_path / 'site'
    (site / 'investment').mkdir(parents=True)
    page = site / 'investment/index.html'
    page.write_text('saved experiment')
    assert module.export(site, ref, cache) is False
    assert page.read_text() == 'saved experiment'


def test_cached_summary_requires_exact_hash(tmp_path, monkeypatch):
    monkeypatch.setenv('RESEARCH_STORAGE', 'git')
    payload, _, ref, cache, report = setup_release(tmp_path)
    report.parent.mkdir(parents=True)
    report.write_bytes(payload + b' ')
    with pytest.raises(ValueError, match='integrity'):
        module.export(tmp_path / 'site', ref, cache)
    report.write_bytes(payload)
    assert module.export(tmp_path / 'site', ref, cache)
    assert (tmp_path / 'site/investment/index.html').exists()


@pytest.mark.parametrize('mismatch', [True, False])
def test_backend_checks_pinned_manifest_before_download(tmp_path, monkeypatch, mismatch):
    monkeypatch.setenv('RESEARCH_STORAGE', 'backend')
    payload, digest, ref, cache, report = setup_release(tmp_path)
    calls = []
    class FakeClient:
        def __init__(self, project):
            assert project == 'investment'
        def snapshot_entries(self, snapshot):
            assert snapshot == 'a' * 32
            return [{'relative_path': 'investment_view.json', 'sha256': 'b' * 64 if mismatch else digest,
                     'byte_size': len(payload)}]
        def download(self, sha, target, size):
            calls.append(sha)
            assert sha == digest and size == len(payload)
            target.parent.mkdir(parents=True)
            target.write_bytes(payload)
    monkeypatch.setattr(module, 'Client', FakeClient)
    if mismatch:
        with pytest.raises(ValueError, match='hash mismatch'):
            module.export(tmp_path / 'site', ref, cache)
        assert not calls and not report.exists()
    else:
        assert module.export(tmp_path / 'site', ref, cache)
        assert calls == [digest]


def test_phone_summary_defers_details_without_losing_evidence(tmp_path):
    import re
    from build_investment_preview import build
    rows = [{'market': 'kr', 'ticker': f'{i:06}', 'opinion': {'status':'watch','label':'관망','evidence':[{'filing_id':'proof'}]},
        'scenarios': {'0': {'score':50, 'median_krw':.1}},
        'financials': {'latest':{'metrics':{'fin_roa':.1}},'history':[{'period_end':'2026-06-30'}]},
        'price_metrics': {'chart':list(range(64))}} for i in range(130)]
    source={'rows':rows};payload=json.dumps(source,ensure_ascii=False).encode()
    (tmp_path/'investment_view.json').write_bytes(payload)
    dest=tmp_path/'site';build(tmp_path,dest)
    assert (dest/'research.json').read_bytes()==payload
    html=(dest/'index.html').read_text(encoding='utf-8')
    summary=json.loads(re.search(r'<script id="investment-data" type="application/json">(.*?)</script>',html).group(1))
    assert len(summary['rows'][0]['price_metrics']['chart'])==16
    assert summary['rows'][0]['price_metrics']['chart'][-1]==63
    assert 'history' not in summary['rows'][0]['financials']
    assert summary['rows'][0]['scenarios']['0']['score']==50
    recovered=[]
    for path in sorted((dest/'details').glob('*.json')):
        recovered.extend(json.loads(path.read_text(encoding='utf-8')))
    assert recovered==rows
    assert re.fullmatch(r'details/001.json\?v=[a-f0-9]{12}', summary['rows'][-1]['detail_bundle'])
    assert re.search(r'investment.js\?v=[a-f0-9]{12}',html)
