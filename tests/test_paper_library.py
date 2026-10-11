from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_paper_library import build, validate


def fixture():
    # Synthetic evidence tests rendering only; it never enters the public source.
    return dict(schema_version=1, updated_at='2026-10-11', papers=[dict(
        id='test-only', title='TEST ONLY </script><script>alert(1)</script>',
        authors=['Test Author'], year=2025, field='Test field', tracks=['mechanical'],
        review_status='abstract', source_url='https://example.org/paper',
        research_question=['Test question'], core_math=[dict(formula='x < y', intuition='Test intuition')],
        empirical_results='Test results', limitations='Test limitations',
        application_hypothesis='Test hypothesis', validation_method='Test validation')])


def test_public_payload_is_exact_and_safe(tmp_path):
    payload = fixture()
    before = copy.deepcopy(payload)
    source = tmp_path / 'input.json'
    source.write_text(json.dumps(payload), encoding='utf-8')
    target = tmp_path / 'papers'
    assert build(target, source) == 1
    page = (target / 'index.html').read_text(encoding='utf-8')
    assert '@@' not in page
    assert '</script><script>alert(1)</script>' not in page
    assert page.index("var theme='dark'") < page.index('<link rel="stylesheet"')
    assert 'href="../papers/" aria-current="page"' in page
    assert json.loads((target / 'library.json').read_text(encoding='utf-8')) == before
    assert payload == before


@pytest.mark.parametrize('change', [
    lambda p: p['papers'][0].update(private_notes='must not publish'),
    lambda p: p['papers'][0].update(source_url='javascript:alert(1)'),
    lambda p: p['papers'][0].update(source_url='https://user:password@example.org'),
    lambda p: p['papers'][0].update(tracks=['unknown']),
    lambda p: p['papers'][0].update(review_status='reproduced'),
    lambda p: p['papers'][0].update(year=True),
    lambda p: p['papers'].append(copy.deepcopy(p['papers'][0])),
    lambda p: p['papers'][0].update(research_question=[]),
    lambda p: p['papers'][0].update(core_math=[]),
])
def test_unsafe_or_ambiguous_public_input_is_rejected(change):
    payload = fixture()
    change(payload)
    with pytest.raises(ValueError):
        validate(payload)


def test_empty_catalog_does_not_invent_evidence(tmp_path):
    source = tmp_path / 'empty.json'
    source.write_text(json.dumps(dict(schema_version=1, updated_at='2026-10-11', papers=[])))
    assert build(tmp_path / 'papers', source) == 0


def test_committed_catalog_is_valid():
    root = Path(__file__).resolve().parents[1]
    validate(json.loads((root / 'docs/papers/library.json').read_text(encoding='utf-8')))
