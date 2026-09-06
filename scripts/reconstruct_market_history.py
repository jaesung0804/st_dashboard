"""Explicit retrospective records, isolated from the immutable live ledger."""
from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.special import expit
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ai_stock_assistant import monthly_ews as live

ROOT = Path('data/dashboard_research')

def finite(value):
    value=float(value)
    return value if np.isfinite(value) else None

def distribution(values):
    x=pd.Series(values).dropna()
    return {**{k:finite(v) for k,v in x.quantile([0,.01,.1,.25,.5,.75,.9,.99,1]).rename(
        {0:'min',.01:'p01',.1:'p10',.25:'p25',.5:'median',.75:'p75',.9:'p90',.99:'p99',1:'max'}).items()},
        'mean':finite(x.mean()),'std':finite(x.std()),'unique':int(x.nunique()),'n':len(x)}

def components(frame, card, boosters):
    result={}
    for head in ['up','down']:
        h=card['heads'][head]; linear=h['linear']; x=frame[live.FEATURES].to_numpy(float)
        z=np.clip((np.where(np.isfinite(x),x,linear['median'])-linear['mean'])/linear['scale'],-12,12)
        lr=expit(z@np.asarray(linear['coef'])+linear['intercept'])
        tree=boosters[head].predict(frame[live.FEATURES],num_threads=1)
        raw=.5*lr+.5*tree
        result.update({head+'_linear':lr,head+'_tree':tree,head+'_raw':raw,
            head+'_cal_only':live.calibrated(raw,{**h['calibration'],'weight':1}),
            head:live.calibrated(raw,h['calibration'])})
    return result

def prediction_rows(frame, card, boosters, listing, market, signal, created):
    probs=components(frame,card,boosters)
    contrib=boosters['down'].predict(frame[live.FEATURES],pred_contrib=True,num_threads=1)[:,:-1]
    rows=[]
    for i,(_,f) in enumerate(frame.iterrows()):
        ticker=str(f.ticker); info=listing.get(ticker,{})
        up,down=float(probs['up'][i]),float(probs['down'][i]);low,high=sorted([down,float(probs['down_raw'][i])])
        reasons=np.argsort(-np.abs(contrib[i]),kind='stable')[:2]
        rows.append({'date':signal,'ticker':ticker,'name':info.get('name',ticker),
            'sector':info.get('sector','') or '미분류','detailSector':info.get('industry','') or '미분류',
            'exchange':info.get('exchange',''),'close':round(float(f.close),4),'closeRaw':round(float(f.close),4),
            'currency':'KRW' if market=='kr' else 'USD','adjustedClose':float(f.adjusted_close),
            'upProb':round(up,6),'downProb':round(down,6),'upScore':round(up*100,2),'downRisk':round(down*100,2),
            'riskEstimateRange':[round(low*100,2),round(high*100,2)],
            'upGrade':'RED' if up>=.35 else 'ORANGE' if up>=.20 else 'YELLOW' if up>=.10 else 'GREEN',
            'downGrade':'RED' if down>=.35 else 'ORANGE' if down>=.20 else 'YELLOW' if down>=.10 else 'GREEN',
            'isUpCandidate':up>=.20,'isFinalCandidate':up>=.20 and high<.15,'isDownRed':down>=.35,
            'modelVersion':card['id'],'modelMonth':card['month'],'trainingCutoff':card['cutoff'],
            'generatedAt':created,'predictionKind':'reconstructed',
            'evidence':{'return20':round(float(f.ret20)*100,2),'relative60':round(float(f.relative60)*100,2),
                'volatility20':round(float(f.vol20)*100,2),'breadth200':round(float(f.breadth200)*100,2)},
            'riskFactors':[{'label':live.FEATURE_LABELS[live.FEATURES[j]],'direction':'위험 증가' if contrib[i,j]>0 else '위험 감소'} for j in reasons]})
    return sorted(rows,key=lambda r:(-r['upProb'],r['downProb'],r['ticker'])),probs

