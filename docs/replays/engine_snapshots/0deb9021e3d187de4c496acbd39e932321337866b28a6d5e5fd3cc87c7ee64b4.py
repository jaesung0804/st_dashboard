"""Causal research staffing followed by a funded replay of dated assignments.

Shadow returns are training evidence. Only the separate funded replay counts as
company performance. Descendants earn no return or tenure before their birth.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ai_stock_assistant.investment_replay import prepare,metrics,sha256,write_json,DESKS
from ai_stock_assistant.investment_replay_v2 import replay_v2
from ai_stock_assistant.investment_supervision import validate_proposal,review_promotion
from run_investment_replay import ledger,write_gzip

GENOMES={'original':{'variant':'baseline','overrides':{}},'efficient':{'variant':'efficient','overrides':{}},
         'guarded':{'variant':'guarded','overrides':{}},'moderate':{'variant':'guarded','overrides':{'rank_buffer':15,'volatility_budget':.35}}}


def evolve(dates,shadow,teams,policy,stress_shadow=None):
    staff,events,schedule={t:[] for t in teams},[],{}
    first=dates[0]
    for team in teams:
        for i,g in enumerate(['original','efficient','guarded']):
            employee=dict(id=f'{team}-E{i+1:02d}',team=team,genome=g,born_index=0,born=first,parent=None,status='funded' if i==0 else 'shadow',evaluations=[])
            staff[team].append(employee)
        schedule.setdefault(first,{})[team]=[dict(id=staff[team][0]['id'],weight=1.,**GENOMES['original'])]
    minimum=policy.get('shadow_minimum_sessions',126)
    lookback=policy.get('staff_lookback_sessions',126)
    previous={t:[dict(id=staff[t][0]['id'],weight=1.,**GENOMES['original'])] for t in teams}
    last_promotion={t:0 for t in teams}
    for day in range(126,len(dates),63):
        date=dates[day]
        for team in teams:
            measured=[]
            for employee in staff[team]:
                # Do not use a shadow strategy's pre-birth performance to judge
                # a newly created employee, even if its code was computable then.
                begin=max(employee['born_index'],day-lookback)
                if day-begin<minimum:continue
                curve=shadow[employee['genome']][team].to_numpy()[begin:day+1]
                m=metrics(curve[1:],curve[0]);score=m['net_return']+.5*m['max_drawdown']
                paused=m['net_return']<0 and m['max_drawdown'] < -policy['probation_drawdown']
                evaluation=dict(date=date,observed_start=dates[begin],net_return=m['net_return'],max_drawdown=m['max_drawdown'],score=score,paused=paused)
                if policy.get('supervisor_enabled'):
                    cost_curve=stress_shadow[employee['genome']][team].to_numpy()[begin:day+1]
                    evidence=dict(observations=day-begin,evidence_end=date,decision_date=date,
                                  cost2_net_return=float(cost_curve[-1]/cost_curve[0]-1),max_drawdown=m['max_drawdown'])
                    review=review_promotion(evidence,policy)
                    events.append(dict(date=date,team=team,employee=employee['id'],action='senior_strategy_review',
                                       supervisor=team+'-LEAD',company_risk_officer=team.split('_')[0]+'-CIO',evidence=evidence,**review))
                    paused=paused or not review['approved']
                    evaluation['paused']=paused
                employee['evaluations'].append(evaluation)
                employee['status']='paused' if paused else 'shadow'
                if not paused:measured.append((score,employee))
            measured.sort(key=lambda p:(-p[0],p[1]['id']))
            if not measured and policy.get('supervisor_enabled') and day>=minimum:
                schedule.setdefault(date,{})[team]=[]
                previous[team]=[]
                events.append(dict(date=date,team=team,action='senior_cash_directive',reason='No strategy meets cost and risk promotion gates. Keep team capital in cash.'))
                continue
            if not measured:
                # Insufficient tenure is not a decision to empty the portfolio.
                continue
            selected=measured[:2] if policy['staff_allocation']=='ensemble' else measured[:1]
            allocations=[]
            if policy['staff_allocation']=='hurdle':
                incumbent=max(previous[team],key=lambda a:a['weight'])['id'] if previous[team] else ''
                candidates={e['id']:(score,e) for score,e in measured}
                best_score,best=measured[0]
                incumbent_score,inc=candidates.get(incumbent,(-float('inf'),None))
                changed=best['id']!=incumbent and (not previous[team] or day-last_promotion[team]>=policy['minimum_promotion_spacing'])
                gain=best_score-incumbent_score
                acceptable=inc is None or best['evaluations'][-1]['max_drawdown']>=inc['evaluations'][-1]['max_drawdown']-.05
                if changed and gain>policy['promotion_score_hurdle'] and acceptable:
                    if inc is None:
                        allocations=[dict(id=best['id'],weight=1.,**GENOMES[best['genome']])]
                    else:
                        allocations=[dict(id=inc['id'],weight=.8,**GENOMES[inc['genome']]),dict(id=best['id'],weight=.2,**GENOMES[best['genome']])]
                    last_promotion[team]=day
                else:
                    allocations=previous[team]
                    if policy.get('supervisor_enabled'):
                        allocations=[a for a in allocations if a['id'] in candidates]
                        total=sum(a['weight'] for a in allocations)
                        if total:allocations=[a|{'weight':a['weight']/total} for a in allocations]
                for a in allocations:
                    next(e for e in staff[team] if e['id']==a['id'])['status']='funded'
            else:
                for i,(_,employee) in enumerate(selected):
                    weight=([.7,.3][i] if len(selected)>1 else 1.)
                    employee['status']='funded'
                    allocations.append(dict(id=employee['id'],weight=weight,**GENOMES[employee['genome']]))
            previous[team]=allocations
            schedule.setdefault(date,{})[team]=allocations
            events.append(dict(date=date,team=team,action='quarterly_staff_review',allocations=allocations,
                               evidence_end=date,reason=f'past {lookback}-session net return + half drawdown; {policy["staff_allocation"]} allocation'))
            # One bounded follow-up hypothesis per team. Its birth follows the
            # review and it must spend 126 sessions in research before funding.
            if day>=252 and len(staff[team])==3 and measured:
                parent=measured[0][1]
                child=dict(id=f'{team}-E04',team=team,genome='moderate',born_index=day,born=date,parent=parent['id'],status='shadow',evaluations=[])
                staff[team].append(child)
                events.append(dict(date=date,team=team,action='spawn_research_variant',employee=child['id'],parent=parent['id'],
                                   genome='moderate',funding=0,reason='test intermediate rank buffer 15 and volatility budget 35%; no pre-birth credit'))
    return staff,events,schedule


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--policy',type=Path,default=Path('data/reference/employee_evolution_policy.json'))
    ap.add_argument('--out',type=Path,default=Path('docs/replays/2026-09-10-employee-evolution'))
    args=ap.parse_args()
    if args.out.exists():raise SystemExit('Preserve previous results; choose a new directory')
    p=json.loads(args.policy.read_text());args.out.mkdir(parents=True)
    start=time.perf_counter()
    prices=pd.read_pickle('data/raw/us_replay_cohort_cache.pkl')
    filings=pd.read_csv('data/dashboard_research/accounting/us/filing_events.csv.gz',dtype={'ticker':str,'filing_id':str})
    data=prepare(prices,filings,p,'us')
    teams=[t for c in p['companies'].values() for t in c['teams']]
    shadow={};stress_shadow={}
    for genome,config in GENOMES.items():
        result=replay_v2(data,p|config['overrides'],config['variant'],record_details=False)
        shadow[genome]=pd.DataFrame({t:result['series'][t] for t in teams})
        dates=result['dates']
        write_gzip(args.out/f'shadow-{genome}.csv.gz',pd.DataFrame({'date':dates,**{t:result['series'][t] for t in teams}}).to_csv(index=False))
        if p.get('supervisor_enabled'):
            proposal={'inputs':['trailing_price','trailing_volume','filing_asof','dated_shadow_returns'],'gross_exposure':1,'max_asset_weight':.1,'cost_bps':10,'fill_delay':1}
            assert validate_proposal(proposal)['approved']
            stress=replay_v2(data,p|config['overrides'],config['variant'],cost_multiplier=2,record_details=False)
            stress_shadow[genome]=pd.DataFrame({t:stress['series'][t] for t in teams})
            write_gzip(args.out/f'shadow-{genome}-cost2.csv.gz',pd.DataFrame({'date':dates,**{t:stress['series'][t] for t in teams}}).to_csv(index=False))
    staff,events,schedule=evolve(dates,shadow,teams,p,stress_shadow)
    write_json(args.out/'staff.json',staff);write_json(args.out/'events.json',events);write_json(args.out/'assignments.json',schedule)
    # The schedule is replayed with actual next-open share orders and costs;
    # shadow-strategy returns are never pasted into funded company NAV.
    funded=replay_v2(data,p|{'employee_schedule':schedule},'efficient')
    body,tail=ledger(funded['decisions'])
    write_gzip(args.out/'decisions.jsonl.gz',body);write_gzip(args.out/'trades.csv.gz',pd.DataFrame(funded['trades']).to_csv(index=False))
    write_gzip(args.out/'funded-nav.csv.gz',pd.DataFrame({'date':dates,**funded['series']}).to_csv(index=False))
    # Strong clock check: changing ALL future shadow returns cannot change past
    # births, evaluations, allocations, or funded decisions.
    cutoff=len(dates)//2
    changed={g:f.copy() for g,f in shadow.items()}
    for f in changed.values():f.iloc[cutoff+1:]*=20
    changed_stress={g:f.copy() for g,f in stress_shadow.items()}
    for f in changed_stress.values():f.iloc[cutoff+1:]*=20
    _,_,future_schedule=evolve(dates,changed,teams,p,changed_stress)
    prefix=lambda a:{k:v for k,v in a.items() if k<=dates[cutoff]}
    assert prefix(schedule)==prefix(future_schedule)
    for employees in staff.values():
        for e in employees:
            assert all(r['observed_start']>=e['born'] for r in e['evaluations'])
    assert all(t['fill_date']>t['signal_date'] for t in funded['trades'])
    summary=dict(schema='employee-evolution-v1',policy=p,genomes=GENOMES,staff_count=sum(map(len,staff.values())),
                 initial_staff_count=27,births=sum(e['action']=='spawn_research_variant' for e in events),
                 reviews=sum(e['action']=='quarterly_staff_review' for e in events),funded_summary=funded['summary'],
                 senior_reviews=sum(e['action']=='senior_strategy_review' for e in events),senior_vetoes=sum(e['action']=='senior_strategy_review' and not e['approved'] for e in events),
                 period=[dates[0],dates[-1]],ledger_tail=tail,elapsed_seconds=round(time.perf_counter()-start,3),
                 checks=['future_shadow_mutation_prefix_unchanged','no_pre_birth_performance_credit','strict_next_open_fills'],
                 limitation='Staff are bounded rule proxies. Genome candidates are designed now, so this is retrospective research. Actual company NAV is independently replayed, not a splice of winning shadow returns.')
    write_json(args.out/'summary.json',summary)
    write_json(args.out/'manifest.json',dict(created_at=pd.Timestamp.now(tz='UTC').isoformat(),policy_sha256=sha256(args.policy),
                runner_sha256=sha256(Path(__file__)),engine_sha256=sha256(Path('src/ai_stock_assistant/investment_replay_v2.py')),
                artifacts={str(f.relative_to(args.out)):sha256(f) for f in args.out.rglob('*') if f.is_file()},llm_api_calls=0,paid_data_calls=0))
    print('COMPLETE',summary['staff_count'],'staff',summary['births'],'births',summary['reviews'],'reviews',summary['elapsed_seconds'],flush=True)
    print({c:funded['summary'][c]['net_return'] for c in p['companies']},flush=True)


if __name__=='__main__':main()
