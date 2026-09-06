"""Matched, purged accounting ablation. Results are research, never live replacements."""
from __future__ import annotations
import argparse,gzip,hashlib,json,sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.special import expit,logit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ai_stock_assistant import monthly_ews as live,shadow_ews as shadow
from ai_stock_assistant.data import accounting_pit as accounting,macro_vintages as macro,price_quality

ROOT=Path('data/dashboard_research');FOLDS=['2024-10','2025-04','2025-10','2026-02']
ARMS={'price':[],'price_macro':macro.FEATURES,'price_accounting':accounting.FEATURES,'price_macro_accounting':macro.FEATURES+accounting.FEATURES}

def inputs(market):
    facts=[];folder=ROOT/'accounting_sources'/market
    if market=='us':
        for path in sorted(folder.glob('*.json.gz')):
            facts.extend(accounting.parse_sec(json.loads(gzip.decompress(path.read_bytes())),path.name.removesuffix('.json.gz')))
    else:
        for path in sorted(folder.glob('*.csv.gz')):
            ticker=path.name.split('_')[0];profile=folder/(ticker+'-profile.json')
            if not profile.exists():continue
            facts.extend(accounting.parse_dart(pd.read_csv(path,dtype=str),json.loads(profile.read_text()).get('acc_mt')))
    listing=pd.read_csv(Path('data/raw')/live.LISTING_FILES[market],dtype={'ticker':str}).fillna('')
    sector=listing.get('sector',pd.Series('',index=listing.index)).astype(str)+' '+listing.get('industry',pd.Series('',index=listing.index)).astype(str)
    financial=set(listing.loc[sector.str.contains('Financial|Bank|Insurance|은행|금융|보험|증권',case=False,regex=True),'ticker'])
    dated=accounting.events(facts,financial)
    if dated.empty:raise ValueError(f'No dated {market} financials')
    path=ROOT/'accounting'/market;path.mkdir(parents=True,exist_ok=True)
    dated.to_csv(path/'filing_events.csv.gz',index=False)
    live.write_json(path/'source_audit.json',{'facts':len(facts),'events':len(dated),'issuers':int(dated.ticker.nunique()),
        'first_available':str(dated.available_at.min()),'last_available':str(dated.available_at.max()),
        'availability':'actual filing day + 1 calendar day; backward as-of join',
        'sources':{str(p.relative_to(ROOT)):live.digest(p) for p in folder.glob('*') if p.is_file()},
        'features':accounting.FEATURES,'financial_sector_issuers':sorted(financial&set(dated.ticker))})
    return dated

