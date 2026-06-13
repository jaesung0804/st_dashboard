from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

import run_walkforward_warning as base


def _macro_columns(features_path: Path) -> list[str]:
    cols = pd.read_csv(features_path, nrows=0).columns.tolist()
    macro_cols = [col for col in cols if col.startswith("macro_")]
    compact = [
        col
        for col in macro_cols
        if col.endswith("_chg_20d") or col.endswith("_z_252d") or not any(token in col for token in ["_chg_", "_diff_", "_z_"])
    ]
    return compact or macro_cols


def _select_features_memory_light(train: pd.DataFrame, target: str, top_n: int = 35) -> list[str]:
    cols = [c for c in base.FEATURE_COLUMNS if c in train.columns]
    y = train[target].astype(int)
    usable: list[str] = []
    scores: list[tuple[str, float]] = []
    for col in cols:
        x = pd.to_numeric(train[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
        if x.notna().mean() < 0.60 or x.nunique(dropna=True) <= 5:
            continue
        usable.append(col)
        auc = np.nan
        if x.notna().sum() >= 500 and y.nunique() == 2:
            try:
                raw = roc_auc_score(y, x.fillna(x.median()))
                auc = max(raw, 1 - raw)
            except ValueError:
                pass
        scores.append((col, auc))
    ranked = [c for c, a in sorted(scores, key=lambda x: -1 if pd.isna(x[1]) else x[1], reverse=True)[:top_n]]
    return ranked or usable[:top_n]


def main() -> None:
    features_path = Path("outputs/daily_features_full_macro/training_features_daily.csv")
    for idx, arg in enumerate(sys.argv):
        if arg == "--features-path" and idx + 1 < len(sys.argv):
            features_path = Path(sys.argv[idx + 1])
            break
    macro_cols = _macro_columns(features_path)
    if not macro_cols:
        raise RuntimeError(f"No macro_* columns found in {features_path}")
    base.FEATURE_COLUMNS = list(dict.fromkeys([*base.FEATURE_COLUMNS, *macro_cols]))
    base.select_features = _select_features_memory_light
    print(f"Using {len(macro_cols)} shared macro covariates.", flush=True)
    base.main()


if __name__ == "__main__":
    main()
