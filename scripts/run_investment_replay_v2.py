"""Run predeclared US experiments from retained short and long data, offline."""
from __future__ import annotations
import argparse
import collections
import json
from pathlib import Path
import sys
import time
import subprocess
import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ai_stock_assistant.investment_replay import prepare, sha256, write_json, metrics, read_prices
from ai_stock_assistant.investment_replay_v2 import replay_v2
from run_investment_replay import ledger, write_gzip


def load_long(source, old, cohort):
    frames, provenance = [], []
    for ticker in cohort:
        p=source/f'collection/prices/{ticker}.csv.gz'
        v=source/f'validation/details/{ticker}.json'
        validation=json.loads(v.read_text()) if v.exists() else {'status':'missing','issues':[]}
        issues=[x['code'] for x in validation['issues']]
        fallback=not p.exists() or 'new_source_loses_retained_history' in issues or any(x.startswith('invalid_sign_') for x in issues)
        if fallback:
            frame=old.loc[old.ticker.eq(ticker)].copy()
            origin='retained_2021_snapshot_not_spliced'
            digest=None
        else:
            frame=pd.read_csv(p,dtype={'ticker':str})
            origin='long_2006_snapshot'
            digest=sha256(p)
        if frame.empty:
            raise ValueError(f'No retained data for {ticker}; cannot silently delete an asset')
        frame['date']=pd.to_datetime(frame.date)
        for col in ['open','high','low','close','adjusted_close']:
            frame.loc[~np.isfinite(frame[col]) | frame[col].le(0),col]=np.nan
        # Invalid cells remain missing; large real crashes are not excluded ex post.
        frames.append(frame)
        provenance.append(dict(ticker=ticker,source=origin,sha256=digest,validation=validation['status'],issues=issues,
                               rows=len(frame),start=str(frame.date.min().date()),end=str(frame.date.max().date())))
    result=pd.concat(frames,ignore_index=True).sort_values(['date','ticker'])
    return result,provenance


def benchmark(prices, dates, capital, rate, fill_delay=1):
    p=prices.set_index('date').reindex(pd.to_datetime(dates))
    adjusted_open=p.open*p.adjusted_close/p.close
    if len(dates)<=fill_delay or not np.isfinite(adjusted_open.iloc[fill_delay]):
        raise ValueError('Missing benchmark next open')
    units=capital/(float(adjusted_open.iloc[fill_delay])*(1+rate))
    nav=units*p.adjusted_close.to_numpy()
    nav[:fill_delay]=capital
    if not np.isfinite(nav).all():raise ValueError('Missing benchmark valuation')
    return nav,capital-capital/(1+rate)


