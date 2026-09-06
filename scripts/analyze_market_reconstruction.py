"""Measured probability compression, model turnover, and short-window market paths."""
import json,sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ai_stock_assistant import monthly_ews as live
from ai_stock_assistant.data import price_quality
from reconstruct_market_history import components,distribution

ROOT=Path('data/dashboard_research/analysis')

def corr(x,y,method='pearson'):
    v=pd.Series(x).reset_index(drop=True).corr(pd.Series(y).reset_index(drop=True),method=method)
    return float(v) if np.isfinite(v) else None

def model_change(market,panel):
    aug=live.load_month(Path('data/dashboard_research/reconstruction')/market/'models/2026-08')
    sep=live.load_month(Path('data/dashboard_ews')/market/'models/2026-09')
    d0=panel.loc[panel.date.eq('2026-08-31')].set_index('ticker');d1=panel.loc[panel.date.eq('2026-09-01')].set_index('ticker')
    common=sorted(set(d0.index)&set(d1.index));d0=d0.loc[common];d1=d1.loc[common]
    p00=components(d0,*aug);p01=components(d1,*aug);p10=components(d0,*sep);p11=components(d1,*sep)
    result={'common_tickers':len(common),'from':'2026-08-31','to':'2026-09-01','method':'symmetric two-order decomposition on identical stocks'}
    for h in ['up','down']:
        result[h]={'observed_change_pp':float((p11[h]-p00[h]).mean()*100),
            'new_price_features_effect_pp':float(((p01[h]-p00[h]+p11[h]-p10[h])/2).mean()*100),
            'monthly_model_effect_pp':float(((p10[h]-p00[h]+p11[h]-p01[h])/2).mean()*100)}
    return result

def main():
    report={'start_close':'2026-07-31','end_close':'2026-09-04','markets':{},'limitations':[
        'Daily price paths and reconstructed signals are retrospective, not an executed portfolio.',
        'The 6-month outcome of August/September 2026 signals has not matured.',
        'Current retained universe, price revisions, missing delisted outcomes and short sample remain.',
        'Cross-market same-date association is descriptive; US-prior-session matching is also shown.']};daily={}
    for market in ['kr','us']:
        panel=pd.read_pickle(ROOT/f'{market}-daily-panel.pkl.gz')
        diag=live.read_json(ROOT/f'{market}-diagnostics.json')
        prices,quality=price_quality.prepare(live.read_prices(Path('data/raw')/live.PRICE_FILES[market]),market)
        prices['return1']=prices.groupby('ticker').adjusted_close.pct_change(fill_method=None)
        recent=prices.loc[prices.date.between('2026-07-31','2026-09-04')].copy()
        valid=recent.quality_valid & recent.volume.gt(0)
        returns=recent.loc[valid & recent.date.gt('2026-07-31')].groupby('date').return1.agg(['mean','median','count'])
        daily[market]=returns['mean'];returns['index']=100*(1+returns['mean']).cumprod()
        returns.to_csv(ROOT/f'{market}-market-path.csv')
        p=recent.loc[valid].pivot(index='date',columns='ticker',values='adjusted_close')
        fixed=p.iloc[-1]/p.loc['2026-07-31']-1;common=fixed.dropna()
        snap=json.loads((Path('data/dashboard_research/reconstruction')/market/'predictions/2026-08-03/rows.json').read_text())
        scored=pd.DataFrame(snap).set_index('ticker')
        realized=(p.iloc[-1]/p.loc['2026-08-03']-1).reindex(scored.index)
        scored['observed_window_return']=realized
        n=int(np.ceil(len(scored)*.1));top=scored.nlargest(n,'upProb');final=scored.loc[scored.isFinalCandidate]
        listing=pd.read_csv(Path('data/raw')/live.LISTING_FILES[market],dtype={'ticker':str}).fillna('').drop_duplicates('ticker').set_index('ticker')
        sectors=pd.DataFrame({'return':common,'sector':listing.get('sector',pd.Series(dtype=str)).reindex(common.index).fillna('미분류')})
        sector=sectors.groupby('sector')['return'].agg(['count','mean','median']).sort_values('mean',ascending=False)
        report['markets'][market]={'diagnostics':diag,'model_transition':model_change(market,panel),
            'price_quality':quality,'july31_to_september4':{'observed_stocks':len(common),'starting_stocks':int(p.loc['2026-07-31'].notna().sum()),
                'mean_return':float(common.mean()),'median_return':float(common.median()),'positive_fraction':float(common.gt(0).mean()),
                'daily_equal_weight_return':float(returns['index'].iloc[-1]/100-1),'distribution':distribution(common)},
            'august3_signal_short_followup':{'note':'Observed to September 4 only, not the six-month target or trading-strategy validation.',
                'stocks':len(scored),'observed_outcomes':int(realized.notna().sum()),'mean_return':float(realized.mean()),
                'up_spearman':corr(scored.upProb,realized,'spearman'),'down_spearman':corr(scored.downProb,realized,'spearman'),
                'top_decile_up_n':len(top),'top_decile_up_return':float(top.observed_window_return.mean()),
                'final_candidates':len(final),'final_candidate_return':float(final.observed_window_return.mean()) if len(final) else None},
            'sectors':sector.reset_index().to_dict('records'),
            'latest_up_top':live.read_json(Path('data/dashboard_research/reconstruction')/market/'predictions/2026-09-04/rows.json')[:15]}
    joined=pd.concat(daily,axis=1).dropna()
    lagged=pd.merge_asof(daily['kr'].rename('kr').reset_index(),daily['us'].rename('us_previous').reset_index().rename(columns={'date':'us_date'}),
        left_on='date',right_on='us_date',direction='backward',allow_exact_matches=False).dropna()
    report['cross_market']={'same_session_date':{'n':len(joined),'pearson':corr(joined.kr,joined.us),'spearman':corr(joined.kr,joined.us,'spearman')},
        'prior_us_session_to_kr':{'n':len(lagged),'pearson':corr(lagged.kr,lagged.us_previous),'spearman':corr(lagged.kr,lagged.us_previous,'spearman')}}
    live.write_json(ROOT/'market_study.json',json.loads(json.dumps(report,default=str).replace('NaN','null')))
    print('MARKET STUDY COMPLETE',flush=True)

if __name__=='__main__':main()
