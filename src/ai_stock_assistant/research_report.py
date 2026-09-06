"""Public, compact research report; never alters the production forecast archive."""
from pathlib import Path
import json
import pandas as pd
from .monthly_ews import read_json, write_json, LISTING_FILES


def export_report(root: Path, raw: Path, market: str, target: Path):
    study = root / 'analysis' / 'market_study.json'
    report = {'available': study.exists(), 'market': market}
    if study.exists():
        research = read_json(study)
        report.update(market_study=research['markets'][market],
                      cross_market=research['cross_market'],
                      period=[research['start_close'], research['end_close']])
    accounting = root / 'accounting' / market
    if (accounting / 'comparison.json').exists():
        report['accounting'] = read_json(accounting / 'comparison.json')
        report['accounting_audit'] = read_json(accounting / 'source_audit.json')
        scores = pd.read_csv(accounting / 'latest_accounting_scores.csv.gz', dtype={'ticker': str})
        listing = pd.read_csv(raw / LISTING_FILES[market], dtype={'ticker': str}).drop_duplicates('ticker')
        scores = scores.merge(listing[['ticker', 'name']], on='ticker', how='left')
        report['accounting_scores'] = json.loads(scores.to_json(orient='records', force_ascii=False))
    write_json(target, report)