def fit_head(train,cal,head,features,save_to=None):
    import lightgbm as lgb
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    x=train[features].copy();lower=x.quantile(.01);upper=x.quantile(.99)
    clipped=[f for f in features if f.startswith('fin_') and f not in ['fin_observed_fraction','fin_financial_sector']]
    x[clipped]=x[clipped].clip(lower[clipped],upper[clipped],axis=1)
    med=x.median().fillna(0);filled=x.fillna(med);w=shadow.weights(train)
    scale=StandardScaler().fit(filled,sample_weight=w)
    lr=LogisticRegression(C=.1,max_iter=600,random_state=804).fit(np.clip(scale.transform(filled),-12,12),train['y_'+head],sample_weight=w)
    tree=lgb.LGBMClassifier(n_estimators=180,learning_rate=.035,num_leaves=15,max_depth=5,
        min_child_samples=150,reg_lambda=10,colsample_bytree=.85,max_bin=63,random_state=804,n_jobs=2,verbosity=-1,deterministic=True,force_col_wise=True)
    tree.fit(x,train['y_'+head].astype(int),sample_weight=w)
    def raw(frame):
        z=frame[features].copy();z[clipped]=z[clipped].clip(lower[clipped],upper[clipped],axis=1)
        return .5*lr.predict_proba(np.clip(scale.transform(z.fillna(med)),-12,12))[:,1]+.5*tree.predict_proba(z)[:,1]
    p=raw(cal);calibrator=LogisticRegression(C=1,max_iter=300).fit(logit(np.clip(p,1e-6,1-1e-6)).reshape(-1,1),cal['y_'+head],sample_weight=shadow.weights(cal))
    slope=float(calibrator.coef_[0,0]);intercept=float(calibrator.intercept_[0])
    settings={'coef':slope if slope>=0 else 1.,'intercept':intercept if slope>=0 else 0.,'weight':.5 if slope>=0 else 0.}
    metadata={'calibration':settings,'calibration_accepted':slope>=0,'original_slope':slope,
        'train_rows':len(train),'train_event_rate':float(np.average(train['y_'+head],weights=w)),
        'train_start':str(train.date.min().date()),'train_end':str(train.date.max().date()),
        'train_label_end':str(train['end_'+head].max().date()),'calibration_start':str(cal.date.min().date()),
        'calibration_end':str(cal.date.max().date()),'features':features,
        'train_keys_sha256':hashlib.sha256(train[['date','ticker','y_'+head]].to_csv(index=False).encode()).hexdigest(),
        'calibration_keys_sha256':hashlib.sha256(cal[['date','ticker','y_'+head]].to_csv(index=False).encode()).hexdigest()}
    if save_to is not None:
        import joblib
        save_to.parent.mkdir(parents=True,exist_ok=True)
        bundle={'features':features,'clipped':clipped,'lower':lower,'upper':upper,
                'median':med,'scaler':scale,'logistic':lr,'tree':tree,'calibration':settings}
        if save_to.exists():
            stored=joblib.load(save_to)
            if not np.allclose(accounting.predict_model(stored,cal),live.calibrated(raw(cal),settings),atol=1e-10):
                raise ValueError('Refusing to replace a different frozen accounting model')
        else:
            joblib.dump(bundle,save_to,compress=3)
            live.write_json(save_to.with_suffix('.json'),{**metadata,'sha256':live.digest(save_to)})
    return lambda frame:live.calibrated(raw(frame),settings),metadata

