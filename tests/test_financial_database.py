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


def test_korean_refresh_collects_verified_comparative_periods(monkeypatch):
    import pandas as pd
    import financial_database as database
    from ai_stock_assistant.data import opendart
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {'status': '000', 'acc_mt': '12', 'corp_name': 'Example', 'induty_code': '26'}
    monkeypatch.setattr(database.requests, 'get', lambda *a, **kw: Response())
    monkeypatch.setattr(database.time, 'sleep', lambda _: None)
    monkeypatch.setattr(opendart, 'get_api_key', lambda: 'test-only')
    calls = []
    def fetch(code, year, report, key):
        calls.append((year, report))
        if report == '11014' and year == 2026:
            return pd.DataFrame(), 'CFS', '013', ''
        frame = pd.DataFrame([{'bsns_year': year, 'reprt_code': report,
            'rcept_no': f'{year}0814000001', 'account_id': 'ifrs-full_Revenue',
            'account_nm': 'Revenue', 'sj_div': 'IS', 'thstrm_amount': '200' if year == 2026 else '100'}])
        return frame, 'CFS', '000', ''
    monkeypatch.setattr(opendart, 'fetch_financial_statement_with_fallback', fetch)
    rows, metadata = database.fetch_kr('000001', '00000001', None, '2026-10-09')
    assert (2024, '11011') in calls and (2025, '11012') in calls
    latest = financial_metrics({'statements': rows, **metadata}, '2026-10-09')['latest']
    assert latest['period_end'] == '2026-06-30'
    assert latest['metrics']['fin_revenue_growth'] == 1


def test_failure_diagnostics_do_not_expose_provider_urls_or_keys():
    from financial_database import error_diagnostic
    try:
        raise ValueError('https://provider.invalid/?crtfc_key=private-test-value')
    except ValueError as error:
        diagnostic = error_diagnostic(error)
    assert diagnostic['error_type'] == 'ValueError'
    assert 'test_financial_database.py:' in diagnostic['error_location']
    assert 'provider.invalid' not in json.dumps(diagnostic)
    assert 'private-test-value' not in json.dumps(diagnostic)


def test_transient_provider_retry_is_bounded_and_does_not_retry_db_conflicts(monkeypatch):
    import requests
    import financial_database as database
    monkeypatch.setattr(database.time, 'sleep', lambda _: None)
    calls = []
    def transient():
        calls.append(1)
        if len(calls) < 3:
            raise requests.ConnectionError('transient')
        return 'saved response'
    assert database.provider_read(transient) == 'saved response'
    assert len(calls) == 3
    calls.clear()
    def failed():
        calls.append(1)
        raise requests.Timeout('timeout')
    with pytest.raises(requests.Timeout):
        database.provider_read(failed)
    assert len(calls) == 3
    calls.clear()
    def conflict():
        calls.append(1)
        raise BackendError(409, 'changed')
    with pytest.raises(BackendError):
        database.provider_read(conflict)
    assert len(calls) == 1


def test_korean_ttm_bridges_cumulative_income_and_cash_flows():
    rows = []
    for year, code, net, cfo, assets in [(2025, '11011', 10, 20, 160),
            (2025, '11012', 3, 7, 100), (2026, '11012', 6, 12, 200)]:
        raw = {'bsns_year': year, 'reprt_code': code, 'fs_div': 'CFS',
               'net_income': net if code == '11011' else 2, 'operating_cash_flow': cfo, 'total_assets': assets}
        rows.append(normalized_statement(raw, source='opendart', observed_at='2026-10-09', source_hash=str(year)+code,
            fiscal_month='12', frequency='annual' if code == '11011' else 'reported',
            ytd_values={'net_income': net, 'operating_cash_flow': cfo}))
    latest = financial_metrics({'statements': rows}, '2026-10-09')['latest']
    assert latest['metrics']['fin_roa'] == pytest.approx(13/150)
    assert latest['metrics']['fin_cfo_assets'] == pytest.approx(25/150)
    assert latest['metric_details']['fin_roa']['basis'] == 'ttm'
    rows[-1]['ytd_values']['net_income'] = None
    assert financial_metrics({'statements': rows}, '2026-10-09')['latest']['metrics']['fin_roa'] is None


