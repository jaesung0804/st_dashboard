from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BacktestResult:
    summary: pd.DataFrame
    trades: pd.DataFrame
    holdings: pd.DataFrame


def month_end_signal_dates(dates: pd.Series) -> list[pd.Timestamp]:
    unique = pd.Series(pd.to_datetime(dates).drop_duplicates()).sort_values()
    return unique.groupby(unique.dt.to_period("M")).max().tolist()


def next_trading_day(all_dates: pd.Series, signal_date: pd.Timestamp) -> pd.Timestamp | None:
    dates = pd.Series(pd.to_datetime(all_dates).drop_duplicates()).sort_values().reset_index(drop=True)
    future = dates[dates > pd.Timestamp(signal_date)]
    return None if future.empty else future.iloc[0]


def apply_transaction_cost_and_slippage(raw_return: float, transaction_cost_bps: float, slippage_bps: float) -> float:
    round_trip_cost = 2 * (transaction_cost_bps + slippage_bps) / 10_000
    return raw_return - round_trip_cost


def portfolio_metrics(returns: pd.Series, turnover: float = 0.0) -> dict[str, float]:
    returns = returns.dropna()
    if returns.empty:
        return {}
    cumulative = (1 + returns).prod() - 1
    years = max(len(returns) / 12, 1 / 12)
    cagr = (1 + cumulative) ** (1 / years) - 1
    vol = returns.std() * np.sqrt(12)
    sharpe = returns.mean() / returns.std() * np.sqrt(12) if returns.std() else np.nan
    downside = returns[returns < 0].std() * np.sqrt(12)
    sortino = returns.mean() * 12 / downside if downside else np.nan
    equity = (1 + returns).cumprod()
    drawdown = equity / equity.cummax() - 1
    max_dd = drawdown.min()
    return {
        "cumulative_return": cumulative,
        "cagr": cagr,
        "volatility": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "calmar": cagr / abs(max_dd) if max_dd < 0 else np.nan,
        "win_rate": (returns > 0).mean(),
        "turnover": turnover,
    }


def run_monthly_backtest(
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    portfolio_name: str,
    candidate_filter,
    top_n: int = 20,
    transaction_cost_bps: float = 15,
    slippage_bps: float = 10,
    max_position_weight: float = 0.10,
    rank_column: str = "p_up",
) -> BacktestResult:
    price = prices.copy()
    price["date"] = pd.to_datetime(price["date"])
    if "adj_close" not in price.columns:
        price["adj_close"] = price["adjusted_close"]
    score = scores.copy()
    score["date"] = pd.to_datetime(score["date"])
    all_dates = price["date"]
    signal_dates = month_end_signal_dates(score["date"])
    trades = []
    holdings = []
    returns = []
    previous_holdings: set[str] = set()

    for signal_date in signal_dates:
        entry_date = next_trading_day(all_dates, signal_date)
        if entry_date is None:
            continue
        exit_date = next_trading_day(all_dates, entry_date + pd.offsets.MonthEnd(0))
        if exit_date is None:
            continue
        universe = score[score["date"].eq(signal_date)].copy()
        candidates = candidate_filter(universe).sort_values(rank_column, ascending=False).head(top_n)
        if candidates.empty:
            continue
        tickers = candidates["ticker"].astype(str).str.zfill(6).tolist()
        entry_prices = price[price["date"].eq(entry_date) & price["ticker"].isin(tickers)][["ticker", "adj_close"]]
        exit_prices = price[price["date"].eq(exit_date) & price["ticker"].isin(tickers)][["ticker", "adj_close"]]
        merged = entry_prices.merge(exit_prices, on="ticker", suffixes=("_entry", "_exit"))
        if merged.empty:
            continue
        weight = min(1 / len(merged), max_position_weight)
        merged["raw_return"] = merged["adj_close_exit"] / merged["adj_close_entry"] - 1
        merged["net_return"] = merged["raw_return"].map(
            lambda value: apply_transaction_cost_and_slippage(value, transaction_cost_bps, slippage_bps)
        )
        period_return = (merged["net_return"] * weight).sum()
        current_holdings = set(merged["ticker"])
        turnover = len(current_holdings.symmetric_difference(previous_holdings)) / max(len(current_holdings), 1)
        previous_holdings = current_holdings
        returns.append({"date": exit_date, "portfolio": portfolio_name, "return": period_return, "turnover": turnover})
        for _, row in merged.iterrows():
            trades.append(
                {
                    "portfolio": portfolio_name,
                    "signal_date": signal_date,
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "ticker": row["ticker"],
                    "weight": weight,
                    "raw_return": row["raw_return"],
                    "net_return": row["net_return"],
                }
            )
            holdings.append(
                {
                    "portfolio": portfolio_name,
                    "date": entry_date,
                    "ticker": row["ticker"],
                    "weight": weight,
                }
            )

    ret_frame = pd.DataFrame(returns)
    trade_frame = pd.DataFrame(trades)
    holding_frame = pd.DataFrame(holdings)
    if ret_frame.empty:
        summary = pd.DataFrame([{"portfolio": portfolio_name}])
    else:
        metrics = portfolio_metrics(ret_frame["return"], turnover=ret_frame["turnover"].mean())
        metrics["portfolio"] = portfolio_name
        metrics["top_n"] = top_n
        summary = pd.DataFrame([metrics])
    return BacktestResult(summary=summary, trades=trade_frame, holdings=holding_frame)


def candidate_level_metrics(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "rows": len(candidates),
                "avg_future_return_20d": candidates["future_return_20d"].mean(),
                "avg_future_return_63d": candidates["future_return_63d"].mean(),
                "avg_future_return_126d": candidates["future_return_126d"].mean(),
                "avg_mfe_63d": candidates["future_mfe_63d"].mean(),
                "avg_mae_63d": candidates["future_mae_63d"].mean(),
                "hit_rate_25pct": (candidates["future_mfe_63d"] >= 0.25).mean(),
                "loss_rate_20pct": (candidates["future_mae_63d"] <= -0.20).mean(),
                "loss_rate_30pct": (candidates["future_mae_63d"] <= -0.30).mean(),
            }
        ]
    )
