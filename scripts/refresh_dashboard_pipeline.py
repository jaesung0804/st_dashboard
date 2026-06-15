from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from ai_stock_assistant.data.macro import fetch_macro_indicators
from ai_stock_assistant.data.opendart import save_korean_financials
from ai_stock_assistant.data.refresh import refresh_kr_daily_data, refresh_us_daily_data
from ai_stock_assistant.data.us import save_us_financials
from ai_stock_assistant.features import build_feature_matrix


RAW = Path("data/raw")
OUT = Path("outputs")
KR_PRICE_STATE = RAW / "krx_ohlcv_kospi_kosdaq_state.csv"
KR_LISTINGS_STATE = RAW / "krx_listings_kospi_kosdaq_state.csv"
KR_FINANCIAL_STATE = RAW / "opendart_financials_state.csv"
US_PRICE_STATE = RAW / "us_ohlcv_nasdaq_nyse_yfinfo_state.csv"
US_LISTINGS_STATE = RAW / "us_listings_nasdaq_nyse_yfinfo_state.csv"
US_FINANCIAL_STATE = RAW / "yfinance_financials_state.csv"


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Collect latest data, rebuild dashboard outputs, and publish Pages.")
    p.add_argument("--asof", default=None, help="YYYYMMDD. Defaults to today on the runner.")
    p.add_argument("--lookback-days", type=int, default=10)
    p.add_argument("--signal-days", type=int, default=45, help="Recent calendar days to score.")
    p.add_argument("--pages-days", type=int, default=22, help="Recent trading days to publish.")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--refresh-financials", choices=["auto", "always", "never"], default="auto")
    p.add_argument("--skip-push", action="store_true")
    return p


def latest(pattern: str) -> Path | None:
    candidates = sorted(Path().glob(pattern), key=lambda path: path.name)
    return candidates[-1] if candidates else None


def latest_data_file(pattern: str) -> Path | None:
    candidates = [path for path in Path().glob(pattern) if "manifest" not in path.stem]
    candidates = sorted(candidates, key=lambda path: path.name)
    return candidates[-1] if candidates else None


def copy_if_exists(source: Path | None, target: Path) -> Path:
    if source is None:
        if not target.exists():
            raise FileNotFoundError(target)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    return target


