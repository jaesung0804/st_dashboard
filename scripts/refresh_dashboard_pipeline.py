"""Collect prices and infer with an existing monthly model. Never fit a model here."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_stock_assistant.data.refresh import refresh_kr_daily_data_fast, refresh_us_daily_data
from ai_stock_assistant.monthly_ews import LISTING_FILES, PRICE_FILES, export_dashboard, infer_latest


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--asof", default=None, help="Latest completed session, YYYYMMDD")
    p.add_argument("--market", choices=["all", "kr", "us"], default="all")
    p.add_argument("--lookback-days", type=int, default=10)
    p.add_argument("--kr-workers", type=int, default=4, help="Bounded KRX range download workers (1..8)")
    p.add_argument("--kr-collection-seconds", type=int, default=3600, help="Stop collection early enough to save checkpoints")
    p.add_argument("--pages-days", type=int, default=60)
    p.add_argument("--collect-only", action="store_true", help="Monthly workflow preparation; no inference")
    p.add_argument("--skip-push", action="store_true")
    # Retain old manual command compatibility; these no longer control training.
    p.add_argument("--refresh-financials", choices=["auto", "always", "never"], default="never", help=argparse.SUPPRESS)
    p.add_argument("--serial-markets", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--model-jobs", type=int, default=1, help=argparse.SUPPRESS)
    args = p.parse_args()
    raw, state = Path("data/raw"), Path("data/dashboard_ews")
    markets = ["kr", "us"] if args.market == "all" else [args.market]
    for market in markets:
        price, listing = raw / PRICE_FILES[market], raw / LISTING_FILES[market]
        if market == "kr":
            result = refresh_kr_daily_data_fast(
                asof=args.asof, prices_path=price, output_path=price, listings_path=listing,
                asof_lookback_days=args.lookback_days, workers=args.kr_workers,
                max_seconds=args.kr_collection_seconds,
            )
        else:
            result = refresh_us_daily_data(asof=args.asof, lookback_days=args.lookback_days,
                                           listings_path=listing, prices_path=price, output_path=price)
        if result.listings_path.resolve() != listing.resolve():
            shutil.copy2(result.listings_path, listing)
        if not args.collect_only:
            infer_latest(price, listing, market, state, asof=args.asof)
    if args.collect_only:
        return
    # Both archives are restored even for a one-market refresh.
    for market in ["kr", "us"]:
        if (state / market).exists():
            export_dashboard(state, market, Path("outputs"), args.pages_days)
    if not args.skip_push:
        raise RuntimeError("Publish only after persisting dashboard-state; use the Daily Refresh workflow or --skip-push")
    subprocess.run([sys.executable, "scripts/build_pages_deploy.py", "--days", str(args.pages_days)], check=True)


if __name__ == "__main__":
    main()
