"""Run isolated monthly macro challengers; daily mode never trains."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pandas as pd
from ai_stock_assistant import monthly_ews as live, shadow_ews as shadow
from ai_stock_assistant.data import macro_vintages as macro
from ai_stock_assistant.data import price_quality


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=["collect", "train", "infer", "export"])
    parser.add_argument("--market", choices=["all", "kr", "us"], default="all")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--state-root", type=Path, default=Path("data/dashboard_ews_shadow"))
    parser.add_argument("--live-state", type=Path, default=Path("data/dashboard_ews"))
    parser.add_argument("--output-root", type=Path, default=Path("outputs"))
    parser.add_argument("--archive-file", type=Path)
    parser.add_argument("--month", default=pd.Timestamp.now(tz="UTC").strftime("%Y-%m"))
    args = parser.parse_args()
    if args.state_root.resolve() == args.live_state.resolve():
        parser.error("Shadow and production state must remain separate")
    if args.command == "collect":
        macro.collect(args.state_root, archive_file=args.archive_file)
        return
    for market in (["kr", "us"] if args.market == "all" else [args.market]):
        if args.command in ("train", "infer"):
            vintages, provenance = macro.load(args.state_root)
            prices_path = args.raw_dir / live.PRICE_FILES[market]
            prices = live.read_prices(prices_path)
            prices, quality = price_quality.prepare(prices, market)
            live.write_json(shadow.market_root(args.state_root, market) / "price_quality.json", quality)
            provenance = {**provenance, "price_quality": quality}
            if args.command == "train":
                boundary = pd.Timestamp(args.month + "-01")
                cutoff = prices.loc[prices.date < boundary, "date"].max()
                if pd.isna(cutoff) or boundary - cutoff > pd.Timedelta(days=7):
                    raise ValueError("Refresh prices through the previous month before shadow training")
                print(f"Building {market} paired research panel from {len(prices):,} prices", flush=True)
                panel = macro.attach(shadow.research_panel(prices, market), vintages)
                print(f"Panel {market}: {len(panel):,}, up known={panel.y_up.notna().sum():,}, event rate={panel.y_up.mean():.4f}", flush=True)
                shadow.audit(panel, prices_path, market, args.state_root, macro.FEATURES, provenance)
                for arm in shadow.ARMS:
                    shadow.fit_month(panel, market, args.month, arm, args.state_root, macro.FEATURES, provenance)
            else:
                shadow.infer(prices, market, args.state_root, vintages)
                shadow.evaluate(prices, market, args.state_root)
        shadow.export_report(args.state_root, args.live_state, market, args.output_root)


if __name__ == "__main__":
    main()
