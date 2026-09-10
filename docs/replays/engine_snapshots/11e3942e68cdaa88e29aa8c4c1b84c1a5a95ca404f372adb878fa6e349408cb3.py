"""Compare three fundamental mandates under the SAME execution and candidates."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ai_stock_assistant.investment_replay import prepare,sha256,write_json,metrics
from ai_stock_assistant.investment_replay_v2 import replay_v2
from run_investment_replay import ledger,write_gzip

TEAMS={
 'compound_quarter':dict(name='성장·수익성 팀',thesis='매출 성장과 ROA·영업현금·낮은 발생액·낮은 부채가 함께 우수한 기업',invalidator='매출 성장 둔화 또는 이익의 현금 전환 악화'),
 'compound_half':dict(name='현금창출·재투자 팀',thesis='영업현금과 설비투자 이후 현금, 현금 보유 및 이익 품질이 우수한 기업',invalidator='설비투자 후 현금 악화 또는 영업현금이 이익을 지속적으로 따라가지 못함'),
 'compound_year':dict(name='재무건전성 팀',thesis='ROA와 이익 품질을 유지하면서 부채 의존도가 낮은 기업',invalidator='ROA 저하와 부채 부담 확대'),
}


def run(args):
    if args.out.exists():raise SystemExit('Use a new output directory; preserve earlier reviews')
    policy=json.loads(args.policy.read_text())
    started=time.perf_counter();args.out.mkdir(parents=True)
    frames={s:pd.read_pickle('data/raw/us_replay_cohort_cache.pkl' if s=='matched' else 'data/raw/us_replay_long_cache.pkl') for s in ['matched','long']}
    filings=pd.read_csv('data/dashboard_research/accounting/us/filing_events.csv.gz',dtype={'ticker':str,'filing_id':str})
    panels={s:prepare(f,filings,policy,'us') for s,f in frames.items()}
    fields=['fin_revenue_growth','fin_roa','fin_cfo_assets','fin_accruals_assets','fin_liabilities_assets','fin_cash_assets','fin_cfo_after_ppe_assets']
    for panel in panels.values():
        # Identical candidate information set: nobody gains by having fewer
        # required fundamental fields than the other two teams.
        panel.features['eligible'] &= np.isfinite(np.stack([panel.features[f] for f in fields])).all(axis=0)
    runs={}
    for case in policy['team_experiments']:
        p=policy|{'start':case['start'],'desk_interval_overrides':{t:case['interval'] for t in TEAMS}}
        result=replay_v2(panels[case['source']],p,'baseline',cost_multiplier=case.get('cost_multiplier',1),rebalance_phase=case.get('phase',0))
        target=args.out/case['id'];target.mkdir()
        decisions=[r for r in result['decisions'] if r['desk'] in TEAMS]
        trades=[r for r in result['trades'] if r['desk'] in TEAMS]
        body,tail=ledger(decisions)
        write_gzip(target/'decisions.jsonl.gz',body)
        write_gzip(target/'trades.csv.gz',pd.DataFrame(trades).to_csv(index=False))
        write_gzip(target/'nav.csv.gz',pd.DataFrame({'date':result['dates'],**{t:result['series'][t] for t in TEAMS}}).to_csv(index=False))
        review=dict(case=case,summary={t:result['summary'][t] for t in TEAMS},ledger_tail=tail,decisions=len(decisions),trades=len(trades),
                    last_decisions={t:next((r for r in reversed(decisions) if r['desk']==t),None) for t in TEAMS})
        # Rolling windows use existing holdings; not repeatedly restarted funds.
        for team in TEAMS:
            nav=np.r_[30000,result['series'][team]]
            rolling=nav[252:]/nav[:-252]-1
            review['summary'][team]['rolling_1y']={'observations':len(rolling),'median':float(np.median(rolling)),'worst':float(rolling.min()),'positive_fraction':float((rolling>0).mean())}
        write_json(target/'summary.json',review);runs[case['id']]=review
        print(case['id'],{t:round(result['summary'][t]['net_return']*100,2) for t in TEAMS},flush=True)
    report=dict(schema='compound-mandate-comparison-v1',teams=TEAMS,policy=policy,runs=runs,elapsed_seconds=round(time.perf_counter()-started,3))
    write_json(args.out/'summary.json',report)
    write_json(args.out/'manifest.json',dict(created_at=pd.Timestamp.now(tz='UTC').isoformat(),policy_sha256=sha256(args.policy),
        code_sha256=sha256(Path(__file__)),engine_sha256=sha256(Path('src/ai_stock_assistant/investment_replay_v2.py')),
        inputs={'matched_panel':sha256(Path('data/raw/us_replay_cohort_cache.pkl')),'long_panel':sha256(Path('data/raw/us_replay_long_cache.pkl')),
                'filings':sha256(Path('data/dashboard_research/accounting/us/filing_events.csv.gz'))},
        artifacts={str(p.relative_to(args.out)):sha256(p) for p in args.out.rglob('*') if p.is_file()},paid_data_calls=0,llm_api_calls=0))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--policy',type=Path,default=Path('data/reference/compound_team_review_policy.json'))
    p.add_argument('--out',type=Path,default=Path('docs/replays/2026-09-10-compound-teams'))
    run(p.parse_args())
