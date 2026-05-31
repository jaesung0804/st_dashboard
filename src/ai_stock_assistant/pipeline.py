from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ai_stock_assistant.backtest import candidate_level_metrics, run_monthly_backtest
from ai_stock_assistant.features import FEATURE_COLUMNS, build_feature_matrix
from ai_stock_assistant.models import fit_calibrated_lgbm, predict_proba
from ai_stock_assistant.selection import select_final_candidates
from ai_stock_assistant.veto import apply_hard_veto


@dataclass(frozen=True)
class PipelineResult:
    output_dir: Path
    scores: pd.DataFrame
    final_candidates: pd.DataFrame
    backtest_summary: pd.DataFrame


def _date_cutoffs(data: pd.DataFrame, train_years: int, validation_months: int, test_months: int) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    dates = pd.Series(pd.to_datetime(data["date"]).drop_duplicates()).sort_values()
    start = dates.min() + pd.DateOffset(years=train_years)
    last = dates.max() - pd.DateOffset(months=test_months)
    cutoffs = []
    current = pd.Timestamp(start) + pd.DateOffset(months=validation_months)
    while current <= last:
        train_end = current - pd.DateOffset(months=validation_months)
        val_end = current
        test_end = current + pd.DateOffset(months=test_months)
        cutoffs.append((train_end, val_end, test_end))
        current += pd.DateOffset(months=test_months)
    return cutoffs


def keep_month_end_rows(data: pd.DataFrame) -> pd.DataFrame:
    frame = data.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    month_end = frame.groupby(["ticker", frame["date"].dt.to_period("M")])["date"].transform("max")
    return frame[frame["date"].eq(month_end)].copy()


