from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from contextlib import redirect_stdout
import importlib
import io
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from ai_stock_assistant.config import DAILY_OUTPUT_DIR, RAW_DATA_DIR, ensure_project_dirs
from ai_stock_assistant.data.krx import (
    PRICE_SCHEMA as KRX_PRICE_SCHEMA,
    fetch_krx_listings_for_markets,
    fetch_krx_ohlcv,
    today_yyyymmdd,
)
from ai_stock_assistant.data.us import PRICE_SCHEMA as US_PRICE_SCHEMA, fetch_us_ohlcv_batch


def _pykrx_stock():
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        return importlib.import_module("pykrx.stock")


def _call_pykrx(func, *args, **kwargs):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        return func(*args, **kwargs)


def _brief_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc).splitlines()[0]}"


@dataclass(frozen=True)
class DailyRefreshResult:
    asof: str
    listings_path: Path
    price_dir: Path
    combined_prices_path: Path
    summary_path: Path
    updated_count: int
    failed_count: int


@dataclass(frozen=True)
class USDailyRefreshResult:
    asof: str
    listings_path: Path
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


def _normalize_krx_by_ticker(raw: pd.DataFrame, asof: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=KRX_PRICE_SCHEMA)
    frame = raw.reset_index()
    column_map = {
        frame.columns[0]: "ticker",
        frame.columns[1]: "open",
        frame.columns[2]: "high",
        frame.columns[3]: "low",
        frame.columns[4]: "close",
        frame.columns[5]: "volume",
    }
    frame = frame.rename(columns=column_map)
    frame["ticker"] = frame["ticker"].astype(str).str.zfill(6)
    frame["date"] = pd.to_datetime(asof).strftime("%Y-%m-%d")
    frame["adjusted_close"] = frame["close"]
    return frame[KRX_PRICE_SCHEMA]


def latest_completed_krx_asof(now: datetime | None = None) -> str:
    """Return the most likely completed KRX trading date to request.

    The scheduled refresh runs before the Korean market has closed. In that
    window, asking KRX for today's date can miss the most recent completed
    close, so default to yesterday until after the regular market close.
    """
    seoul_now = now.astimezone(ZoneInfo("Asia/Seoul")) if now else datetime.now(ZoneInfo("Asia/Seoul"))
    target = seoul_now.date()
    if seoul_now.time() < time(18, 0):
        target -= timedelta(days=1)
    return target.strftime("%Y%m%d")


def latest_completed_us_asof(now: datetime | None = None) -> str:
    """Return a date whose regular US session has finished and settled.

    A manual recovery may run while New York is still trading. Requesting that
    calendar date exposes a moving Yahoo snapshot whose close can temporarily
    sit outside its high/low and whose universe coverage is incomplete. Keep a
    one-hour provider-settlement buffer after the 16:00 regular close. Weekend
    and holiday dates are harmless because the collector uses the latest real
    observation without fabricating a bar.
    """
    new_york_now = now.astimezone(ZoneInfo("America/New_York")) if now else datetime.now(ZoneInfo("America/New_York"))
    target = new_york_now.date()
    if new_york_now.time() < time(17, 0):
        target -= timedelta(days=1)
    return target.strftime("%Y%m%d")


def _krx_candidate_asofs(requested_asof: str, lookback_days: int) -> list[str]:
    candidates: list[str] = []
    for offset in range(max(0, lookback_days) + 1):
        candidate = pd.Timestamp(requested_asof) - pd.Timedelta(days=offset)
        if candidate.weekday() >= 5:
            continue
        candidates.append(candidate.strftime("%Y%m%d"))
    return candidates


def _krx_refresh_asofs(requested_asof: str, existing: pd.DataFrame, lookback_days: int) -> list[str]:
    requested = pd.Timestamp(requested_asof).normalize()
    if existing.empty or "date" not in existing.columns:
        return [requested.strftime("%Y%m%d")]

    existing_dates = set(pd.to_datetime(existing["date"], errors="coerce").dropna().dt.strftime("%Y%m%d"))
    start = requested - pd.Timedelta(days=max(0, lookback_days))
    targets = []
    for candidate in pd.date_range(start=start, end=requested, freq="D"):
        if candidate.weekday() >= 5:
            continue
        yyyymmdd = candidate.strftime("%Y%m%d")
        if yyyymmdd not in existing_dates or yyyymmdd == requested.strftime("%Y%m%d"):
            targets.append(yyyymmdd)
    return targets or [requested.strftime("%Y%m%d")]


