import numpy as np
import pandas as pd
import pytest

from ai_stock_assistant.asymmetric_ews import (relabel, market_percentiles, top_weights,
    warning_threshold, warning_metrics, selection_summary, ranking_diagnostics)


def panel(n=100):
    r=np.linspace(-.4,.8,n)
    return pd.DataFrame({'date':pd.Timestamp('2025-01-01'),'ticker':[str(i) for i in range(n)],
        'future_return':r,'excess_return':r-np.median(r),'benchmark_coverage':1.})


def test_asymmetric_labels_and_median_centering_do_not_change_order():
    a=relabel(panel())
    assert [a[c].sum() for c in ['y_up5','y_up10','y_down30']] == [5,10,30]
    assert (a.y_up5 <= a.y_up10).all()
    assert ((a.y_up10+a.y_down30)<=1).all()
    shifted=panel();shifted.future_return-=.3
    b=relabel(shifted)
    pd.testing.assert_frame_equal(a[['y_up5','y_up10','y_down30','future_percentile']],b[['y_up5','y_up10','y_down30','future_percentile']])


def test_missing_and_all_equal_outcomes_do_not_get_arbitrary_labels():
    x=panel();x.loc[:5,'future_return']=np.nan
    assert relabel(x).y_up5.isna().all()
    assert relabel(panel().assign(future_return=.1)).y_down30.isna().all()


def test_ranking_ties_and_nulls_remain_honest():
    scores=market_percentiles([3,3,1,np.nan])
    assert scores[0]==scores[1]
    assert scores[2] < scores[0]
    assert np.isnan(scores[3])
    assert market_percentiles([1,1,1]).eq(50).all()
    np.testing.assert_array_equal(top_weights([.2,.2,.2,.2],.25),[.25]*4)


def test_recall_threshold_uses_only_given_calibration_and_reports_false_alarms():
    y=np.array([1]*10+[0]*10);p=np.linspace(.01,.9,20)
    threshold=warning_threshold(y,p)
    result=warning_metrics(y,p,threshold)
    assert result['recall']>=.8
    assert result['false_positive_rate']==1
    assert result['alert_fraction']>.8


def test_filtered_selection_leaves_cash_and_does_not_backfill():
    f=relabel(panel(100));scores=np.arange(100);down=np.zeros(100);down[95:]=1
    baseline=selection_summary(f,scores,down,.5,.05,filtered=False)
    filtered=selection_summary(f,scores,down,.5,.05,filtered=True)
    assert baseline['means']['up5_precision']==1
    assert filtered['means']['invested_fraction']==0
    assert filtered['means']['net_return_with_cash']==0
    assert filtered['means']['payoff_ratio'] is None
    assert filtered['observed_date_counts']['payoff_ratio']==0


def test_midrange_rank_quality_is_distinct_from_full_universe():
    f=relabel(panel())
    good=ranking_diagnostics(f,np.arange(100))
    assert good['mean_date_rank_ic']==pytest.approx(1)
    assert good['mean_date_middle_rank_ic']==pytest.approx(1)
    tied=ranking_diagnostics(f,np.ones(100))
    assert tied['mean_date_rank_ic'] is None


def test_generalized_trainer_preserves_probability_and_rank_shapes():
    import importlib.util,sys
    from pathlib import Path
    scripts=Path(__file__).parents[1]/'scripts'
    sys.path.insert(0,str(scripts))
    from run_relative_ews_research import fit_bundle,predict
    from ai_stock_assistant.relative_ews import FEATURES
    rng=np.random.default_rng(6)
    x=pd.DataFrame(rng.normal(size=(2000,len(FEATURES))),columns=FEATURES)
    x['date']=np.repeat(pd.bdate_range('2020-01-01',periods=20),100)
    x['end_down']=x.date+pd.offsets.BDay(63);x['ticker']=[str(i%100) for i in range(len(x))]
    x['future_return']=np.tile(np.linspace(-.3,.5,100),20);x['benchmark_coverage']=1.
    x=relabel(x)
    bundle,card=fit_bundle(x.iloc[:1400],x.iloc[1400:1900],heads=('up5','up10','down30'),quantiles=False,ranker=True)
    result=predict(bundle,x.iloc[1900:])
    assert set(result)=={'up5','up10','down30','rank'}
    assert all(v.shape==(100,) and np.isfinite(v).all() for v in result.values())
