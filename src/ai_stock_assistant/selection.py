from __future__ import annotations

import pandas as pd


def select_final_candidates(
    scores: pd.DataFrame,
    up_min_probability: float = 0.60,
    up_top_percentile: float = 0.15,
    crash_probability_threshold: float = 0.50,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Select candidates using Up candidates minus Crash/Veto candidates.

    Final ranking is p_up descending. p_crash is never subtracted from p_up.
    """
    frame = scores.copy()
    rank_group = frame.groupby("date")["p_up"] if "date" in frame.columns else frame["p_up"]
    frame["up_rank_pct"] = rank_group.rank(pct=True, ascending=False, method="first") if hasattr(rank_group, "rank") else frame["p_up"].rank(pct=True, ascending=False, method="first")
    hard_flag = frame.get("hard_crash_flag", False)

    up_candidates = frame[
        (frame["p_up"] >= up_min_probability) | (frame["up_rank_pct"] <= up_top_percentile)
    ].copy()
    crash_candidates = frame[(frame["p_crash"] >= crash_probability_threshold) | hard_flag].copy()

    final_candidates = up_candidates[~up_candidates["ticker"].isin(crash_candidates["ticker"])].copy()
    final_candidates = final_candidates.sort_values("p_up", ascending=False).reset_index(drop=True)
    vetoed_up_candidates = up_candidates[up_candidates["ticker"].isin(crash_candidates["ticker"])].copy()
    vetoed_up_candidates = vetoed_up_candidates.sort_values("p_up", ascending=False).reset_index(drop=True)
    return final_candidates, vetoed_up_candidates, crash_candidates.reset_index(drop=True)
