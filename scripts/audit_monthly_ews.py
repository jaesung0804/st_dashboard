"""Evaluate frozen models on subsequent, fully matured months (never publish these as live)."""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd
from scipy.special import expit

from ai_stock_assistant.monthly_ews import (
    FEATURES, HORIZONS, PRICE_FILES, feature_panel, load_month, metrics, predict_panel, read_prices, write_json,
)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    p.add_argument("--state-root", type=Path, required=True)
    p.add_argument("--market", choices=["kr", "us"], required=True)
    p.add_argument("--months", nargs="+", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--calibration-weight", type=float, default=None, help="Explicit development comparison; recorded in the report, never edits a model")
    args = p.parse_args()
    started = time.monotonic()
    path = args.raw_dir / PRICE_FILES[args.market]
    prices = read_prices(path)
    latest = prices["date"].max()
    panel = feature_panel(prices, args.market, training=True)
    del prices
    results = []
    for month in args.months:
        card, boosters = load_month(args.state_root / args.market / "models" / month)
        if args.calibration_weight is not None:
            if not 0 <= args.calibration_weight <= 1:
                raise ValueError("Calibration weight must be in [0, 1]")
            for head in HORIZONS:
                card["heads"][head]["calibration"]["weight"] = args.calibration_weight
        frame = panel.loc[panel["date"].dt.strftime("%Y-%m") == month].copy()
        if frame.empty or frame["date"].min() <= pd.Timestamp(card["cutoff"]):
            raise ValueError("Evaluation must follow the training cutoff")
        for head in HORIZONS:
            f = frame.loc[frame[f"end_{head}"].le(latest) & frame[f"y_{head}"].notna()]
            if f.empty:
                raise ValueError(f"No matured labels: {month} {head}")
            y = f[f"y_{head}"]
            predicted = predict_panel(f, card, boosters)[head]
            linear = card["heads"][head]["linear"]
            x = f[FEATURES].to_numpy(dtype=float)
            x = np.where(np.isfinite(x), x, linear["median"])
            x = np.clip((x - linear["mean"]) / linear["scale"], -12, 12)
            baseline = expit(x @ linear["coef"] + linear["intercept"])
            result = {"month": month, "head": head, "cutoff": card["cutoff"],
                      "test_start": str(f["date"].min().date()), "test_end": str(f["date"].max().date()),
                      "signal_dates": f["date"].nunique(), "missing_unmatured": len(frame) - len(f),
                      "hybrid": metrics(y, predicted), "linear_baseline": metrics(y, baseline),
                      "tree_baseline": metrics(y, boosters[head].predict(f[FEATURES], num_threads=1)),
                      "constant_training_prevalence": metrics(y, np.full(len(y), card["heads"][head]["train_event_rate"]))}
            results.append(result)
            print(f"{args.market} {month} {head}: AUC={result['hybrid']['auc']:.3f}, Brier={result['hybrid']['brier']:.3f}, top-decile lift={result['hybrid']['top_decile_lift']:.2f}", flush=True)
    write_json(args.output, {"market": args.market, "price_cutoff": str(latest.date()),
                            "evaluation": "subsequent-month simulation; not a live track record",
                            "limitations": ["survivorship and data revision bias in source history", "overlapping labels; rows are not independent trials", "no transaction costs or execution simulation", "calibration diagnostics are not test performance"],
                            "calibration_weight_override": args.calibration_weight,
                            "elapsed_seconds": round(time.monotonic() - started, 2), "results": results})


if __name__ == "__main__":
    main()
