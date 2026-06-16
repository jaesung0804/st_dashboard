from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR / "src") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "src"))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ai_stock_assistant.features import FEATURE_COLUMNS


MODEL_BASE_COLUMNS = {
    "date",
    "ticker",
    "adj_close",
    "avg_trading_value_20d",
    "future_return_20d",
    "future_return_63d",
    "future_return_126d",
    "future_return_252d",
}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--features-path", required=True)
    p.add_argument("--macro-features-path", required=True)
    p.add_argument("--output-path", required=True)
    p.add_argument("--chunksize", type=int, default=250_000)
    p.add_argument("--model-only", action="store_true", help="Write only columns needed by walkforward training.")
    return p


def main() -> None:
    args = parser().parse_args()
    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    macro = pd.read_csv(args.macro_features_path)
    macro["date"] = pd.to_datetime(macro["date"]).dt.normalize()
    macro = macro.sort_values("date")
    macro_cols = [col for col in macro.columns if col.startswith("macro_")]
    if not macro_cols:
        raise RuntimeError("No macro feature columns found.")
    full_dates = pd.date_range(macro["date"].min(), macro["date"].max(), freq="D")
    macro = (
        macro.set_index("date")
        .reindex(full_dates)
        .ffill()
        .reset_index()
        .rename(columns={"index": "date"})
    )
    macro["date"] = macro["date"].dt.strftime("%Y-%m-%d")

    wrote_header = False
    total_rows = 0
    usecols = None
    if args.model_only:
        keep = MODEL_BASE_COLUMNS | set(FEATURE_COLUMNS)
        usecols = lambda col: col in keep

    for chunk in pd.read_csv(args.features_path, dtype={"ticker": str}, chunksize=args.chunksize, usecols=usecols):
        chunk["date"] = pd.to_datetime(chunk["date"]).dt.strftime("%Y-%m-%d")
        merged = chunk.merge(macro[["date", *macro_cols]], on="date", how="left", sort=False)
        merged.to_csv(
            output_path,
            mode="a",
            index=False,
            header=not wrote_header,
            encoding="utf-8-sig" if not wrote_header else "utf-8",
        )
        wrote_header = True
        total_rows += len(merged)
        print(f"attached rows={total_rows:,}", flush=True)
    print(output_path)


if __name__ == "__main__":
    main()