def run(market,start,end):
    root=ROOT/'reconstruction'; raw=Path('data/raw')/live.PRICE_FILES[market]
    prices=live.read_prices(raw,end)
    sessions=sorted(prices.loc[prices.date.between(start,end),'date'].unique())
    if not sessions:raise ValueError('No requested sessions')
    counts=prices.groupby('date').ticker.nunique()
    for day in sessions:
        prior=counts.loc[counts.index<day].tail(20)
        if counts.loc[day]<.8*prior.median():raise ValueError(f'Incomplete prices {market} {day}')
    models={};model_roots={}
    for month in sorted({pd.Timestamp(d).strftime('%Y-%m') for d in sessions}):
        existing=Path('data/dashboard_ews')/market/'models'/month
        if existing.exists():folder=existing
        else:folder=live.train_month(raw,market,month,root,jobs=2,research=True)
        card,boosters=live.load_month(folder)
        if pd.Timestamp(card['cutoff'])>=pd.Timestamp(month+'-01'):raise ValueError('Future training cutoff')
        models[month]=(card,boosters);model_roots[month]=str(folder)
    print('Building one causal multi-date panel',market,flush=True)
    panel=live.feature_panel(prices,market,training=False,signal_dates=sessions)
    cache=ROOT/'analysis';cache.mkdir(parents=True,exist_ok=True)
    panel.to_pickle(cache/f'{market}-daily-panel.pkl.gz')
    listing=pd.read_csv(Path('data/raw')/live.LISTING_FILES[market],dtype={'ticker':str}).fillna('').drop_duplicates('ticker').set_index('ticker').to_dict('index')
    created=live.utc_now();daily=[];scores=[];models_summary={}
    for date,f in panel.groupby('date',sort=True):
        signal=date.strftime('%Y-%m-%d');card,boosters=models[signal[:7]]
        f=f.reset_index(drop=True);rows,probs=prediction_rows(f,card,boosters,listing,market,signal,created)
        live.freeze_prediction(root,market,signal,rows,{'model':card['id'],'created_at':created,
            'prediction_kind':'reconstructed','training_cutoff':card['cutoff'],'model_path':model_roots[signal[:7]],
            'feature_sha256':hashlib.sha256(pd.util.hash_pandas_object(f[['date','ticker',*live.FEATURES]],index=False).to_numpy().tobytes()).hexdigest(),
            'rows':len(rows),'source_price_sha256':live.digest(raw),
            'limitations':['Retrospective application of a model designed after these dates; not live trading history.',
                'Current retained adjusted prices and listings; historical constituent and revision bias may remain.']})
        entry={'date':signal,'rows':len(f),'model_month':signal[:7],
            'up':distribution(probs['up']),'down':distribution(probs['down']),
            'final_candidates':sum(r['isFinalCandidate'] for r in rows),
            'up_candidates':sum(r['isUpCandidate'] for r in rows),
            'down_red':sum(r['isDownRed'] for r in rows),
            'breadth200':float(f.breadth200.iloc[0]),'market_ret20':float(f.market_ret20.iloc[0]),
            'market_ret60':float(f.market_ret60.iloc[0]),'market_vol20':float(f.market_vol20.iloc[0])}
        daily.append(entry);scores.append(pd.DataFrame({'date':signal,'ticker':f.ticker,**probs}))
        if signal==end:
            models_summary[signal[:7]]={h:{'calibration':card['heads'][h]['calibration'],
                'train_event_rate':card['heads'][h]['train_event_rate'],
                'calibration_diagnostics_not_test':card['heads'][h]['calibration_diagnostics_not_test'],
                'components':{k:distribution(v) for k,v in probs.items() if k==h or k.startswith(h+'_')},
                'linear_tree_pearson':finite(np.corrcoef(probs[h+'_linear'],probs[h+'_tree'])[0,1]),
                'feature_spearman':{col:finite(pd.Series(probs[h]).corr(f[col],method='spearman')) for col in live.FEATURES}}
                for h in ['up','down']}
            # An independent reconstruction must reproduce stored final values on
            # a known date before any missing date can be published.
            known=Path('data/dashboard_ews')/market/'predictions'/signal/'rows.json'
            if known.exists():
                old={r['ticker']:r for r in live.read_json(known)}
                assert len(old)==len(rows)
                assert all(abs(r['upProb']-old[r['ticker']]['upProb'])<=.000002 and abs(r['downProb']-old[r['ticker']]['downProb'])<=.000002 for r in rows),'Batch and stored inference disagree'
        print('Reconstructed',market,signal,len(rows),flush=True)
    all_scores=pd.concat(scores,ignore_index=True);all_scores.to_csv(cache/f'{market}-score-components.csv.gz',index=False)
    live.write_json(cache/f'{market}-diagnostics.json',{'market':market,'start':start,'end':end,'kind':'reconstructed',
        'daily':daily,'latest_model':models_summary,'model_paths':model_roots,'created_at':created})
    print('RECONSTRUCTION COMPLETE',market,len(daily),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(__doc__);p.add_argument('--market',choices=['kr','us'],required=True)
    p.add_argument('--start',default='2026-08-03');p.add_argument('--end',default='2026-09-04');a=p.parse_args()
    run(a.market,a.start,a.end)