def save_run(out, key, result, spy, policy, detail):
    target=out/key
    target.mkdir(parents=True)
    capital=policy['initial_capital']['us']
    spy_nav,fee=benchmark(spy,result['dates'],capital,policy['all_in_cost_bps']['us']*result['cost_multiplier']/10000,result['fill_delay'])
    for name,nav,cost in [('spy',spy_nav,fee),('spy_cash_matched',.9*spy_nav+.1*capital,.9*fee)]:
        result['series'][name]=nav.tolist()
        result['reserved_series'][name]=nav.tolist()
        result['summary'][name]=metrics(nav,capital)|dict(cost_paid=cost,trade_count=1,stale_days=0,
                                                       cagr=(nav[-1]/capital)**(252/len(nav))-1,stale_reserved_return=nav[-1]/capital-1,periods={})
        for label,start,end in policy['review_windows']:
            indexes=np.flatnonzero((np.array(result['dates'])>=start)&(np.array(result['dates'])<=end))
            if len(indexes):
                prev=capital if indexes[0]==0 else nav[indexes[0]-1]
                result['summary'][name]['periods'][label]=metrics(nav[indexes],prev)
    write_gzip(target/'nav.csv.gz',pd.DataFrame({'date':result['dates'],**result['series']}).to_csv(index=False))
    write_gzip(target/'stale_reserve_nav.csv.gz',pd.DataFrame({'date':result['dates'],**result['reserved_series']}).to_csv(index=False))
    tail=None
    if detail:
        content,tail=ledger(result['decisions'])
        write_gzip(target/'decisions.jsonl.gz',content)
        write_gzip(target/'trades.csv.gz',pd.DataFrame(result['trades']).to_csv(index=False))
        write_json(target/'governance.json',result['governance'])
        write_json(target/'transfers.json',result['transfers'])
    summary={k:result[k] for k in ['variant','summary','audit','fill_delay','cost_multiplier','rebalance_phase','unfilled_order_batches']}
    summary.update(start=result['dates'][0],end=result['dates'][-1],decision_count=len(result['decisions']),
                   governance_count=len(result['governance']),transfer_count=len(result['transfers']),ledger_tail=tail,
                   full_ledger_saved=detail)
    write_json(target/'summary.json',summary)
    return summary


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--policy',type=Path,default=Path('data/reference/investment_replay_v2_policy.json'))
    ap.add_argument('--source',type=Path,required=True)
    ap.add_argument('--out',type=Path,default=Path('docs/replays/2026-09-10-v2'))
    args=ap.parse_args()
    if args.out.exists():raise SystemExit('Output exists; preserve it and use a new directory')
    policy=json.loads(args.policy.read_text())
    if any(policy.get(k) for k in ['llm_api_calls','paid_data_calls','telegram_enabled','daily_schedule_enabled']):
        raise SystemExit('Only offline execution is permitted')
    started=time.perf_counter()
    args.out.mkdir(parents=True)
    write_json(args.out/'policy.json',policy)
    cohort=json.loads(Path('data/reference/accounting_cohort.json').read_text())['markets']['us']
    cache=Path('data/raw/us_replay_cohort_cache.pkl')
    if cache.exists():
        old=pd.read_pickle(cache)
    else:
        old=read_prices(Path('data/raw/us_ohlcv_nasdaq_nyse_yfinfo_state.csv'),cohort,'us')
        old.to_pickle(cache)
    long,provenance=load_long(args.source,old,cohort)
    write_json(args.out/'data_provenance.json',provenance)
    # Cache is local only. It is reproducible from the individually hashed inputs.
    long.to_pickle('data/raw/us_replay_long_cache.pkl')
    filings=pd.read_csv('data/dashboard_research/accounting/us/filing_events.csv.gz',dtype={'ticker':str,'filing_id':str})
    spy_path=args.source/'collection/prices/SPY.csv.gz'
    spy=pd.read_csv(spy_path,parse_dates=['date'])
    panels={'retained':prepare(old,filings,policy,'us'),'long':prepare(long,filings,policy,'us')}
    for key,panel in panels.items():
        panel.audit['official_market_benchmark_available']=True
        panel.audit['benchmark']='SPY adjusted-price buy and hold; 90% sleeve + 10% zero-interest cash control also shown'
        panel.audit['cohort_warning']='128 retained current-listing names selected with 2022 liquidity. Before 2023 this is a biased historical stress study, not investable PIT evidence.'
    summaries={}
    for case in policy['experiments']:
        mark=time.perf_counter()
        run_policy=policy|{'start':case['start']}
        print('START',case['id'],flush=True)
        result=replay_v2(panels[case['source']],run_policy,variant=case['variant'],cost_multiplier=case.get('cost_multiplier',1),
                         fill_delay=case.get('fill_delay',1),rebalance_phase=case.get('phase',0),record_details=case.get('detail',False))
        summaries[case['id']]=save_run(args.out,case['id'],result,spy,run_policy,case.get('detail',False))
        summaries[case['id']]['elapsed_seconds']=round(time.perf_counter()-mark,3)
        print('DONE',case['id'],round(time.perf_counter()-mark,2),{c:round(result['summary'][c]['net_return']*100,2) for c in policy['companies']},flush=True)
    summary=dict(schema='us-investment-replay-v2',policy=policy,runs=summaries,elapsed_seconds=round(time.perf_counter()-started,3),
                 data_sources=dict(long_start=str(long.date.min().date()),long_rows=len(long),tickers=len(cohort),
                                   origin_counts=dict(collections.Counter(x['source'] for x in provenance))))
    write_json(args.out/'summary.json',summary)
    artifacts={str(p.relative_to(args.out)):sha256(p) for p in sorted(args.out.rglob('*')) if p.is_file()}
    write_json(args.out/'manifest.json',dict(created_at=pd.Timestamp.now(tz='UTC').isoformat(),policy_sha256=sha256(args.policy),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        inputs={'accounting_cohort':sha256(Path('data/reference/accounting_cohort.json')),
                'filings':sha256(Path('data/dashboard_research/accounting/us/filing_events.csv.gz')),
                'SPY':sha256(spy_path),'retained_prices':'d20459d3205b7694d2e77068bd7e1d7e329c970e3c0d2f46c17f0b334110d94a'},
        code={str(p):sha256(p) for p in [Path(__file__),Path('src/ai_stock_assistant/investment_replay.py'),Path('src/ai_stock_assistant/investment_replay_v2.py')]},
        artifacts=artifacts,llm_api_calls=0,paid_data_calls=0,scheduled_tasks_created=0,telegram_messages_sent=0))
    print('COMPLETE',args.out,summary['elapsed_seconds'],flush=True)


if __name__=='__main__':main()
