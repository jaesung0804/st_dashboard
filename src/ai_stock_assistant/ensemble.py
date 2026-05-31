from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ai_stock_assistant.backtest import run_monthly_backtest
from ai_stock_assistant.features import FEATURE_COLUMNS, FINANCIAL_FEATURES, PRICE_FEATURES


UP_MODEL_COLUMNS = [
    "p_up_rule",
    "p_up_logistic_elasticnet",
    "p_up_lightgbm",
    "p_up_extra_trees",
    "p_up_price_volume",
    "p_up_fundamental_change",
]
CRASH_MODEL_COLUMNS = [
    "p_crash_rule",
    "p_crash_logistic_elasticnet",
    "p_crash_lightgbm_general",
    "p_crash_lightgbm_breakout_failure",
    "p_crash_financial_fragility",
    "p_crash_blowoff_exhaustion",
]


@dataclass(frozen=True)
class EnsembleRunResult:
    output_dir: Path
    scores: pd.DataFrame
    final_candidates: pd.DataFrame
    backtest_summary: pd.DataFrame


def _available(cols: list[str], data: pd.DataFrame) -> list[str]:
    return [col for col in cols if col in data.columns]


def _col(data: pd.DataFrame, name: str, default: float = 0.0) -> pd.Series:
    if name in data.columns:
        return pd.to_numeric(data[name], errors="coerce")
    return pd.Series(default, index=data.index, dtype=float)


def _calibrate_series(raw_train: pd.Series, y_train: pd.Series, raw_val: pd.Series, y_val: pd.Series, raw_test: pd.Series) -> pd.Series:
    raw_train = raw_train.replace([np.inf, -np.inf], np.nan).fillna(raw_train.median()).to_numpy().reshape(-1, 1)
    raw_val = raw_val.replace([np.inf, -np.inf], np.nan).fillna(raw_val.median()).to_numpy().reshape(-1, 1)
    raw_test = raw_test.replace([np.inf, -np.inf], np.nan).fillna(raw_val.mean()).to_numpy().reshape(-1, 1)
    if y_train.nunique() < 2 or y_val.nunique() < 2:
        return pd.Series(np.repeat(float(y_train.mean()), len(raw_test)))
    base = LogisticRegression(max_iter=200)
    base.fit(raw_train, y_train)
    try:
        calibrated = CalibratedClassifierCV(FrozenEstimator(base), method="isotonic")
        calibrated.fit(raw_val, y_val)
    except Exception:
        calibrated = CalibratedClassifierCV(FrozenEstimator(base), method="sigmoid")
        calibrated.fit(raw_val, y_val)
    return pd.Series(calibrated.predict_proba(raw_test)[:, 1])


