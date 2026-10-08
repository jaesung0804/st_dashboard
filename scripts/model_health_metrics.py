"""Aggregate diagnostics of an existing model. No fitting or individual exports."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix, log_loss, matthews_corrcoef

from ai_stock_assistant import monthly_ews as live


def probability_summary(p):
    p = np.asarray(p, dtype=float)
    return {"rows": len(p), "mean": float(p.mean()),
            "quantiles": {str(q): float(np.quantile(p, q)) for q in (0, .1, .25, .5, .75, .9, 1)},
            "threshold_counts": {str(t): int((p >= t).sum()) for t in (.15, .20, .35, .5, .75)}}


def threshold_metrics(y, p, threshold):
    y, selected = np.asarray(y, dtype=int), np.asarray(p) >= threshold
    tn, fp, fn, tp = (int(x) for x in confusion_matrix(y, selected, labels=[0, 1]).ravel())
    recall = tp / (tp + fn) if tp + fn else None
    specificity = tn / (tn + fp) if tn + fp else None
    precision = tp / (tp + fp) if tp + fp else None
    return {"threshold": threshold, "tn": tn, "fp": fp, "fn": fn, "tp": tp,
            "alert_fraction": float(selected.mean()), "precision": precision, "recall": recall,
            "specificity": specificity, "accuracy": (tp + tn) / len(y),
            "balanced_accuracy": (recall + specificity) / 2 if recall is not None and specificity is not None else None,
            "mcc": float(matthews_corrcoef(y, selected)),
            "precision_lift": precision / float(y.mean()) if precision is not None and y.any() else None}


def score_audit(dates, y, components, train_rate):
    y = np.asarray(y, dtype=int)
    baseline = {"all_positive": np.ones(len(y)), "training_prevalence": np.full(len(y), train_rate),
                "same_sample_prevalence_hindsight_only": np.full(len(y), y.mean())}
    evaluated = {}
    for name, p in {**components, **baseline}.items():
        evaluated[name] = {**live.metrics(y, p), "log_loss": float(log_loss(y, p, labels=[0, 1])),
                           "distribution": probability_summary(p)}
    final = components["final"]
    deciles = []
    # Ties stay together; a constant prediction must not acquire arbitrary ranks.
    buckets = pd.qcut(final, 10, duplicates="drop")
    for interval in buckets.categories:
        mask = buckets == interval
        deciles.append({"rows": int(mask.sum()), "mean_prediction": float(final[mask].mean()),
                        "event_rate": float(y[mask].mean()), "lift": float(y[mask].mean() / y.mean()) if y.any() else None})
    return {"components_and_baselines": evaluated,
            "by_date": live.dated_metrics(dates, y, final), "deciles": deciles,
            "thresholds": [threshold_metrics(y, final, t) for t in (.15, .20, .35, .5, .75)],
            "all_positive_classifier": threshold_metrics(y, np.ones(len(y)), .5)}


def coefficient_report(linear, frames):
    coef = np.asarray(linear["coef"], dtype=float)
    scale, mean = np.asarray(linear["scale"]), np.asarray(linear["mean"])
    transformed = {}
    for name, frame in frames.items():
        x = frame[live.FEATURES].to_numpy(float)
        z = (np.where(np.isfinite(x), x, linear["median"]) - mean) / scale
        transformed[name] = (np.clip(z, -12, 12), z)
    rows = []
    for j, f in enumerate(live.FEATURES):
        rows.append({"feature": f, "label": live.FEATURE_LABELS[f], "coefficient_per_training_sd": float(coef[j]),
                     "odds_ratio_per_training_sd": float(np.exp(coef[j])), "coefficient_original_units": float(coef[j] / scale[j]),
                     "training_mean": float(mean[j]), "training_scale": float(scale[j]),
                     "populations": {name: {"mean_standardized_value": float(z[:, j].mean()),
                         "mean_logit_contribution": float((z[:, j] * coef[j]).mean()),
                         "clipped_fraction": float((np.abs(raw[:, j]) > 12).mean())}
                         for name, (z, raw) in transformed.items()}})
    pairs = []
    if "train" in transformed:
        corr = np.corrcoef(transformed["train"][0], rowvar=False)
        pairs = sorted([{"a": live.FEATURES[i], "b": live.FEATURES[j], "correlation": float(corr[i, j])}
                       for i in range(len(coef)) for j in range(i + 1, len(coef)) if np.isfinite(corr[i, j])],
                       key=lambda x: -abs(x["correlation"]))[:15]
    return {"intercept_standardized": float(linear["intercept"]),
            "intercept_original_units_without_clipping": float(linear["intercept"] - np.sum(coef * mean / scale)),
            "interpretation": "Penalized logistic component only, not the ensemble or causal effects. Original-unit expression holds inside clipping limits. No standard errors or p-values inferred.",
            "features": rows, "largest_training_correlations": pairs}


def label_audit(prices, panel, cutoff):
    """Independently verify each calibration label with explicit forward slices."""
    _, cal = live.chronological_split(panel, "down", pd.Timestamp(cutoff))
    n, checked, mismatches, boundary_cases = live.HORIZONS["down"], 0, 0, 0
    mins, terminals, event_steps, has_jump, outcomes = [], [], [], [], []
    events_by_date, large_jumps = {}, {}
    price_groups = {ticker: g for ticker, g in prices.groupby("ticker", sort=False)}
    for ticker, rows in cal.groupby("ticker", sort=False):
        g = price_groups[ticker]
        p = g.adjusted_close.where(g.adjusted_close.gt(0), g.close).to_numpy(float)
        dates = pd.DatetimeIndex(g.date)
        for row in rows.itertuples():
            i = dates.get_loc(row.date)
            future = p[i + 1:i + n + 1]
            if len(future) != n or not np.isfinite(future).all() or p[i] <= 0:
                raise ValueError("Incomplete or invalid calibration label horizon")
            low, terminal = float(future.min() / p[i] - 1), float(future[-1] / p[i] - 1)
            expected = int(low <= -.20)
            checked += 1
            mismatches += int(expected != row.y_down or dates[i + n] != row.end_down)
            # Compare literal <=80% target to implemented return <=-20% at floating-point boundary.
            boundary_cases += int((future.min() <= .8 * p[i]) != bool(expected))
            mins.append(low)
            terminals.append(terminal)
            path = p[i:i + n + 1]
            has_jump.append(bool(np.any(path[1:] / path[:-1] - 1 < -.301)))
            outcomes.append(expected)
            crossings = np.flatnonzero(future / p[i] - 1 <= -.2)
            if len(crossings):
                step = int(crossings[0] + 1)
                event_steps.append(step)
                day = str(dates[i + step].date())
                events_by_date[day] = events_by_date.get(day, 0) + 1
    # Unique source observations, not duplicate overlapping label windows.
    for g in price_groups.values():
        p = g.adjusted_close.where(g.adjusted_close.gt(0), g.close)
        jump = p.pct_change(fill_method=None).lt(-.301)
        for date in g.loc[jump & g.date.ge(cal.date.min()), "date"]:
            day = str(date.date())
            large_jumps[day] = large_jumps.get(day, 0) + 1
    unaffected = ~np.asarray(has_jump)
    return {"checked_rows": checked, "implementation_mismatches": mismatches,
            "rows_with_daily_loss_below_minus_30_1_percent_in_horizon": int(np.sum(has_jump)),
            "event_rate_excluding_these_rows_sensitivity_only": float(np.asarray(outcomes)[unaffected].mean()) if unaffected.any() else None,
            "literal_80_percent_boundary_differences": boundary_cases,
            "minimum_forward_return_quantiles": {str(q): float(np.quantile(mins, q)) for q in (0, .1, .25, .5, .75, .9, 1)},
            "terminal_63_session_return_quantiles": {str(q): float(np.quantile(terminals, q)) for q in (0, .1, .25, .5, .75, .9, 1)},
            "terminal_loss_at_least_20_fraction": float((np.asarray(terminals) <= -.2).mean()),
            "first_crossing_step_median": float(np.median(event_steps)) if event_steps else None,
            "first_crossing_dates_top20": sorted(events_by_date.items(), key=lambda x: -x[1])[:20],
            "unique_daily_adjusted_losses_below_minus_30_1_percent": sum(large_jumps.values()),
            "large_loss_dates_top20": sorted(large_jumps.items(), key=lambda x: -x[1])[:20]}
