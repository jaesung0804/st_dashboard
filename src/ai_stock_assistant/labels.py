from __future__ import annotations

import pandas as pd
import numpy as np


def _future_rolling(series: pd.Series, horizon: int, method: str) -> pd.Series:
    shifted = series.shift(-1)
    reversed_window = shifted.iloc[::-1].rolling(horizon, min_periods=1)
    if method == "max":
        return reversed_window.max().iloc[::-1]
    if method == "min":
        return reversed_window.min().iloc[::-1]
    raise ValueError(f"Unsupported rolling method: {method}")


def _future_fixed_return(series: pd.Series, horizon: int) -> pd.Series:
    return series.shift(-horizon) / series - 1


def _future_window_close_return(series: pd.Series, horizon: int, method: str) -> pd.Series:
    future = series.shift(-1)
    window = future.iloc[::-1].rolling(horizon, min_periods=horizon)
    if method == "max":
        return window.max().iloc[::-1] / series - 1
    if method == "min":
        return window.min().iloc[::-1] / series - 1
    raise ValueError(f"Unsupported window method: {method}")


def _future_post_peak_drawdown(close: pd.Series, peak_horizon: int = 20, failure_horizon: int = 63) -> pd.Series:
    values = close.to_numpy(dtype=float)
    drawdowns: list[float] = []
    for idx in range(len(values)):
        peak_window = values[idx + 1 : idx + 1 + peak_horizon]
        if len(peak_window) < peak_horizon or np.isnan(peak_window).all():
            drawdowns.append(np.nan)
            continue
        peak_offset = int(np.nanargmax(peak_window))
        peak = peak_window[peak_offset]
        low_start = idx + 1 + peak_offset
        low_window = values[low_start : idx + 1 + failure_horizon]
        if len(low_window) == 0 or not np.isfinite(peak) or peak == 0:
            drawdowns.append(np.nan)
            continue
        drawdowns.append(np.nanmin(low_window) / peak - 1)
    return pd.Series(drawdowns, index=close.index)


def add_forward_mfe_mae(
    prices: pd.DataFrame,
    horizon: int = 63,
    close_col: str = "close",
    high_col: str = "high",
    low_col: str = "low",
) -> pd.DataFrame:
    """Add future MFE/MAE without using current-day high/low."""
    frame = prices.sort_values(["ticker", "date"]).copy()

    def apply_group(group: pd.DataFrame) -> pd.DataFrame:
        future_high = _future_rolling(group[high_col], horizon=horizon, method="max")
        future_low = _future_rolling(group[low_col], horizon=horizon, method="min")
        group[f"future_mfe_{horizon}d"] = future_high / group[close_col] - 1
        group[f"future_mae_{horizon}d"] = future_low / group[close_col] - 1
        return group

    return frame.groupby("ticker", group_keys=False).apply(apply_group)


def add_adjusted_ohlc(prices: pd.DataFrame) -> pd.DataFrame:
    frame = prices.copy()
    adj_factor = frame["adjusted_close"] / frame["close"]
    frame["adj_open"] = frame["open"] * adj_factor
    frame["adj_high"] = frame["high"] * adj_factor
    frame["adj_low"] = frame["low"] * adj_factor
    frame["adj_close"] = frame["adjusted_close"]
    return frame


