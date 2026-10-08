from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ai_stock_assistant.relative_ews import attach_targets, attractiveness, fundamental_opinion, krw_return, purged_split


def data(count=20):
    dates = pd.bdate_range('2025-01-01', periods=5)
    rows = []
    for i in range(count):
        p = np.array([100., 100., 100., 60.+i*3, 200.])
        rows.extend({'date': d, 'ticker': str(i), 'adjusted_close': v, 'volume': 100.}
                    for d, v in zip(dates, p))
    prices = pd.DataFrame(rows)
    panel = prices.loc[prices.date == dates[0], ['date', 'ticker']].copy()
    return panel, prices, dates


def test_relative_events_are_invariant_to_a_common_market_shock():
    panel, prices, dates = data()
    base = attach_targets(panel, prices, horizon=3)
    shocked = prices.copy()
    shocked.loc[shocked.date == dates[3], 'adjusted_close'] *= .6
    actual = attach_targets(panel, shocked, horizon=3)
    assert base.y_up.mean() == pytest.approx(.2)
    assert base.y_down.mean() == pytest.approx(.2)
    assert base.benchmark_median.iloc[0] > actual.benchmark_median.iloc[0]
    pd.testing.assert_frame_equal(base[['y_up','y_down']], actual[['y_up','y_down']])


def test_missing_endpoint_is_not_shifted_forward_or_replaced_by_zero():
    panel, prices, dates = data()
    prices = prices.loc[~(prices.ticker.eq('0') & prices.date.eq(dates[3]))]
    result = attach_targets(panel, prices, horizon=3)
    assert result.benchmark_coverage.iloc[0] == .95
    assert result.loc[result.ticker.eq('0'), 'y_down'].isna().all()
    assert result.end_down.eq(dates[3]).all()
    assert result.loc[~result.ticker.eq('0'), 'y_down'].notna().all()


def test_more_than_five_percent_missing_blocks_relative_labels_for_whole_date():
    panel, prices, dates = data()
    prices.loc[prices.ticker.isin(['0','1']) & prices.date.eq(dates[3]), 'volume'] = 0
    result = attach_targets(panel, prices, horizon=3)
    assert result.y_up.isna().all() and result.y_down.isna().all()
    assert result.benchmark_coverage.eq(.9).all()


def test_unmatured_labels_unknown_and_equal_returns_not_arbitrarily_ranked():
    panel, prices, _ = data()
    assert attach_targets(panel, prices, horizon=5).y_down.isna().all()
    prices.adjusted_close = 100.
    result = attach_targets(panel, prices, horizon=3)
    assert result.y_up.isna().all() and result.y_down.isna().all()


def test_future_break_invalidates_outcome_without_changing_signal_pool():
    panel, prices, dates = data()
    prices['quality_break'] = prices.ticker.eq('0') & prices.date.eq(dates[2])
    result = attach_targets(panel, prices, horizon=3)
    assert len(result) == 20
    assert result.benchmark_coverage.eq(.95).all()
    assert result.loc[result.ticker.eq('0'), 'future_return'].isna().all()


def test_training_labels_are_purged_before_calibration_and_holdout():
    dates = pd.bdate_range('2020-01-01', periods=1000)
    panel = pd.DataFrame({'date': np.repeat(dates[::5], 60),
                          'end_down': np.repeat(dates[::5] + pd.offsets.BDay(63), 60),
                          'y_down': np.tile([0,1],6000), 'y_up': np.tile([1,0],6000)})
    train, cal = purged_split(panel, '2023-10-01')
    assert train.end_down.max() < cal.date.min()
    assert cal.end_down.max() < pd.Timestamp('2023-10-01')


def test_currency_conversion_is_multiplicative_and_kr_not_exposed():
    assert krw_return(.1,'us',-.05) == pytest.approx(.045)
    assert krw_return(.1,'kr',-.05) == pytest.approx(.1)
    assert attractiveness(.1,-.1,'us',fx_change=.05)['score'] > attractiveness(.1,-.1,'us',fx_change=-.05)['score']
    assert attractiveness(.1,-.1,'kr',fx_change=.05) ['score'] == attractiveness(.1,-.1,'kr',fx_change=-.05)['score']


def test_domestic_winner_can_score_below_us_stock_and_missing_not_neutral():
    assert attractiveness(-.03,-.15,'kr')['score'] < attractiveness(.08,-.05,'us')['score']
    assert attractiveness(.08,-.05,'kr')['score'] == attractiveness(.08,-.05,'us')['score']
    assert attractiveness(np.nan,-.1,'kr') is None
    assert attractiveness(-.1,.1,'kr') is None
    with pytest.raises(ValueError): krw_return(.1,'us',-1)


def events():
    return pd.DataFrame([{'ticker':'TEST','period_end':pd.Timestamp(p),'available_at':pd.Timestamp(a),'filing_id':f,
        'fin_revenue_growth':.1,'fin_operating_margin':.12,'fin_cfo_assets':.1,'fin_cfo_after_ppe_assets':.03,
        'fin_liabilities_assets':.5,'fin_interest_coverage':5.,'fin_financial_sector':0.}
        for p,a,f in [('2025-03-31','2025-05-01','A'),('2025-06-30','2025-08-01','B')]])


def test_opinion_uses_only_available_distinct_periods():
    e = events()
    assert fundamental_opinion(e,'TEST','2025-07-31')['status'] == 'withheld'
    assert fundamental_opinion(e,'TEST','2025-08-01')['status'] == 'buy_review'
    amendments = pd.concat([e.iloc[:1],e.iloc[:1].assign(available_at=pd.Timestamp('2025-06-01'),filing_id='A2')])
    assert fundamental_opinion(amendments,'TEST','2025-08-01')['status'] == 'withheld'


@pytest.mark.parametrize('change',[{'fin_interest_coverage':np.nan},{'fin_financial_sector':1}])
def test_missing_or_financial_sector_not_converted_to_neutral_or_buy(change):
    assert fundamental_opinion(events().assign(**change),'TEST','2025-08-01')['status'] == 'withheld'


def test_stale_financials_and_unknown_ticker_have_no_opinion():
    assert fundamental_opinion(events(),'TEST','2026-08-01')['status'] == 'withheld'
    assert fundamental_opinion(events(),'MISSING','2025-08-01')['status'] == 'withheld'


def test_sell_rule_requires_corroboration_or_negative_equity():
    e=events().assign(fin_revenue_growth=-.2,fin_operating_margin=-.1,fin_cfo_assets=-.1)
    assert fundamental_opinion(e,'TEST','2025-08-01')['status'] == 'sell_review'
    e.loc[0,'fin_revenue_growth']=.1
    assert fundamental_opinion(e,'TEST','2025-08-01')['status'] == 'watch'
    assert fundamental_opinion(events().assign(fin_liabilities_assets=1.1),'TEST','2025-08-01')['status'] == 'sell_review'


def test_preview_escapes_raw_text_script_termination(tmp_path):
    import importlib.util,json
    from pathlib import Path
    source=Path(__file__).parents[1]/'scripts/build_investment_preview.py'
    spec=importlib.util.spec_from_file_location('investment_preview',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    (tmp_path/'investment_view.json').write_text(json.dumps({'name':'</script><script>alert(1)</script>'}),encoding='utf-8')
    module.build(tmp_path,tmp_path/'preview')
    html=(tmp_path/'preview/index.html').read_text(encoding='utf-8')
    assert '</script><script>alert(1)' not in html
    assert '\\u003c/script\\u003e' in html
