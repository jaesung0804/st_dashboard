from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from contextlib import redirect_stdout
import importlib
import io
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import pandas as pd
import FinanceDataReader as fdr
import requests

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


def _pykrx_stock():
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        return importlib.import_module("pykrx.stock")


def _call_pykrx(func, *args, **kwargs):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        return func(*args, **kwargs)


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
    stock = _pykrx_stock()
    try:
        tickers = _call_pykrx(stock.get_market_ticker_list, asof, market=market)
    except Exception:
        return fetch_krx_listings_from_fdr(market=market)
    if not tickers:
        return fetch_krx_listings_from_fdr(market=market)

    rows = [
        {
            "ticker": ticker,
            "name": _call_pykrx(stock.get_market_ticker_name, ticker),
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
    stock = _pykrx_stock()
    raw = _call_pykrx(stock.get_market_ohlcv_by_date, start, end, ticker, adjusted=True)
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


def fetch_krx_ohlcv_bounded(
    ticker: str, start: str, end: str, *, before_request=None,
    timeout: tuple[float, float] = (5.0, 15.0), attempts: int = 2,
) -> pd.DataFrame:
    """Read the same adjusted Naver series as pykrx, with bounded HTTP retries.

    The chart API's count is relative to today, NOT ``end``. Request enough
    calendar days, then filter locally; never interpret HTML/invalid XML as a
    legitimate empty trading session. No KRX login or new credential is needed.
    """
    ticker = str(ticker).zfill(6)
    if len(ticker) != 6 or not ticker.isdigit():
        raise ValueError("KRX ticker must contain six digits")
    first, last = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    if first > last or attempts < 1 or min(timeout) <= 0:
        raise ValueError("Invalid price range or HTTP limits")
    today = pd.Timestamp.now(tz="Asia/Seoul").tz_localize(None).normalize()
    count = max(2, (max(today, last) - first).days + 2)
    for attempt in range(attempts):
        if before_request is not None:
            before_request()
        try:
            response = requests.get(
                "https://fchart.stock.naver.com/sise.nhn",
                params={"symbol": ticker, "timeframe": "day", "count": count, "requestType": "0"},
                timeout=timeout,
            )
            response.raise_for_status()
            break
        except requests.RequestException as exc:
            status = exc.response.status_code if exc.response is not None else None
            retryable = status is None or status in (429, 500, 502, 503, 504)
            if not retryable or attempt + 1 == attempts:
                # Do not copy request URLs, headers or response bodies into logs.
                raise RuntimeError(f"Naver price request failed ({type(exc).__name__}, HTTP {status})") from None
            time.sleep(1.0 + attempt)
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError:
        raise ValueError("Naver returned invalid price XML") from None
    chart = root if root.tag == "chartdata" else root.find(".//chartdata")
    if chart is None or str(chart.get("symbol", ticker)).zfill(6) != ticker:
        raise ValueError("Naver price response has no matching chartdata")
    rows = []
    for node in chart.iter("item"):
        values = (node.get("data") or "").split("|")
        if len(values) != 6:
            raise ValueError("Naver price item must contain date and five OHLCV fields")
        rows.append(values)
    if not rows:
        return pd.DataFrame(columns=PRICE_SCHEMA)
    frame = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    frame["date"] = pd.to_datetime(frame["date"], format="%Y%m%d", errors="raise")
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    if frame[["open", "high", "low", "close", "volume"]].isna().any().any():
        raise ValueError("Naver price response contains missing numbers")
    frame = frame.loc[frame["date"].between(first, last)].copy()
    frame["date"] = frame["date"].dt.strftime("%Y-%m-%d")
    frame["ticker"] = ticker
    frame["adjusted_close"] = frame["close"]
    return frame[PRICE_SCHEMA].sort_values("date").reset_index(drop=True)


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
    manifest.to_csv(manifest_path, index=False, encoding="" \
    "utf-8-sig")

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
