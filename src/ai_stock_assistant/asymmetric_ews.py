"""Asymmetric tail experiments and honest full-universe ordering diagnostics."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import monthly_ews as live

VERSION = 'ews-relative-asymmetric-63-v3'
TARGETS = {'up5': .05, 'up10': .10, 'down30': .30}
POLICY = {
    'version': VERSION, 'horizon_sessions': 63, 'up_tails': [.05, .10], 'down_tail': .30,
    'benchmark': 'signal-date eligible pool; subtracting its future median does not change percentile order',
    'market_score': '100 times current-market midrank percentile of the continuous rank model; not event probability or expected return',
    'rank_target': 'future (average rank - 0.5)/observable pool size',
    'warning': 'threshold selected on calibration data for >=80% down30 recall; test recall is reported separately',
    'selection_fractions': [.01, .05, .10], 'cost': .003,
    'risk_filter': 'remove warned names from initial selection without backfill; unused budget earns zero',
    'payoff': 'weighted mean positive outcome / absolute weighted mean negative outcome, after assumed cost',
    'evaluation': 'same-date stock selection; overlapping 63-session close-to-close outcomes, not an executable compounded portfolio',
    'limitations': ['same four folds were inspected previously; development comparison, not a fresh untouched final test',
                    'tail labels do not assure positive absolute returns',
                    'independently fitted probabilities can violate nesting; rates are reported',
                    'full-ranking scores compress uncertainty into an ordering; middle-group quality is evaluated separately',
                    'same-close entry is hypothetical; next-session execution, slippage, tax and FX realized outcomes not validated'],
}


def relabel(panel: pd.DataFrame) -> pd.DataFrame:
    """Reuse the immutable feature/outcome panel; never change the source snapshot."""
    out = panel.copy()
    if out.duplicated(['date', 'ticker']).any():
        raise ValueError('Duplicate dated stock keys')
    for head in TARGETS:
        out['y_' + head] = np.nan
    out['future_percentile'] = np.nan
    for _, ids in out.groupby('date').groups.items():
        g = out.loc[ids]
        valid = g.future_return.notna() & np.isfinite(g.future_return) & g.future_return.gt(-1)
        if valid.mean() + 1e-12 < .95 or g.benchmark_coverage.min() < .95:
            continue
        r = g.loc[valid, 'future_return']
        if r.empty or np.ptp(r) < 1e-12:
            continue
        median = r.median()
        out.loc[r.index, 'future_percentile'] = (r.rank(method='average') - .5) / len(r)
        for head, fraction in TARGETS.items():
            label = ((r <= r.quantile(fraction)) & (r < median)) if head.startswith('down') else (
                (r >= r.quantile(1-fraction)) & (r > median))
            out.loc[r.index, 'y_' + head] = label.astype(float)
    # Existing purge code uses aliases only for eligibility, not target semantics.
    out['y_up'], out['y_down'] = out.y_up5, out.y_down30
    return out


def market_percentiles(raw):
    values = pd.Series(raw, dtype=float).replace([np.inf, -np.inf], np.nan)
    n = values.notna().sum()
    return 100 * (values.rank(method='average') - .5) / n if n else values


def top_weights(scores, fraction):
    scores = np.asarray(scores, dtype=float)
    if not np.isfinite(scores).all() or not 0 < fraction <= 1:
        raise ValueError('Finite scores and valid selection fraction required')
    k = max(1, int(np.ceil(len(scores) * fraction)))
    threshold = np.sort(scores)[-k]
    above, ties = scores > threshold, scores == threshold
    return above.astype(float) + ties * ((k - above.sum()) / ties.sum())


def warning_threshold(y, p, recall=.8):
    y, p = np.asarray(y), np.asarray(p, dtype=float)
    if not (0 < recall <= 1) or not np.isfinite(p).all() or not (y == 1).any():
        raise ValueError('Valid calibration positives required')
    return float(np.quantile(p[y == 1], 1-recall, method='lower'))


def warning_metrics(y, p, threshold):
    y, p = np.asarray(y, dtype=bool), np.asarray(p, dtype=float)
    flag = p >= threshold
    return {'threshold': threshold, 'alert_fraction': float(flag.mean()),
            'precision': float(y[flag].mean()) if flag.any() else None,
            'recall': float(flag[y].mean()) if y.any() else None,
            'false_positive_rate': float(flag[~y].mean()) if (~y).any() else None}


def selection_summary(frame, scores, down, threshold, fraction, *, filtered, cost=.003):
    """One fixed budget per signal; no compounding overlapping outcomes."""
    records = []
    f = frame.reset_index(drop=True)
    for date, ids in f.groupby('date').groups.items():
        g = f.loc[ids]
        w = top_weights(np.asarray(scores)[ids], fraction)
        budget = w.sum()
        if filtered:
            w *= np.asarray(down)[ids] < threshold
        used = w.sum()
        ret = g.future_return.to_numpy(float) - cost
        positive, negative = ret > 0, ret < 0
        average_gain = float(np.average(ret[positive], weights=w[positive])) if w[positive].sum() else None
        average_loss = float(np.average(ret[negative], weights=w[negative])) if w[negative].sum() else None
        portfolio = float(np.dot(w, ret) / budget)
        records.append({'date': str(pd.Timestamp(date).date()), 'budget_names': float(budget),
            'invested_fraction': float(used/budget), 'net_return_with_cash': portfolio,
            'excess_vs_pool_mean': portfolio - float(g.future_return.mean()-cost),
            'selected_excess_vs_median': float(np.dot(w, g.excess_return)/used) if used else None,
            'up5_precision': float(np.dot(w, g.y_up5)/used) if used else None,
            'up10_precision': float(np.dot(w, g.y_up10)/used) if used else None,
            'down30_fraction': float(np.dot(w, g.y_down30)/used) if used else None,
            'loss_fraction': float(np.dot(w, negative)/used) if used else None,
            'payoff_ratio': average_gain / -average_loss if average_gain is not None and average_loss is not None else None})
    keys = [k for k in records[0] if k != 'date']
    return {'date_count': len(records), 'observed_date_counts': {k: sum(r[k] is not None for r in records) for k in keys},
            'means': {k: float(np.mean([r[k] for r in records if r[k] is not None]))
                if any(r[k] is not None for r in records) else None for k in keys}, 'by_date': records}


def ranking_diagnostics(frame, scores):
    f = frame.reset_index(drop=True).copy()
    f['score'] = scores
    dates, deciles = [], []
    for date, g in f.groupby('date'):
        percentile = market_percentiles(g.score).to_numpy() / 100
        middle = g.loc[(percentile >= .2) & (percentile <= .8)]
        ic = g.score.corr(g.future_return, method='spearman') if g.score.nunique() > 1 else np.nan
        mic = middle.score.corr(middle.future_return, method='spearman') if middle.score.nunique() > 1 else np.nan
        dates.append({'date': str(pd.Timestamp(date).date()), 'rank_ic': float(ic) if np.isfinite(ic) else None,
            'middle_rank_ic': float(mic) if np.isfinite(mic) else None,
            'unique_scores': int(g.score.nunique()), 'rows': len(g)})
        buckets = np.minimum(9, (percentile * 10).astype(int))
        for decile in range(10):
            group = g.loc[buckets == decile]
            if len(group):
                deciles.append({'date': str(pd.Timestamp(date).date()), 'decile': decile+1,
                                'excess': float(group.excess_return.mean()), 'rows': len(group)})
    average = lambda key: float(np.mean([r[key] for r in dates if r[key] is not None])) if any(r[key] is not None for r in dates) else None
    return {'mean_date_rank_ic': average('rank_ic'),
            'mean_date_middle_rank_ic': average('middle_rank_ic'),
            'by_date': dates, 'deciles': deciles}


def evaluate(frame, prediction, card, threshold):
    result = {'rows': len(frame), 'first': str(frame.date.min().date()), 'last': str(frame.date.max().date())}
    for head in TARGETS:
        y, p = frame['y_' + head], prediction[head]
        result[head] = {'pooled': live.metrics(y, p), 'within_date': live.dated_metrics(frame.date, y, p),
            'constant_brier': float(np.mean((y-card['heads'][head]['train_prevalence'])**2))}
    result['down_warning'] = warning_metrics(frame.y_down30, prediction['down30'], threshold)
    result['nesting_violation_fraction'] = float(np.mean(prediction['up5'] > prediction['up10']))
    result['rank'] = ranking_diagnostics(frame, prediction['rank'])
    result['selection'] = {}
    for model in ('up5', 'up10', 'rank'):
        for fraction in POLICY['selection_fractions']:
            for filtered in (False, True):
                key = f'{model}-top{round(fraction*100)}-' + ('filtered' if filtered else 'all')
                result['selection'][key] = selection_summary(frame, prediction[model], prediction['down30'], threshold,
                                                              fraction, filtered=filtered)
    return result
