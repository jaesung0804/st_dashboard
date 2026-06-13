from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

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
    print(f"Using {len(macro_cols)} shared macro covariates.", flush=True)
    base.main()


if __name__ == "__main__":
    main()
