import numpy as np
import pandas as pd

from ai_stock_assistant.data.price_quality import prepare
from ai_stock_assistant.shadow_ews import smooth_targets


def test_confirmed_ppcb_split_is_not_a_62500_fold_investment_gain_or_double_adjusted():
    p = pd.DataFrame({'date':pd.to_datetime(['2025-01-28','2025-01-29']), 'ticker':'PPCB',
                      'adjusted_close':[.01,625.], 'close':[.01,625.], 'volume':[1000,32]})
    original = p.copy(deep=True)
    fixed, report = prepare(p,'us')
    assert np.isclose(fixed.adjusted_close.iloc[1] / fixed.adjusted_close.iloc[0],625/600)
    assert report['adjustments'][0]['applied']
    pd.testing.assert_frame_equal(p,original)
    again, report = prepare(fixed,'us')
    assert not report['adjustments'][0]['applied']
    pd.testing.assert_series_equal(again.adjusted_close, fixed.adjusted_close)


def test_unverified_jump_invalidates_outcome_without_capping_or_future_feature_leakage():
    dates = pd.bdate_range('2023-01-01',periods=270)
    records=[]
    for ticker in ['BROKEN','NORMAL']:
        for i,date in enumerate(dates):
            value=100. if ticker=='NORMAL' or i<100 else 10000.
            records.append({'date':date,'ticker':ticker,'adjusted_close':value,'close':value,'volume':10})
    p=pd.DataFrame(records)
    fixed, report=prepare(p,'us')
    bad=fixed.loc[fixed.ticker=='BROKEN']
    assert bad.loc[bad.date<dates[100],'quality_valid'].all()
    assert not bad.loc[bad.date>=dates[100],'quality_valid'].any()
    assert bad.adjusted_close.iloc[-1]==10000.  # Values are not winsorized.
    target=smooth_targets(fixed,[dates[0]]).set_index('ticker')
    assert pd.isna(target.loc['BROKEN','future_up'])
    assert target.y_up.isna().all()  # Only 50% benchmark coverage, not an invented market return.
    assert len(report['unverified_discontinuities'])==1