def add_model_labels(prices: pd.DataFrame) -> pd.DataFrame:
    """Add cross-sectional tail-event labels used by the two-model architecture."""
    frame = add_adjusted_ohlc(prices)
    frame = add_forward_mfe_mae(frame, horizon=20, close_col="adj_close", high_col="adj_high", low_col="adj_low")
    frame = add_forward_mfe_mae(frame, horizon=63, close_col="adj_close", high_col="adj_high", low_col="adj_low")
    frame = add_forward_mfe_mae(frame, horizon=126, close_col="adj_close", high_col="adj_high", low_col="adj_low")

    frame["future_return_20d"] = frame.groupby("ticker")["adj_close"].transform(lambda s: _future_fixed_return(s, 20))
    frame["future_return_63d"] = frame.groupby("ticker")["adj_close"].transform(lambda s: _future_fixed_return(s, 63))
    frame["future_return_126d"] = frame.groupby("ticker")["adj_close"].transform(lambda s: _future_fixed_return(s, 126))
    frame["future_max_close_return_20d"] = frame.groupby("ticker")["adj_close"].transform(
        lambda s: _future_window_close_return(s, 20, "max")
    )
    frame["future_max_close_return_63d"] = frame.groupby("ticker")["adj_close"].transform(
        lambda s: _future_window_close_return(s, 63, "max")
    )
    frame["future_min_close_return_63d"] = frame.groupby("ticker")["adj_close"].transform(
        lambda s: _future_window_close_return(s, 63, "min")
    )
    frame["post_peak_drawdown"] = frame.groupby("ticker")["adj_close"].transform(_future_post_peak_drawdown)

    by_date = frame.groupby("date")
    frame["universe_median_future_return_63d"] = by_date["future_return_63d"].transform("median")
    frame["universe_median_future_max_close_return_63d"] = by_date["future_max_close_return_63d"].transform("median")
    frame["universe_median_future_min_close_return_63d"] = by_date["future_min_close_return_63d"].transform("median")
    frame["universe_median_future_max_close_return_20d"] = by_date["future_max_close_return_20d"].transform("median")
    frame["excess_return_63d"] = frame["future_return_63d"] - frame["universe_median_future_return_63d"]
    frame["excess_max_close_return_63d"] = (
        frame["future_max_close_return_63d"] - frame["universe_median_future_max_close_return_63d"]
    )
    frame["excess_min_close_return_63d"] = (
        frame["future_min_close_return_63d"] - frame["universe_median_future_min_close_return_63d"]
    )
    frame["excess_max_close_return_20d"] = (
        frame["future_max_close_return_20d"] - frame["universe_median_future_max_close_return_20d"]
    )

    frame["future_return_rank_pct"] = by_date["future_return_63d"].rank(pct=True, ascending=True)
    frame["future_max_return_rank_pct"] = by_date["future_max_close_return_63d"].rank(pct=True, ascending=True)
    frame["future_min_return_rank_pct"] = by_date["future_min_close_return_63d"].rank(pct=True, ascending=True)
    frame["future_max_close_return_20d_rank_pct"] = by_date["future_max_close_return_20d"].rank(
        pct=True,
        ascending=True,
    )
    frame["post_peak_drawdown_rank_pct"] = by_date["post_peak_drawdown"].rank(pct=True, ascending=True)

    frame["up_label_63d"] = (
        (frame["future_max_return_rank_pct"] >= 0.95)
        & (frame["excess_max_close_return_63d"] >= 0.20)
        & (frame["future_max_close_return_63d"] >= 0.10)
    )
    frame["extreme_up_label_63d"] = (frame["excess_return_63d"] >= 0.65) | (
        frame["future_return_rank_pct"] >= 0.99
    )
    frame["crash_label_63d"] = (
        (frame["future_min_return_rank_pct"] <= 0.05)
        & (frame["excess_min_close_return_63d"] <= -0.20)
        & (frame["future_min_close_return_63d"] <= -0.10)
    )
    frame["extreme_crash_label_63d"] = (frame["excess_return_63d"] <= -0.65) | (
        frame["future_return_rank_pct"] <= 0.01
    )
    frame["initial_up_event"] = (frame["future_max_close_return_20d_rank_pct"] >= 0.85) | (
        frame["excess_max_close_return_20d"] >= 0.10
    )
    frame["post_peak_failure"] = (frame["post_peak_drawdown_rank_pct"] <= 0.10) & (
        frame["post_peak_drawdown"] <= -0.20
    )
    frame["failed_breakout_label"] = frame["initial_up_event"] & frame["post_peak_failure"]
    frame["direct_crash_label"] = frame["crash_label_63d"] | frame["extreme_crash_label_63d"]
    frame["up_label"] = frame["up_label_63d"] | frame["extreme_up_label_63d"]
    frame["crash_label"] = frame["direct_crash_label"]
    return frame


def add_up_label(prices: pd.DataFrame, horizon: int = 63, threshold: float = 0.25) -> pd.DataFrame:
    frame = add_forward_mfe_mae(prices, horizon=horizon)
    mfe_col = f"future_mfe_{horizon}d"
    frame["up_label"] = frame[mfe_col] >= threshold
    return frame


def add_crash_label(prices: pd.DataFrame, horizon: int = 63, threshold: float = -0.25) -> pd.DataFrame:
    frame = add_forward_mfe_mae(prices, horizon=horizon)
    mae_col = f"future_mae_{horizon}d"
    frame["direct_crash_label"] = frame[mae_col] <= threshold
    return frame


def add_failed_breakout_label(
    prices: pd.DataFrame,
    breakout_horizon: int = 20,
    failure_horizon: int = 63,
    breakout_return: float = 0.10,
    failure_return: float = -0.30,
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
) -> pd.DataFrame:
    frame = prices.sort_values(["ticker", "date"]).copy()

    def apply_group(group: pd.DataFrame) -> pd.DataFrame:
        highs = group[high_col].to_numpy(dtype=float)
        lows = group[low_col].to_numpy(dtype=float)
        closes = group[close_col].to_numpy(dtype=float)
        labels = []
        for idx, close in enumerate(closes):
            high_window = highs[idx + 1 : idx + 1 + breakout_horizon]
            if len(high_window) == 0 or not pd.notna(close):
                labels.append(False)
                continue
            peak_offset = int(np.nanargmax(high_window))
            peak = high_window[peak_offset]
            hit_up_first = peak / close - 1 >= breakout_return
            low_start = idx + 1 + peak_offset
            low_window = lows[low_start : idx + 1 + failure_horizon]
            if len(low_window) == 0 or not hit_up_first:
                labels.append(False)
                continue
            trough_after_peak = np.nanmin(low_window)
            labels.append(trough_after_peak / peak - 1 <= failure_return)
        group["failed_breakout_label"] = labels
        return group

    return frame.groupby("ticker", group_keys=False).apply(apply_group)
