"""Explicit migration/collection into Oracle records; publish only derived ratios.

Uses the deployed authenticated, versioned SQL record API. No new DB, local DB,
raw-data Git branch or browser credentials. Temporary migration inputs are removed.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.parse

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ai_stock_assistant.data.financial_store import (canonical, financial_metrics,
    merge_statements, normalized_statement, number, payload_hash, statement_id)
from research_backend_client import Client, BackendError
from unpack_dashboard_state import restore_state

PREFIX = 'financial-statements-v1/'
SNAPSHOT = 'financial-metrics-current'
LISTINGS = {'kr': 'data/raw/krx_listings_kospi_kosdaq_state.csv',
            'us': 'data/raw/us_listings_nasdaq_nyse_yfinfo_state.csv'}
SUMMARY_FILES = {'kr': 'kr.json', 'us': 'us.json'}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def restore_inputs(client, root, markets, bootstrap=False):
    """Restore selected inputs from the verified pipeline snapshot before collecting."""
    sid = client.json('GET', '/snapshot-heads/pipeline-state')['snapshot_id']
    if not sid:
        raise ValueError('Existing pipeline-state is required; refusing empty initialization')
    entries = {e['relative_path']: e for e in client.snapshot_entries(sid)}
    packed = root / 'packed'
    def download(path):
        e = entries[path]
        client.download(e['sha256'], packed / path, e['byte_size'])
    download('state-manifest.json')
    raw = (packed / 'state-manifest.json').read_bytes()
    manifest = json.loads(raw)
    wanted = [LISTINGS[m] for m in markets]
    if 'kr' in markets:
        wanted.append('data/raw/opendart_corp_codes.csv')
    if bootstrap:
        if 'kr' in markets:
            wanted.append('data/raw/opendart_financials_state.csv')
        if 'us' in markets:
            wanted.append('data/raw/yfinance_financials')
    selected = {p: manifest[p] for p in wanted}
    paths = {part for p, info in selected.items() for part in info.get('parts', [p])}
    if sum(entries[p]['byte_size'] for p in paths) > 256 * 1024**2:
        raise ValueError('Financial restore exceeds 256 MiB compressed input budget')
    for path in sorted(paths):
        download(path)
    (packed / 'state-manifest.json').write_bytes(canonical(selected))
    restored = root / 'restored'
    restore_state(packed, restored)
    return restored, {'snapshot_id': sid, 'manifest_sha256': hashlib.sha256(raw).hexdigest(),
                      'source_hashes': {p: manifest[p]['sha256'] for p in wanted}}


def source_index(client):
    # Read bounded summaries first, then get only companies selected for work.
    result, cursor = {}, ''
    while True:
        page = client.json('GET', '/records/sources?' + urllib.parse.urlencode({'after': cursor, 'limit': 20}))
        for item in page['items']:
            if item['record_key'].startswith(PREFIX):
                result[item['record_key']] = item
        cursor = page['next_cursor']
        if cursor is None:
            return result


def record_path(key):
    return '/records/sources/' + urllib.parse.quote(key, safe='')


def write_company(client, index, market, ticker, rows, metadata, checked_at=None, import_only=False):
    key = PREFIX + market + '/' + ticker
    old = client.json('GET', record_path(key)) if key in index else None
    previous = old['payload'] if old else {}
    if import_only:
        existing = {statement_id(r) for r in previous.get('statements', [])}
        rows = [r for r in rows if statement_id(r) not in existing]
    payload = {**previous, **metadata, 'schema': 1, 'market': market, 'ticker': ticker,
               'statements': merge_statements(previous.get('statements', []), rows)}
    if not payload['statements']:
        raise ValueError('Empty financial result cannot replace retained statements')
    # Check times live in the small metrics checkpoint. An unchanged provider
    # response must not create a full statement revision just because time passed.
    payload.pop('last_checked_at', None)
    if len(canonical(payload)) > 120000:
        raise ValueError('Company record exceeds budget; split by fiscal year before publishing')
    result = client.json('PUT', record_path(key), {'payload': payload,
        'expected_version': old['version'] if old else 0,
        'summary': f'Financial statements {market}/{ticker}: {len(payload["statements"])} periods',
        'observed_at': checked_at or utc_now()})
    # A conflict propagates: the caller stops publication instead of overwriting.
    return payload, {'key': key, 'version': result['version'], 'sha256': payload_hash(payload)}


def us_raw_statements(raw, frequency, observed_at, sha):
    result = []
    if raw.empty:
        return result
    for period, group in raw.groupby('period_end'):
        row = group.iloc[0].to_dict()
        for item in group.to_dict('records'):
            value = number(item.get('amount'))
            if value is not None:
                row[item['account_id']] = value
        result.append(normalized_statement(row, source='yahoo', observed_at=observed_at,
            source_hash=sha, period_end=str(period)[:10], frequency=frequency))
    return result


def import_rows(restored, market, observed_at, provenance):
    if market == 'kr':
        path = restored / 'data/raw/opendart_financials_state.csv'
        frame = pd.read_csv(path, dtype=str).fillna('')
        for ticker, group in frame.groupby('ticker', sort=True):
            rows = [normalized_statement(r, source='opendart', observed_at=observed_at,
                source_hash=provenance['source_hashes']['data/raw/opendart_financials_state.csv'],
                frequency='annual' if r['reprt_code'] == '11011' else 'reported')
                for r in group.to_dict('records')]
            yield str(ticker), rows
    else:
        folder = restored / 'data/raw/yfinance_financials'
        companies = {}
        for path in sorted(folder.rglob('*_raw.csv')):
            frequency = 'quarterly' if path.name.endswith('_quarterly_raw.csv') else 'annual'
            try:
                raw = pd.read_csv(path, dtype=str).fillna('')
            except pd.errors.EmptyDataError:
                continue  # Retained empty provider responses contain no statements.
            if raw.empty:
                continue
            if 'period_end' not in raw:
                raise ValueError('Saved US statement has no actual fiscal date')
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            for ticker, group in raw.groupby('ticker'):
                companies.setdefault(str(ticker), []).extend(us_raw_statements(group, frequency, observed_at, sha))
        yield from sorted(companies.items())


def fetch_kr(ticker, code, previous, observed_at):
    from ai_stock_assistant.data.opendart import (get_api_key,
        fetch_financial_statement_with_fallback, normalize_financial_accounts)
    key = get_api_key()
    response = requests.get('https://opendart.fss.or.kr/api/company.json',
        params={'crtfc_key': key, 'corp_code': code}, timeout=30)
    response.raise_for_status()
    profile = response.json()
    if profile.get('status') != '000':
        raise ValueError('DART profile unavailable')
    month = str(profile.get('acc_mt', '')).zfill(2)
    if month != '12':
        raise ValueError('Non-December fiscal calendar requires a verified period adapter')
    now = datetime.fromisoformat(observed_at)
    # Two annual periods and same-quarter comparatives make growth and annual
    # average-asset ratios verifiable even when legacy fiscal dates are unknown.
    periods = [(now.year - 2, '11011'), (now.year - 1, '11011')]
    periods += [(year, report) for end, report in ((3, '11013'), (6, '11012'), (9, '11014'))
                if now.month > end for year in (now.year - 1, now.year)]
    result = []
    for year, report in periods:
        frame, scope, status, _ = fetch_financial_statement_with_fallback(code, year, report, key)
        if status == '013':
            continue  # No filing yet: keep the last successful period.
        if status != '000' or frame.empty:
            raise ValueError('DART statement request failed')
        frame['ticker'], frame['corp_code'], frame['corp_name'], frame['fs_div'] = ticker, code, profile['corp_name'], scope
        sha = hashlib.sha256(frame.to_csv(index=False).encode()).hexdigest()
        filings = sorted(set(frame.rcept_no.dropna().astype(str)))
        if len(filings) != 1:
            raise ValueError('Ambiguous DART filing receipts')
        for row in normalize_financial_accounts(frame).to_dict('records'):
            for field, tag in (('current_assets', 'CurrentAssets'), ('current_liabilities', 'CurrentLiabilities')):
                matches = frame.loc[frame.account_id.astype(str).str.endswith('_' + tag) & frame.sj_div.eq('BS')]
                if len(matches) == 1:
                    row[field] = number(matches.iloc[0].thstrm_amount)
            result.append(normalized_statement(row, source='opendart', observed_at=observed_at,
                source_hash=sha, frequency='annual' if report == '11011' else 'reported',
                filing_id=filings[0], fiscal_month=month))
        time.sleep(.25)
    return result, {'financial_sector': str(profile.get('induty_code', ''))[:2] in ('64', '65', '66'),
                    'fiscal_month': month}


def fetch_us(ticker, name, observed_at):
    from ai_stock_assistant.data.us import fetch_us_financials_for_ticker
    rows = []
    for frequency in ('annual', 'quarterly'):
        raw, _ = fetch_us_financials_for_ticker(ticker=ticker, frequency=frequency, name=name,
                                               include_fourth_quarter=True)
        if raw.empty:
            continue
        sha = hashlib.sha256(raw.to_csv(index=False).encode()).hexdigest()
        rows.extend(us_raw_statements(raw, frequency, observed_at, sha))
        time.sleep(.3)
    if not rows:
        raise ValueError('Provider returned no financial statements')
    return rows, {}


def update_metrics(summary, ticker, payload, receipt, asof):
    summary.setdefault('companies', {})[ticker] = financial_metrics(payload, asof)
    summary.setdefault('record_versions', {})[ticker] = receipt


def run(args, client=None):
    if os.getenv('RESEARCH_STORAGE') != 'backend':
        raise ValueError('Financial DB tasks require configured backend storage; no local fallback')
    client = client or Client(project='investment')
    ready = client.json('GET', '/ready')
    if ready.get('database') != 'oracle':
        raise ValueError('Expected the existing Oracle investment database')
    markets = ['kr', 'us'] if args.market == 'all' else [args.market]
    observed_at = utc_now()
    with tempfile.TemporaryDirectory(prefix='financial-database-') as temporary:
        root = Path(temporary)
        summaries = root / 'summaries'
        summaries.mkdir()
        head = client.json('GET', '/snapshot-heads/' + SNAPSHOT)['snapshot_id']
        if head:
            client.pull(SNAPSHOT, summaries)
        elif args.command != 'bootstrap':
            raise ValueError('Bootstrap the retained statements before enabling scheduled refresh')
        restored, provenance = restore_inputs(client, root, markets, args.command == 'bootstrap')
        index = source_index(client)
        results = {}
        for market in markets:
            target = summaries / SUMMARY_FILES[market]
            summary = json.loads(target.read_text()) if target.exists() else {'schema': 1, 'market': market, 'companies': {}}
            listing = pd.read_csv(restored / LISTINGS[market], dtype=str).fillna('').set_index('ticker').to_dict('index')
            failures, saved = [], 0
            if args.command == 'bootstrap':
                work = list(import_rows(restored, market, observed_at, provenance))
            else:
                def priority(ticker):
                    key = PREFIX + market + '/' + ticker
                    return summary.get('collection_checks', {}).get(ticker,
                        index.get(key, {}).get('observed_at', ''))
                tickers = args.tickers or sorted(listing, key=lambda t: (priority(t), t))[:args.limit]
                work = [(ticker, None) for ticker in tickers if ticker in listing]
            if not work:
                raise ValueError('No financial inputs found for the requested market')
            codes = {}
            if market == 'kr':
                codes = pd.read_csv(restored / 'data/raw/opendart_corp_codes.csv', dtype=str).set_index('ticker').corp_code.to_dict()
            def process(item):
                ticker, rows = item
                worker = Client(project='investment')
                metadata = {'name': listing.get(ticker, {}).get('name', ticker)}
                if rows is None:
                    try:
                        if market == 'kr':
                            rows, extra = fetch_kr(ticker, codes[ticker], None, observed_at)
                        else:
                            rows, extra = fetch_us(ticker, metadata['name'], observed_at)
                        if not rows:
                            raise ValueError('No available reports')
                        metadata.update(extra)
                    except Exception as error:
                        # Never log provider URLs, request parameters or tokens.
                        return ticker, None, {'error_type': type(error).__name__}
                else:
                    metadata['migration_source'] = provenance
                payload, receipt = write_company(worker, index, market, ticker, rows, metadata,
                    observed_at if args.command == 'refresh' else None, import_only=args.command == 'bootstrap')
                return ticker, payload, receipt
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                for n, (ticker, payload, receipt) in enumerate(pool.map(process, work), 1):
                    if payload is None:
                        failures.append({'ticker': ticker, **receipt})
                    else:
                        update_metrics(summary, ticker, payload, receipt, observed_at[:10])
                        saved += 1
                    if args.command == 'refresh':
                        summary.setdefault('collection_checks', {})[ticker] = observed_at
                    if n % 100 == 0:
                        print(json.dumps({'market': market, 'processed': n, 'saved_to_db': saved, 'failed': len(failures)}), flush=True)
            if saved == 0:
                raise ValueError('No successful financial writes; previous public summary retained')
            # The saved collection status makes partial provider failures visible.
            summary.update(generated_at=observed_at, storage='oracle', presentation_only=True,
                collection={'attempted': len(work), 'saved': saved, 'failed': len(failures),
                            'failures': failures, 'mode': args.command}, source_snapshot=provenance['snapshot_id'])
            target.write_bytes(canonical(summary))
            results[market] = {'companies': len(summary['companies']), 'saved': saved, 'failed': len(failures)}
        publication = client.push(SNAPSHOT, summaries, [SUMMARY_FILES[m] for m in markets])
        print(json.dumps({'storage': 'oracle', 'metrics_snapshot': publication['snapshot_id'], 'markets': results}), flush=True)
        if any(r['failed'] > max(3, r['saved'] * .1) for r in results.values()):
            raise ValueError('Financial collection exceeded failure threshold; inspect retained diagnostics')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['bootstrap', 'refresh'])
    parser.add_argument('--market', choices=['kr', 'us', 'all'], default='all')
    parser.add_argument('--limit', type=int, default=600)
    parser.add_argument('--workers', type=int, choices=[1, 2, 3], default=3)
    parser.add_argument('--tickers', nargs='+')
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error('--limit must be 1..1000')
    try:
        run(args)
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__,
                          'http_status': error.status if isinstance(error, BackendError) else None}), flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
