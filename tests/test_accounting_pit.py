import pandas as pd
import numpy as np
from ai_stock_assistant.data import accounting_pit as a
from ai_stock_assistant import monthly_ews as live

def test_cumulative_cash_flow_is_not_summed_twice():
    start=pd.Timestamp('2025-01-01')
    book={('cfo',start,pd.Timestamp(e)):v for e,v in [('2025-03-31',10),('2025-06-30',25),('2025-09-30',45),('2025-12-31',70)]}
    assert a.quarter_value(book,'cfo',pd.Timestamp('2025-06-30'))[0]==15
    assert a.trailing(book,'cfo',pd.Timestamp('2025-12-31'))==70
    assert np.isnan(a.trailing(book,'cfo',pd.Timestamp('2025-09-30')))

def test_dart_uses_actual_receipt_and_ytd_not_a_guessed_lag():
    f=pd.DataFrame([{'rcept_no':'20250814003690','currency':'KRW','bsns_year':'2025','reprt_code':'11012',
        'account_id':'ifrs-full_Revenue','sj_div':'IS','thstrm_amount':'100','thstrm_add_amount':'180',
        'ticker':'005930','fs_div':'CFS'}])
    r=a.parse_dart(f)[0]
    assert r['value']==180 and r['start']=='2025-01-01' and r['filed']=='2025-08-14'
    assert a.parse_dart(f,fiscal_month='03')==[]

def test_amendment_never_rewrites_the_earlier_asof_value():
    facts=[]
    for filed,value in [('2025-02-15',100),('2025-05-20',200)]:
        for field,val in [('assets',value),('cash',10),('liabilities',50)]:
            facts.append(dict(ticker='A',scope='us-gaap',field=field,start='',end='2024-12-31',
                filed=filed,filing_id=filed,value=val,priority=0,currency='USD'))
    panel=pd.DataFrame({'ticker':['A']*4,'date':pd.to_datetime(['2025-02-15','2025-02-16','2025-05-20','2025-05-21'])})
    r=a.attach(panel,a.events(facts))
    assert pd.isna(r.fin_cash_assets.iloc[0])
    assert r.fin_cash_assets.iloc[1:].tolist()==[.1,.1,.05]

def test_annual_input_never_enters_before_its_filing():
    data={'facts':{'us-gaap':{'Assets':{'units':{'USD':[{'form':'10-K','val':100,'end':'2024-12-31',
        'filed':'2025-03-10','accn':'123'}]}}}}}
    events=a.events(a.parse_sec(data,'A'))
    assert events.available_at.iloc[0]==pd.Timestamp('2025-03-11')

def test_multi_date_features_never_consume_later_quotes():
    dates=pd.bdate_range('2023-01-02',periods=450)
    p=pd.DataFrame({'date':dates,'ticker':'A','close':100+np.arange(450)*.1,
        'adjusted_close':100+np.arange(450)*.1,'open':100,'high':150,'low':90,'volume':1e8})
    chosen=[dates[400],dates[449]]
    batch=live.feature_panel(p,'us',training=False,signal_dates=chosen)
    for d in chosen:
        one=live.feature_panel(p.loc[p.date<=d],'us',training=False,signal_date=d)
        np.testing.assert_allclose(batch.loc[batch.date==d,live.FEATURES],one[live.FEATURES],equal_nan=True,rtol=1e-5,atol=1e-10)
    changed=p.copy();changed.loc[changed.date>chosen[0],'adjusted_close']*=10
    again=live.feature_panel(changed,'us',training=False,signal_dates=chosen)
    np.testing.assert_allclose(batch.iloc[0][live.FEATURES].astype(float),again.iloc[0][live.FEATURES].astype(float),equal_nan=True)
