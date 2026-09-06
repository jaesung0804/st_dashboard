"""Receipt-dated accounting facts, as-of revisions, and nonoverlapping-quarter ratios.

No financial year-end is an availability date. No current shares are used to
manufacture historical valuation multiples. Original and amended values become
usable only on/after the calendar day following their actual filing date.
"""
from __future__ import annotations
import gzip,json,math
from pathlib import Path
import numpy as np
import pandas as pd

TAGS={
 'revenue':['RevenueFromContractWithCustomerExcludingAssessedTax','RevenueFromContractWithCustomerIncludingAssessedTax','Revenues','SalesRevenueNet','Revenue','RevenueFromContractsWithCustomers','OperatingRevenue'],
 'operating_income':['OperatingIncomeLoss'], 'net_income':['ProfitLoss','NetIncomeLoss'],
 'gross_profit':['GrossProfit'], 'assets':['Assets'], 'liabilities':['Liabilities'],
 'equity':['StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest','StockholdersEquity','Equity'],
 'cash':['CashAndCashEquivalentsAtCarryingValue','CashAndCashEquivalents'],
 'receivables':['AccountsReceivableNetCurrent','TradeAndOtherCurrentReceivables','TradeReceivables'],
 'inventory':['InventoryNet','Inventories'], 'current_assets':['AssetsCurrent','CurrentAssets'],
 'current_liabilities':['LiabilitiesCurrent','CurrentLiabilities'],
 'cfo':['NetCashProvidedByUsedInOperatingActivities','CashFlowsFromUsedInOperatingActivities'],
 'ppe_capex':['PaymentsToAcquirePropertyPlantAndEquipment','PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities'],
 'interest':['InterestExpenseNonOperating','InterestExpense','FinanceCosts'],
}
FLOW={'revenue','operating_income','net_income','gross_profit','cfo','ppe_capex','interest'}
FEATURES=['fin_revenue_growth','fin_gross_margin','fin_operating_margin','fin_roa',
 'fin_cfo_assets','fin_accruals_assets','fin_cfo_after_ppe_assets','fin_ppe_sales',
 'fin_cash_assets','fin_liabilities_assets','fin_current_ratio','fin_receivables_growth_gap',
 'fin_inventory_growth_gap','fin_interest_coverage','fin_statement_age','fin_observed_fraction','fin_financial_sector']

def numeric(v):
    try:
        x=float(str(v).replace(',',''))
        return x if math.isfinite(x) else np.nan
    except (TypeError,ValueError):return np.nan

def parse_sec(data,ticker):
    records=[]
    for taxonomy in ['us-gaap','ifrs-full']:
        allfacts=data.get('facts',{}).get(taxonomy,{})
        for field,tags in TAGS.items():
            # Different synonym tags remain candidates; priority resolves only
            # within the same filing and exact duration, never across time.
            for priority,tag in enumerate(tags):
                for item in allfacts.get(tag,{}).get('units',{}).get('USD',[]):
                    if item.get('form') not in ['10-K','10-Q','10-K/A','10-Q/A','20-F','20-F/A','40-F','40-F/A','6-K']:
                        continue
                    if not item.get('filed') or not item.get('end') or not np.isfinite(numeric(item.get('val'))):continue
                    if field in FLOW and not item.get('start'):continue
                    if item['end']>item['filed']:continue
                    records.append({'ticker':ticker,'scope':taxonomy,'field':field,'start':item.get('start',''),
                        'end':item['end'],'filed':item['filed'],'filing_id':item['accn'],
                        'value':float(item['val']),'priority':priority,'currency':'USD'})
    return records

