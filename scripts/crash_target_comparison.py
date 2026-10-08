"""Read-only matched-date crash-target comparison. No fitting or raw exports."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ai_stock_assistant import monthly_ews as live

VOL_BINS = [0, .30, .50, .80, 1.20, np.inf]
VOL_LABELS = ["below_30pct", "30_to_50pct", "50_to_80pct", "80_to_120pct", "120pct_plus"]


def path_outcomes(paths, annual_vol):
    """Paths start on the next session; all paths share the same 63 sessions.

    The benchmark is an equal-weight, buy-and-hold portfolio fixed on the signal
    date, restricted to complete paths. It is NOT an official market index.
    Relative outcomes are descriptive, not a beta-neutral causal decomposition.
    """
    paths, annual_vol = np.asarray(paths, float), np.asarray(annual_vol, float)
    benchmark = paths.mean(axis=0)
    downside = paths <= .8
    upside = paths >= 1.2
    steps = np.arange(1, paths.shape[1] + 1)
    first_down = np.where(downside, steps, np.inf).min(axis=1)
    first_up = np.where(upside, steps, np.inf).min(axis=1)
    low = paths.min(axis=1)
    sigma = annual_vol * np.sqrt(paths.shape[1] / 252)
    out = pd.DataFrame({"barrier20": downside.any(axis=1), "terminal20": paths[:, -1] <= .8,
                        "terminal_return": paths[:, -1] - 1, "min_return": low - 1,
                        "relative_barrier20": (paths / benchmark <= .8).any(axis=1),
                        "up20_before_down20": first_up < first_down,
                        "recovered_breakeven_after_barrier": downside.any(axis=1) & (paths[:, -1] >= 1),
                        "recovered_above_minus10_after_barrier": downside.any(axis=1) & (paths[:, -1] >= .9),
                        "annual_vol": annual_vol})
    for factor in (1., 1.5, 2.):
        out[f"log_loss_beyond_{factor:g}_sigma"] = np.log(low) <= -factor * sigma
    return out, {"terminal_return": float(benchmark[-1] - 1), "minimum_return": float(benchmark.min() - 1)}


def summarize(rows):
    if rows.empty:
        return {"rows": 0}
    event = rows.barrier20.to_numpy(bool)
    n = int(event.sum())
    return {"rows": len(rows), "barrier20_rate": float(event.mean()),
            "terminal20_rate": float(rows.terminal20.mean()),
            "relative_barrier20_rate": float(rows.relative_barrier20.mean()),
            "median_annual_vol": float(rows.annual_vol.median()),
            "median_terminal_return": float(rows.terminal_return.median()),
            "median_minimum_return": float(rows.min_return.median()),
            "among_barrier_events_recovered_breakeven": float(rows.recovered_breakeven_after_barrier.sum() / n) if n else None,
            "among_barrier_events_recovered_above_minus10": float(rows.recovered_above_minus10_after_barrier.sum() / n) if n else None,
            "among_barrier_events_up20_first": float(rows.loc[event, "up20_before_down20"].mean()) if n else None,
            "vol_scaled_event_rates": {str(k): float(rows[f"log_loss_beyond_{k:g}_sigma"].mean()) for k in (1., 1.5, 2.)}}


def compare_targets(prices, market, anchors):
    """Use the same calendar anchors and prior two years; no model evaluation."""
    dates = pd.DatetimeIndex(sorted(prices.date.unique()))
    requested = []
    for offset in (0, 1, 2):
        for anchor in anchors:
            date = pd.Timestamp(anchor) - pd.DateOffset(years=offset)
            index = dates.searchsorted(date, side="right") - 1
            if index < 0 or index + 63 >= len(dates):
                raise ValueError("Comparison requires fully matured 63-session paths")
            signal = dates[index]
            if (date - signal).days > 7:
                raise ValueError("Comparison anchor is not near an observed session")
            requested.append({"period": f"year_minus_{offset}", "anchor": str(date.date()),
                              "signal": signal, "future_dates": dates[index + 1:index + 64]})
    signals = sorted({r["signal"] for r in requested})
    candidates = {signal: [] for signal in signals}
    eligible_counts = {signal: 0 for signal in signals}
    future_dates = {r["signal"]: r["future_dates"] for r in requested}
    for _, group in prices.groupby("ticker", sort=False):
        group = group.reset_index(drop=True)
        features = live.ticker_features(group, labels=False).set_index("date")
        p = group.adjusted_close.where(group.adjusted_close.gt(0), group.close).astype(float)
        log_vol = np.log(p).diff().rolling(60, min_periods=60).std() * np.sqrt(252)
        history = pd.Series(p.to_numpy(), index=pd.DatetimeIndex(group.date))
        vol = pd.Series(log_vol.to_numpy(), index=history.index)
        for signal in signals:
            if signal not in features.index:
                continue
            f = features.loc[signal]
            if not (f["history"] >= 253 and f["active"] and f["turnover20"] >= (500_000_000 if market == "kr" else 5_000_000)
                    and f["close"] >= (1000 if market == "kr" else 1)):
                continue
            eligible_counts[signal] += 1
            future = history.reindex(future_dates[signal]).to_numpy(float)
            sigma = vol.loc[signal]
            if not np.isfinite(future).all() or (future <= 0).any() or not np.isfinite(sigma) or sigma <= 0:
                continue
            candidates[signal].append((future / float(history.loc[signal]), float(sigma), float(f["turnover20"])))
    all_rows, by_date = [], []
    for request in requested:
        signal = request["signal"]
        values = candidates[signal]
        if not values:
            raise ValueError("No eligible complete paths for requested comparison date")
        outcomes, benchmark = path_outcomes(np.stack([v[0] for v in values]), [v[1] for v in values])
        turnover = np.asarray([v[2] for v in values])
        top = np.zeros(len(values), dtype=bool)
        top[np.argsort(-turnover, kind="stable")[:200]] = True
        outcomes["liquidity_top200"] = top
        outcomes["period"] = request["period"]
        outcomes["vol_band"] = pd.cut(outcomes.annual_vol, VOL_BINS, labels=VOL_LABELS, right=False)
        by_date.append({"period": request["period"], "anchor": request["anchor"], "signal_date": str(signal.date()),
                        "end_date": str(request["future_dates"][-1].date()), "eligible_rows": eligible_counts[signal],
                        "excluded_incomplete_or_invalid_paths": eligible_counts[signal] - len(outcomes),
                        **summarize(outcomes), "equal_weight_fixed_pool_benchmark": benchmark,
                        "liquidity_top200": summarize(outcomes.loc[top])})
        all_rows.append(outcomes)
    combined = pd.concat(all_rows, ignore_index=True)
    return {"definition": "Observed outcomes, not model forecasts or investable returns. Absolute barrier <=80%; same 63 market sessions; prior-60-session log volatility; no fitting.",
            "limitations": "Signal-date liquid pools differ by market. Complete-path restriction can omit delisted/suspended/missing stocks. Retained history may be revised. Overlapping outcomes are not independent. Equal-weight benchmark is not an official index or beta-adjusted counterfactual. Prior years are descriptive controls, not held-out model tests.",
            "periods": {period: {**summarize(rows), "liquidity_top200": summarize(rows.loc[rows.liquidity_top200]),
                "by_volatility": {band: summarize(rows.loc[rows.vol_band.eq(band)]) for band in VOL_LABELS}}
                for period, rows in combined.groupby("period", sort=True)}, "by_date": by_date}