def test_us_ttm_four_quarters_and_average_balance_endpoints():
    rows = []
    for end, net, assets in [('2025-06-30', 1, 100), ('2025-09-30', 3, 120),
                             ('2025-12-31', 4, 140), ('2026-03-31', 5, 170), ('2026-06-30', 6, 200)]:
        row = {'bsns_year': end[:4], 'reprt_code': {'03':'11013','06':'11012','09':'11014','12':'11011'}[end[5:7]],
               'net_income': net, 'operating_cash_flow': net*2, 'total_assets': assets}
        rows.append(normalized_statement(row, source='yahoo', observed_at='2026-10-09', source_hash=end,
                                         period_end=end, frequency='quarterly'))
    latest = financial_metrics({'statements': rows}, '2026-10-09')['latest']
    assert latest['metrics']['fin_roa'] == pytest.approx(18/150)
    assert latest['metrics']['fin_cfo_assets'] == pytest.approx(36/150)
    assert latest['metric_details']['fin_cfo_assets']['basis'] == 'ttm'
    rows.pop(2)
    assert financial_metrics({'statements': rows}, '2026-10-09')['latest']['metrics']['fin_roa'] is None


def test_latest_annual_fallback_has_its_own_date_and_denominator():
    quarter = statement(2026, total_assets=300)
    quarter.update(period_end='2026-03-31', period_label='2026-03-31', report_code='11013', frequency='quarterly')
    rows = [statement(2024, total_assets=100), statement(2025, current_assets=100, current_liabilities=0), quarter]
    latest = financial_metrics({'statements': rows}, '2026-10-09')['latest']
    assert latest['metrics']['fin_roa'] == pytest.approx(10/150)
    assert latest['metric_details']['fin_roa']['basis'] == 'annual_fallback'
    assert latest['metric_details']['fin_roa']['period_end'] == '2025-12-31'
    assert latest['period_end'] == '2026-03-31'
    assert latest['metrics']['fin_current_ratio'] is None


def test_dart_parser_separates_ytd_from_quarter_and_ignores_equity_statement():
    import pandas as pd
    from financial_database import kr_raw_statements, enrich_retained
    base = {'ticker':'005930','bsns_year':'2026','reprt_code':'11012','fs_div':'CFS','rcept_no':'20260814000001'}
    entries = []
    for tag, section, amount, cumulative in [('ifrs-full_ProfitLoss','SCE',999,999),
        ('ifrs-full_ProfitLoss','IS',2,6), ('ifrs-full_ProfitLoss','CIS',2,6),
        ('ifrs-full_CashFlowsFromUsedInOperatingActivities','CF',12,''),
        ('ifrs-full_CurrentAssets','BS',100,''), ('ifrs-full_CurrentLiabilities','BS',50,''),
        ('ifrs-full_OtherCurrentAssets','BS',500,'')]:
        entries.append({**base,'account_id':tag,'sj_div':section,'thstrm_amount':amount,'thstrm_add_amount':cumulative})
    row = kr_raw_statements(pd.DataFrame(entries), '2026-10-09', 'raw', '12')[0]
    assert row['values']['net_income'] == 2
    assert row['ytd_values']['net_income'] == 6
    assert row['ytd_values']['operating_cash_flow'] == 12
    latest = financial_metrics({'statements':[row]}, '2026-10-09')['latest']
    assert latest['metrics']['fin_current_ratio'] == 2
    old = copy.deepcopy(row); old.pop('normalization_version'); old.pop('ytd_values')
    assert merge_statements([old],[row])[0]['ytd_values']['net_income'] == 6


def test_retained_enrichment_is_non_destructive_and_preserves_observation(tmp_path):
    import pandas as pd
    from financial_database import enrich_retained
    old = normalized_statement({'bsns_year':'2025','reprt_code':'11011','fs_div':'CFS','total_assets':200},
        source='opendart', observed_at='2026-10-09', source_hash='original', frequency='annual')
    entries = [{'ticker':'005930','bsns_year':'2025','reprt_code':'11011','fs_div':'CFS','rcept_no':'20260314000001',
                'account_id':tag,'sj_div':'BS','thstrm_amount':value}
               for tag,value in [('ifrs-full_Assets',200),('ifrs-full_CurrentAssets',100),('ifrs-full_CurrentLiabilities',50)]]
    path=tmp_path/'source.csv'; pd.DataFrame(entries).to_csv(path,index=False)
    enriched=enrich_retained({'statements':[old]},[path])
    merged=merge_statements([old],enriched)[0]
    assert merged['values']['current_assets'] == 100
    assert merged['observed_at'] == old['observed_at']
    assert merged['source_sha256'] == 'original' and merged['period_end'] is None
    changed=copy.deepcopy(old); changed['values']['total_assets']=300
    assert enrich_retained({'statements':[changed]},[path]) == [changed]