def parse_dart(frame, fiscal_month='12'):
    if str(fiscal_month).zfill(2)!='12':return []
    records=[];quarter={'11013':1,'11012':2,'11014':3,'11011':4};dates_cache={}
    for row in frame.fillna('').to_dict('records'):
        receipt=str(row.get('rcept_no',''))
        if len(receipt)!=14 or not receipt.isdigit() or row.get('currency')!='KRW':continue
        year=int(row['bsns_year']);q=quarter[str(row['reprt_code'])]
        date_key=(year,q,receipt[:8])
        if date_key not in dates_cache:
            dates_cache[date_key]=(pd.Period(f'{year}Q{q}').end_time.normalize(),pd.to_datetime(receipt[:8],format='%Y%m%d'))
        end,filed=dates_cache[date_key]
        if not 1<=(filed-end).days<=180:continue
        tag=str(row.get('account_id','')).split('_',1)[-1]
        candidates=[(field,tags.index(tag)) for field,tags in TAGS.items() if tag in tags]
        if not candidates:continue
        field,priority=candidates[0];statement=str(row.get('sj_div',''))
        if field in FLOW and statement not in (['CF'] if field in ['cfo','ppe_capex'] else ['IS','CIS']):continue
        if field not in FLOW and statement!='BS':continue
        amount=numeric(row.get('thstrm_amount'));start=''
        if field in FLOW:
            cumulative=numeric(row.get('thstrm_add_amount'))
            if statement=='CF' or q==4 or np.isfinite(cumulative):
                start=f'{year}-01-01'
                if statement!='CF' and q!=4:amount=cumulative
            else:start=pd.Period(f'{year}Q{q}').start_time.strftime('%Y-%m-%d')
        if not np.isfinite(amount):continue
        records.append({'ticker':str(row['ticker']).zfill(6),'scope':row.get('fs_div',''),
            'field':field,'start':start,'end':end.strftime('%Y-%m-%d'),'filed':filed.strftime('%Y-%m-%d'),
            'filing_id':receipt,'value':amount,'priority':priority+(10 if statement=='CIS' else 0),'currency':'KRW'})
    return records

def quarter_value(book,field,end):
    options=[(start,value) for (name,start,finish),value in book.items() if name==field and finish==end and start]
    direct=[(start,val) for start,val in options if 60<=(end-start).days<=120]
    if direct:
        start,val=max(direct,key=lambda t:t[0]);return val,start
    for start,value in sorted(options,key=lambda t:t[0]):
        if not 120<(end-start).days<=380:continue
        prior=[(finish,val) for (name,beg,finish),val in book.items() if name==field and beg==start and 60<=(end-finish).days<=120]
        if prior:
            finish,prev=max(prior,key=lambda t:t[0]);return value-prev,finish+pd.Timedelta(days=1)
    return np.nan,None

def trailing(book,field,end):
    annual=[(start,value) for (name,start,finish),value in book.items()
        if name==field and start is not None and finish==end and 330<=(end-start).days<=400]
    if annual:
        return max(annual,key=lambda t:t[0])[1]
    ends=sorted({finish for name,start,finish in book if name==field and start and finish<=end},reverse=True)[:4]
    if len(ends)!=4 or ends[0]!=end or any(not 60<=(a-b).days<=120 for a,b in zip(ends,ends[1:])):return np.nan
    quarters=[quarter_value(book,field,e) for e in ends]
    if not all(np.isfinite(v) for v,s in quarters) or not 330<=(end-quarters[-1][1]).days<=400:return np.nan
    for (v,start),previous_end in zip(quarters,ends[1:]):
        if not 0<(start-previous_end).days<=7:return np.nan
    return sum(v for v,s in quarters)

def div(a,b):return a/b if np.isfinite([a,b]).all() and b>0 else np.nan

