"""Dated presentation metrics from retained inputs; no collection or inference."""
from __future__ import annotations

import numpy as np
import pandas as pd

FIN_FIELDS = ['fin_revenue_growth', 'fin_gross_margin', 'fin_operating_margin', 'fin_roa',
    'fin_cfo_assets', 'fin_cfo_after_ppe_assets', 'fin_accruals_assets', 'fin_cash_assets',
    'fin_liabilities_assets', 'fin_current_ratio', 'fin_ppe_sales', 'fin_interest_coverage']


def finite(value):
    return float(value) if pd.notna(value) and np.isfinite(value) else None


def financial_summary(events, ticker, asof):
    date = pd.Timestamp(asof)
    known = events.loc[events.ticker.eq(ticker) & (events.available_at <= date) &
        (events.period_end <= date)].sort_values(['period_end', 'available_at', 'filing_id'])
    known = known.drop_duplicates('period_end', keep='last').tail(4)
    history = []
    for _, row in known.iterrows():
        metrics = {k: finite(row.get(k, np.nan)) for k in FIN_FIELDS}
        ratio = metrics['fin_liabilities_assets']
        metrics['equity_assets'] = 1 - ratio if ratio is not None else None
        metrics['liabilities_equity'] = ratio / (1 - ratio) if ratio is not None and 0 <= ratio < 1 else None
        history.append({'period_end': str(row.period_end.date()), 'available_at': str(row.available_at.date()),
            'filing_id': str(row.filing_id), 'scope': str(row.get('scope', '')), 'metrics': metrics})
    age = int((date - known.iloc[-1].period_end).days) if len(known) else None
    return {'history': history, 'latest': history[-1] if history else None, 'age_days': age,
        'stale': age is not None and age > 240,
        'financial_sector': finite(known.iloc[-1].get('fin_financial_sector', np.nan)) if len(known) else None}


def price_summaries(prices, listing, asof):
    date = pd.Timestamp(asof)
    prices = prices.loc[prices.date <= date].sort_values(['ticker', 'date'])
    calendar = np.sort(prices.date.unique())
    names = listing.fillna('').set_index('ticker').to_dict('index')
    result = {}
    for ticker, g in prices.groupby('ticker', sort=False):
        end = g.iloc[-1]
        a = g.adjusted_close.to_numpy(float)
        dates = g.date.to_numpy()
        breaks = g.quality_break.to_numpy(bool)
        current = end.date == date
        info = names.get(ticker, {})
        row = {'quote_date': str(end.date.date()), 'close': finite(end.close), 'currency': info.get('currency'),
            'exchange': str(info.get('exchange', '')), 'sector': str(info.get('sector', '')),
            'stale': not current, 'returns': {}, 'volatility20': None, 'drawdown252': None,
            'range_position252': None, 'turnover20': None, 'chart': [], 'chart_start': None,
            'chart_end': None, 'history_status': 'insufficient_or_unverified_history'}
        def window(n):
            # Require n+1 consecutive market sessions, not an arbitrary number
            # of old quotes from a suspended/security-specific sparse series.
            if not current or len(g) < n+1 or len(calendar) < n+1:
                return None
            w = a[-n-1:]
            if not np.array_equal(dates[-n-1:], calendar[-n-1:]) or breaks[-n-1:].any():
                return None
            return w if np.isfinite(w).all() and (w > 0).all() else None
        for n in (1, 21, 63, 126):
            w = window(n)
            row['returns'][str(n)] = finite(w[-1]/w[0]-1) if w is not None else None
        w = window(20)
        if w is not None:
            row['volatility20'] = finite(np.std(np.diff(np.log(w)), ddof=1) * np.sqrt(252))
            turn = g.close.to_numpy(float)[-20:] * g.volume.to_numpy(float)[-20:]
            if np.isfinite(turn).all() and (turn >= 0).all():
                row['turnover20'] = finite(turn.mean())
        w = window(251)
        if w is not None:
            row['drawdown252'] = finite(w[-1]/w.max()-1)
            row['range_position252'] = finite((w[-1]-w.min())/(w.max()-w.min())) if w.max() > w.min() else None
        w = window(126)
        if w is not None:
            # A normalized chart summary, not a second raw-price archive.
            positions = np.unique(np.linspace(0,126,64,dtype=int))
            row['chart'] = [round(float(w[i]/w[0]*100),3) for i in positions]
            row['chart_start'] = str(pd.Timestamp(dates[-127]).date())
            row['chart_end'] = str(date.date())
            row['history_status'] = 'available'
        result[str(ticker)] = row
    return result
