import numpy as np
import pandas as pd

from ai_stock_assistant.shadow_ews import smooth_targets


def prices_for_targets():
    dates = pd.bdate_range('2024-01-02', periods=130)
    records = []
    for ticker in ['STABLE', 'SPIKE', 'FLAT']:
        p = np.full(len(dates), 100.)
        if ticker == 'STABLE':
            p[122:127] = 150.
        if ticker == 'SPIKE':
            p[126] = 350.  # Same five-day average as STABLE, but one-day only.
        records.extend({'date': date, 'ticker': ticker, 'adjusted_close': value,
                        'close': value, 'volume': 1} for date, value in zip(dates, p))
    return pd.DataFrame(records), dates


def test_smoothed_target_rejects_one_day_windfall_and_uses_whole_mean():
    prices, dates = prices_for_targets()
    result = smooth_targets(prices, [dates[0]]).set_index('ticker')
    assert np.isclose(result.loc['STABLE', 'future_up'], .5)
    assert np.isclose(result.loc['SPIKE', 'future_up'], .5)
    assert np.isclose(result.loc['STABLE', 'benchmark_up'], 1 / 3)
    assert result.loc['STABLE', 'y_up'] == 1
    assert result.loc['SPIKE', 'y_up'] == 0
    assert result.loc['SPIKE', 'persistent_days'] == 1
    assert result.loc['STABLE', 'end_up'] == dates[126]


def test_missing_terminal_quote_stays_missing_and_gates_benchmark_coverage():
    prices, dates = prices_for_targets()
    prices = prices.loc[~((prices.ticker == 'STABLE') & (prices.date == dates[124]))]
    result = smooth_targets(prices, [dates[0]]).set_index('ticker')
    assert pd.isna(result.loc['STABLE', 'future_up'])
    assert np.isclose(result.loc['STABLE', 'benchmark_coverage'], 2 / 3)
    assert result['y_up'].isna().all()
    assert result.loc['SPIKE', 'end_up'] == dates[126]  # No per-stock date shifting.


def test_targets_exclude_signal_day_crash_and_do_not_label_unmatured_rows():
    prices, dates = prices_for_targets()
    prices.loc[(prices.ticker == 'STABLE') & (prices.date == dates[1]), 'adjusted_close'] = 79.
    result = smooth_targets(prices, [dates[0], dates[10]])
    first = result.loc[result.date == dates[0]].set_index('ticker')
    assert first.loc['STABLE', 'y_down'] == 1
    assert first.loc['FLAT', 'y_down'] == 0
    assert result.loc[result.date == dates[10], 'y_up'].isna().all()


def test_suspended_terminal_prices_are_not_persistent_tradeable_gains():
    prices, dates = prices_for_targets()
    prices.loc[(prices.ticker == 'STABLE') & prices.date.isin(dates[122:127]), 'volume'] = 0
    result = smooth_targets(prices, [dates[0]]).set_index('ticker')
    assert pd.isna(result.loc['STABLE', 'future_up'])
    assert result.y_up.isna().all()


def test_date_weighting_prevents_large_cross_sections_dominating_macro_fit():
    from ai_stock_assistant.shadow_ews import weights
    frame = pd.DataFrame({'date': ['A'] * 100 + ['B'] * 2, 'ticker': list(range(102))})
    frame['weight'] = weights(frame)
    assert np.isclose(frame.loc[frame.date == 'A', 'weight'].sum(), frame.loc[frame.date == 'B', 'weight'].sum())


def test_saved_shadow_forecast_is_immutable_even_if_new_inputs_change(tmp_path):
    from ai_stock_assistant import monthly_ews as live
    root = live.freeze_prediction(tmp_path, 'kr', '2026-09-04', [{'ticker': 'A', 'price_up': .2}], {'prediction_kind': 'delayed'})
    before = (root / 'rows.json').read_bytes()
    live.freeze_prediction(tmp_path, 'kr', '2026-09-04', [{'ticker': 'A', 'price_up': .9}], {'prediction_kind': 'live'})
    assert (root / 'rows.json').read_bytes() == before
