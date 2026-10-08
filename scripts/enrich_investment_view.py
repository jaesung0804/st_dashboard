"""Explicit read-only enrichment of the pinned research presentation."""
import argparse
import hashlib
import json
import os
from pathlib import Path

import pandas as pd

from research_backend_client import Client
from run_relative_ews_research import restore_inputs, live, price_quality
from build_investment_metrics import financial_summary, price_summaries


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--snapshot-name', required=True)
    a = p.parse_args()
    root = a.root.resolve()
    allowed = (Path.cwd()/'.research-backend/artifacts').resolve()
    if root.exists() or root == allowed or not root.is_relative_to(allowed):
        raise ValueError('Use a new ignored artifact directory; restore saved outputs before continuing')
    ref = json.loads(Path('data/reference/relative_ews_release.json').read_text())
    client = Client(project='investment')
    if client.json('GET', '/snapshot-heads/'+a.snapshot_name)['snapshot_id']:
        raise ValueError('Existing output must be restored, not overwritten')
    entries = [e for e in client.snapshot_entries(ref['snapshot_id']) if e['relative_path']=='investment_view.json']
    assert len(entries)==1 and entries[0]['sha256']==ref['report_sha256']
    source = root/'source/investment_view.json'
    client.download(ref['report_sha256'],source,entries[0]['byte_size'])
    report = json.loads(source.read_text())
    original = { (r['market'],r['ticker']):dict(r) for r in report['rows'] }
    restored, provenance = restore_inputs(root/'inputs',report['source']['snapshot_id'])
    coverage = {}
    for market in ('kr','us'):
        date = report['markets'][market]['latest']['asof']
        prices, quality = price_quality.prepare(live.read_prices(restored/'data/raw'/live.PRICE_FILES[market]),market)
        listings = pd.read_csv(restored/'data/raw'/live.LISTING_FILES[market],dtype={'ticker':str})
        summaries = price_summaries(prices,listings,date)
        events = pd.read_csv(restored/f'data/dashboard_research/accounting/{market}/filing_events.csv.gz',
            dtype={'ticker':str,'filing_id':str},parse_dates=['available_at','period_end'])
        for row in report['rows']:
            if row['market']!=market: continue
            row['price_metrics'] = summaries.get(row['ticker'])
            if row['price_metrics']: row['price_metrics']['currency'] = 'KRW' if market=='kr' else 'USD'
            row['financials'] = financial_summary(events,row['ticker'],date)
            # Enrichment must never alter a saved score, opinion or model field.
            assert {k:v for k,v in row.items() if k not in ('price_metrics','financials')} == original[(market,row['ticker'])]
        rows = [r for r in report['rows'] if r['market']==market]
        coverage[market] = {'prices':sum(bool(r['price_metrics']) for r in rows),
            'financials':sum(bool(r['financials']['latest']) for r in rows),
            'charts':sum(bool(r['price_metrics'] and r['price_metrics']['chart']) for r in rows)}
    report['presentation_metrics'] = {'version':1,'created_at':live.utc_now(),'code_commit':os.getenv('GITHUB_SHA','local'),
        'base_snapshot':ref['snapshot_id'],'base_report_sha256':ref['report_sha256'],
        'source_snapshot':provenance['snapshot_id'],'coverage':coverage,
        'price_basis':'retained adjusted close; current quote uses unadjusted close; consecutive local-market sessions required',
        'volatility':'20 daily log returns, sample standard deviation * sqrt(252)',
        'drawdown':'current adjusted close / maximum of last 252 closes - 1; not maximum drawdown',
        'financial_basis':'filed+1 day; latest available amendment per reported period; cash-flow/ROA denominator is average assets',
        'valuation':'PER/PBR/ROE not manufactured from incomplete per-share, earnings or average-equity inputs'}
    output = root/'result'; output.mkdir(parents=True)
    live.write_json(output/'investment_view.json',report)
    archived = client.push(a.snapshot_name,output,['investment_view.json'])
    print(json.dumps({'archived':archived,'report_sha256':live.digest(output/'investment_view.json'),'coverage':coverage}))


if __name__=='__main__': main()
