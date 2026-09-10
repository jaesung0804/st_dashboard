"""Paired comparisons across declared execution scenarios, not independent trials."""
from __future__ import annotations
from pathlib import Path
import json,sys
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ai_stock_assistant.investment_replay import sha256,write_json


def main():
    source=Path('docs/replays/2026-09-10-r03-fixed-roster');out=source.parent/'2026-09-10-r03-analysis'
    if out.exists():raise ValueError('Keep prior analysis; choose a new directory')
    data=json.loads((source/'summary.json').read_text());runs=data['runs'];rows=[]
    labels={'efficient_control':'효율 전략 고정','equal_staff':'재직 직원 균등','senior_balanced':'효율 70%·위험 통제 30%','senior_graded':'상위자 제한적 재배분'}
    for mode,label in labels.items():
        for company in ['pulse','compound','adaptive']:
            matching=[]
            for key,run in runs.items():
                case=run['case']
                if case['mode']!=mode:continue
                suffix=key[len(mode):];control=runs['efficient_control'+suffix]['summary'][company]
                m=run['summary'][company];spy=run['summary']['spy_cash_matched']
                matching.append(dict(case=key,net_return=m['net_return'],max_drawdown=m['max_drawdown'],cost_paid=m['cost_paid'],
                    return_difference=m['net_return']-control['net_return'],drawdown_improvement=m['max_drawdown']-control['max_drawdown'],
                    excess_spy=m['net_return']-spy['net_return']))
            basic=[r for r in matching if '-c1' in r['case'] and 'delay' not in r['case']]
            rows.append(dict(mode=mode,label=label,company=company,scenario_count=len(matching),
                return_better_count=sum(r['return_difference']>1e-10 for r in matching),risk_better_count=sum(r['drawdown_improvement']>1e-10 for r in matching),
                jointly_better_count=sum(r['return_difference']>1e-10 and r['drawdown_improvement']>1e-10 for r in matching),
                median_return_difference=float(np.median([r['return_difference'] for r in matching])),
                phase_return_min=min(r['net_return'] for r in basic),phase_return_max=max(r['net_return'] for r in basic),
                phase_drawdown_min=min(r['max_drawdown'] for r in basic),phase_drawdown_max=max(r['max_drawdown'] for r in basic),
                beats_spy_count=sum(r['excess_spy']>0 for r in matching),cases=matching))
    out.mkdir(parents=True)
    result=dict(run_count=len(runs),rows=rows,source_summary_sha256=sha256(source/'summary.json'),
        interpretation='These nine execution scenarios share prices and rules. Counts are sensitivity checks, not nine independent samples or a significance test.',
        findings=['A fixed 70/30 efficiency/guard blend improves Compound return and drawdown across all declared paired scenarios; retain for further research, not deployment proof.',
                  'Pulse returns vary substantially with periodic review phase. Blending reduces drawdown and return; a single best starting phase is not a defensible market-beating claim.',
                  'Bounded senior selection helps Adaptive in some phases but is inconsistent; senior instructions remain subject to comparison.',
                  'No in-round personnel changes. One retrospective scenario family is insufficient grounds for firing or guaranteed promotions.'])
    write_json(out/'summary.json',result)
    write_json(out/'manifest.json',dict(source_manifest_sha256=sha256(source/'manifest.json'),runner_sha256=sha256(Path(__file__)),artifacts={'summary.json':sha256(out/'summary.json')}))
    print('Analyzed',len(runs),'arms; paired company/mode rows',len(rows))
    for row in rows:
        if row['mode']!='efficient_control':print(row['company'],row['mode'],row['return_better_count'],row['risk_better_count'],row['jointly_better_count'],'of',row['scenario_count'])


if __name__=='__main__':main()
