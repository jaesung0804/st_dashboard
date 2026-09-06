from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_stock_assistant.monthly_ews import (
    LISTING_FILES, PRICE_FILES, evaluate_archive, export_dashboard, infer_latest, migrate_legacy, train_month,
)


def main() -> None:
    p = argparse.ArgumentParser(description="Immutable monthly training / daily inference for the live early-warning model")
    p.add_argument("command", choices=["train", "infer", "export", "migrate", "evaluate"])
    p.add_argument("--market", choices=["all", "kr", "us"], default="all")
    p.add_argument("--month", default=datetime.now(timezone.utc).strftime("%Y-%m"))
    p.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    p.add_argument("--state-root", type=Path, default=Path("data/dashboard_ews"))
    p.add_argument("--output-root", type=Path, default=Path("outputs"))
    p.add_argument("--legacy-pages", type=Path, default=Path(".legacy-pages"))
    p.add_argument("--asof", default=None)
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--jobs", type=int, default=1)
    p.add_argument("--research", action="store_true", help="Simulation only; use a separate state-root, never publish as live")
    args = p.parse_args()
    if args.research and args.state_root.resolve() == Path("data/dashboard_ews").resolve():
        p.error("--research requires a separate --state-root")
    if args.command == "migrate":
        migrate_legacy(args.legacy_pages, args.state_root)
        return
    for market in (["kr", "us"] if args.market == "all" else [args.market]):
        if args.command == "train":
            train_month(args.raw_dir / PRICE_FILES[market], market, args.month, args.state_root, jobs=args.jobs, research=args.research)
        elif args.command == "infer":
            infer_latest(args.raw_dir / PRICE_FILES[market], args.raw_dir / LISTING_FILES[market], market,
                         args.state_root, asof=args.asof, research=args.research)
        elif args.command == "evaluate":
            evaluate_archive(args.raw_dir / PRICE_FILES[market], market, args.state_root)
        else:
            export_dashboard(args.state_root, market, args.output_root, args.days)


if __name__ == "__main__":
    main()
