from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from ai_stock_assistant.config import DAILY_OUTPUT_DIR, RAW_DATA_DIR, ensure_project_dirs
from ai_stock_assistant.data.krx import (
    fetch_krx_listings_for_markets,
    fetch_krx_ohlcv,
    today_yyyymmdd,
)


@dataclass(frozen=True)
class DailyRefreshResult:
    asof: str
    listings_path: Path
    price_dir: Path
    combined_prices_path: Path
    summary_path: Path
    updated_count: int
    failed_count: int


def _find_latest_price_dir(market_slug: str) -> Path | None:
    root = RAW_DATA_DIR / "krx_ohlcv_daily"
    candidates = sorted(root.glob(f"{market_slug}_*_*"), key=lambda path: path.name)
    return candidates[-1] if candidates else None


def _find_latest_combined_prices(market_slug: str) -> Path | None:
    candidates = sorted(RAW_DATA_DIR.glob(f"krx_ohlcv_{market_slug}_*.csv"), key=lambda path: path.name)
    return candidates[-1] if candidates else None


def _next_fetch_start(existing_path: Path, lookback_days: int) -> str:
    if not existing_path.exists():
        return "20200101"
    existing = pd.read_csv(existing_path, usecols=["date"])
    if existing.empty:
        return "20200101"
    latest = pd.to_datetime(existing["date"]).max().date()
    return (latest - pd.Timedelta(days=lookback_days)).strftime("%Y%m%d")


def refresh_kr_daily_data(
    markets: list[str] | None = None,
    asof: str | None = None,
    lookback_days: int = 10,
    rebuild_combined: bool = True,
    limit: int | None = None,
) -> DailyRefreshResult:
    ensure_project_dirs()
    markets = markets or ["KOSPI", "KOSDAQ"]
    asof = asof or today_yyyymmdd()
    market_slug = "_".join(market.lower() for market in markets)
    run_dir = DAILY_OUTPUT_DIR / asof
    run_dir.mkdir(parents=True, exist_ok=True)

    listings = fetch_krx_listings_for_markets(asof=asof, markets=markets)
    canonical_listings_path = RAW_DATA_DIR / f"krx_listings_{market_slug}_{asof}.csv"
    listings.to_csv(canonical_listings_path, index=False, encoding="utf-8-sig")
    if limit is not None:
        listings = listings.head(limit)
        listings_path = run_dir / f"krx_listings_{market_slug}_{asof}_limit_{limit}.csv"
        listings.to_csv(listings_path, index=False, encoding="utf-8-sig")
    else:
        listings_path = canonical_listings_path

    price_dir = _find_latest_price_dir(market_slug) or (RAW_DATA_DIR / "krx_ohlcv_daily" / f"{market_slug}_daily")
    price_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    updated_count = 0
    failed_count = 0
    for idx, row in listings.reset_index(drop=True).iterrows():
        ticker = str(row["ticker"]).zfill(6)
        path = price_dir / f"{ticker}.csv"
        try:
            start = _next_fetch_start(path, lookback_days=lookback_days)
            update = fetch_krx_ohlcv(ticker=ticker, start=start, end=asof)
            if path.exists():
                existing = pd.read_csv(path, dtype={"ticker": str})
                merged = pd.concat([existing, update], ignore_index=True)
            else:
                merged = update
            if not merged.empty:
                merged["ticker"] = merged["ticker"].astype(str).str.zfill(6)
                merged["date"] = pd.to_datetime(merged["date"]).dt.strftime("%Y-%m-%d")
                merged = merged.drop_duplicates(["ticker", "date"], keep="last")
                merged = merged.sort_values(["ticker", "date"]).reset_index(drop=True)
            merged.to_csv(path, index=False, encoding="utf-8-sig")
            updated_count += 1
            rows.append(
                {
                    "ticker": ticker,
                    "name": row["name"],
                    "exchange": row["exchange"],
                    "status": "updated",
                    "rows": len(merged),
                    "latest_date": None if merged.empty else merged["date"].max(),
                    "path": str(path),
                    "error": "",
                }
            )
        except Exception as exc:  # noqa: BLE001 - keep daily refresh resumable.
            failed_count += 1
            rows.append(
                {
                    "ticker": ticker,
                    "name": row["name"],
                    "exchange": row["exchange"],
                    "status": "failed",
                    "rows": 0,
                    "latest_date": "",
                    "path": str(path),
                    "error": repr(exc),
                }
            )
        print(f"[{idx + 1}/{len(listings)}] {ticker} {rows[-1]['status']}", flush=True)

    summary = pd.DataFrame(rows)
    summary_path = run_dir / "daily_data_refresh_summary.csv"
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")

    combined_path = _find_latest_combined_prices(market_slug) or (RAW_DATA_DIR / f"krx_ohlcv_{market_slug}_daily.csv")
    if rebuild_combined:
        frames = [pd.read_csv(path, dtype={"ticker": str}) for path in sorted(price_dir.glob("*.csv"))]
        combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if not combined.empty:
            combined["ticker"] = combined["ticker"].astype(str).str.zfill(6)
            combined = combined.drop_duplicates(["ticker", "date"], keep="last")
            combined = combined.sort_values(["ticker", "date"]).reset_index(drop=True)
        combined.to_csv(combined_path, index=False, encoding="utf-8-sig")

    return DailyRefreshResult(
        asof=asof,
        listings_path=listings_path,
        price_dir=price_dir,
        combined_prices_path=combined_path,
        summary_path=summary_path,
        updated_count=updated_count,
        failed_count=failed_count,
    )


def write_daily_readme(asof: str | None = None) -> Path:
    ensure_project_dirs()
    asof = asof or date.today().strftime("%Y%m%d")
    run_dir = DAILY_OUTPUT_DIR / asof
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "README.md"
    path.write_text(
        "\n".join(
            [
                f"# Daily Stock Assistant Run {asof}",
                "",
                "Generated data refresh artifacts:",
                "",
                "- `daily_data_refresh_summary.csv`: per-ticker price refresh status",
                "",
                "Future model outputs should be written here:",
                "",
                "- `final_candidates.csv`",
                "- `vetoed_up_candidates.csv`",
                "- `crash_watchlist.csv`",
                "- `up_model_scores.csv`",
                "- `crash_model_scores.csv`",
                "- `html_report.html`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path
