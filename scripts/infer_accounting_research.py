"""Daily application of frozen monthly accounting models, separate from live EWS."""
import argparse, json, sys
from pathlib import Path
import joblib
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ai_stock_assistant import monthly_ews as live
from ai_stock_assistant.data import accounting_pit as accounting, macro_vintages as macro, price_quality


def run(market):
    root = Path('data/dashboard_research/accounting') / market
    prices, quality = price_quality.prepare(live.read_prices(Path('data/raw') / live.PRICE_FILES[market]), market)
    signal = prices.date.max(); month = signal.strftime('%Y-%m')
    folder = root / 'models' / 'accounting-pit-v3' / month
    if not folder.exists():
        raise ValueError(f'{market}: no frozen accounting model for {month}; run the monthly comparison first')
    panel = live.feature_panel(prices, market, training=False)
    dated = pd.read_csv(root / 'filing_events.csv.gz', dtype={'ticker': str, 'filing_id': str},
                        parse_dates=['available_at', 'filed', 'period_end'])
    cohort = json.loads(Path('data/reference/accounting_cohort.json').read_text())['markets'][market]
    panel = accounting.attach(panel.loc[panel.ticker.isin(cohort)], dated)
    vintages, _ = macro.load(Path('data/dashboard_ews_shadow'))
    panel = macro.attach(panel.loc[panel.fin_observed_fraction.ge(.25)], vintages)
    result = panel[['ticker', 'date', *accounting.FEATURES, 'filing_id', 'filed', 'period_end', 'available_at']].copy()
    for file in sorted(folder.glob('*.joblib')):
        card = live.read_json(file.with_suffix('.json'))
        if live.digest(file) != card['sha256']:
            raise ValueError('Accounting model checksum mismatch')
        arm, head = file.stem.rsplit('-', 1)
        result[arm + '_' + head] = accounting.predict_model(joblib.load(file), panel)
    result.to_csv(root / 'latest_accounting_scores.csv.gz', index=False)
    comparison=live.read_json(root / 'comparison.json');comparison['latest_rows']=len(result)
    comparison['latest_inference_at']=live.utc_now();comparison['latest_price_quality']=quality
    live.write_json(root / 'comparison.json',comparison)
    # Accounting source refresh is separate. Age/missingness remain visible.
    print(market, signal.date(), len(result), 'frozen accounting research scores')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--market', choices=['kr', 'us', 'all'], default='all')
    args = parser.parse_args()
    for market in (['kr', 'us'] if args.market == 'all' else [args.market]):
        run(market)
