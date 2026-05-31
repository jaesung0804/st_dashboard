from __future__ import annotations

import numpy as np
import pandas as pd


def apply_hard_veto(features: pd.DataFrame, min_avg_trading_value_20d: float = 100_000_000) -> pd.DataFrame:
    frame = features.sort_values(["ticker", "date"]).copy() if {"ticker", "date"}.issubset(features.columns) else features.copy()
    reasons: list[list[str]] = [[] for _ in range(len(frame))]

    def add_reason(mask: pd.Series, reason: str) -> None:
        for idx in np.flatnonzero(mask.fillna(False).to_numpy()):
            reasons[idx].append(reason)

    total_debt = frame.get("total_debt", 0)
    add_reason(frame.get("total_equity", pd.Series(index=frame.index, dtype=float)) <= 0, "total_equity<=0")
    add_reason(frame.get("debt_to_equity", pd.Series(index=frame.index, dtype=float)) > 3, "high_debt_to_equity")
    add_reason(frame.get("equity_ratio", pd.Series(index=frame.index, dtype=float)) < 0.1, "low_equity_ratio")
    add_reason(frame.get("avg_trading_value_20d", pd.Series(index=frame.index, dtype=float)) < min_avg_trading_value_20d, "insufficient_liquidity")
    add_reason(
        (frame.get("free_cash_flow", pd.Series(index=frame.index, dtype=float)) < 0)
        & (
            frame.groupby("ticker")["cash"].pct_change(fill_method=None).fillna(0)
            if {"ticker", "cash"}.issubset(frame.columns)
            else pd.Series(False, index=frame.index)
        ).lt(-0.2),
        "negative_fcf_declining_cash",
    )
    add_reason((frame.get("return_60d", pd.Series(index=frame.index, dtype=float)) > 1.5) & (frame.get("revenue_yoy", 0) <= 0), "parabolic_no_revenue_growth")
    add_reason(frame.get("total_assets", pd.Series(index=frame.index, dtype=float)).isna(), "missing_essential_financial_data")
    add_reason(pd.Series(total_debt, index=frame.index).isna(), "missing_debt_data")

    frame["hard_veto_reasons"] = [";".join(item) for item in reasons]
    frame["hard_crash_flag"] = frame["hard_veto_reasons"].str.len() > 0
    return frame
