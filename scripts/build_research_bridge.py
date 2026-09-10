"""Connect company research and observed staff results without inventing tests."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import gzip,json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ai_stock_assistant.investment_replay import sha256,write_json
from run_investment_replay import write_gzip

HORIZONS={'day':('하루',1),'week':('주간',5),'month':('한 달',21),'quarter':('분기',63),'half':('반기',126),'companion':('장기',252)}
TAXONOMY={'payments':['금융','거래 인프라','결제 네트워크'],'custody':['금융','금융 서비스','수탁·자산관리 지원'],
          'exchanges':['금융','거래 인프라','거래소·청산'],'waste_services':['산업재','환경 서비스','폐기물 수거·처리'],
          'regulated_utilities':['필수 인프라','규제 유틸리티','상수도'],'energy-infrastructure':['에너지','에너지 인프라','운송·저장']}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,required=True);args=ap.parse_args()
    out=Path('docs/research_library/2026-09-10-imported');out.mkdir(parents=True,exist_ok=True)
    now=datetime.now(timezone.utc).isoformat();companies=[];sources=[];inputs={}
    previous_path=Path('data/reference/investment_research_library.json')
    previous=json.loads(previous_path.read_text()) if previous_path.exists() else {}
    previous_requests={r['id']:r for r in previous.get('requests',[])}
    imported_at=json.loads((out/'manifest.json').read_text())['imported_at'] if (out/'manifest.json').exists() else now
    cohort=set(json.loads(Path('data/reference/accounting_cohort.json').read_text())['markets']['us'])
    for group in ['finance','services','energy']:
        path=args.source/f'{group}.json';body=path.read_bytes();inputs[group]=sha256(path);data=json.loads(body)
        stored=out/f'{group}.json.gz'
        if stored.exists():
            if gzip.open(stored,'rb').read()!=body:raise ValueError('Imported source changed; create a new version')
        else:write_gzip(stored,body.decode())
        group_sources={s['id']:s for s in data['sources']};sources.extend(group_sources.values())
        for company in data['companies']:
            c=dict(company);c['research_group']=group;c['taxonomy']=TAXONOMY[c['sectorId']]
            c['imported_from']=str(stored);c['source_sha256']=inputs[group];c['known_at']=imported_at
            c['in_simulation_cohort']=c['ticker'] in cohort
            c['tested_hypothesis_ids']=[];c['executable']=False
            c['status']='가설 작성 완료 · 기업 고유 KPI 검증 대기'
            c['horizons']={key:dict(value,hypothesis_id=f'{c["ticker"]}-{key}-20260910',label=HORIZONS[key][0],
                         review_sessions=HORIZONS[key][1],executable=False,tested_by_experiments=[]) for key,value in c['horizons'].items()}
            c['source_links']=list(group_sources.values())
            companies.append(c)
    org=json.loads(Path('data/reference/investment_organization.json').read_text())
    run=Path('docs/replays/2026-09-10-r03-fixed-roster/efficient_control-p0-c1')
    performance=json.loads((run/'summary.json').read_text())['summary'];last={}
    for line in gzip.open(run/'decisions.jsonl.gz','rt'):
        row=json.loads(line)
        if row['company']!='control':last[row['desk']]=row
    catalog_names={c['ticker'] for c in companies};requests=[]
    for company_id,company in org['companies'].items():
        for team_id,team in company['teams'].items():
            employee=next(e for e in team['employees'] if e['genome']=='efficient')
            evidence=performance[team_id];decision=last[team_id]
            targets=[p['ticker'] for p in sorted(decision['positions'],key=lambda p:(-p['score'],p['ticker']))[:3]]
            horizon={'pulse_day':'day','pulse_week':'week','pulse_month':'month','compound_quarter':'quarter',
                     'compound_half':'half','compound_year':'companion','adaptive_tactical':'week','adaptive_quality':'month','adaptive_defensive':'quarter'}[team_id]
            if company_id=='pulse':
                question='가격 순위가 실제 기업 사건과 연결되는가? 발표시각·발표 전 기대·거래량과 다음 시가 비용을 분리하고, 단기 전술 종료를 장기 논리 실패로 바꾸지 말아 주세요.'
            elif company_id=='compound':
                question='성장·현금흐름·부채 대리변수가 해당 기업의 반복 가능한 주당 현금으로 연결되는가? 기업 고유 KPI, 희석·재투자·경쟁 변화와 논리 철회 조건을 확인해 주세요.'
            else:
                question='방어 성과가 기업 현금 안정성에서 왔는지, 낮은 변동성·현금 비중에서 왔는지 분리해 주세요. 사업 충격과 가격 변동만으로 만든 경고의 차이도 조사해 주세요.'
            requests.append(dict(id=f'R03-RESEARCH-{team_id}',created_at=now,requester=employee['id'],requester_name=employee['display_name'],
                company=company_id,team=team_id,reviewer=team['team_lead'],reviewer_name=team['team_lead_name'],
                last_target_portfolio=[p['ticker'] for p in decision['positions']],
                employee_strategy=employee['strategy_brief'],requested_horizon=horizon,target_tickers=targets,
                evidence=dict(reference=str(run/'summary.json'),last_signal=decision['signal_date'],net_return=evidence['net_return'],
                              max_drawdown=evidence['max_drawdown'],cost_paid=evidence['cost_paid'],trade_count=evidence['trade_count']),
                question=question,status='기업별 근거 수집·조건 명세 대기',matched_imported_hypotheses=[c['ticker'] for c in companies if c['ticker'] in targets],
                laboratory_response='기존 9개 기업의 54개 가설을 연결했으나 이번 거래 표본과 겹치지 않습니다. 매매 결과를 기업 논리의 검증 성적으로 표시하지 않습니다. 실제 거래 대상의 공시와 KPI를 확보해 별도 가설·실험으로 등록합니다.',
                tested_hypothesis_ids=[]))
    pilot=Path('docs/research_library/2026-09-10-cohort-pilot.json')
    pilots=[]
    if pilot.exists():
        data=json.loads(pilot.read_text());pilots=data.get('companies',[])
    pilot_by_ticker={c['ticker']:c for c in pilots}
    for request in requests:
        prior=previous_requests.get(request['id'])
        if prior:request['created_at']=prior['created_at']
        request['updated_at']=now
        linked=[t for t in request['last_target_portfolio'] if t in pilot_by_ticker]
        request['follow_up_tickers']=[t for t in linked if t not in request['target_tickers']]
        request['new_pilot_hypotheses']=[t+'-'+request['requested_horizon']+'-20260910' for t in linked]
        if linked:
            request['status']='기업 가설 작성 · KPI 시계열·가격조건·성과검증 대기'
            request['laboratory_response']=' · '.join(linked)+'의 보존 신고 ID와 일치하는 SEC 원문을 확인하고 6개 기간별 기업 논리를 작성했습니다. 목표 편입은 실제 체결·수익 기여와 다릅니다. 오늘 논리를 과거 신호에 넣지 않으며, 매수 가격·KPI 시계열·사전 조건과 별도 실험이 필요합니다.'
    payload=dict(schema='investment-research-bridge-v1',updated_at=now,companies=companies,sources=sources,
                 imported_hypotheses=sum(len(c['horizons']) for c in companies),imported_company_count=len(companies),
                 imported_cohort_overlap=sorted(catalog_names&cohort),requests=requests,pilot_companies=pilots,
                 pilot_hypotheses=sum(len(c.get('horizons',{})) for c in pilots),pilot_cohort_overlap=sorted(set(pilot_by_ticker)&cohort),
                 rule='A company thesis is linked to a result only through an explicit tested hypothesis ID. Matching a ticker, sector or horizon does not validate it.',
                 note='Imported 2026-09-10 research is not available to past simulated decisions. Price/financial proxy backtests do not validate the 54 business hypotheses.')
    write_json(Path('data/reference/investment_research_library.json'),payload)
    if not (out/'manifest.json').exists():
        write_json(out/'manifest.json',dict(imported_at=imported_at,source_files_sha256=inputs,files={p.name:sha256(p) for p in out.iterdir() if p.is_file() and p.name!='manifest.json'},
                                         imported_companies=len(companies),hypotheses=payload['imported_hypotheses'],original_sources_modified=False))
    print('Research bridge:',len(companies),'companies,',payload['imported_hypotheses'],'hypotheses,',len(requests),'staff requests, overlap',payload['imported_cohort_overlap'])


if __name__=='__main__':main()
