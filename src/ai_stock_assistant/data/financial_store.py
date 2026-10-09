"""Presentation-only financial statements. Never used as historical model inputs.

Statements retain fiscal dates, provider, observation time and source hashes.
Unknown filing dates and fiscal calendars are never invented from fiscal years.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import math


FIELDS = ('revenue', 'gross_profit', 'operating_income', 'net_income', 'total_assets',
          'total_liabilities', 'total_equity', 'cash', 'operating_cash_flow', 'capex',
          'current_assets', 'current_liabilities', 'interest_expense', 'short_term_debt',
          'long_term_debt', 'investing_cash_flow', 'financing_cash_flow', 'eps', 'bps')
METRICS = ('fin_revenue_growth', 'fin_gross_margin', 'fin_operating_margin', 'fin_roa',
           'fin_cfo_assets', 'fin_cfo_after_ppe_assets', 'fin_accruals_assets',
           'fin_cash_assets', 'fin_liabilities_assets', 'fin_current_ratio',
           'fin_ppe_sales', 'fin_interest_coverage', 'equity_assets', 'liabilities_equity')
REPORTS = {'11013': '1분기', '11012': '반기', '11014': '3분기', '11011': '연간'}


def number(value):
    try:
        result = float(str(value).replace(',', ''))
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(',', ':')).encode()


def statement_id(row):
    return '|'.join(str(row.get(k) or '') for k in
                    ('source', 'scope', 'fiscal_year', 'report_code', 'frequency', 'period_end'))


def merge_statements(previous, incoming):
    """Retain every registered statement; SQL revisions retain amended values."""
    result = {statement_id(row): row for row in previous}
    for row in incoming:
        key = statement_id(row)
        old = result.get(key)
        if old and old.get('source_sha256') == row.get('source_sha256'):
            continue
        if old and old.get('observed_at', '') > row.get('observed_at', ''):
            continue
        # An empty or partial provider response cannot erase retained values.
        if old:
            row = {**old, **row, 'values': {**old['values'],
                   **{k: v for k, v in row['values'].items() if v is not None}}}
        result[key] = row
    return [result[key] for key in sorted(result)]


def normalized_statement(row, *, source, observed_at, source_hash, period_end=None,
                         frequency=None, filing_id=None, fiscal_month=None):
    year, code = str(row['bsns_year']), str(row['reprt_code'])
    # DART quarter ends are valid only after verifying a December fiscal calendar.
    if source == 'opendart' and str(fiscal_month).zfill(2) == '12':
        period_end = year + {'11013': '-03-31', '11012': '-06-30',
                            '11014': '-09-30', '11011': '-12-31'}[code]
    filed_at = None
    if filing_id and len(filing_id) == 14 and filing_id.isdigit():
        filed_at = date.fromisoformat(f'{filing_id[:4]}-{filing_id[4:6]}-{filing_id[6:8]}').isoformat()
    if period_end:
        period_end = date.fromisoformat(str(period_end)[:10]).isoformat()
    return {'source': source, 'scope': 'provider' if source == 'yahoo' else str(row.get('fs_div') or ''),
            'fiscal_year': year, 'report_code': code, 'frequency': frequency or 'reported',
            'period_end': period_end, 'period_label': period_end or f'{year}년 {REPORTS.get(code, code)}',
            'filed_at': filed_at, 'filing_id': filing_id, 'observed_at': observed_at,
            'source_sha256': source_hash, 'values': {k: number(row.get(k)) for k in FIELDS}}


def ratio(a, b):
    return number(a / b) if a is not None and b is not None and b > 0 else None


def financial_metrics(payload, asof):
    """Safe current-period ratios, with TTM only when durations are verified."""
    today = date.fromisoformat(asof[:10])
    rows = [r for r in payload.get('statements', [])
            if r.get('observed_at', '')[:10] <= asof[:10]
            and (not r.get('filed_at') or r['filed_at'] <= asof[:10])
            and (not r.get('period_end') or r['period_end'] <= asof[:10])
            and any(v is not None for v in r['values'].values())]
    empty = {'latest': None, 'history': [], 'age_days': None, 'stale': False,
             'status': 'not_collected', 'asof': asof, 'financial_sector': None}
    if not rows:
        return empty
    # A scope change is not a source of missing-value fills.
    scopes = {r['scope'] for r in rows}
    scope = 'CFS' if 'CFS' in scopes else ('OFS' if 'OFS' in scopes else sorted(scopes)[0])
    rows = [r for r in rows if r['scope'] == scope]
    def period_key(r):
        return tuple(r.get(k) for k in ('source', 'scope', 'fiscal_year', 'report_code', 'frequency'))
    dated_periods = {period_key(r) for r in rows if r.get('period_end')}
    rows = [r for r in rows if r.get('period_end') or period_key(r) not in dated_periods]
    quarter_order = {'11013': 1, '11012': 2, '11014': 3, '11011': 4}
    def order(r):
        nominal = f"{r['fiscal_year']}-{quarter_order.get(r['report_code'], 0) * 3:02}-31"
        return (r.get('period_end') or nominal, r['observed_at'], r['frequency'] == 'annual')
    rows.sort(key=order)
    last = rows[-1]
    v = last['values']
    metrics = dict.fromkeys(METRICS)
    prior = [r for r in rows if int(r['fiscal_year']) == int(last['fiscal_year']) - 1
             and r['report_code'] == last['report_code'] and r['frequency'] == last['frequency']]
    # Exact fiscal dates must also agree across the year comparison.
    if last.get('period_end'):
        prior = [r for r in prior if r.get('period_end') and
                 345 <= (date.fromisoformat(last['period_end']) - date.fromisoformat(r['period_end'])).days <= 385]
    prev = prior[-1]['values'] if prior else {}
    growth = ratio(v.get('revenue'), prev.get('revenue'))
    metrics['fin_revenue_growth'] = growth - 1 if growth is not None else None
    metrics['fin_operating_margin'] = ratio(v.get('operating_income'), v.get('revenue'))
    metrics['fin_gross_margin'] = ratio(v.get('gross_profit'), v.get('revenue'))
    for key, numerator, denominator in (
        ('fin_cash_assets', 'cash', 'total_assets'),
        ('fin_liabilities_assets', 'total_liabilities', 'total_assets'),
        ('equity_assets', 'total_equity', 'total_assets'),
        ('liabilities_equity', 'total_liabilities', 'total_equity'),
        ('fin_current_ratio', 'current_assets', 'current_liabilities')):
        metrics[key] = ratio(v.get(numerator), v.get(denominator))
    basis = 'reported_period'
    # Annual values are a full-year flow; unverified quarterly/YTD flows are not annualized.
    if last['frequency'] == 'annual':
        basis = 'annual'
        assets, old_assets = v.get('total_assets'), prev.get('total_assets')
        avg = (assets + old_assets) / 2 if assets is not None and old_assets is not None else None
        metrics['fin_roa'] = ratio(v.get('net_income'), avg)
        metrics['fin_cfo_assets'] = ratio(v.get('operating_cash_flow'), avg)
        cfo, net, capex = v.get('operating_cash_flow'), v.get('net_income'), v.get('capex')
        metrics['fin_accruals_assets'] = ratio(net - cfo, avg) if net is not None and cfo is not None else None
        metrics['fin_cfo_after_ppe_assets'] = ratio(cfo - abs(capex), avg) if cfo is not None and capex is not None else None
        metrics['fin_ppe_sales'] = ratio(abs(capex), v.get('revenue')) if capex is not None else None
        metrics['fin_interest_coverage'] = ratio(v.get('operating_income'), v.get('interest_expense'))
    financial_sector = payload.get('financial_sector')
    if financial_sector:
        for key in ('fin_gross_margin', 'fin_cfo_assets', 'fin_cfo_after_ppe_assets',
                    'fin_accruals_assets', 'fin_current_ratio', 'fin_ppe_sales', 'fin_interest_coverage'):
            metrics[key] = None
    age = (today - date.fromisoformat(last['period_end'])).days if last.get('period_end') else None
    latest = {k: last.get(k) for k in ('period_end', 'period_label', 'filed_at', 'filing_id',
                                      'observed_at', 'source', 'scope')}
    latest.update(metrics=metrics, basis=basis, available_at=last.get('filed_at'))
    return {'latest': latest, 'history': [], 'age_days': age, 'stale': age is not None and age > 240,
            'status': 'available' if last.get('filed_at') else 'filing_date_unverified',
            'asof': asof, 'financial_sector': financial_sector,
            'date_precision': 'day' if last.get('period_end') else 'fiscal_period',
            'model_eligible': False}


def payload_hash(payload):
    return hashlib.sha256(canonical(payload)).hexdigest()
