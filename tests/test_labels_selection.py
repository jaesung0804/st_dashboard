from __future__ import annotations

import pandas as pd

from ai_stock_assistant.labels import add_crash_label, add_failed_breakout_label, add_model_labels, add_up_label
from ai_stock_assistant.selection import select_final_candidates
from ai_stock_assistant.backtest import apply_transaction_cost_and_slippage, next_trading_day
from ai_stock_assistant.ensemble import EnsembleSelectionConfig, select_ensemble_candidates
from ai_stock_assistant.veto import apply_hard_veto


def make_prices(closes: list[float], highs: list[float] | None = None, lows: list[float] | None = None) -> pd.DataFrame:
    highs = highs or closes
    lows = lows or closes
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=len(closes), freq="D"),
            "ticker": "005930",
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": 1000,
        }
    )


def test_up_label_uses_future_high_without_current_day() -> None:
    prices = make_prices([100, 100, 100], highs=[200, 110, 130])
    labeled = add_up_label(prices, horizon=1, threshold=0.25)
    assert not bool(labeled.loc[0, "up_label"])
    assert bool(labeled.loc[1, "up_label"])


def test_crash_label_uses_future_low_without_current_day() -> None:
    prices = make_prices([100, 100, 100], lows=[50, 90, 70])
    labeled = add_crash_label(prices, horizon=1, threshold=-0.25)
    assert not bool(labeled.loc[0, "direct_crash_label"])
    assert bool(labeled.loc[1, "direct_crash_label"])


def test_failed_breakout_label() -> None:
    prices = make_prices([100, 100, 100, 100], highs=[100, 112, 105, 100], lows=[100, 100, 70, 100])
    labeled = add_failed_breakout_label(prices, breakout_horizon=1, failure_horizon=2)
    assert bool(labeled.loc[0, "failed_breakout_label"])


def test_final_selection_is_up_minus_crash_and_ranks_by_p_up_only() -> None:
    scores = pd.DataFrame(
        {
            "ticker": ["A", "B", "C"],
            "p_up": [0.90, 0.95, 0.80],
            "p_crash": [0.10, 0.80, 0.20],
            "hard_crash_flag": [False, False, False],
        }
    )
    final, vetoed, crash = select_final_candidates(
        scores,
        up_min_probability=0.70,
        up_top_percentile=0.15,
        crash_probability_threshold=0.50,
    )
    assert final["ticker"].tolist() == ["A", "C"]
    assert vetoed["ticker"].tolist() == ["B"]
    assert crash["ticker"].tolist() == ["B"]


def test_hard_veto_always_excludes() -> None:
    scores = pd.DataFrame(
        {
            "ticker": ["A", "B"],
            "p_up": [0.99, 0.80],
            "p_crash": [0.01, 0.01],
            "hard_crash_flag": [True, False],
        }
    )
    final, vetoed, _ = select_final_candidates(scores, up_min_probability=0.7, crash_probability_threshold=0.9)
    assert final["ticker"].tolist() == ["B"]
    assert vetoed["ticker"].tolist() == ["A"]


def test_backtest_entry_uses_next_trading_day() -> None:
    dates = pd.Series(pd.to_datetime(["2024-01-31", "2024-02-01", "2024-02-02"]))
    assert next_trading_day(dates, pd.Timestamp("2024-01-31")) == pd.Timestamp("2024-02-01")


def test_transaction_cost_and_slippage_applied() -> None:
    assert apply_transaction_cost_and_slippage(0.10, transaction_cost_bps=10, slippage_bps=15) == 0.095


def test_hard_veto_rule_engine_flags_bad_rows() -> None:
    features = pd.DataFrame(
        {
            "ticker": ["A"],
            "total_equity": [-1.0],
            "avg_trading_value_20d": [1_000_000_000],
            "total_assets": [100.0],
            "total_debt": [10.0],
        }
    )
    flagged = apply_hard_veto(features)
    assert bool(flagged.loc[0, "hard_crash_flag"])


