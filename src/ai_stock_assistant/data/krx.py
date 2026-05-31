from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
import time

import pandas as pd
import FinanceDataReader as fdr
from pykrx import stock

from ai_stock_assistant.config import RAW_DATA_DIR, ensure_project_dirs


PRICE_SCHEMA = ["date", "ticker", "open", "high", "low", "close", "adjusted_close", "volume"]
COMPANY_SCHEMA = [








    "ticker",
    "name",
    "exchange",
    "sector",
    "industry",
    "market_cap",
    "shares_outstanding",
    "listing_date",
]


@dataclass(frozen=True)
class UniversePriceFetchResult:
    listings_path: Path
    price_dir: Path
    combined_prices_path: Path | None
    manifest_path: Path
    requested_count: int
    saved_count: int
    failed_count: int


def today_yyyymmdd() -> str:
    return date.today().strftime("%Y%m%d")


def five_years_ago_yyyymmdd(end: str | None = None) -> str:
    end_date = pd.to_datetime(end or today_yyyymmdd()).date()
    try:
        start_date = end_date.replace(year=end_date.year - 5)
    except ValueError:
        start_date = end_date - timedelta(days=365 * 5)
    return start_date.strftime("%Y%m%d")


def fetch_krx_listings(asof: str | None = None, market: str = "ALL") -> pd.DataFrame:
    """Fetch KRX tickers from pykrx and normalize to project company schema."""
    asof = asof or today_yyyymmdd()
    tickers = stock.get_market_ticker_list(asof, market=market)
    if not tickers:
        return fetch_krx_listings_from_fdr(market=market)

    rows = [
        {
            "ticker": ticker,
            "name": stock.get_market_ticker_name(ticker),
            "exchange": "KRX",
            "sector": None,
            "industry": None,
            "market_cap": None,
            "shares_outstanding": None,
            "listing_date": None,
        }
        for ticker in tickers
    ]
    return pd.DataFrame(rows, columns=COMPANY_SCHEMA).sort_values("ticker").reset_index(drop=True)


def fetch_krx_listings_from_fdr(market: str = "ALL") -> pd.DataFrame:
    """Fetch current Korean listings from FinanceDataReader as a KRX fallback."""
    listings = fdr.StockListing("KRX")
    if market != "ALL":
        listings = listings[listings["Market"].eq(market)]

    frame = pd.DataFrame(
        {
            "ticker": listings["Code"].astype(str).str.zfill(6),
            "name": listings["Name"],
            "exchange": listings["Market"],
            "sector": None,
            "industry": listings.get("Dept"),
            "market_cap": listings.get("Marcap"),
            "shares_outstanding": listings.get("Stocks"),
            "listing_date": None,
        }
    )
    frame = frame[frame["ticker"].str.fullmatch(r"\d{6}")]
    return frame[COMPANY_SCHEMA].sort_values("ticker").reset_index(drop=True)


def fetch_krx_listings_for_markets(asof: str | None = None, markets: list[str] | None = None) -> pd.DataFrame:
    markets = markets or ["KOSPI", "KOSDAQ"]
    frames = [fetch_krx_listings(asof=asof, market=market) for market in markets]
    listings = pd.concat(frames, ignore_index=True)
    listings["ticker"] = listings["ticker"].astype(str).str.zfill(6)
    listings = listings.drop_duplicates("ticker", keep="first")
    return listings.sort_values(["exchange", "ticker"]).reset_index(drop=True)


def save_krx_listings(asof: str | None = None, market: str = "ALL") -> Path:
    ensure_project_dirs()
    asof = asof or today_yyyymmdd()
    listings = fetch_krx_listings(asof=asof, market=market)
    output_path = RAW_DATA_DIR / f"krx_listings_{market.lower()}_{asof}.csv"
    listings.to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_path