def refresh_kr_daily_data_fast(
    markets: list[str] | None = None,
    asof: str | None = None,
    prices_path: Path | None = None,
    output_path: Path | None = None,
    asof_lookback_days: int = 7,
    listings_path: Path | None = None,
    checkpoint_dir: Path | None = None,
    workers: int = 4,
    max_seconds: float = 3600,
) -> DailyRefreshResult:
    """Refresh each restored ticker's missing range plus a correction overlap.

    Kept as a compatible entry point. The bulk/date x ticker fallback is no
    longer used: pykrx's authenticated KRX endpoint may be unavailable, while
    its adjusted daily data already comes from Naver. Universe refresh remains
    an explicit separate task; a data outage must not change the model universe.
    """
    from ai_stock_assistant.data.krx_incremental import refresh_ranges

    ensure_project_dirs()
    markets = markets or ["KOSPI", "KOSDAQ"]
    requested_asof = asof or latest_completed_krx_asof()
    market_slug = "_".join(market.lower() for market in markets)
    prices_path = prices_path or _find_latest_combined_prices(market_slug) or (RAW_DATA_DIR / f"krx_ohlcv_{market_slug}_daily.csv")
    output_path = output_path or prices_path
    if listings_path is None:
        candidates = sorted(RAW_DATA_DIR.glob(f"krx_listings_{market_slug}_*.csv"))
        if not candidates:
            raise FileNotFoundError("No restored KRX listings CSV; seed dashboard-state first")
        listings_path = candidates[-1]
    return refresh_ranges(
        markets=markets, requested_asof=requested_asof, prices_path=prices_path,
        output_path=output_path, listings_path=listings_path,
        run_dir=DAILY_OUTPUT_DIR / requested_asof,
        checkpoint_dir=checkpoint_dir or RAW_DATA_DIR.parent / "cache" / "krx_ranges",
        overlap_days=asof_lookback_days, workers=workers, max_seconds=max_seconds,
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


def _find_latest_us_listings() -> Path | None:
    candidates = sorted(
        (path for path in RAW_DATA_DIR.glob("us_listings_nasdaq_nyse_yfinfo_*.csv") if "manifest" not in path.stem),
        key=lambda path: path.name,
    )
    if candidates:
        return candidates[-1]
    candidates = sorted(
        (path for path in RAW_DATA_DIR.glob("us_listings_nasdaq_nyse_*.csv") if "manifest" not in path.stem),
        key=lambda path: path.name,
    )
    return candidates[-1] if candidates else None


def _find_latest_us_combined_prices() -> Path | None:
    candidates = sorted(
        RAW_DATA_DIR.glob("us_ohlcv_nasdaq_nyse_yfinfo_*.csv"),
        key=lambda path: path.name,
    )
    if candidates:
        return candidates[-1]
    candidates = sorted(RAW_DATA_DIR.glob("us_ohlcv_nasdaq_nyse_*.csv"), key=lambda path: path.name)
    return candidates[-1] if candidates else None


def refresh_us_daily_data(
    listings_path: Path | None = None,
    prices_path: Path | None = None,
    output_path: Path | None = None,
    asof: str | None = None,
    lookback_days: int = 10,
    batch_size: int = 100,
    minimum_coverage: float = .95,
    max_seconds: float = 3600,
) -> USDailyRefreshResult:
    """Refresh each US ticker's missing range; validate before replacing prices."""
    from ai_stock_assistant.data.us_incremental import refresh_ranges

    ensure_project_dirs()
    asof = asof or latest_completed_us_asof()
    listings_path = listings_path or _find_latest_us_listings()
    prices_path = prices_path or _find_latest_us_combined_prices()
    if listings_path is None:
        raise FileNotFoundError("No US listings CSV found under data/raw.")
    if prices_path is None:
        raise FileNotFoundError("No US combined OHLCV CSV found under data/raw.")

    return refresh_ranges(
        listings_path=listings_path, prices_path=prices_path, output_path=output_path or prices_path,
        requested_asof=asof, run_dir=DAILY_OUTPUT_DIR / asof, fetch=fetch_us_ohlcv_batch,
        overlap_days=lookback_days, batch_size=batch_size,
        minimum_coverage=minimum_coverage, max_seconds=max_seconds,
    )
