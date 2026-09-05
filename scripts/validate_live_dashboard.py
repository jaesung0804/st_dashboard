import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from ai_stock_assistant.monthly_ews import PRICE_FILES, read_json, verify_prediction


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--market", choices=["all", "kr", "us"], default="all")
    p.add_argument("--asof", default="")
    args = p.parse_args()
    for market in (["kr", "us"] if args.market == "all" else [args.market]):
        manifest = read_json(Path(f"outputs/lgbm_warning_dashboard_macro_{market}_latest/manifest.json"))
        dates = pd.to_datetime(pd.read_csv(Path("data/raw") / PRICE_FILES[market], usecols=["date"])["date"])
        if args.asof:
            dates = dates[dates <= pd.Timestamp(args.asof)]
        latest = dates.max().strftime("%Y-%m-%d")
        if manifest["latest"] < latest:
            raise ValueError(f"{market}: raw data {latest} differs from latest published signal {manifest['latest']}")
        if args.asof and latest < pd.Timestamp(args.asof).strftime("%Y-%m-%d"):
            raise ValueError(f"{market}: forced date {args.asof} was not collected (latest {latest})")
        record = Path("data/dashboard_ews") / market / "predictions" / latest
        if record.exists():
            verify_prediction(record)
        elif not (Path("data/dashboard_ews") / market / "legacy" / f"{latest}.json").exists():
            raise ValueError(f"{market}: no frozen or preserved prediction for {latest}")
        print(f"{market}: raw={latest}, dashboard={manifest['latest']}, archive verified")


if __name__ == "__main__":
    main()
