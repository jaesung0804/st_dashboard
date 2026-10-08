import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from build_investment_metrics import price_summaries, financial_summary


def sample():
    dates=pd.bdate_range('2025-01-01',periods=260)
    p=pd.DataFrame({'ticker':'T','date':dates,'close':np.arange(260)+100.,
        'adjusted_close':np.arange(260)+100.,'volume':100.,'quality_break':False})
    return p,pd.DataFrame([{'ticker':'T','exchange':'TEST'}]),dates


def test_price_metrics_are_asof_and_use_actual_formulas():
    p,l,d=sample();x=price_summaries(p,l,d[251])['T']
    assert x['close']==351
    assert x['returns']['21']==pytest.approx(351/330-1)
    assert x['returns']['126']==pytest.approx(351/225-1)
    assert x['volatility20']==pytest.approx(np.std(np.diff(np.log(np.arange(331,352))),ddof=1)*np.sqrt(252))
    assert x['drawdown252']==0 and x['range_position252']==1
    assert x['chart'][0]==100 and x['chart'][-1]==pytest.approx(round(351/225*100,3))
    assert x['turnover20']==pytest.approx(np.arange(332,352).mean()*100)


@pytest.mark.parametrize('condition',['gap','break','stale','invalid'])
def test_incomplete_or_unverified_prices_do_not_create_returns(condition):
    p,l,d=sample()
    if condition=='gap': p=p.drop(250)
    elif condition=='break':p.loc[250,'quality_break']=True
    elif condition=='stale':p=p.iloc[:-1]
    else:p.loc[250,'adjusted_close']=np.nan
    # Another security defines the market calendar even when T misses a day.
    market,_,_=sample();market['ticker']='REFERENCE';p=pd.concat([p,market])
    x=price_summaries(p,l,d[-1])['T']
    assert x['returns']['21'] is None and x['chart']==[]


def test_flat_chart_has_no_invented_range_position():
    p,l,d=sample();p['adjusted_close']=100
    x=price_summaries(p,l,d[-1])['T'];assert x['range_position252'] is None
    assert x['returns']['126']==0 and x['volatility20']==0


def test_financial_amendments_availability_and_derived_capital():
    e=pd.DataFrame([{'ticker':'T','period_end':pd.Timestamp('2025-03-31'),
        'available_at':pd.Timestamp(a),'filing_id':str(i),'fin_liabilities_assets':ratio}
        for i,(a,ratio) in enumerate([('2025-05-01',.6),('2025-05-20',.7),('2025-06-01',.8)])])
    x=financial_summary(e,'T','2025-05-25');assert len(x['history'])==1
    assert x['latest']['metrics']['equity_assets']==pytest.approx(.3)
    assert x['latest']['metrics']['liabilities_equity']==pytest.approx(.7/.3)
    assert x['latest']['metrics']['fin_roa'] is None
    assert financial_summary(e,'T','2026-05-25')['stale']
    e['fin_liabilities_assets']=1.1
    assert financial_summary(e,'T','2025-05-25')['latest']['metrics']['liabilities_equity'] is None
    assert financial_summary(e,'MISSING','2025-05-25')['latest'] is None