def test_cross_sectional_tail_labels_use_relative_ranks() -> None:
    rows = []
    final_prices = [("A", 300), ("B", 105), ("C", 100), ("D", 95), ("E", 70)]
    final_prices.extend([(f"X{i:02d}", 100) for i in range(15)])
    for ticker, final_price in final_prices:
        closes = [100.0] * 64
        closes[-1] = float(final_price)
        for idx, close in enumerate(closes):
            rows.append(
                {
                    "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=idx),
                    "ticker": ticker,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "adjusted_close": close,
                    "volume": 1000,
                }
            )
    labeled = add_model_labels(pd.DataFrame(rows))
    first_day = labeled[labeled["date"].eq(pd.Timestamp("2024-01-01"))].set_index("ticker")
    assert bool(first_day.loc["A", "up_label_63d"])
    assert bool(first_day.loc["A", "extreme_up_label_63d"])
    assert bool(first_day.loc["E", "crash_label_63d"])
    assert first_day.loc["A", "future_return_rank_pct"] > first_day.loc["B", "future_return_rank_pct"]


def test_ensemble_selection_is_row_level_up_minus_crash_and_ranks_by_up_only() -> None:
    scores = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-31", "2024-01-31", "2024-02-29"]),
            "ticker": ["A", "B", "A"],
            "p_up_ensemble": [0.30, 0.40, 0.20],
            "up_vote_count": [4, 4, 4],
            "up_disagreement": [0.02, 0.02, 0.02],
            "p_crash_ensemble": [0.01, 0.40, 0.01],
            "crash_vote_count": [0, 3, 0],
            "hard_crash_flag": [False, False, False],
            "p_breakout_failure": [0.01, 0.01, 0.01],
            "p_up_rule": [0.2, 0.2, 0.2],
            "p_up_logistic_elasticnet": [0.2, 0.2, 0.2],
            "p_up_lightgbm": [0.2, 0.2, 0.2],
            "p_up_extra_trees": [0.2, 0.2, 0.2],
            "p_up_price_volume": [0.2, 0.2, 0.2],
            "p_up_fundamental_change": [0.2, 0.2, 0.2],
            "p_crash_rule": [0.01, 0.20, 0.01],
            "p_crash_logistic_elasticnet": [0.01, 0.20, 0.01],
            "p_crash_lightgbm_general": [0.01, 0.20, 0.01],
            "p_crash_lightgbm_breakout_failure": [0.01, 0.01, 0.01],
            "p_crash_financial_fragility": [0.01, 0.01, 0.01],
            "p_crash_blowoff_exhaustion": [0.01, 0.01, 0.01],
        }
    )
    final, vetoed, crash, _ = select_ensemble_candidates(
        scores,
        config=EnsembleSelectionConfig(up_threshold=0.10, crash_threshold=0.10),
    )
    assert final[["date", "ticker"]].astype(str).values.tolist() == [["2024-01-31", "A"], ["2024-02-29", "A"]]
    assert vetoed["ticker"].tolist() == ["B"]
    assert crash["ticker"].tolist() == ["B"]


def test_ensemble_hard_veto_excludes_and_debate_keeps_uncertain_rows_out_of_final() -> None:
    base = {
        "date": pd.to_datetime(["2024-01-31", "2024-01-31"]),
        "ticker": ["A", "B"],
        "p_up_ensemble": [0.40, 0.40],
        "up_vote_count": [4, 2],
        "up_disagreement": [0.02, 0.20],
        "p_crash_ensemble": [0.01, 0.01],
        "crash_vote_count": [0, 0],
        "hard_crash_flag": [True, False],
        "p_breakout_failure": [0.01, 0.01],
    }
    for col in [
        "p_up_rule",
        "p_up_logistic_elasticnet",
        "p_up_lightgbm",
        "p_up_extra_trees",
        "p_up_price_volume",
        "p_up_fundamental_change",
    ]:
        base[col] = [0.3, 0.3]
    for col in [
        "p_crash_rule",
        "p_crash_logistic_elasticnet",
        "p_crash_lightgbm_general",
        "p_crash_lightgbm_breakout_failure",
        "p_crash_financial_fragility",
        "p_crash_blowoff_exhaustion",
    ]:
        base[col] = [0.01, 0.01]
    final, vetoed, _, debate = select_ensemble_candidates(
        pd.DataFrame(base),
        config=EnsembleSelectionConfig(up_threshold=0.10),
    )
    assert final.empty
    assert vetoed["ticker"].tolist() == ["A"]
    assert debate["ticker"].tolist() == ["B"]