def merge_financials(base_path: Path, update_path: Path | None, output_path: Path) -> Path:
    frames = []
    if base_path.exists():
        frames.append(pd.read_csv(base_path, dtype={"ticker": str, "corp_code": str}))
    if update_path is not None and update_path.exists() and update_path.resolve() != base_path.resolve():
        frames.append(pd.read_csv(update_path, dtype={"ticker": str, "corp_code": str}))
    if not frames:
        raise FileNotFoundError(base_path)
    merged = pd.concat(frames, ignore_index=True)
    keys = [col for col in ["ticker", "bsns_year", "reprt_code", "report_name"] if col in merged.columns]
    if keys:
        merged = merged.drop_duplicates(keys, keep="last")
        merged = merged.sort_values(keys).reset_index(drop=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_path


def should_refresh_financials(mode: str) -> bool:
    if mode == "always":
        return True
    if mode == "never":
        return False
    return date.today().weekday() == 0 or not KR_FINANCIAL_STATE.exists() or not US_FINANCIAL_STATE.exists()


def run(args: list[str]) -> None:
    print(" ".join(args), flush=True)
    subprocess.run(args, check=True)


def max_price_date(path: Path) -> pd.Timestamp:
    dates = pd.read_csv(path, usecols=["date"])
    return pd.to_datetime(dates["date"]).max()


def signal_start(path: Path, days: int) -> str:
    return (max_price_date(path) - pd.Timedelta(days=days)).strftime("%Y-%m-%d")


def attach_macro(features_path: Path, macro_path: Path, output_path: Path) -> Path:
    run(
        [
            sys.executable,
            "scripts/attach_macro_features.py",
            "--features-path",
            str(features_path),
            "--macro-features-path",
            str(macro_path),
            "--output-path",
            str(output_path),
            "--model-only",
        ]
    )
    return output_path


def build_walkforward(market: str, features_path: Path, out_dir: Path, start: str, listings_path: Path | None = None) -> None:
    cmd = [
        sys.executable,
        "scripts/run_walkforward_warning_macro.py",
        "--market",
        market,
        "--features-path",
        str(features_path),
        "--out-dir",
        str(out_dir),
        "--frequency",
        "daily",
        "--start",
        start,
        "--end",
        "auto",
    ]
    if market == "us" and listings_path is not None:
        cmd.extend(["--listings-path", str(listings_path)])
    run(cmd)


def build_dashboard(wf_dir: Path, out_dir: Path, market_name: str, recent_days: int, listings_path: Path | None = None) -> None:
    cmd = [
        sys.executable,
        "scripts/build_walkforward_dashboard.py",
        "--wf-dir",
        str(wf_dir),
        "--out-dir",
        str(out_dir),
        "--market-name",
        market_name,
        "--recent-days",
        str(recent_days),
    ]
    if listings_path is not None:
        cmd.extend(["--listings-path", str(listings_path)])
    run(cmd)


def main() -> None:
    args = parser().parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    asof = args.asof
    copy_if_exists(latest("data/raw/krx_ohlcv_kospi_kosdaq_*.csv"), KR_PRICE_STATE)
    copy_if_exists(latest_data_file("data/raw/krx_listings_kospi_kosdaq_*.csv"), KR_LISTINGS_STATE)
    copy_if_exists(latest("data/raw/us_ohlcv_nasdaq_nyse_yfinfo_*.csv"), US_PRICE_STATE)
    copy_if_exists(latest_data_file("data/raw/us_listings_nasdaq_nyse_yfinfo_*.csv"), US_LISTINGS_STATE)
    copy_if_exists(latest("data/raw/opendart_financials_all_reports_*.csv") or latest("data/raw/opendart_financials_state.csv"), KR_FINANCIAL_STATE)
    copy_if_exists(latest("data/raw/yfinance_financials_annual_quarterly.csv") or latest("data/raw/yfinance_financials_state.csv"), US_FINANCIAL_STATE)

    kr_refresh = refresh_kr_daily_data(asof=asof, lookback_days=args.lookback_days)
    copy_if_exists(kr_refresh.combined_prices_path, KR_PRICE_STATE)
    copy_if_exists(kr_refresh.listings_path, KR_LISTINGS_STATE)

    us_refresh = refresh_us_daily_data(
        asof=asof,
        lookback_days=args.lookback_days,
        listings_path=US_LISTINGS_STATE,
        prices_path=US_PRICE_STATE,
        output_path=US_PRICE_STATE,
    )
    copy_if_exists(us_refresh.listings_path, US_LISTINGS_STATE)

    if should_refresh_financials(args.refresh_financials):
        if os.getenv("OPENDART_API_KEY") or os.getenv("DART_API_KEY"):
            current_year = date.today().year
            kr_fin = save_korean_financials(
                listings_path=KR_LISTINGS_STATE,
                years=[current_year - 1, current_year],
                reports=["annual", "q1", "half", "q3"],
                workers=args.workers,
                sleep_seconds=0,
            )
            merge_financials(KR_FINANCIAL_STATE, kr_fin.normalized_financials_path, KR_FINANCIAL_STATE)
        else:
            print("OPENDART_API_KEY is not set. Keeping the existing Korean financial state.", flush=True)
        us_fin = save_us_financials(
            listings_path=US_LISTINGS_STATE,
            reports=["annual", "quarterly"],
            workers=max(1, min(args.workers, 4)),
            sleep_seconds=0,
        )
        merge_financials(US_FINANCIAL_STATE, us_fin.normalized_financials_path, US_FINANCIAL_STATE)
    else:
        print("Skipping financial statement refresh for this run.", flush=True)

    min_date = min(max_price_date(KR_PRICE_STATE), max_price_date(US_PRICE_STATE)) - pd.Timedelta(days=365 * 5 + 30)
    max_date = max(max_price_date(KR_PRICE_STATE), max_price_date(US_PRICE_STATE))
    macro = fetch_macro_indicators(start=min_date.strftime("%Y-%m-%d"), end=max_date.strftime("%Y-%m-%d"), sleep_seconds=0)

    kr_features = OUT / "daily_features_kr" / "training_features_daily.csv"
    us_features = OUT / "daily_features_us" / "training_features_daily.csv"
    kr_macro_features = OUT / "daily_features_kr_macro" / "training_features_daily.csv"
    us_macro_features = OUT / "daily_features_us_macro" / "training_features_daily.csv"
    build_feature_matrix(KR_PRICE_STATE, KR_FINANCIAL_STATE, KR_LISTINGS_STATE, kr_features)
    build_feature_matrix(US_PRICE_STATE, US_FINANCIAL_STATE, US_LISTINGS_STATE, us_features)
    attach_macro(kr_features, macro.feature_path, kr_macro_features)
    attach_macro(us_features, macro.feature_path, us_macro_features)

    kr_wf = OUT / "walkforward_warning_macro_kr_latest_auto"
    us_wf = OUT / "walkforward_warning_macro_us_latest_auto"
    build_walkforward("kr", kr_macro_features, kr_wf, signal_start(KR_PRICE_STATE, args.signal_days))
    build_walkforward("us", us_macro_features, us_wf, signal_start(US_PRICE_STATE, args.signal_days), US_LISTINGS_STATE)

    build_dashboard(
        kr_wf,
        OUT / "lgbm_warning_dashboard_macro_kr_latest",
        "한국",
        args.pages_days,
    )
    build_dashboard(
        us_wf,
        OUT / "lgbm_warning_dashboard_macro_us_latest",
        "미국",
        args.pages_days,
        US_LISTINGS_STATE,
    )
    publish = [sys.executable, "scripts/build_pages_deploy.py", "--days", str(args.pages_days)]
    if not args.skip_push:
        publish.append("--push")
    run(publish)


if __name__ == "__main__":
    main()
