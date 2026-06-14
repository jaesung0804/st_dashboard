from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Combine single-date walk-forward output folders into one dashboard source.")
    p.add_argument("--out-dir", required=True)
    p.add_argument("input_dirs", nargs="+")
    return p


def read_csvs(input_dirs: list[Path], name: str) -> pd.DataFrame:
    frames = []
    for directory in input_dirs:
        path = directory / name
        if path.exists():
            frames.append(pd.read_csv(path, dtype={"ticker": str}, low_memory=False))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> None:
    args = parser().parse_args()
    input_dirs = [Path(x) for x in args.input_dirs]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    scores = read_csvs(input_dirs, "walkforward_scores.csv")
    if scores.empty:
        raise RuntimeError("No walkforward_scores.csv found.")
    scores = scores.drop_duplicates(["date", "ticker"], keep="last").sort_values(["date", "ticker"])
    scores.to_csv(out_dir / "walkforward_scores.csv", index=False, encoding="utf-8-sig")

    for flag, name, sort_col in [
        ("isFinalCandidate", "walkforward_candidates.csv", "upScore"),
        ("isUpCandidate", "walkforward_up_candidates.csv", "upScore"),
        ("isDownRed", "walkforward_down_red.csv", "downRisk"),
    ]:
        if flag in scores:
            subset = scores[scores[flag].astype(bool)].copy()
            subset.sort_values(["date", sort_col], ascending=[True, False]).to_csv(out_dir / name, index=False, encoding="utf-8-sig")

    validations = read_csvs(input_dirs, "walkforward_validation.csv")
    if not validations.empty:
        validations = validations.drop_duplicates(["signal_month", "label_cutoff"], keep="last")
        validations.to_csv(out_dir / "walkforward_validation.csv", index=False, encoding="utf-8-sig")
    print(out_dir / "walkforward_scores.csv")


if __name__ == "__main__":
    main()