def run(market):
    dated=inputs(market);folder=ROOT/'accounting'/market
    cache=Path('.work')/(market+'-smooth-panel.pkl.gz');cache.parent.mkdir(exist_ok=True)
    if cache.exists():panel=pd.read_pickle(cache)
    else:
        prices,quality=price_quality.prepare(live.read_prices(Path('data/raw')/live.PRICE_FILES[market]),market)
        print('Constructing full-market smooth targets',market,len(prices),flush=True)
        panel=shadow.research_panel(prices,market);panel.to_pickle(cache)
        live.write_json(folder/'price_quality.json',quality)
    vintages,provenance=macro.load(Path('data/dashboard_ews_shadow'))
    panel=macro.attach(panel,vintages)
    cohort=json.loads(Path('data/reference/accounting_cohort.json').read_text())['markets'][market]
    panel=accounting.attach(panel.loc[panel.ticker.isin(cohort)],dated)
    coverage={'cohort_issuers':len(cohort),'panel_rows':len(panel),'dated_rows':int(panel.available_at.notna().sum()),
        'usable_rows':int(panel.fin_observed_fraction.ge(.25).sum()),
        'coverage_by_year':panel.assign(year=panel.date.dt.year).groupby('year').fin_observed_fraction.apply(lambda x:float(x.ge(.25).mean())).to_dict()}
    # Identical input/label pairs in every arm, so coverage cannot create a
    # spurious advantage for the accounting model.
    paired=panel.loc[panel.fin_observed_fraction.ge(.25)].copy()
    outputs=[];folds=[]
    for month in FOLDS:
        boundary=pd.Timestamp(month+'-01');test=paired.loc[paired.date.between(boundary,boundary+pd.offsets.MonthEnd(0))].copy()
        if test.empty:continue
        fold={'month':month,'arms':{},'status':'evaluated'}
        splits={};fold['excluded_heads']={}
        for head in ['up','down']:
            try:
                splits[head]=live.chronological_split(paired,head,boundary-pd.Timedelta(days=1))
            except ValueError as e:
                fold['excluded_heads'][head]=str(e)
        if not splits:
            fold['status']='insufficient_dated_history';folds.append(fold);continue
        for arm,extra in ARMS.items():
            fold['arms'][arm]={}
            for head in splits:
                train,cal=splits[head];known=test.loc[test['y_'+head].notna()]
                if known.empty:continue
                predictor,meta=fit_head(train,cal,head,live.FEATURES+extra)
                prob=predictor(known)
                result=known[['date','ticker','y_'+head,'future_up','future_down','benchmark_up']].copy()
                result['arm']=arm;result['head']=head;result['probability']=prob;result['fold']=month
                outputs.append(result)
                fold['arms'][arm][head]={'metrics':shadow.score_metrics(known['y_'+head],prob),'training':meta}
                print('Accounting holdout',market,month,arm,head,len(known),flush=True)
        folds.append(fold)
    if not outputs:raise ValueError('No independently evaluable accounting fold')
    predictions=pd.concat(outputs,ignore_index=True);predictions.to_csv(folder/'holdout_predictions.csv.gz',index=False)
    aggregate={}
    for arm,df in predictions.groupby('arm'):
        aggregate[arm]={}
        for head,group in df.groupby('head'):
            out=shadow.score_metrics(group['y_'+head],group.probability)
            daily=[]
            for date,g in group.groupby('date'):
                n=max(1,int(np.ceil(len(g)*.1)));top=g.nlargest(n,'probability')
                daily.append({'date':str(date.date()),'n':len(g),'event_rate':float(g['y_'+head].mean()),
                    'top_event_rate':float(top['y_'+head].mean()),'top_return':float(top.future_up.mean()),
                    'top_excess':float((top.future_up-top.benchmark_up).mean()),
                    'spearman_return':float(g.probability.corr(g.future_up,method='spearman'))})
            out['by_date']=daily;out['top_mean_return']=float(np.nanmean([x['top_return'] for x in daily]));out['top_mean_excess']=float(np.nanmean([x['top_excess'] for x in daily]))
            aggregate[arm][head]=out
    # A fixed September experimental model is fitted to the same dated cohort.
    latest_panel=pd.read_pickle(ROOT/'analysis'/f'{market}-daily-panel.pkl.gz')
    latest_panel=macro.attach(latest_panel.loc[latest_panel.date==latest_panel.date.max()],vintages)
    latest_panel=accounting.attach(latest_panel.loc[latest_panel.ticker.isin(cohort)],dated)
    current=latest_panel.loc[latest_panel.fin_observed_fraction.ge(.25)].copy()
    latest=current[['ticker','date',*accounting.FEATURES,'filing_id','filed','period_end','available_at']].copy();cards={}
    for arm,extra in ARMS.items():
        cards[arm]={}
        for head in ['up','down']:
            train,cal=live.chronological_split(paired,head,pd.Timestamp('2026-08-31'))
            predictor,meta=fit_head(train,cal,head,live.FEATURES+extra,folder/'models'/'2026-09'/f'{arm}-{head}.joblib')
            latest[arm+'_'+head]=predictor(current);cards[arm][head]=meta
    latest.to_csv(folder/'latest_accounting_scores.csv.gz',index=False)
    report={'market':market,'created_at':live.utc_now(),'status':'research_comparison','target':shadow.TARGET,
        'coverage':coverage,'folds':folds,'aggregate':aggregate,'latest_training':cards,
        'latest_rows':len(latest),'macro_provenance':provenance,
        'limitations':['128-company predeclared pilot; retained-current-listing survivorship remains.',
            'Publication day + 1 calendar day; historical corrections affect only later as-of joins.',
            'Four mature holdout months; overlapping six-month outcomes are not independent bets.',
            'Missing accounting fields are not zeros. Non-December Korean fiscal years excluded.',
            'Valuation, customer concentration and one-off gains require additional dated shares/notes; not fabricated.',
            'Cash after PP&E purchases excludes intangible investment and acquisitions; not comprehensive free cash flow.']}
    live.write_json(folder/'comparison.json',json.loads(json.dumps(report,default=str).replace('NaN','null')))
    print('ACCOUNTING COMPARISON COMPLETE',market,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(__doc__);p.add_argument('--market',choices=['kr','us'],required=True);a=p.parse_args();run(a.market)
