import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from ai_stock_assistant.data.financial_store import financial_metrics, merge_statements, normalized_statement
from export_financial_metrics import overlay
from financial_database import write_company, import_rows
from research_backend_client import BackendError


def statement(year=2025, **kwargs):
    row = {'bsns_year': str(year), 'reprt_code': '11011', 'fs_div': 'YF',
           'revenue': 100, 'operating_income': 20, 'net_income': 10,
           'total_assets': 200, 'total_liabilities': 100, 'total_equity': 100,
           'cash': 30, 'operating_cash_flow': 20, 'capex': 5}
    row.update(kwargs)
    return normalized_statement(row, source='yahoo', observed_at='2026-10-09T00:00:00+00:00',
        source_hash=str(year), period_end=f'{year}-12-31', frequency='annual')


def test_saved_values_are_not_eligible_before_observation_or_for_models():
    p = {'statements': [statement()]}
    assert financial_metrics(p, '2026-10-08')['latest'] is None
    f = financial_metrics(p, '2026-10-09')
    assert f['latest']['metrics']['fin_operating_margin'] == .2
    assert f['model_eligible'] is False
    assert f['latest']['available_at'] is None
    assert f['status'] == 'filing_date_unverified'


def test_no_invented_korean_calendar_or_ttm():
    row = {'bsns_year': '2026', 'reprt_code': '11012', 'revenue': 100,
           'operating_income': 10, 'operating_cash_flow': 40, 'total_assets': 100, 'fs_div': 'CFS'}
    s = normalized_statement(row, source='opendart', observed_at='2026-10-09', source_hash='a')
    f = financial_metrics({'statements': [s]}, '2026-10-09')
    assert f['latest']['period_end'] is None
    assert f['latest']['period_label'] == '2026년 반기'
    assert f['latest']['metrics']['fin_cfo_assets'] is None
    s = normalized_statement(row, source='opendart', observed_at='2026-10-09', source_hash='a',
                             fiscal_month='12', filing_id='20260814001234')
    assert s['period_end'] == '2026-06-30'
    assert s['filed_at'] == '2026-08-14'


def test_actual_us_fiscal_date_controls_latest_not_annual_report_code():
    annual = statement(2026)
    annual['period_end'] = '2026-03-31'
    quarter = statement(2026, revenue=300)
    quarter.update(period_end='2026-06-30', report_code='11012', frequency='quarterly')
    latest = financial_metrics({'statements': [annual, quarter]}, '2026-10-09')['latest']
    assert latest['period_end'] == '2026-06-30'
    assert latest['basis'] == 'reported_period'
    assert latest['metrics']['fin_cfo_assets'] is None


def test_annual_average_assets_and_zero_negative_denominators():
    previous, current = statement(2024, total_assets=100, revenue=50), statement(2025)
    f = financial_metrics({'statements': [previous, current]}, '2026-10-09')['latest']['metrics']
    assert f['fin_revenue_growth'] == 1
    assert f['fin_roa'] == pytest.approx(10 / 150)
    current['values']['total_equity'] = -1
    current['values']['revenue'] = 0
    f = financial_metrics({'statements': [current]}, '2026-10-09')['latest']['metrics']
    assert f['liabilities_equity'] is None
    assert f['fin_operating_margin'] is None


def test_merge_retains_history_values_and_original_observation_on_retry():
    old = statement()
    repeated = copy.deepcopy(old)
    repeated['observed_at'] = '2026-10-10'
    assert merge_statements([old], [repeated]) == [old]
    changed = copy.deepcopy(repeated)
    changed['source_sha256'] = 'new'
    changed['values']['revenue'] = None
    changed['values']['cash'] = 0
    merged = merge_statements([statement(2024), old], [changed])
    assert len(merged) == 2
    assert merged[-1]['values']['revenue'] == 100
    assert merged[-1]['values']['cash'] == 0


def test_conflict_stops_and_does_not_blindly_retry():
    class Client:
        def json(self, method, path, body=None):
            if method == 'GET':
                return {'version': 3, 'payload': {'statements': [statement(2024)]}}
            assert body['expected_version'] == 3
            assert len(body['payload']['statements']) == 2
            raise BackendError(409, 'changed')
    key = 'financial-statements-v1/us/AAPL'
    with pytest.raises(BackendError) as error:
        write_company(Client(), {key: {}}, 'us', 'AAPL', [statement()], {})
    assert error.value.status == 409


