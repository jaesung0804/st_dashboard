"""A predeclared fixed-roster comparison, using actual funded trade replays.

The roster is fixed in real research time. Replaying today's rule hypotheses
through past prices is retrospective and does not create historical employees.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import sys
import time
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ai_stock_assistant.investment_replay import prepare,metrics,sha256,write_json
from ai_stock_assistant.investment_replay_v2 import replay_v2
from ai_stock_assistant.investment_rounds import register_experiment,validate_roster_lock
from ai_stock_assistant.investment_strategy_briefs import strategy_proposal
from ai_stock_assistant.investment_supervision import validate_proposal
from run_employee_evolution import GENOMES
from run_investment_replay import ledger,write_gzip
from run_investment_replay_v2 import benchmark

MODES=('efficient_control','equal_staff','senior_balanced','senior_graded')


def assignments(people,mode,policy,dates,shadow=None,phase=0):
    teams=defaultdict(list)
    for person in people:teams[person['team']].append(person)
    schedule={dates[0]:{}};events=[]
    def allocation(person,weight):return dict(id=person['id'],weight=float(weight),**GENOMES[person['genome']])
    for team,staff in teams.items():
        efficient=next(p for p in staff if p['genome']=='efficient')
        guarded=next(p for p in staff if p['genome']=='guarded')
        if mode=='efficient_control':selected=[allocation(efficient,1.)]
        elif mode=='equal_staff':selected=[allocation(p,1/len(staff)) for p in staff]
        else:selected=[allocation(efficient,.7),allocation(guarded,.3)]
        schedule[dates[0]][team]=selected
        if mode!='senior_graded':continue
        for day in range(252,len(dates)):
            if day%63!=phase%63:continue
            evidence=[]
            for person in staff:
                curve=shadow[person['genome']][team].to_numpy()[day-252:day+1]
                m=metrics(curve[1:],curve[0])
                evidence.append(dict(employee=person['id'],genome=person['genome'],cost2_net_return=m['net_return'],
                                     max_drawdown=m['max_drawdown'],score=m['net_return']+.5*m['max_drawdown']))
            chosen=sorted(evidence,key=lambda e:(-e['score'],e['employee']))[0]
            person=next(p for p in staff if p['id']==chosen['employee'])
            budget=.3*min(1.,.25/max(abs(chosen['max_drawdown']),1e-9))
            if chosen['cost2_net_return']<=0:budget*=.5
            proposal=strategy_proposal(team,person['genome'],policy,dates[day])
            assert validate_proposal(proposal)['approved']
            selected=[allocation(efficient,1.)] if person['id']==efficient['id'] else [allocation(efficient,1-budget),allocation(person,budget)]
            schedule.setdefault(dates[day],{})[team]=selected
            events.append(dict(simulation_date=dates[day],evidence_start=dates[day-252],evidence_end=dates[day],team=team,
                               action='bounded_allocation_advice',supervisor=team+'-LEAD',employee=person['id'],proposal=proposal,
                               consideration=evidence,chosen=chosen,advice_budget=budget,allocations=selected,
                               reason='Keep at least 70% with the efficiency mandate; limit the alternative by trailing drawdown and halve it when double-cost evidence is negative. No personnel change.'))
    allowed={p['id'] for p in people}
    for by_team in schedule.values():
        for group in by_team.values():
            assert all(a['id'] in allowed and a['weight']>=0 for a in group)
            assert np.isclose(sum(a['weight'] for a in group),1.)
    return schedule,events


def register(out,round_path,base_policy):
    if out.exists():raise ValueError('Preserve completed/attempted results; use a new folder')
    state=json.loads(round_path.read_text());record=state['rounds'][-1]
    org=json.loads(Path('data/reference/investment_organization.json').read_text())
    assert validate_roster_lock(record,org)['valid']
    cases=[dict(id=f'{mode}-p{phase}-c{cost}',mode=mode,phase=phase,cost_multiplier=cost,fill_delay=1)
           for phase in [0,7,14,21] for cost in [1,2] for mode in MODES]
    cases += [dict(id=f'{mode}-p0-c1-delay2',mode=mode,phase=0,cost_multiplier=1,fill_delay=2) for mode in MODES]
    now=datetime.now(timezone.utc).isoformat()
    protocol=dict(schema='fixed-roster-hour-r03-v1',registered_at=now,round_id=record['id'],cases=cases,
                  people=record['strategy_employees'],policy=base_policy,
                  comparison='All 36 arms retained. Compare each mode against the same phase/cost/delay efficient control, also SPY 90% + cash. No in-round hiring or departures.',
                  senior_rule='70% efficiency anchor plus at most 30% alternative. Every 63 sessions after 252, rank past 252-session double-cost net return + half maximum drawdown. Reduce the alternative for drawdown >25% and halve it if net negative.',
                  evidence_rule='Shadow reference sleeves use the same common efficient execution cadence and phase. Double-cost evidence is 20bp and one-session delay for every actual cost/delay case.',
                  phase_rule='Initial allocation is common at the first signal close; phase changes later periodic reviews. Assignment changes also cause next-open rebalancing.',
                  caveat='Today\'s fixed roster and strategies replayed on retained current-listing sample. Survivorship, retrospective selection and adjusted-price limitations remain. Not independent OOS or historical real employee performance.',
                  personnel_rule='No names, genomes, hiring or departures change in this actual-hour round. Personnel decisions only after the full hour and results review.')
    out.mkdir(parents=True);write_json(out/'protocol.json',protocol)
    now=datetime.now(timezone.utc).isoformat()
    for case in cases:record=register_experiment(record,dict(id=case['id'],protocol_reference=str(out/'protocol.json'),protocol_sha256=sha256(out/'protocol.json')),now)
    state['rounds'][-1]=record;state['actual_updated_at']=now;write_json(round_path,state)
    return protocol


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,required=True)
    ap.add_argument('--out',type=Path,default=Path('docs/replays/2026-09-10-r03-fixed-roster'))
    args=ap.parse_args();started=time.perf_counter()
    round_path=Path('data/reference/investment_rounds.json')
    p=json.loads(Path('data/reference/employee_supervision_policy.json').read_text())
    protocol=register(args.out,round_path,p);people=protocol['people']
    cache=Path('data/raw/us_replay_cohort_cache.pkl');filing_path=Path('data/dashboard_research/accounting/us/filing_events.csv.gz')
    spy_path=args.source/'collection/prices/SPY.csv.gz';cohort=Path('data/reference/accounting_cohort.json')
    raw_path=Path('data/raw/us_ohlcv_nasdaq_nyse_yfinfo_state.csv')
    inputs={str(path):sha256(path) for path in [cache,filing_path,spy_path,cohort,raw_path]}
    prices=pd.read_pickle(cache);cohort_names=json.loads(cohort.read_text())['markets']['us']
    assert set(prices.ticker.unique())==set(cohort_names)
    filings=pd.read_csv(filing_path,dtype={'ticker':str,'filing_id':str});data=prepare(prices,filings,p,'us')
    spy=pd.read_csv(spy_path,parse_dates=['date'])
    dates=[str(d.date()) for d in data.dates[data.dates.searchsorted(p['start']):]]
    team_names=list({person['team'] for person in people});shadows={};schedules={};senior_events={}
    for phase in [0,7,14,21]:
        shadow={}
        for genome,config in GENOMES.items():
            # Reference sleeves are numerical controls, not newly hired staff.
            reference={dates[0]:{t:[dict(id=f'REFERENCE-{t}-{genome}',weight=1.,**config)] for t in team_names}}
            result=replay_v2(data,p|{'employee_schedule':reference},'efficient',cost_multiplier=2,rebalance_phase=phase,record_details=False)
            shadow[genome]=pd.DataFrame({t:result['series'][t] for t in team_names})
            write_gzip(args.out/f'shadow-p{phase}-{genome}.csv.gz',pd.DataFrame({'date':dates,**{t:result['series'][t] for t in team_names}}).to_csv(index=False))
        shadows[phase]=shadow
        for mode in MODES:
            schedule,events=assignments(people,mode,p,dates,shadow,phase);schedules[phase,mode]=schedule;senior_events[phase,mode]=events
        cutoff=len(dates)//2;changed={g:f.copy() for g,f in shadow.items()}
        for frame in changed.values():frame.iloc[cutoff+1:]*=20
        altered,_=assignments(people,'senior_graded',p,dates,changed,phase)
        prefix=lambda schedule:{k:v for k,v in schedule.items() if k<=dates[cutoff]}
        assert prefix(altered)==prefix(schedules[phase,'senior_graded'])
        write_gzip(args.out/f'senior-events-p{phase}.jsonl.gz','\n'.join(json.dumps(e,ensure_ascii=False,allow_nan=False) for e in senior_events[phase,'senior_graded'])+'\n')
    runs={}
    for case in protocol['cases']:
        mark=time.perf_counter();folder=args.out/case['id'];folder.mkdir()
        detail=case['phase']==0 and case['cost_multiplier']==1 and case['fill_delay']==1
        result=replay_v2(data,p|{'employee_schedule':schedules[case['phase'],case['mode']]},'efficient',
                         cost_multiplier=case['cost_multiplier'],fill_delay=case['fill_delay'],rebalance_phase=case['phase'],record_details=detail)
        spy_nav,fee=benchmark(spy,dates,100000,.001*case['cost_multiplier'],case['fill_delay'])
        spy_nav=.9*spy_nav+10000;result['series']['spy_cash_matched']=spy_nav.tolist()
        result['summary']['spy_cash_matched']=metrics(spy_nav,100000)|dict(cost_paid=.9*fee,cagr=(spy_nav[-1]/100000)**(252/len(dates))-1)
        write_gzip(folder/'nav.csv.gz',pd.DataFrame({'date':dates,**result['series']}).to_csv(index=False))
        write_gzip(folder/'stale-reserve-nav.csv.gz',pd.DataFrame({'date':dates,**result['reserved_series']}).to_csv(index=False))
        tail=None
        if detail:
            body,tail=ledger(result['decisions']);write_gzip(folder/'decisions.jsonl.gz',body)
            write_gzip(folder/'trades.csv.gz',pd.DataFrame(result['trades']).to_csv(index=False))
            assert all(t['fill_date']>t['signal_date'] for t in result['trades'])
            assert all(a['id'] in {x['id'] for x in people} for d in result['decisions'] for a in (d.get('employees') or []))
        s=dict(case=case,summary=result['summary'],full_ledger_saved=detail,ledger_tail=tail,
               unfilled_order_batches=result['unfilled_order_batches'],elapsed_seconds=round(time.perf_counter()-mark,3))
        write_json(folder/'summary.json',s);runs[case['id']]=s
        print('DONE',case['id'],{c:round(result['summary'][c]['net_return']*100,2) for c in p['companies']},flush=True)
    summary=dict(schema=protocol['schema'],round_id=protocol['round_id'],run_count=len(runs),people_count=len(people),
                 period=[dates[0],dates[-1]],runs=runs,elapsed_seconds=round(time.perf_counter()-started,3),
                 checks=['future_shadow_mutation_preserves_past_assignments_all_phases','no_personnel_changes','funded_next_open_replay','all_predeclared_arms_retained'],
                 caveat=protocol['caveat'])
    write_json(args.out/'summary.json',summary)
    sources=[Path(__file__),Path('src/ai_stock_assistant/investment_replay.py'),Path('src/ai_stock_assistant/investment_replay_v2.py'),Path('src/ai_stock_assistant/investment_rounds.py'),Path('src/ai_stock_assistant/investment_strategy_briefs.py'),Path('src/ai_stock_assistant/investment_supervision.py')]
    for source in sources:Path('docs/replays/engine_snapshots',sha256(source)+'.py').write_bytes(source.read_bytes())
    write_json(args.out/'manifest.json',dict(created_at=datetime.now(timezone.utc).isoformat(),inputs=inputs,
                sources={str(path):sha256(path) for path in sources},
                artifacts={str(path.relative_to(args.out)):sha256(path) for path in args.out.rglob('*') if path.is_file()},paid_data_calls=0,llm_api_calls=0))
    now=datetime.now(timezone.utc).isoformat();state=json.loads(round_path.read_text());record=state['rounds'][-1]
    assert validate_roster_lock(record,json.loads(Path('data/reference/investment_organization.json').read_text()))['valid']
    for experiment in record['experiments']:
        if experiment['id'] in runs:experiment.update(status='completed',completed_at=now,result_reference=str(args.out/experiment['id']/'summary.json'),result_sha256=sha256(args.out/experiment['id']/'summary.json'))
    record['new_completed_experiments']=len(runs);state['actual_updated_at']=now;write_json(round_path,state)
    print('COMPLETE',len(runs),'new arms',summary['elapsed_seconds'],'seconds',flush=True)


if __name__=='__main__':main()