def _model_matrix(data: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    return data[cols].replace([np.inf, -np.inf], np.nan)


def _fit_calibrated_classifier(model, train: pd.DataFrame, val: pd.DataFrame, test: pd.DataFrame, cols: list[str], label: str) -> np.ndarray:
    cols = _available(cols, train)
    if not cols or train[label].nunique() < 2:
        return np.repeat(float(train[label].mean()), len(test))
    pipe = make_pipeline(SimpleImputer(strategy="median"), model)
    train_x = _model_matrix(train, cols)
    val_x = _model_matrix(val, cols)
    test_x = _model_matrix(test, cols)
    pipe.fit(train_x, train[label].astype(int))
    if len(val) > 50 and val[label].nunique() == 2:
        try:
            calibrated = CalibratedClassifierCV(FrozenEstimator(pipe), method="isotonic")
            calibrated.fit(val_x, val[label].astype(int))
        except Exception:
            calibrated = CalibratedClassifierCV(FrozenEstimator(pipe), method="sigmoid")
            calibrated.fit(val_x, val[label].astype(int))
        return calibrated.predict_proba(test_x)[:, 1]
    return pipe.predict_proba(test_x)[:, 1]


def _trimmed_mean(values: pd.DataFrame, trim: float = 0.15) -> pd.Series:
    arr = values.to_numpy(dtype=float)
    arr.sort(axis=1)
    k = int(arr.shape[1] * trim)
    if arr.shape[1] - 2 * k <= 0:
        return pd.Series(np.nanmean(arr, axis=1), index=values.index)
    return pd.Series(np.nanmean(arr[:, k : arr.shape[1] - k], axis=1), index=values.index)


def _rule_up_raw(data: pd.DataFrame) -> pd.Series:
    return (
        _col(data, "return_60d").fillna(0)
        + 0.30 * _col(data, "breakout_60d_high").fillna(0)
        + 0.08 * _col(data, "volume_ratio_20d_to_60d", 1).fillna(1)
        - 0.20 * _col(data, "volatility_60d").fillna(0)
    )


def _price_volume_up_raw(data: pd.DataFrame) -> pd.Series:
    return (
        _col(data, "return_20d").fillna(0)
        + _col(data, "return_60d").fillna(0)
        + 0.10 * _col(data, "volume_ratio_5d_to_60d", 1).fillna(1)
        + 0.20 * _col(data, "breakout_20d_high").fillna(0)
    )


def _fundamental_up_raw(data: pd.DataFrame) -> pd.Series:
    return (
        _col(data, "revenue_yoy").fillna(0)
        + _col(data, "operating_income_yoy").fillna(0)
        + 0.5 * _col(data, "eps_yoy").fillna(0)
        + _col(data, "cfo_margin").fillna(0)
    )


def _rule_crash_raw(data: pd.DataFrame) -> pd.Series:
    return (
        _col(data, "volatility_expansion", 1).fillna(1)
        + _col(data, "parabolic_move_score").fillna(0)
        + _col(data, "debt_to_equity").clip(upper=10).fillna(0) * 0.2
        - _col(data, "cfo_margin").fillna(0)
    )


def _financial_fragility_raw(data: pd.DataFrame) -> pd.Series:
    return (
        _col(data, "debt_to_equity").clip(upper=10).fillna(0)
        - _col(data, "equity_ratio").fillna(0)
        - _col(data, "cash_to_assets").fillna(0)
        - _col(data, "fcf_margin").fillna(0)
    )


def _blowoff_raw(data: pd.DataFrame) -> pd.Series:
    return (
        _col(data, "return_20d").clip(lower=0).fillna(0)
        + _col(data, "return_60d").clip(lower=0).fillna(0)
        + _col(data, "volume_ratio_5d_to_60d", 1).fillna(1) * 0.2
        + _col(data, "upper_wick_ratio").fillna(0)
    )


@dataclass(frozen=True)
class EnsembleSelectionConfig:
    up_threshold: float = 0.08
    min_up_votes: int = 3
    max_up_disagreement: float = 0.12
    crash_threshold: float = 0.10
    min_crash_votes: int = 2
    severe_breakout_threshold: float = 0.20


def _date_cutoffs(data: pd.DataFrame, step_months: int = 3) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    dates = pd.Series(pd.to_datetime(data["date"]).drop_duplicates()).sort_values()
    current = dates.min() + pd.DateOffset(years=2, months=6)
    last = dates.max() - pd.DateOffset(months=3)
    cutoffs = []
    while current <= last:
        cutoffs.append((current - pd.DateOffset(months=6), current, current + pd.DateOffset(months=3)))
        current += pd.DateOffset(months=step_months)
    return cutoffs


def walk_forward_ensemble_scores(features: pd.DataFrame, fold_step_months: int = 3) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = features.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.dropna(subset=["up_label", "crash_label", "failed_breakout_label"])
    feature_cols = _available(FEATURE_COLUMNS, frame)
    price_cols = _available(PRICE_FEATURES, frame)
    fundamental_cols = _available(FINANCIAL_FEATURES, frame)
    scores = []
    calibration_rows = []

    for fold, (train_end, val_end, test_end) in enumerate(_date_cutoffs(frame, step_months=fold_step_months), start=1):
        train = frame[(frame["date"] >= train_end - pd.DateOffset(years=2)) & (frame["date"] < train_end)].copy()
        val = frame[(frame["date"] >= train_end) & (frame["date"] < val_end)].copy()
        test = frame[(frame["date"] >= val_end) & (frame["date"] < test_end)].copy()
        if len(train) < 1000 or len(val) < 200 or test.empty:
            continue
        y_up = train["up_label"].astype(int)
        y_up_val = val["up_label"].astype(int)
        y_crash = train["crash_label"].astype(int)
        y_crash_val = val["crash_label"].astype(int)

        out = test.copy()
        out["p_up_rule"] = _calibrate_series(_rule_up_raw(train), y_up, _rule_up_raw(val), y_up_val, _rule_up_raw(test)).to_numpy()
        out["p_up_price_volume"] = _calibrate_series(_price_volume_up_raw(train), y_up, _price_volume_up_raw(val), y_up_val, _price_volume_up_raw(test)).to_numpy()
        out["p_up_fundamental_change"] = _calibrate_series(_fundamental_up_raw(train), y_up, _fundamental_up_raw(val), y_up_val, _fundamental_up_raw(test)).to_numpy()
        out["p_crash_rule"] = _calibrate_series(_rule_crash_raw(train), y_crash, _rule_crash_raw(val), y_crash_val, _rule_crash_raw(test)).to_numpy()
        out["p_crash_financial_fragility"] = _calibrate_series(_financial_fragility_raw(train), y_crash, _financial_fragility_raw(val), y_crash_val, _financial_fragility_raw(test)).to_numpy()
        out["p_crash_blowoff_exhaustion"] = _calibrate_series(_blowoff_raw(train), y_crash, _blowoff_raw(val), y_crash_val, _blowoff_raw(test)).to_numpy()

        out["p_up_logistic_elasticnet"] = _fit_calibrated_classifier(
            make_pipeline(StandardScaler(with_mean=False), LogisticRegression(penalty="elasticnet", solver="saga", l1_ratio=0.25, max_iter=500, n_jobs=-1)),
            train,
            val,
            test,
            feature_cols,
            "up_label",
        )
        out["p_crash_logistic_elasticnet"] = _fit_calibrated_classifier(
            make_pipeline(StandardScaler(with_mean=False), LogisticRegression(penalty="elasticnet", solver="saga", l1_ratio=0.25, max_iter=500, n_jobs=-1)),
            train,
            val,
            test,
            feature_cols,
            "crash_label",
        )
        out["p_up_extra_trees"] = _fit_calibrated_classifier(
            ExtraTreesClassifier(n_estimators=120, min_samples_leaf=20, random_state=fold, n_jobs=-1),
            train,
            val,
            test,
            feature_cols,
            "up_label",
        )
        lightgbm_params = dict(n_estimators=140, learning_rate=0.04, num_leaves=31, subsample=0.8, colsample_bytree=0.8, random_state=fold, n_jobs=-1, verbosity=-1)
        out["p_up_lightgbm"] = _fit_calibrated_classifier(LGBMClassifier(**lightgbm_params), train, val, test, feature_cols, "up_label")
        out["p_crash_lightgbm_general"] = _fit_calibrated_classifier(LGBMClassifier(**lightgbm_params), train, val, test, feature_cols, "crash_label")
        out["p_crash_lightgbm_breakout_failure"] = _fit_calibrated_classifier(
            LGBMClassifier(**lightgbm_params),
            train,
            val,
            test,
            feature_cols,
            "failed_breakout_label",
        )

        out["p_up_ensemble"] = _trimmed_mean(out[UP_MODEL_COLUMNS])
        out["up_vote_count"] = (out[UP_MODEL_COLUMNS] >= 0.08).sum(axis=1)
        out["up_disagreement"] = out[UP_MODEL_COLUMNS].std(axis=1)
        out["p_breakout_failure"] = out["p_crash_lightgbm_breakout_failure"]
        out["p_crash_ensemble"] = np.maximum(
            out[CRASH_MODEL_COLUMNS].quantile(0.75, axis=1),
            out[["p_crash_lightgbm_general", "p_crash_lightgbm_breakout_failure"]].max(axis=1) * 0.85,
        )
        out["crash_vote_count"] = (out[CRASH_MODEL_COLUMNS] >= 0.08).sum(axis=1)
        out["fold"] = fold
        scores.append(out)
        for col in UP_MODEL_COLUMNS + CRASH_MODEL_COLUMNS:
            calibration_rows.append({"fold": fold, "model": col, "validation_rows": len(val)})

    return (
        pd.concat(scores, ignore_index=True) if scores else pd.DataFrame(),
        pd.DataFrame(calibration_rows),
    )


def select_ensemble_candidates(
    scores: pd.DataFrame,
    config: EnsembleSelectionConfig | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    config = config or EnsembleSelectionConfig()
    frame = scores.copy()
    frame["up_candidate"] = (
        (frame["p_up_ensemble"] >= config.up_threshold)
        & (frame["up_vote_count"] >= config.min_up_votes)
        & (frame["up_disagreement"] <= config.max_up_disagreement)
    )
    frame["crash_candidate"] = (
        frame["hard_crash_flag"]
        | ((frame["p_crash_ensemble"] >= config.crash_threshold) & (frame["crash_vote_count"] >= config.min_crash_votes))
        | (frame["p_breakout_failure"] >= config.severe_breakout_threshold)
    )
    frame["debate_candidate"] = (
        (frame["p_up_ensemble"] >= config.up_threshold)
        & (
            (frame["up_disagreement"] > config.max_up_disagreement)
            | (frame["up_vote_count"] < config.min_up_votes)
            | ((frame["p_crash_ensemble"] >= config.crash_threshold * 0.75) & ~frame["crash_candidate"])
        )
    )
    frame["contributing_up_models"] = frame[UP_MODEL_COLUMNS].ge(config.up_threshold).apply(lambda row: ";".join(row.index[row]), axis=1)
    frame["contributing_crash_warnings"] = frame[CRASH_MODEL_COLUMNS].ge(config.crash_threshold).apply(lambda row: ";".join(row.index[row]), axis=1)
    up = frame[frame["up_candidate"]].copy()
    crash = frame[frame["crash_candidate"]].copy()
    up_key = up["date"].astype(str) + "|" + up["ticker"].astype(str)
    crash_key = set(crash["date"].astype(str) + "|" + crash["ticker"].astype(str))
    final = up[~up_key.isin(crash_key)].sort_values("p_up_ensemble", ascending=False).reset_index(drop=True)
    vetoed = up[up_key.isin(crash_key)].sort_values("p_up_ensemble", ascending=False).reset_index(drop=True)
    debate = frame[frame["debate_candidate"] & ~frame["up_candidate"]].sort_values("p_up_ensemble", ascending=False).reset_index(drop=True)
    return final, vetoed, crash, debate


def _with_reason_columns(data: pd.DataFrame) -> pd.DataFrame:
    if data.empty:
        return data
    frame = data.copy()
    frame["up_ensemble_reason"] = (
        "p_up_ensemble="
        + frame["p_up_ensemble"].round(4).astype(str)
        + "; votes="
        + frame["up_vote_count"].astype(str)
        + "; models="
        + frame["contributing_up_models"].fillna("")
    )
    frame["crash_veto_reason"] = (
        "hard_veto="
        + frame["hard_crash_flag"].astype(str)
        + "; p_crash_ensemble="
        + frame["p_crash_ensemble"].round(4).astype(str)
        + "; crash_votes="
        + frame["crash_vote_count"].astype(str)
        + "; warnings="
        + frame["contributing_crash_warnings"].fillna("")
    )
    return frame


def run_ensemble_backtests(scores: pd.DataFrame, prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    def lgbm_up(data: pd.DataFrame) -> pd.DataFrame:
        return data.sort_values("p_up_lightgbm", ascending=False)

    def rule_up(data: pd.DataFrame) -> pd.DataFrame:
        return data.sort_values("p_up_rule", ascending=False)

    def ensemble_only(data: pd.DataFrame) -> pd.DataFrame:
        return data[data["p_up_ensemble"] >= 0.08].sort_values("p_up_ensemble", ascending=False)

    def ensemble_minus_hard(data: pd.DataFrame) -> pd.DataFrame:
        return ensemble_only(data)[~ensemble_only(data)["hard_crash_flag"]]

    def ensemble_minus_crash(data: pd.DataFrame) -> pd.DataFrame:
        candidates = ensemble_only(data)
        crash = (candidates["p_crash_ensemble"] >= 0.10) & (candidates["crash_vote_count"] >= 2)
        return candidates[~(candidates["hard_crash_flag"] | crash)].sort_values("p_up_ensemble", ascending=False)

    def ensemble_minus_crash_disagreement(data: pd.DataFrame) -> pd.DataFrame:
        candidates = ensemble_minus_crash(data)
        return candidates[(candidates["up_vote_count"] >= 3) & (candidates["up_disagreement"] <= 0.12)]

    filters = {
        "LightGBM Up Only": (lgbm_up, "p_up_lightgbm"),
        "Rule-Based Up Only": (rule_up, "p_up_rule"),
        "Up Ensemble Only": (ensemble_only, "p_up_ensemble"),
        "Up Ensemble minus Hard Veto": (ensemble_minus_hard, "p_up_ensemble"),
        "Up Ensemble minus Crash Ensemble": (ensemble_minus_crash, "p_up_ensemble"),
        "Up Ensemble minus Crash Ensemble minus Disagreement Filter": (ensemble_minus_crash_disagreement, "p_up_ensemble"),
    }
    summaries, trades, holdings = [], [], []
    for name, (fn, rank_column) in filters.items():
        for top_n in [10, 20, 50]:
            result = run_monthly_backtest(scores, prices, name, fn, top_n=top_n, rank_column=rank_column)
            summaries.append(result.summary)
            trades.append(result.trades)
            holdings.append(result.holdings)
    return pd.concat(summaries, ignore_index=True), pd.concat(trades, ignore_index=True), pd.concat(holdings, ignore_index=True)


def run_ensemble_from_features(
    features_path: Path,
    prices_path: Path,
    output_dir: Path,
    fold_step_months: int = 3,
    selection_config: EnsembleSelectionConfig | None = None,
) -> EnsembleRunResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    features = pd.read_csv(features_path, dtype={"ticker": str})
    scores, calibration = walk_forward_ensemble_scores(features, fold_step_months=fold_step_months)
    final, vetoed, crash, debate = select_ensemble_candidates(scores, config=selection_config)
    final = _with_reason_columns(final)
    vetoed = _with_reason_columns(vetoed)
    crash = _with_reason_columns(crash)
    debate = _with_reason_columns(debate)
    prices = pd.read_csv(prices_path, dtype={"ticker": str})
    prices["adj_close"] = prices.get("adj_close", prices["adjusted_close"])
    summary, trades, holdings = run_ensemble_backtests(scores, prices)
    scores.to_csv(output_dir / "ensemble_model_scores.csv", index=False, encoding="utf-8-sig")
    scores[[col for col in scores.columns if not col.startswith("p_crash_")]].to_csv(
        output_dir / "up_model_scores.csv", index=False, encoding="utf-8-sig"
    )
    scores[[col for col in scores.columns if not col.startswith("p_up_")]].to_csv(
        output_dir / "crash_model_scores.csv", index=False, encoding="utf-8-sig"
    )
    final.to_csv(output_dir / "final_candidates.csv", index=False, encoding="utf-8-sig")
    vetoed.to_csv(output_dir / "vetoed_up_candidates.csv", index=False, encoding="utf-8-sig")
    crash.to_csv(output_dir / "crash_watchlist.csv", index=False, encoding="utf-8-sig")
    debate.to_csv(output_dir / "debate_candidates.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(output_dir / "backtest_summary.csv", index=False, encoding="utf-8-sig")
    trades.to_csv(output_dir / "backtest_trades.csv", index=False, encoding="utf-8-sig")
    holdings.to_csv(output_dir / "backtest_holdings.csv", index=False, encoding="utf-8-sig")
    calibration.to_csv(output_dir / "calibration_report.csv", index=False, encoding="utf-8-sig")
    (output_dir / "html_report.html").write_text(_render_ensemble_html(summary, final, vetoed, debate), encoding="utf-8")
    return EnsembleRunResult(output_dir=output_dir, scores=scores, final_candidates=final, backtest_summary=summary)


def _render_ensemble_html(summary: pd.DataFrame, final: pd.DataFrame, vetoed: pd.DataFrame, debate: pd.DataFrame) -> str:
    cols = [
        "date",
        "ticker",
        "p_up_ensemble",
        "up_vote_count",
        "up_disagreement",
        "p_crash_ensemble",
        "crash_vote_count",
        "hard_crash_flag",
        "contributing_up_models",
        "contributing_crash_warnings",
    ]
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Ensemble Report</title>
<style>body{{font-family:Arial,sans-serif;margin:24px}} table{{border-collapse:collapse;font-size:12px}} td,th{{border:1px solid #ddd;padding:4px 6px}}</style>
</head><body><h1>AI Stock Assistant Ensemble Report</h1>
<h2>Backtest Comparison</h2>{summary.to_html(index=False)}
<h2>Final Candidates</h2>{final[[c for c in cols if c in final.columns]].head(100).to_html(index=False)}
<h2>Vetoed Up Candidates</h2>{vetoed[[c for c in cols if c in vetoed.columns]].head(100).to_html(index=False)}
<h2>Debate Candidates</h2>{debate[[c for c in cols if c in debate.columns]].head(100).to_html(index=False)}
</body></html>"""