def test_presentation_contains_only_metrics_and_preserves_frozen_model_and_opinion():
    original = {'rows': [{'market': 'us', 'ticker': 'AAPL', 'score': 10,
        'scenarios': {'0': {'score': 50}}, 'opinion': {'status': 'withheld'}, 'financials': None}]}
    before = copy.deepcopy(original)
    summary = {'companies': {'AAPL': financial_metrics({'statements': [statement()]}, '2026-10-09')},
        'generated_at': '2026-10-09', 'collection': {'attempted': 1, 'saved': 1, 'failed': 0, 'mode': 'bootstrap'}}
    out = overlay(original, {'snapshot_id': 'a', 'markets': {'us': summary}}, '2026-10-09')
    assert original == before
    row = out['rows'][0]
    assert row['scenarios'] == before['rows'][0]['scenarios']
    assert row['opinion'] == before['rows'][0]['opinion']
    assert row['financials']['latest']['metrics']['fin_operating_margin'] == .2
    assert 'values' not in json.dumps(out)
    assert 'source_sha256' not in json.dumps(out)
    original['rows'][0]['financials'] = {'latest': {'period_end': '2026-06-30', 'metrics': {'fin_roa': .1}}}
    kept = overlay(original, {'snapshot_id': 'a', 'markets': {'us': summary}}, '2026-10-09')
    assert kept['rows'][0]['financials'] == original['rows'][0]['financials']


def test_import_skips_retained_empty_responses(tmp_path):
    folder = tmp_path / 'data/raw/yfinance_financials/annual_quarterly'
    folder.mkdir(parents=True)
    (folder / 'EMPTY_quarterly_raw.csv').write_text('\n')
    assert list(import_rows(tmp_path, 'us', '2026-10-09', {})) == []


def test_reimport_does_not_replace_a_refreshed_statement_or_add_clock_revisions():
    saved = statement(revenue=150)
    saved['source_sha256'] = 'refreshed'
    payload = {'schema': 1, 'market': 'us', 'ticker': 'AAPL', 'statements': [saved]}
    class Client:
        def json(self, method, path, body=None):
            if method == 'GET':
                return {'version': 4, 'payload': copy.deepcopy(payload)}
            assert body['expected_version'] == 4
            assert body['payload'] == payload
            return {'version': 4, 'changed': False}
    write_company(Client(), {'financial-statements-v1/us/AAPL': {}}, 'us', 'AAPL',
                  [statement()], {}, checked_at='2026-10-10', import_only=True)


def test_verified_period_supersedes_undated_legacy_view_without_deleting_source():
    row = {'bsns_year': '2026', 'reprt_code': '11012', 'fs_div': 'CFS', 'revenue': 100, 'operating_income': 10}
    old = normalized_statement(row, source='opendart', observed_at='2026-10-09', source_hash='a')
    new = normalized_statement(row, source='opendart', observed_at='2026-10-10', source_hash='b', fiscal_month='12')
    records = merge_statements([old], [new])
    assert len(records) == 2
    assert financial_metrics({'statements': records}, '2026-10-10')['latest']['period_end'] == '2026-06-30'


def test_new_us_collector_keeps_fourth_quarter_distinct_from_annual(monkeypatch):
    from ai_stock_assistant.data import us
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {'timeseries': {'result': [{'meta': {'type': ['quarterlyTotalRevenue']},
                'quarterlyTotalRevenue': [{'asOfDate': '2025-12-31', 'reportedValue': {'raw': 100}}]}]}}
    monkeypatch.setattr(us.requests, 'get', lambda *a, **kw: Response())
    raw, _ = us._fetch_yahoo_timeseries_financials_for_ticker('TEST', 'quarterly', include_fourth_quarter=True)
    assert raw.period_end.tolist() == ['2025-12-31']
    from financial_database import us_raw_statements
    converted = us_raw_statements(raw, 'quarterly', '2026-10-09', 'hash')
    assert converted[0]['frequency'] == 'quarterly'
    assert converted[0]['values']['revenue'] == 100
