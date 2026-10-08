"""Research contract for market-relative warnings and cross-market comparison.

This version never relabels or overwrites ews-price-v1 forecasts. A rank is not
an event probability. All forward outcomes share the market's session calendar.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import monthly_ews as live

VERSION = 'ews-relative-63-v2-research'
HORIZON = 63
TAIL = .20
MIN_COVERAGE = .95
# ret = relative + market: remove the exact redundant terms for interpretable LR.
FEATURES = [f for f in live.FEATURES if f not in ('ret20', 'ret60')]
CONTRACT = {
    'version': VERSION, 'horizon_sessions': HORIZON, 'tail_fraction': TAIL,
    'benchmark': 'signal-date liquid eligible pool; terminal adjusted-price return',
    'reference_statistics': ['mean', 'median', 'q20', 'q80'],
    'minimum_outcome_coverage': MIN_COVERAGE,
    'ties': 'inclusive quantile boundaries; require strict separation from median; all-flat dates unlabelled',
    'currency': 'KRW', 'fx': 'unhedged USD/KRW proportional scenarios; flat base is an assumption, not an FX forecast',
    'score': '50 + 50*tanh((KRW median return - 0.5*max(0,-KRW q10 return) - cost)/0.20)',
    'score_status': 'unvalidated decision index, not a probability; same absolute scale for both markets',
    'limitations': ['current-listing survivorship', 'retained adjusted prices may be revised',
                    '63 local sessions are approximately three months; country holidays differ',
                    'quantile bands are conditional forecasts, not guaranteed coverage',
                    'no taxes; user-specified round-trip cost; no FX forecast'],
}


def attach_targets(panel: pd.DataFrame, prices: pd.DataFrame, *, horizon=HORIZON,
                   tail=TAIL, minimum_coverage=MIN_COVERAGE) -> pd.DataFrame:
    """Pool membership is fixed before inspecting any future outcomes.

    Missing/suspended endpoints and unverified discontinuities are unknown, not
    zeros. More than 5% unknown outcomes invalidate that date's relative labels.
    A missing stock session cannot extend the target horizon by one day.
    """
    if not 0 < tail < .5 or not 0 < minimum_coverage <= 1 or horizon < 1:
        raise ValueError('Invalid relative target policy')
    if panel.duplicated(['date', 'ticker']).any():
        raise ValueError('Duplicate signal keys')
    p = prices.pivot(index='date', columns='ticker', values='adjusted_close').sort_index()
    volume = prices.pivot(index='date', columns='ticker', values='volume').reindex_like(p)
    breaks = prices.assign(quality_break=prices.get('quality_break', False)).pivot(
        index='date', columns='ticker', values='quality_break').reindex_like(p).eq(True).cumsum()
    out = panel.drop(columns=[c for c in panel if c.startswith(('y_', 'end_', 'future_'))]).copy()
    for col in ('future_return', 'benchmark_mean', 'benchmark_median', 'benchmark_q20',
                'benchmark_q80', 'benchmark_coverage', 'excess_return', 'y_up', 'y_down'):
        out[col] = np.nan
    out['end_up'] = pd.NaT
    out['end_down'] = pd.NaT
    for date, indices in out.groupby('date').groups.items():
        i = p.index.get_indexer([date])[0]
        if i < 0 or i + horizon >= len(p):
            continue
        tickers = out.loc[indices, 'ticker']
        start = p.iloc[i].reindex(tickers).to_numpy(float)
        end = p.iloc[i + horizon].reindex(tickers).to_numpy(float)
        active = volume.iloc[i + horizon].reindex(tickers).to_numpy(float) > 0
        unchanged = (breaks.iloc[i + horizon] == breaks.iloc[i]).reindex(tickers).fillna(False).to_numpy()
        valid = np.isfinite(start) & (start > 0) & np.isfinite(end) & (end > 0) & active & unchanged
        returns = np.full(len(indices), np.nan)
        returns[valid] = end[valid] / start[valid] - 1
        coverage = float(valid.mean())
        out.loc[indices, 'future_return'] = returns
        out.loc[indices, ['end_up', 'end_down']] = p.index[i + horizon]
        out.loc[indices, 'benchmark_coverage'] = coverage
        if not valid.any():
            continue
        median = float(np.median(returns[valid]))
        low, high = np.quantile(returns[valid], [tail, 1 - tail])
        out.loc[indices, ['benchmark_mean', 'benchmark_median', 'benchmark_q20', 'benchmark_q80']] = [
            np.mean(returns[valid]), median, low, high]
        if coverage + 1e-12 < minimum_coverage:
            continue
        out.loc[indices, 'excess_return'] = returns - median
        # All-equal outcomes carry no information about relative leadership.
        if np.ptp(returns[valid]) < 1e-12:
            continue
        out.loc[indices, 'y_down'] = np.where(valid, ((returns <= low) & (returns < median)).astype(float), np.nan)
        out.loc[indices, 'y_up'] = np.where(valid, ((returns >= high) & (returns > median)).astype(float), np.nan)
    return out


def purged_split(panel: pd.DataFrame, boundary, calibration_dates=13):
    boundary = pd.Timestamp(boundary)
    known = panel.loc[(panel.end_down < boundary) & panel.y_down.notna() & panel.y_up.notna()]
    dates = sorted(known.date.unique())
    if len(dates) < calibration_dates + 52:
        raise ValueError('Insufficient dated history')
    cal_start = pd.Timestamp(dates[-calibration_dates])
    train = known.loc[(known.end_down < cal_start) & (known.date >= boundary - pd.Timedelta(days=365 * 4))]
    cal = known.loc[known.date >= cal_start]
    if len(train) < 1000 or len(cal) < 500:
        raise ValueError('Insufficient training or calibration observations')
    return train, cal


def krw_return(local_return, market, fx_change=0.0):
    if market not in ('kr', 'us') or not np.isfinite(fx_change) or fx_change <= -1:
        raise ValueError('Invalid currency scenario')
    value = np.asarray(local_return, dtype=float)
    return (1 + value) * (1 + fx_change if market == 'us' else 1) - 1


def attractiveness(median, q10, market, *, fx_change=0.0, cost=.003):
    """Identical absolute units across markets; never domestic percentile ranks.

    The 0.5 loss penalty, 20pp score scale and 30bp cost are explicit policy
    assumptions, not backtest-selected parameters or market-specific fees.
    """
    if not np.isfinite([median, q10, cost]).all() or median < -1 or q10 < -1 or q10 > median or cost < 0:
        return None
    central = float(krw_return(median, market, fx_change))
    lower = float(krw_return(q10, market, fx_change))
    utility = central - .5 * max(0., -lower) - cost
    return {'score': float(50 + 50 * np.tanh(utility / .20)), 'median_krw': central,
            'q10_krw': lower, 'utility': utility, 'cost': cost, 'fx_change': fx_change}


RULE_VERSION = 'fundamental-opinion-v1'
RULES = {
    'buy_review': 'Two distinct reported periods: sales growth >0, operating margin >0, CFO/assets >0 and CFO after PPE/assets >0; latest liabilities/assets <0.8 and interest coverage >=2.',
    'sell_review': 'Latest liabilities/assets >1, or two distinct periods with sales growth <=-10%, operating margin <0 and CFO/assets <0.',
    'watch': 'Fresh sufficient evidence without a buy/sell rule match.',
    'withheld': 'No dated filing, stale >240 days, financial industry, or insufficient required evidence.',
}


def fundamental_opinion(events: pd.DataFrame, ticker: str, asof) -> dict:
    """Independent financial corroboration, not a target-price or valuation call."""
    result = {'version': RULE_VERSION, 'status': 'withheld', 'label': '판단 보류',
              'reasons': [], 'evidence': [], 'valuation': 'not_assessed'}
    if events.empty:
        result['reasons'] = ['공시일이 확인된 재무 데이터 없음']
        return result
    date = pd.Timestamp(asof)
    known = events.loc[events.ticker.eq(ticker) & (events.available_at <= date)].sort_values(
        ['period_end', 'available_at', 'filing_id'])
    # Amendments to one period are not two consecutive reported periods.
    known = known.drop_duplicates('period_end', keep='last').tail(2)
    if known.empty:
        result['reasons'] = ['해당 기준일까지 확인된 공시 없음']
        return result
    fields = ['fin_revenue_growth', 'fin_operating_margin', 'fin_cfo_assets',
              'fin_cfo_after_ppe_assets', 'fin_liabilities_assets', 'fin_interest_coverage']
    for _, row in known.iterrows():
        result['evidence'].append({
            'period_end': str(row.period_end.date()), 'available_at': str(row.available_at.date()),
            'filing_id': str(row.filing_id),
            'metrics': {f: float(row[f]) if pd.notna(row.get(f)) and np.isfinite(row[f]) else None for f in fields}})
    last = known.iloc[-1]
    if not 0 <= (date - last.period_end).days <= 240:
        result['reasons'] = ['최근 재무 기간이 240일을 초과했거나 날짜가 유효하지 않음']
        return result
    if pd.isna(last.get('fin_financial_sector')) or last.fin_financial_sector != 0:
        result['reasons'] = ['금융업 전용 기준 또는 업종 확인 필요']
        return result
    debt = last.get('fin_liabilities_assets', np.nan)
    if np.isfinite(debt) and debt > 1:
        result.update(status='sell_review', label='매도 검토', reasons=['부채가 자산을 초과: 자본잠식 징후'])
        return result
    consecutive = len(known) == 2 and 45 <= (known.iloc[-1].period_end - known.iloc[0].period_end).days <= 400
    # annual as well as quarterly reporting is supported; both periods are named.
    required = known.reindex(columns=fields).replace([np.inf, -np.inf], np.nan)
    if not consecutive or required.isna().any().any():
        result['reasons'] = ['서로 다른 두 보고기간의 필수 재무지표가 부족함']
        return result
    if ((required.fin_revenue_growth <= -.10) & (required.fin_operating_margin < 0)
            & (required.fin_cfo_assets < 0)).all():
        result.update(status='sell_review', label='매도 검토', reasons=['두 보고기간 모두 매출 10% 이상 감소·영업 적자·영업현금흐름 음수'])
    elif ((required.fin_revenue_growth > 0) & (required.fin_operating_margin > 0)
          & (required.fin_cfo_assets > 0) & (required.fin_cfo_after_ppe_assets > 0)).all() and debt < .8 and last.fin_interest_coverage >= 2:
        result.update(status='buy_review', label='매수 검토', reasons=['두 보고기간의 성장·영업 수익·현금흐름 양호', '부채·이자보상 기준 충족; 적정 주가 평가는 별도'])
    else:
        result.update(status='watch', label='관망', reasons=['필수 재무지표 확인; 매수·매도 검토 조건 미충족'])
    return result