def fetch_krx_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch daily adjusted OHLCV for a Korean ticker.

    pykrx returns adjusted OHLCV when adjusted=True. It does not expose a
    separate adjusted close column, so adjusted_close is mirrored from close.
    """
    raw = stock.get_market_ohlcv_by_date(start, end, ticker, adjusted=True)
    if raw.empty:
        return pd.DataFrame(columns=PRICE_SCHEMA)

    frame = raw.reset_index()
    column_map = {
        frame.columns[0]: "date",
        frame.columns[1]: "open",
        frame.columns[2]: "high",
        frame.columns[3]: "low",
        frame.columns[4]: "close",
        frame.columns[5]: "volume",
    }
    frame = frame.rename(columns=column_map)
    frame["ticker"] = ticker
    frame["adjusted_close"] = frame["close"]
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    return frame[PRICE_SCHEMA].sort_values(["ticker", "date"]).reset_index(drop=True)


def save_krx_ohlcv(tickers: list[str], start: str, end: str) -> Path:
    ensure_project_dirs()
    frames = [fetch_krx_ohlcv(ticker=ticker, start=start, end=end) for ticker in tickers]
    prices = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    output_path = RAW_DATA_DIR / f"krx_ohlcv_{start}_{end}.csv"
    prices.to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_path


def save_krx_universe_ohlcv(
    start: str | None = None,
    end: str | None = None,
    markets: list[str] | None = None,
    sleep_seconds: float = 0.1,
    limit: int | None = None,
    force: bool = False,
    combine: bool = True,
) -> UniversePriceFetchResult:
    ensure_project_dirs()
    end = end or today_yyyymmdd()
    start = start or five_years_ago_yyyymmdd(end)
    markets = markets or ["KOSPI", "KOSDAQ"]
    market_slug = "_".join(market.lower() for market in markets)

    listings = fetch_krx_listings_for_markets(asof=end, markets=markets)
    if limit is not None:
        listings = listings.head(limit)

    listings_path = RAW_DATA_DIR / f"krx_listings_{market_slug}_{end}.csv"
    listings.to_csv(listings_path, index=False, encoding="utf-8-sig")

    price_dir = RAW_DATA_DIR / "krx_ohlcv_daily" / f"{market_slug}_{start}_{end}"
    price_dir.mkdir(parents=True, exist_ok=True)

    manifest_rows: list[dict[str, object]] = []
    saved_count = 0
    failed_count = 0

    for idx, row in listings.reset_index(drop=True).iterrows():
        ticker = str(row["ticker"]).zfill(6)
        output_path = price_dir / f"{ticker}.csv"
        if output_path.exists() and not force:
            saved_count += 1
            manifest_rows.append(
                {
                    "ticker": ticker,
                    "name": row["name"],
                    "exchange": row["exchange"],
                    "status": "cached",
                    "rows": len(pd.read_csv(output_path, usecols=["date"])),
                    "path": str(output_path),
                    "error": "",
                }
            )
            continue

        try:
            prices = fetch_krx_ohlcv(ticker=ticker, start=start, end=end)
            prices.to_csv(output_path, index=False, encoding="utf-8-sig")
            saved_count += 1
            status = "empty" if prices.empty else "saved"
            manifest_rows.append(
                {
                    "ticker": ticker,
                    "name": row["name"],
                    "exchange": row["exchange"],
                    "status": status,
                    "rows": len(prices),
                    "path": str(output_path),
                    "error": "",
                }
            )
        except Exception as exc:  # noqa: BLE001 - keep universe fetch resumable.
            failed_count += 1
            manifest_rows.append(
                {
                    "ticker": ticker,
                    "name": row["name"],
                    "exchange": row["exchange"],
                    "status": "failed",
                    "rows": 0,
                    "path": str(output_path),
                    "error": repr(exc),
                }
            )

        print(f"[{idx + 1}/{len(listings)}] {ticker} {manifest_rows[-1]['status']}", flush=True)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    manifest = pd.DataFrame(manifest_rows)
    manifest_path = RAW_DATA_DIR / f"krx_ohlcv_manifest_{market_slug}_{start}_{end}.csv"
    manifest.to_csv(manifest_path, index=False, encoding="utf-8-sig")

    combined_prices_path: Path | None = None
    if combine:
        requested_paths = [Path(path) for path in manifest["path"].tolist()]
        frames = [pd.read_csv(path, dtype={"ticker": str}) for path in requested_paths if path.exists()]
        combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PRICE_SCHEMA)
        combined["ticker"] = combined["ticker"].astype(str).str.zfill(6)
        combined_prices_path = RAW_DATA_DIR / f"krx_ohlcv_{market_slug}_{start}_{end}.csv"
        combined.to_csv(combined_prices_path, index=False, encoding="utf-8-sig")

    return UniversePriceFetchResult(
        listings_path=listings_path,
        price_dir=price_dir,
        combined_prices_path=combined_prices_path,
        manifest_path=manifest_path,
        requested_count=len(listings),
        saved_count=saved_count,
        failed_count=failed_count,
    )
