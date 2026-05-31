from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.metrics import brier_score_loss, roc_auc_score


@dataclass
class ModelFitResult:
    model: object
    feature_importance: pd.DataFrame
    calibration: pd.DataFrame


def make_lgbm(seed: int = 42, scale_pos_weight: float | None = None) -> LGBMClassifier:
    params = {
        "objective": "binary",
        "n_estimators": 300,
        "learning_rate": 0.03,
        "num_leaves": 31,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "min_child_samples": 30,
        "random_state": seed,
        "n_jobs": -1,
        "verbosity": -1,
    }
    if scale_pos_weight is not None and np.isfinite(scale_pos_weight):
        params["scale_pos_weight"] = scale_pos_weight
    return LGBMClassifier(**params)


def fit_calibrated_lgbm(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    feature_cols: list[str],
    label_col: str,
    model_name: str,
) -> ModelFitResult:
    train = train.dropna(subset=[label_col]).copy()
    validation = validation.dropna(subset=[label_col]).copy()
    x_train = train[feature_cols].replace([np.inf, -np.inf], np.nan)
    y_train = train[label_col].astype(int)
    x_val = validation[feature_cols].replace([np.inf, -np.inf], np.nan)
    y_val = validation[label_col].astype(int)
    pos = y_train.sum()
    neg = len(y_train) - pos
    scale = neg / pos if pos > 0 else None
    base = make_lgbm(scale_pos_weight=scale)
    base.fit(x_train, y_train)

    if len(validation) >= 50 and y_val.nunique() == 2:
        calibrated = CalibratedClassifierCV(FrozenEstimator(base), method="isotonic")
        calibrated.fit(x_val, y_val)
        model = calibrated
    else:
        model = base

    probs = model.predict_proba(x_val)[:, 1] if len(validation) else np.array([])
    calibration = pd.DataFrame(
        [
            {
                "model": model_name,
                "rows": len(validation),
                "positive_rate": float(y_val.mean()) if len(y_val) else np.nan,
                "brier": brier_score_loss(y_val, probs) if len(np.unique(y_val)) > 1 else np.nan,
                "auc": roc_auc_score(y_val, probs) if len(np.unique(y_val)) > 1 else np.nan,
            }
        ]
    )
    importance_source = base
    importance = pd.DataFrame(
        {
            "feature": feature_cols,
            "importance": importance_source.feature_importances_,
            "model": model_name,
        }
    ).sort_values("importance", ascending=False)
    return ModelFitResult(model=model, feature_importance=importance, calibration=calibration)


def predict_proba(model: object, data: pd.DataFrame, feature_cols: list[str]) -> np.ndarray:
    if data.empty:
        return np.array([])
    x = data[feature_cols].replace([np.inf, -np.inf], np.nan)
    return model.predict_proba(x)[:, 1]
