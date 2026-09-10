"""Small fixed-instrument long-history controls with a chronological selector."""
from pathlib import Path
import argparse,json,sys
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ai_stock_assistant.investment_replay import metrics,sha256,write_json
from run_investment_replay import write_gzip


def simulate(dates,close,opening,targets,cost):
    cash=100000.;units=np.zeros(4);pending=None;nav=[];fees=0.;trades=0
    for day in range(len(dates)):
        if pending is not None:
            delta=pending-units;sell=np.minimum(delta,0);cash+=float((-sell*opening[day]).sum())*(1-cost);units+=sell
            buy=np.maximum(delta,0);amount=float((buy*opening[day]).sum());scale=min(1,cash/(amount*(1+cost))) if amount else 1
            buy*=scale;cash-=float((buy*opening[day]).sum())*(1+cost);units+=buy
            fees+=float((abs(sell+buy)*opening[day]).sum())*cost;trades+=int((abs(sell+buy)>1e-8).sum());pending=None
        value=cash+float((units*close[day]).sum());nav.append(value)
        if day in targets:pending=value*targets[day]/close[day]
        assert cash>=-1e-7 and units.min()>=-1e-7
    return np.array(nav),fees,trades


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,required=True);args=ap.parse_args()
    out=Path('docs/replays/2026-09-10-market-controls')
    if out.exists():raise SystemExit('Output already exists')
    out.mkdir(parents=True)
    symbols=['SPY','XLF','XLE','XLU'];frames=[];inputs={}
    for t in symbols:
        p=args.source/f'collection/prices/{t}.csv.gz';f=pd.read_csv(p,parse_dates=['date']);frames.append(f);inputs[t]=sha256(p)
    raw=pd.concat(frames);close=raw.pivot(index='date',columns='ticker',values='adjusted_close')[symbols]
    original=raw.pivot(index='date',columns='ticker',values='close')[symbols]
    opening=raw.pivot(index='date',columns='ticker',values='open')[symbols]*close/original
    trend=close/close.rolling(200).mean()-1;mom=close/close.shift(126)-1
    mask=(close.index>='2007-01-03')&(close.index<='2026-08-31')
    dates=close.index[mask];c=close.loc[mask].to_numpy();o=opening.loc[mask].to_numpy();tr=trend.loc[mask].to_numpy();mo=mom.loc[mask].to_numpy()
    assert np.isfinite(c).all() and np.isfinite(o).all()
    protocol={'registered_at':pd.Timestamp.now(tz='UTC').isoformat(),'symbols':symbols,'start':str(dates[0].date()),'end':str(dates[-1].date()),'rules':['spy100','spy90','trend200','relative126','diversified_trend','walkforward_selector'],'cost_bps':[10,20],'decision_clock':'signal close, next open','selector':'every 63 sessions after 504 sessions, compare preceding 504-session net return + half drawdown of three fixed rules; no future suffix; choose one for subsequent decisions','caveat':'ETF menu chosen retrospectively. No historical individual-stock cohort selection, but this remains exploratory and not independent OOS.'}
    write_json(out/'protocol.json',protocol)
    rules={k:{} for k in protocol['rules'] if k!='walkforward_selector'}
    for day in range(len(dates)):
        if day==0:rules['spy100'][day]=np.array([1.,0,0,0]);rules['spy90'][day]=np.array([.9,0,0,0])
        if day%21==0:
            rules['trend200'][day]=np.array([.9 if tr[day,0]>0 else 0,0,0,0])
            allowed=(tr[day]>0)&(mo[day]>0);order=np.argsort(-mo[day]);selected=[i for i in order if allowed[i]][:2]
            weights=np.zeros(4);weights[selected]=.45;rules['relative126'][day]=weights
            rules['diversified_trend'][day]=np.where(tr[day]>0,.225,0.)
    summaries={};all_nav={};records=[]
    for bps in [10,20]:
        historical={}
        for key,targets in rules.items():
            nav,fees,trades=simulate(dates,c,o,targets,bps/10000);historical[key]=nav
            all_nav[f'{key}_{bps}']=nav
            m=metrics(nav,100000);m.update(cagr=(nav[-1]/100000)**(252/len(nav))-1,cost_paid=fees,trade_count=trades)
            idx=np.flatnonzero(dates>='2020-01-01');m['later_return']=nav[-1]/nav[idx[0]-1]-1;summaries[f'{key}_{bps}']=m
        targets={};chosen='diversified_trend'
        for day in range(len(dates)):
            if day>=504 and day%63==0:
                scores={}
                for key in ['trend200','relative126','diversified_trend']:
                    curve=historical[key][day-504:day+1];m=metrics(curve[1:],curve[0]);scores[key]=m['net_return']+.5*m['max_drawdown']
                chosen=max(scores,key=scores.get);records.append(dict(date=str(dates[day].date()),bps=bps,chosen=chosen,scores=scores))
            if day%21==0:targets[day]=rules[chosen][day]
        nav,fees,trades=simulate(dates,c,o,targets,bps/10000);all_nav[f'walkforward_selector_{bps}']=nav
        m=metrics(nav,100000);idx=np.flatnonzero(dates>='2020-01-01');m.update(cagr=(nav[-1]/100000)**(252/len(nav))-1,cost_paid=fees,trade_count=trades,later_return=nav[-1]/nav[idx[0]-1]-1);summaries[f'walkforward_selector_{bps}']=m
    names={'spy100':'SPY 100% 보유','spy90':'SPY 90% + 현금','trend200':'SPY 200일 추세','relative126':'ETF 상대강도 2종','diversified_trend':'ETF 분산·추세','walkforward_selector':'과거 2년으로 분기별 선택'}
    write_gzip(out/'nav.csv.gz',pd.DataFrame({'date':dates,**all_nav}).to_csv(index=False));write_json(out/'selection_events.json',records);write_json(out/'summary.json',summaries)
    ui=dict(run_count=len(summaries),description='2007–2026 · SPY·XLF·XLE·XLU 고정 ETF 메뉴 · 편도 10bp · 종가 결정 후 다음 시가 체결. 기업별 과거 표본을 다시 고르는 문제와 분리한 가격 규칙 대조입니다.',rows=[dict(name=names[k],**summaries[k+'_10']) for k in names],caveat='ETF 메뉴와 전략 설계도 현재 시점의 탐색입니다. 2020년 이후는 순서대로 진행한 과거 구간이며 독립적인 미관측 검증이 아닙니다. 배당은 공급자 수정주가 대용, 현금 이자 0%.')
    write_json(out/'ui-summary.json',ui)
    write_json(out/'manifest.json',dict(inputs=inputs,code_sha256=sha256(Path(__file__)),artifacts={str(p.relative_to(out)):sha256(p) for p in out.rglob('*') if p.is_file()},paid_data_calls=0,llm_api_calls=0))
    print('COMPLETE',len(summaries),'market control experiments',flush=True)


if __name__=='__main__':main()
