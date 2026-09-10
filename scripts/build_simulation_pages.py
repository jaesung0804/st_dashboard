"""Build small, dependency-free simulation pages from completed result bundles."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import hashlib
import sys
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ai_stock_assistant.investment_operations import review_queue


def build(target:Path):
    source=ROOT/'docs/replays/2026-09-10-v2'
    if not (source/'summary.json').exists() or not (source/'manifest.json').exists():
        raise FileNotFoundError('Simulation requires a completed, verified run bundle')
    summary=json.loads((source/'summary.json').read_text())
    manifest=json.loads((source/'manifest.json').read_text())
    for name,digest in manifest['artifacts'].items():
        if hashlib.sha256((source/name).read_bytes()).hexdigest()!=digest:
            raise ValueError(f'Result artifact changed: {name}')
    target.mkdir(parents=True,exist_ok=True)
    for p in (ROOT/'scripts/simulation_web').iterdir():
        if p.is_file():shutil.copyfile(p,target/p.name)
    keys=['pulse','compound','adaptive','spy','spy_cash_matched','baseline_cash_matched']
    runs={}
    for key,run in summary['runs'].items():
        frame=pd.read_csv(source/key/'nav.csv.gz',parse_dates=['date'])
        # All drawdowns are computed BEFORE downsampling, including initial cash.
        dd={k:frame[k].to_numpy()/np.maximum.accumulate(np.r_[100000,frame[k].to_numpy()])[1:]-1 for k in keys}
        month=frame.date.dt.to_period('M')
        indexes=np.flatnonzero(month.ne(month.shift(-1)).to_numpy())
        indexes=np.unique(np.r_[0,indexes,len(frame)-1])
        chart=[dict(date=str(frame.date.iloc[i].date()),nav={k:round(float(frame[k].iloc[i]/100000),7) for k in keys},
                    dd={k:round(float(dd[k][i]),7) for k in keys}) for i in indexes]
        # Full summary retains team returns and all period statistics; small UI only
        # needs the six displayed portfolios and recent governance records.
        runs[key]={k:v for k,v in run.items() if k not in ['summary','audit']}
        runs[key].update(id=key,summary={k:run['summary'][k] for k in keys},chart=chart)
        gov=source/key/'governance.json'
        if gov.exists():runs[key]['governance']=json.loads(gov.read_text())[-3:]
    payload=dict(schema='simulation-ui-v2',runs=runs,data_sources=summary['data_sources'])
    org=json.loads((ROOT/'data/reference/investment_organization.json').read_text())
    # Keep final employee evidence, not every repeated evaluation, in the UI.
    for company in org['companies'].values():
        for team in company['teams'].values():
            for employee in team['employees']:
                employee['evaluations']=employee['evaluations'][-1:]
    payload['organization']=org
    payload['meeting_rooms']=json.loads((ROOT/'data/reference/investment_meeting_rooms.json').read_text())
    operations=ROOT/'data/reference/investment_operations_policy.json'
    if operations.exists():
        policy=json.loads(operations.read_text())
        payload['operations']={'actors':policy['actors']}
        queue=ROOT/'data/reference/investment_operations_queue.json'
        if queue.exists():
            observed=json.loads(queue.read_text())
            review=review_queue(observed,policy,observed['observed_at'])
            payload['operations']['reviewed_as_of']=review['reviewed_as_of']
            payload['operations']['queue']={'tasks':[dict(id=t['id'],title=t['title'],owner=t['next_owner'],
                status=t['reviewed_status'],next_action=t['next_action'],blocker_reason=', '.join(t['blocked_reasons'])) for t in review['tasks']]}
    rounds=ROOT/'data/reference/investment_rounds.json'
    if rounds.exists():payload['rounds']=json.loads(rounds.read_text())
    payload['team_reviews']={}
    for folder,company in [('2026-09-10-compound-teams','compound'),('2026-09-10-company-teams',None)]:
        data=json.loads((ROOT/'docs/replays'/folder/'summary.json').read_text())
        for key,value in data['runs'].items():
            c=company or value['case']['company']
            payload['team_reviews'].setdefault(c,{})[key]={k:v for k,v in value.items() if k!='last_decisions'}
    payload['evolution']={}
    for suffix,label in [('employee-evolution','빠른 직원 재배분'),('employee-hurdle','관찰·승진 기준 강화'),('employee-supervised','상위자 비용·낙폭 심사')]:
        data=json.loads((ROOT/f'docs/replays/2026-09-10-{suffix}/summary.json').read_text())
        payload['evolution'][suffix]={'label':label,'summary':{c:data['funded_summary'][c] for c in ['pulse','compound','adaptive']},
                                     **{k:data.get(k,0) for k in ['staff_count','births','reviews','senior_reviews','senior_vetoes']}}
    payload['total_experiments']=len(runs)+sum(len(v) for v in payload['team_reviews'].values())+len(payload['evolution'])
    market=ROOT/'docs/replays/2026-09-10-market-controls/ui-summary.json'
    if market.exists():
        payload['market_validation']=json.loads(market.read_text())
        payload['total_experiments']+=payload['market_validation']['run_count']
    content=json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    (target/'results.json').write_text(content+'\n')
    print(f'Simulation: {payload["total_experiments"]} experiments; results {len(content.encode()):,} bytes; {target}')
    return target


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,default=Path('.simulation-deploy/simulation'))
    build(ap.parse_args().out)
