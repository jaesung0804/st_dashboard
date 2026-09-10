"""Add dated strategy documentation without rewriting historical trade results."""
from __future__ import annotations
import json
from pathlib import Path
import sys
from datetime import datetime, timezone
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ai_stock_assistant.investment_strategy_briefs import strategy_proposal,brief_markdown
from ai_stock_assistant.investment_supervision import validate_proposal
from ai_stock_assistant.investment_replay import sha256,write_json
from run_investment_replay import write_gzip
from run_employee_evolution import evolve


def main():
    source=Path('docs/replays/2026-09-10-employee-supervised')
    out=Path('docs/replays/2026-09-10-approval-briefs')
    if out.exists():raise SystemExit('Choose a new output path; retain published documentation')
    policy=json.loads((source/'summary.json').read_text())['policy']
    org_path=Path('data/reference/investment_organization.json')
    org=json.loads(org_path.read_text())
    employees={}
    for company in org['companies'].values():
        for team_id,team in company['teams'].items():
            for employee in team['employees']:
                last=employee['evaluations'][-1]['date'] if employee['evaluations'] else employee['born']
                employee['strategy_brief']=strategy_proposal(team_id,employee['genome'],policy,last)['strategy_brief']
                employees[employee['id']]=employee
    now=datetime.now(timezone.utc).isoformat()
    org['strategy_brief_documented_at']=now
    org['approval_record_requirements']=['투자 논리','매수 조건','축소·매도 조건','검토 주기','위험·비용','근거 기준일','심사 결과·사유']
    write_json(org_path,org)
    old_events=json.loads((source/'events.json').read_text())
    rows=[]
    for event in old_events:
        if event['action']!='senior_strategy_review':continue
        employee=employees[event['employee']]
        proposal=strategy_proposal(event['team'],employee['genome'],policy,event['date'])
        assert validate_proposal(proposal)['approved']
        rows.append(dict(record_kind='retrospective_strategy_documentation',documented_at=now,
                         simulation_date=event['date'],employee=employee['id'],display_name=employee['display_name'],
                         team=event['team'],proposal=proposal,historical_review_approved=event['approved'],
                         historical_reasons=event['reasons'],source_event_index=old_events.index(event)))
    # Re-evaluate existing shadow evidence. Required explanations must preserve
    # prior allocations, and every new review must now carry its own proposal.
    shadow={};stress={}
    for genome in ('original','efficient','guarded','moderate'):
        frame=pd.read_csv(source/f'shadow-{genome}.csv.gz');dates=frame.pop('date').tolist();shadow[genome]=frame
        stress[genome]=pd.read_csv(source/f'shadow-{genome}-cost2.csv.gz').drop(columns='date')
    teams=list(shadow['original'].columns)
    _,new_events,schedule=evolve(dates,shadow,teams,policy,stress)
    assert schedule==json.loads((source/'assignments.json').read_text())
    reviews=[e for e in new_events if e['action']=='senior_strategy_review']
    assert len(reviews)==len(rows) and all(e['proposal']['strategy_brief'] for e in reviews)
    assert [e['approved'] for e in reviews]==[r['historical_review_approved'] for r in rows]
    out.mkdir(parents=True)
    write_gzip(out/'annotations.jsonl.gz','\n'.join(json.dumps(row,ensure_ascii=False,allow_nan=False) for row in rows)+'\n')
    summary=dict(documented_at=now,employee_briefs=len(employees),historical_reviews_documented=len(rows),
                 checks=['existing_assignments_unchanged','historical_review_outcomes_unchanged','every_future_review_requires_strategy_brief'],
                 note='Explanations added on the documented date; no claim these texts existed at historical decision time. Not an additional performance experiment.')
    write_json(out/'summary.json',summary)
    sources=[Path(__file__),Path('src/ai_stock_assistant/investment_strategy_briefs.py'),Path('src/ai_stock_assistant/investment_supervision.py'),Path('scripts/run_employee_evolution.py')]
    snapshots=Path('docs/replays/engine_snapshots');snapshots.mkdir(exist_ok=True)
    for path in sources:(snapshots/(sha256(path)+'.py')).write_bytes(path.read_bytes())
    write_json(out/'manifest.json',dict(created_at=now,sources={str(path):sha256(path) for path in sources},
                                      source_events_sha256=sha256(source/'events.json'),
                                      artifacts={p.name:sha256(p) for p in out.iterdir() if p.is_file()}))
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
