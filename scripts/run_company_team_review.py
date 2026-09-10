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
 'pulse_day':dict(name='초단기 모멘텀 팀',thesis='5일·20일 상대강도',invalidator='추세 이탈과 비용 초과'),
 'pulse_week':dict(name='단기 추세 팀',thesis='20일 상대강도',invalidator='순위 하락·추세 이탈'),
 'pulse_month':dict(name='중기 모멘텀 팀',thesis='63일 상대강도',invalidator='중기 상대강도 약화'),
 'adaptive_tactical':dict(name='기회·저변동 팀',thesis='20일 모멘텀과 낮은 변동성',invalidator='시장 폭 악화'),
 'adaptive_quality':dict(name='품질·추세 팀',thesis='재무 품질과 63일 모멘텀',invalidator='품질과 가격 동반 약화'),
 'adaptive_defensive':dict(name='방어 팀',thesis='낮은 20일 변동성',invalidator='위험 급등·가격 미관측'),
}


def run(args):
    if args.out.exists():raise SystemExit('Use a new output directory; preserve earlier reviews')
    policy=json.loads(args.policy.read_text())
    started=time.perf_counter();args.out.mkdir(parents=True)
    frames={s:pd.read_pickle('data/raw/us_replay_cohort_cache.pkl' if s=='matched' else 'data/raw/us_replay_long_cache.pkl') for s in ['matched','long']}
    filings=pd.read_csv('data/dashboard_research/accounting/us/filing_events.csv.gz',dtype={'ticker':str,'filing_id':str})
    panels={s:prepare(f,filings,policy,'us') for s,f in frames.items()}
    fields=['fin_revenue_growth','fin_roa','fin_cfo_assets','fin_accruals_assets','fin_liabilities_assets','fin_cash_assets','fin_cfo_after_ppe_assets']
    for panel in []:
        # Identical candidate information set: nobody gains by having fewer
        # required fundamental fields than the other two teams.
        panel.features['eligible'] &= np.isfinite(np.stack([panel.features[f] for f in fields])).all(axis=0)
    runs={}
    for case in policy['team_experiments']:
        active={t for t in TEAMS if t.startswith(case['company']+'_')}
        p=policy|{'start':case['start'],'desk_interval_overrides':{t:case['interval'] for t in active}}
        result=replay_v2(panels[case['source']],p,'baseline',cost_multiplier=case.get('cost_multiplier',1),rebalance_phase=case.get('phase',0))
        target=args.out/case['id'];target.mkdir()
        decisions=[r for r in result['decisions'] if r['desk'] in active]
        trades=[r for r in result['trades'] if r['desk'] in active]
        body,tail=ledger(decisions)
        write_gzip(target/'decisions.jsonl.gz',body)
        write_gzip(target/'trades.csv.gz',pd.DataFrame(trades).to_csv(index=False))
        write_gzip(target/'nav.csv.gz',pd.DataFrame({'date':result['dates'],**{t:result['series'][t] for t in active}}).to_csv(index=False))
        review=dict(case=case,summary={t:result['summary'][t] for t in active},ledger_tail=tail,decisions=len(decisions),trades=len(trades),
                    last_decisions={t:next((r for r in reversed(decisions) if r['desk']==t),None) for t in active})
        # Rolling windows use existing holdings; not repeatedly restarted funds.
        for team in active:
            nav=np.r_[30000,result['series'][team]]
            rolling=nav[252:]/nav[:-252]-1
            review['summary'][team]['rolling_1y']={'observations':len(rolling),'median':float(np.median(rolling)),'worst':float(rolling.min()),'positive_fraction':float((rolling>0).mean())}
        write_json(target/'summary.json',review);runs[case['id']]=review
        print(case['id'],{t:round(result['summary'][t]['net_return']*100,2) for t in active},flush=True)
    report=dict(schema='company-mandate-comparison-v1',teams=TEAMS,policy=policy,runs=runs,elapsed_seconds=round(time.perf_counter()-started,3))
    write_json(args.out/'summary.json',report)
    write_json(args.out/'manifest.json',dict(created_at=pd.Timestamp.now(tz='UTC').isoformat(),policy_sha256=sha256(args.policy),
        code_sha256=sha256(Path(__file__)),engine_sha256=sha256(Path('src/ai_stock_assistant/investment_replay_v2.py')),
        inputs={'matched_panel':sha256(Path('data/raw/us_replay_cohort_cache.pkl')),'long_panel':sha256(Path('data/raw/us_replay_long_cache.pkl')),
                'filings':sha256(Path('data/dashboard_research/accounting/us/filing_events.csv.gz'))},
        artifacts={str(p.relative_to(args.out)):sha256(p) for p in args.out.rglob('*') if p.is_file()},paid_data_calls=0,llm_api_calls=0))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--policy',type=Path,default=Path('data/reference/company_team_review_policy.json'))
    p.add_argument('--out',type=Path,default=Path('docs/replays/2026-09-10-company-teams'))
    run(p.parse_args())
