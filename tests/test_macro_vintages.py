import io
import numpy as np
import pandas as pd
import pytest

from ai_stock_assistant.data import macro_vintages as macro


def vintage(month, revision=0):
    dates = pd.date_range('2018-01-01', month + '-01', freq='MS')
    frame = pd.DataFrame({'sasdate': dates.strftime('%m/%d/%Y')})
    for column in macro.SERIES:
        frame[column] = np.arange(len(dates), dtype=float) + 100 + revision
    return macro.vintage_features(frame.to_csv(index=False).encode(), month, 'test://vintage')


def test_later_revision_never_changes_earlier_signal_or_same_day():
    earlier, later = vintage('2020-01'), vintage('2020-02', revision=700)
    data = pd.DataFrame([earlier, later])
    data['available_date'] = pd.to_datetime(data.available_date)
    panel = pd.DataFrame({'date': pd.to_datetime(['2020-03-02', '2020-04-01', '2020-04-02']), 'ticker': 'A'})
    result = macro.attach(panel, data)
    assert result.vintage_month.tolist() == ['2020-01', '2020-01', '2020-02']
    assert result.macro_policy_rate.iloc[0] == result.macro_policy_rate.iloc[1]
    assert result.macro_policy_rate.iloc[2] > result.macro_policy_rate.iloc[1] + 600
    with pytest.raises(ValueError, match='same complete'):
        macro.attach(pd.DataFrame({'date': [pd.Timestamp('2020-03-01')], 'ticker': ['A']}), data)


def test_stale_snapshot_fails_instead_of_filling_forever():
    data = pd.DataFrame([vintage('2020-01')])
    data['available_date'] = pd.to_datetime(data.available_date)
    with pytest.raises(ValueError, match='stale'):
        macro.attach(pd.DataFrame({'date': [pd.Timestamp('2020-07-01')], 'ticker': ['A']}), data)


def test_future_observations_inside_a_file_cannot_leak_into_features():
    first = vintage('2020-01')
    dates = pd.date_range('2018-01-01', '2020-12-01', freq='MS')
    data = pd.DataFrame({'sasdate': dates.strftime('%m/%d/%Y')})
    for column in macro.SERIES:
        data[column] = np.arange(len(dates), dtype=float) + 100
        data.loc[dates > pd.Timestamp('2020-01-01'), column] = 90000
    second = macro.vintage_features(data.to_csv(index=False).encode(), '2020-01', 'test://revised')
    assert [first[c] for c in macro.FEATURES] == [second[c] for c in macro.FEATURES]
