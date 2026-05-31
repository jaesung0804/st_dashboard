from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai_stock_assistant")
    subparsers = parser.add_subparsers(dest="command", required=True)

    listings = subparsers.add_parser("fetch-kr-listings")
    listings.add_argument("--asof", default=None, help="YYYYMMDD date. Defaults to today.")
    listings.add_argument("--market", default="ALL", choices=["ALL", "KOSPI", "KOSDAQ", "KONEX"])

    prices = subparsers.add_parser("fetch-kr-prices")
    prices.add_argument("--tickers", nargs="+", required=True)
    prices.add_argument("--start", required=True, help="YYYYMMDD start date.")
    prices.add_argument("--end", required=True, help="YYYYMMDD end date.")

    universe = subparsers.add_parser("fetch-kr-universe-prices")
    universe.add_argument("--markets", nargs="+", default=["KOSPI", "KOSDAQ"], choices=["KOSPI", "KOSDAQ", "KONEX"])
    universe.add_argument("--start", default=None, help="YYYYMMDD start date. Defaults to five years before end.")
    universe.add_argument("--end", default=None, help="YYYYMMDD end date. Defaults to today.")
    universe.add_argument("--sleep", type=float, default=0.1, help="Delay between ticker requests.")
    universe.add_argument("--limit", type=int, default=None, help="Optional ticker limit for smoke tests.")
    universe.add_argument("--force", action="store_true", help="Refetch tickers even if cached CSV exists.")
    universe.add_argument("--no-combine", action="store_true", help="Skip combined universe CSV creation.")

    corp_codes = subparsers.add_parser("fetch-opendart-corp-codes")
    corp_codes.add_argument("--force", action="store_true", help="Refetch corp code mapping.")

    financials = subparsers.add_parser("fetch-kr-financials")
    financials.add_argument("--listings-path", required=True, help="CSV with ticker column.")
    financials.add_argument("--years", nargs="+", type=int, default=None)
    financials.add_argument(
        "--reports",
        nargs="+",
        default=["annual"],
        choices=["annual", "q1", "half", "q3", "11011", "11013", "11012", "11014"],
    )
    financials.add_argument("--sleep", type=float, default=0.05)
    financials.add_argument("--limit", type=int, default=None, help="Optional ticker limit for smoke tests.")
    financials.add_argument("--force-corp-codes", action="store_true")
    financials.add_argument("--workers", type=int, default=1, help="Parallel OpenDART request workers.")

    combine_financials = subparsers.add_parser("combine-kr-financials")
    combine_financials.add_argument("--listings-path", required=True, help="CSV with ticker column.")
    combine_financials.add_argument("--account-paths", nargs="+", required=True)
    combine_financials.add_argument("--manifest-paths", nargs="+", required=True)
    combine_financials.add_argument("--output-slug", default="all_reports_2021_2025")

    combine_cache = subparsers.add_parser("combine-kr-financial-cache")
    combine_cache.add_argument("--listings-path", required=True, help="CSV with ticker column.")
    combine_cache.add_argument("--cache-dir", required=True)
    combine_cache.add_argument("--output-slug", required=True)

    daily = subparsers.add_parser("daily-refresh")
    daily.add_argument("--markets", nargs="+", default=["KOSPI", "KOSDAQ"], choices=["KOSPI", "KOSDAQ", "KONEX"])
    daily.add_argument("--asof", default=None, help="YYYYMMDD date. Defaults to today.")
    daily.add_argument("--lookback-days", type=int, default=10)
    daily.add_argument("--limit", type=int, default=None, help="Optional ticker limit for smoke tests.")
    daily.add_argument("--no-rebuild-combined", action="store_true")

    model = subparsers.add_parser("run-modeling")
    model.add_argument("--prices-path", required=True)
    model.add_argument("--financials-path", required=True)
    model.add_argument("--listings-path", required=True)
    model.add_argument("--output-dir", required=True)
    model.add_argument("--up-min-probability", type=float, default=0.60)
    model.add_argument("--up-top-percentile", type=float, default=0.15)
    model.add_argument("--crash-threshold", type=float, default=0.50)
    model.add_argument("--daily-training-rows", action="store_true", help="Use all daily rows instead of month-end rows.")

    ensemble = subparsers.add_parser("run-ensemble")
    ensemble.add_argument("--features-path", required=True, help="Existing training_features.csv from run-modeling.")
    ensemble.add_argument("--prices-path", required=True, help="Adjusted OHLCV CSV used for backtesting.")
    ensemble.add_argument("--output-dir", required=True)
    ensemble.add_argument("--fold-step-months", type=int, default=3, help="Walk-forward fold spacing. Use 6 for a faster pass.")
    ensemble.add_argument("--up-threshold", type=float, default=0.08)
    ensemble.add_argument("--min-up-votes", type=int, default=3)
    ensemble.add_argument("--max-up-disagreement", type=float, default=0.12)
    ensemble.add_argument("--crash-threshold", type=float, default=0.10)
    ensemble.add_argument("--min-crash-votes", type=int, default=2)
    ensemble.add_argument("--severe-breakout-threshold", type=float, default=0.20)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "fetch-kr-listings":
        from ai_stock_assistant.data.krx import save_krx_listings

        output_path = save_krx_listings(asof=args.asof, market=args.market)
        print(output_path)
        return

    if args.command == "fetch-kr-prices":
        from ai_stock_assistant.data.krx import save_krx_ohlcv

        output_path = save_krx_ohlcv(tickers=args.tickers, start=args.start, end=args.end)
        print(output_path)
        return

    if args.command == "fetch-kr-universe-prices":
        from ai_stock_assistant.data.krx import save_krx_universe_ohlcv

        result = save_krx_universe_ohlcv(
            start=args.start,
            end=args.end,
            markets=args.markets,
            sleep_seconds=args.sleep,
            limit=args.limit,
            force=args.force,
            combine=not args.no_combine,
        )
        print(f"listings={result.listings_path}")
        print(f"price_dir={result.price_dir}")
        print(f"combined_prices={result.combined_prices_path}")
        print(f"manifest={result.manifest_path}")
        print(f"requested={result.requested_count} saved={result.saved_count} failed={result.failed_count}")
        return

    if args.command == "fetch-opendart-corp-codes":
        from ai_stock_assistant.data.opendart import save_corp_codes

        output_path = save_corp_codes()
        print(output_path)
        return

    if args.command == "fetch-kr-financials":
        from ai_stock_assistant.data.opendart import save_korean_financials

        years = args.years or list(range(date.today().year - 5, date.today().year))
        result = save_korean_financials(
            listings_path=Path(args.listings_path),
            years=years,
            reports=args.reports,
            sleep_seconds=args.sleep,
            limit=args.limit,
            force_corp_codes=args.force_corp_codes,
            workers=args.workers,
        )
        print(f"corp_codes={result.corp_codes_path}")
        print(f"raw_accounts={result.raw_accounts_path}")
        print(f"normalized_financials={result.normalized_financials_path}")
        print(f"manifest={result.manifest_path}")
        print(f"requested={result.requested_count} saved={result.saved_count} failed={result.failed_count}")
        return

    if args.command == "combine-kr-financials":
        from ai_stock_assistant.data.opendart import combine_korean_financials

        result = combine_korean_financials(
            listings_path=Path(args.listings_path),
            account_paths=[Path(path) for path in args.account_paths],
            manifest_paths=[Path(path) for path in args.manifest_paths],
            output_slug=args.output_slug,
        )
        print(f"raw_accounts={result.raw_accounts_path}")
        print(f"normalized_financials={result.normalized_financials_path}")
        print(f"manifest={result.manifest_path}")
        print(f"raw_rows={result.raw_rows} normalized_rows={result.normalized_rows}")
        return

    if args.command == "combine-kr-financial-cache":
        from ai_stock_assistant.data.opendart import combine_korean_financial_cache

        result = combine_korean_financial_cache(
            listings_path=Path(args.listings_path),
            cache_dir=Path(args.cache_dir),
            output_slug=args.output_slug,
        )
        print(f"raw_accounts={result.raw_accounts_path}")
        print(f"normalized_financials={result.normalized_financials_path}")
        print(f"manifest={result.manifest_path}")
        print(f"raw_rows={result.raw_rows} normalized_rows={result.normalized_rows}")
        return

    if args.command == "daily-refresh":
        from ai_stock_assistant.data.refresh import refresh_kr_daily_data, write_daily_readme

        result = refresh_kr_daily_data(
            markets=args.markets,
            asof=args.asof,
            lookback_days=args.lookback_days,
            rebuild_combined=not args.no_rebuild_combined,
            limit=args.limit,
        )
        readme_path = write_daily_readme(asof=result.asof)
        print(f"asof={result.asof}")
        print(f"listings={result.listings_path}")
        print(f"price_dir={result.price_dir}")
        print(f"combined_prices={result.combined_prices_path}")
        print(f"summary={result.summary_path}")
        print(f"readme={readme_path}")
        print(f"updated={result.updated_count} failed={result.failed_count}")
        return

    if args.command == "run-modeling":
        from ai_stock_assistant.pipeline import run_modeling_pipeline

        result = run_modeling_pipeline(
            prices_path=Path(args.prices_path),
            financials_path=Path(args.financials_path),
            listings_path=Path(args.listings_path),
            output_dir=Path(args.output_dir),
            up_min_probability=args.up_min_probability,
            up_top_percentile=args.up_top_percentile,
            crash_threshold=args.crash_threshold,
            monthly_only=not args.daily_training_rows,
        )
        print(f"output_dir={result.output_dir}")
        print(f"scores={len(result.scores)} final_candidates={len(result.final_candidates)}")
        return

    if args.command == "run-ensemble":
        from ai_stock_assistant.ensemble import EnsembleSelectionConfig, run_ensemble_from_features

        result = run_ensemble_from_features(
            features_path=Path(args.features_path),
            prices_path=Path(args.prices_path),
            output_dir=Path(args.output_dir),
            fold_step_months=args.fold_step_months,
            selection_config=EnsembleSelectionConfig(
                up_threshold=args.up_threshold,
                min_up_votes=args.min_up_votes,
                max_up_disagreement=args.max_up_disagreement,
                crash_threshold=args.crash_threshold,
                min_crash_votes=args.min_crash_votes,
                severe_breakout_threshold=args.severe_breakout_threshold,
            ),
        )
        print(f"output_dir={result.output_dir}")
        print(f"scores={len(result.scores)} final_candidates={len(result.final_candidates)}")
        return

    parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