def walk_forward_scores(
    features: pd.DataFrame,
    feature_cols: list[str],
    train_years: int = 2,
    validation_months: int = 6,
    test_months: int = 3,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    frame = features.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.dropna(subset=["up_label", "crash_label", "failed_breakout_label"])
    score_frames = []
    importances = []
    calibrations = []

    for fold, (train_end, val_end, test_end) in enumerate(
        _date_cutoffs(frame, train_years=train_years, validation_months=validation_months, test_months=test_months),
        start=1,
    ):
        train_start = train_end - pd.DateOffset(years=train_years)
        train = frame[(frame["date"] >= train_start) & (frame["date"] < train_end)].copy()
        validation = frame[(frame["date"] >= train_end) & (frame["date"] < val_end)].copy()
        test = frame[(frame["date"] >= val_end) & (frame["date"] < test_end)].copy()
        if len(train) < 1000 or len(validation) < 200 or test.empty:
            continue

        up_fit = fit_calibrated_lgbm(train, validation, feature_cols, "up_label", "up")
        crash_fit = fit_calibrated_lgbm(train, validation, feature_cols, "crash_label", "general_crash")
        breakout_train = train[(train["return_20d"] > 0.05) | (train["breakout_20d_high"] == 1)].copy()
        breakout_val = validation[(validation["return_20d"] > 0.05) | (validation["breakout_20d_high"] == 1)].copy()
        if len(breakout_train) < 500 or breakout_train["failed_breakout_label"].nunique() < 2:
            breakout_train = train
            breakout_val = validation
        breakout_fit = fit_calibrated_lgbm(
            breakout_train,
            breakout_val,
            feature_cols,
            "failed_breakout_label",
            "breakout_failure",
        )

        scored = test.copy()
        scored["p_up"] = predict_proba(up_fit.model, scored, feature_cols)
        scored["p_up_tail"] = scored["p_up"]
        scored["p_general_crash"] = predict_proba(crash_fit.model, scored, feature_cols)
        scored["p_breakout_failure"] = predict_proba(breakout_fit.model, scored, feature_cols)
        scored["p_crash"] = scored[["p_general_crash", "p_breakout_failure"]].max(axis=1)
        scored["fold"] = fold
        scored["train_end"] = train_end
        scored["validation_end"] = val_end
        score_frames.append(scored)

        for result in [up_fit, crash_fit, breakout_fit]:
            imp = result.feature_importance.copy()
            imp["fold"] = fold
            importances.append(imp)
            cal = result.calibration.copy()
            cal["fold"] = fold
            calibrations.append(cal)

    scores = pd.concat(score_frames, ignore_index=True) if score_frames else pd.DataFrame()
    importance = pd.concat(importances, ignore_index=True) if importances else pd.DataFrame()
    calibration = pd.concat(calibrations, ignore_index=True) if calibrations else pd.DataFrame()
    return scores, importance, calibration


def make_strategy_filters(
    up_min_probability: float,
    up_top_percentile: float,
    crash_threshold: float,
):
    def benchmark(data: pd.DataFrame) -> pd.DataFrame:
        return data.sort_values("avg_trading_value_20d", ascending=False)

    def rule_up(data: pd.DataFrame) -> pd.DataFrame:
        data = data.copy()
        data["rule_score"] = data["return_60d"].fillna(0) + data["volume_ratio_20d_to_60d"].fillna(1) * 0.05
        return data.sort_values("rule_score", ascending=False).head(max(10, int(len(data) * up_top_percentile)))

    def ml_up(data: pd.DataFrame) -> pd.DataFrame:
        frame = data.copy()
        frame["up_rank_pct"] = frame["p_up"].rank(pct=True, ascending=False, method="first")
        return frame[(frame["p_up"] >= up_min_probability) | (frame["up_rank_pct"] <= up_top_percentile)]

    def ml_up_minus_hard(data: pd.DataFrame) -> pd.DataFrame:
        return ml_up(data)[~ml_up(data)["hard_crash_flag"]]

    def ml_up_minus_general(data: pd.DataFrame) -> pd.DataFrame:
        candidates = ml_up(data)
        veto = (candidates["p_general_crash"] >= crash_threshold) | candidates["hard_crash_flag"]
        return candidates[~veto]

    def ml_up_minus_all(data: pd.DataFrame) -> pd.DataFrame:
        candidates = ml_up(data)
        veto = (candidates["p_crash"] >= crash_threshold) | candidates["hard_crash_flag"]
        return candidates[~veto]

    return {
        "Benchmark": benchmark,
        "Rule-Based Up Only": rule_up,
        "ML Up Only": ml_up,
        "ML Up minus Hard Veto": ml_up_minus_hard,
        "ML Up minus General Crash": ml_up_minus_general,
        "ML Up minus General Crash minus Breakout Failure": ml_up_minus_all,
    }


def run_backtests_for_scores(
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    up_min_probability: float,
    up_top_percentile: float,
    crash_threshold: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summaries = []
    trades = []
    holdings = []
    filters = make_strategy_filters(up_min_probability, up_top_percentile, crash_threshold)
    for name, filter_fn in filters.items():
        top_ns = [50] if name == "Benchmark" else [10, 20, 50]
        for top_n in top_ns:
            result = run_monthly_backtest(scores, prices, name, filter_fn, top_n=top_n)
            summaries.append(result.summary)
            trades.append(result.trades)
            holdings.append(result.holdings)
    return (
        pd.concat(summaries, ignore_index=True) if summaries else pd.DataFrame(),
        pd.concat(trades, ignore_index=True) if trades else pd.DataFrame(),
        pd.concat(holdings, ignore_index=True) if holdings else pd.DataFrame(),
    )


def veto_analysis(scores: pd.DataFrame, up_min_probability: float, up_top_percentile: float, crash_threshold: float) -> pd.DataFrame:
    frame = scores.copy()
    frame["up_rank_pct"] = frame.groupby("date")["p_up"].rank(pct=True, ascending=False, method="first")
    up = frame[(frame["p_up"] >= up_min_probability) | (frame["up_rank_pct"] <= up_top_percentile)].copy()
    up["vetoed"] = (up["p_crash"] >= crash_threshold) | up["hard_crash_flag"]
    up["winner"] = up["future_mfe_63d"] >= 0.25
    up["crashed"] = up["future_mae_63d"] <= -0.25
    before = up
    after = up[~up["vetoed"]]
    vetoed = up[up["vetoed"]]
    return pd.DataFrame(
        [
            {
                "up_candidates": len(up),
                "vetoed": len(vetoed),
                "true_veto": ((vetoed["crashed"]) | (vetoed["failed_breakout_label"])).sum(),
                "false_veto": (vetoed["winner"] & ~vetoed["crashed"]).sum(),
                "good_pass": (after["winner"] & ~after["crashed"]).sum(),
                "bad_pass": (after["crashed"] | after["failed_breakout_label"]).sum(),
                "crash_rate_before_veto": before["crashed"].mean(),
                "crash_rate_after_veto": after["crashed"].mean(),
                "mdd_before_veto": before["future_mae_63d"].mean(),
                "mdd_after_veto": after["future_mae_63d"].mean(),
                "missed_winner_rate": (vetoed["winner"]).mean(),
            }
        ]
    )


def run_modeling_pipeline(
    prices_path: Path,
    financials_path: Path,
    listings_path: Path,
    output_dir: Path,
    up_min_probability: float = 0.60,
    up_top_percentile: float = 0.15,
    crash_threshold: float = 0.50,
    monthly_only: bool = True,
) -> PipelineResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    feature_path = output_dir / "training_features.csv"
    features = build_feature_matrix(prices_path, financials_path, listings_path, None)
    features = apply_hard_veto(features)
    if monthly_only:
        features = keep_month_end_rows(features)
        features.to_csv(feature_path, index=False, encoding="utf-8-sig")
    feature_cols = [col for col in FEATURE_COLUMNS if col in features.columns]
    scores, importance, calibration = walk_forward_scores(features, feature_cols)
    if scores.empty:
        raise RuntimeError("No walk-forward scores were produced. Check date range and feature availability.")

    final, vetoed, crash_watchlist = select_final_candidates(
        scores,
        up_min_probability=up_min_probability,
        up_top_percentile=up_top_percentile,
        crash_probability_threshold=crash_threshold,
    )
    prices = pd.read_csv(prices_path, dtype={"ticker": str})
    if "adj_close" not in prices.columns:
        prices["adj_close"] = prices["adjusted_close"]
    summary, trades, holdings = run_backtests_for_scores(
        scores,
        prices,
        up_min_probability=up_min_probability,
        up_top_percentile=up_top_percentile,
        crash_threshold=crash_threshold,
    )
    veto = veto_analysis(scores, up_min_probability, up_top_percentile, crash_threshold)

    scores.to_csv(output_dir / "up_model_scores.csv", index=False, encoding="utf-8-sig")
    scores[["date", "ticker", "p_general_crash", "p_breakout_failure", "p_crash", "hard_crash_flag", "hard_veto_reasons"]].to_csv(
        output_dir / "crash_model_scores.csv",
        index=False,
        encoding="utf-8-sig",
    )
    final.to_csv(output_dir / "final_candidates.csv", index=False, encoding="utf-8-sig")
    vetoed.to_csv(output_dir / "vetoed_up_candidates.csv", index=False, encoding="utf-8-sig")
    crash_watchlist.to_csv(output_dir / "crash_watchlist.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(output_dir / "backtest_summary.csv", index=False, encoding="utf-8-sig")
    trades.to_csv(output_dir / "backtest_trades.csv", index=False, encoding="utf-8-sig")
    holdings.to_csv(output_dir / "backtest_holdings.csv", index=False, encoding="utf-8-sig")
    veto.to_csv(output_dir / "veto_analysis.csv", index=False, encoding="utf-8-sig")
    importance[importance["model"].eq("up")].to_csv(output_dir / "feature_importance_up.csv", index=False, encoding="utf-8-sig")
    importance[~importance["model"].eq("up")].to_csv(output_dir / "feature_importance_crash.csv", index=False, encoding="utf-8-sig")
    calibration.to_csv(output_dir / "calibration_report.csv", index=False, encoding="utf-8-sig")
    candidate_level_metrics(final).to_csv(output_dir / "candidate_metrics.csv", index=False, encoding="utf-8-sig")
    (output_dir / "html_report.html").write_text(_render_html(summary, veto, final), encoding="utf-8")
    return PipelineResult(output_dir=output_dir, scores=scores, final_candidates=final, backtest_summary=summary)


def _render_html(summary: pd.DataFrame, veto: pd.DataFrame, final: pd.DataFrame) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>AI Stock Assistant Report</title>
<style>body{{font-family:Arial,sans-serif;margin:24px}} table{{border-collapse:collapse}} td,th{{border:1px solid #ddd;padding:4px 8px}}</style>
</head><body>
<h1>AI Stock Assistant Backtest Report</h1>
<h2>Backtest Summary</h2>{summary.to_html(index=False)}
<h2>Veto Analysis</h2>{veto.to_html(index=False)}
<h2>Latest Final Candidates</h2>{final.sort_values("date").tail(50).to_html(index=False)}
</body></html>"""