def events(facts, financial_tickers=()):
    if not facts:return pd.DataFrame()
    data=pd.DataFrame(facts);data['filed']=pd.to_datetime(data.filed);data['end']=pd.to_datetime(data.end)
    data['start']=pd.to_datetime(data.start,errors='coerce')
    result=[]
    for ticker,company in data.groupby('ticker',sort=True):
        # Never alternate standalone and consolidated statements to fill holes.
        scope='CFS' if 'CFS' in set(company.scope) else ('OFS' if 'OFS' in set(company.scope) else ('us-gaap' if 'us-gaap' in set(company.scope) else 'ifrs-full'))
        company=company.loc[company.scope==scope];book={};latest=None
        for filed,items in company.groupby('filed',sort=True):
            items=items.sort_values(['filing_id','priority'],ascending=[True,False])
            for f in items.itertuples():
                key=(f.field,None if pd.isna(f.start) else f.start,f.end);book[key]=f.value
                if f.field=='assets':latest=max(latest or f.end,f.end)
            if latest is None:continue
            end=latest
            at=lambda field,e=end:book.get((field,None,e),np.nan)
            prior_ends=[e for n,s,e in book if n=='assets' and s is None and 345<=(end-e).days<=385]
            prior=max(prior_ends) if prior_ends else None
            annual={f:trailing(book,f,end) for f in FLOW}
            a=at('assets');prev_a=at('assets',prior) if prior is not None else np.nan
            avg_a=(a+prev_a)/2 if np.isfinite(prev_a) else np.nan
            revenue=annual['revenue'];prev_rev=trailing(book,'revenue',prior) if prior is not None else np.nan
            growth=div(revenue,prev_rev)-1
            financial=ticker in financial_tickers
            values={'fin_revenue_growth':growth,'fin_gross_margin':div(annual['gross_profit'],revenue),
                'fin_operating_margin':div(annual['operating_income'],revenue),'fin_roa':div(annual['net_income'],avg_a),
                'fin_cfo_assets':div(annual['cfo'],avg_a),'fin_accruals_assets':div(annual['net_income']-annual['cfo'],avg_a),
                'fin_cfo_after_ppe_assets':div(annual['cfo']-abs(annual['ppe_capex']),avg_a),
                'fin_ppe_sales':div(abs(annual['ppe_capex']),revenue),'fin_cash_assets':div(at('cash'),a),
                'fin_liabilities_assets':div(at('liabilities'),a),'fin_current_ratio':div(at('current_assets'),at('current_liabilities')),
                'fin_receivables_growth_gap':div(at('receivables'),at('receivables',prior))-1-growth if prior is not None else np.nan,
                'fin_inventory_growth_gap':div(at('inventory'),at('inventory',prior))-1-growth if prior is not None else np.nan,
                'fin_interest_coverage':div(annual['operating_income'],annual['interest'])}
            if financial:
                for k in ['fin_gross_margin','fin_cfo_assets','fin_accruals_assets','fin_cfo_after_ppe_assets','fin_ppe_sales','fin_current_ratio','fin_inventory_growth_gap','fin_receivables_growth_gap','fin_interest_coverage']:
                    values[k]=np.nan
            values={k:(float(v) if np.isfinite(v) else np.nan) for k,v in values.items()}
            result.append({'ticker':ticker,'available_at':filed+pd.Timedelta(days=1),'filed':filed,'period_end':end,
                'filing_id':str(items.iloc[-1].filing_id),'scope':scope,**values,
                'fin_observed_fraction':sum(np.isfinite(v) for v in values.values())/len(values),
                'fin_financial_sector':float(financial)})
    return pd.DataFrame(result).sort_values(['available_at','ticker']) if result else pd.DataFrame()

def attach(panel, dated):
    if dated.empty:raise ValueError('No receipt-dated financial inputs')
    left=panel.sort_values(['date','ticker']).copy();right=dated.sort_values(['available_at','ticker'])
    if right.duplicated(['available_at','ticker']).any():raise ValueError('Ambiguous filing events')
    merged=pd.merge_asof(left,right,on=None,left_on='date',right_on='available_at',by='ticker',direction='backward',allow_exact_matches=True)
    assert (merged.available_at.isna()|(merged.available_at<=merged.date)).all()
    age=(merged.date-merged.period_end).dt.days
    stale=age.gt(550)|age.lt(0)
    merged.loc[stale,[f for f in FEATURES if f in merged]]=np.nan
    merged['fin_statement_age']=age.where(~stale)
    merged['fin_observed_fraction']=merged.fin_observed_fraction.fillna(0)
    return merged


def predict_model(bundle, frame):
    """Apply a trusted, locally persisted monthly research model without refitting."""
    from scipy.special import expit, logit
    x=frame[bundle['features']].copy();cols=bundle['clipped']
    x[cols]=x[cols].clip(bundle['lower'][cols],bundle['upper'][cols],axis=1)
    z=np.clip(bundle['scaler'].transform(x.fillna(bundle['median'])),-12,12)
    raw=.5*bundle['logistic'].predict_proba(z)[:,1]+.5*bundle['tree'].predict_proba(x)[:,1]
    c=bundle['calibration'];adjusted=expit(c['coef']*logit(np.clip(raw,1e-6,1-1e-6))+c['intercept'])
    return (1-c['weight'])*raw+c['weight']*adjusted
