"""Read-only presentation data; never adds to or rewrites frozen forecasts.

Past returns use the retained adjusted-price vintage and end on/before the
selected signal date. Listing names are a current lookup, not a historical
universe. Missing/stale quotes and incomplete horizons stay explicitly missing.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PRICE_FILES = {"kr": "krx_ohlcv_kospi_kosdaq_state.csv", "us": "us_ohlcv_nasdaq_nyse_yfinfo_state.csv"}
LISTING_FILES = {"kr": "krx_listings_kospi_kosdaq_state.csv", "us": "us_listings_nasdaq_nyse_yfinfo_state.csv"}
HORIZON = 126


def number(value: object, digits: int = 4) -> float | None:
    value = float(value)
    return round(value, digits) if np.isfinite(value) else None


def build_price_context(raw_dir: Path, target: Path, market: str, dates: list[str]) -> dict:
    """Publish small per-date lookup files, including stocks without a score."""
    prices_path = raw_dir / PRICE_FILES[market]
    listing_path = raw_dir / LISTING_FILES[market]
    if not prices_path.exists():
        return {"available": False, "reason": "Retained prices unavailable"}
    columns = ["date", "ticker", "close", "adjusted_close", "volume"]
    prices = pd.read_csv(prices_path, usecols=columns, dtype={"ticker": str})
    prices["date"] = pd.to_datetime(prices["date"], errors="raise")
    prices = prices.loc[prices["date"] <= pd.Timestamp(max(dates))]
    prices = prices.drop_duplicates(["ticker", "date"], keep="last").sort_values(["ticker", "date"])
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from ai_stock_assistant.data.price_quality import prepare
    prices, quality = prepare(prices, market)
    prices["date"] = prices["date"].dt.strftime("%Y-%m-%d")
    listings = {}
    if listing_path.exists():
        frame = pd.read_csv(listing_path, dtype=str).fillna("")
        listings = frame.drop_duplicates("ticker", keep="last").set_index("ticker").to_dict("index")
    with prices_path.open("rb") as stream:
        price_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    by_date: dict[str, list[dict]] = {date: [] for date in dates}
    for ticker, group in prices.groupby("ticker", sort=False):
        ticker = str(ticker)
        info = listings.get(ticker, {})
        observed_dates = group["date"].to_numpy()
        close = group["close"].to_numpy(dtype=float)
        adjusted = group["adjusted_close"].to_numpy(dtype=float)
        volume = group["volume"].to_numpy(dtype=float)
        breaks = group["quality_break"].to_numpy(dtype=bool)
        for date in dates:
            end = int(np.searchsorted(observed_dates, date, side="right")) - 1
            if end < 0:
                continue  # Never use a later quote in a historical view.
            row = {
                "ticker": ticker, "name": info.get("name", ticker),
                "exchange": info.get("exchange", ""), "sector": info.get("sector", ""),
                "currency": "KRW" if market == "kr" else "USD",
                "quoteDate": str(observed_dates[end]), "closeRaw": number(close[end]),
                "trailingReturn6mPct": None, "returnStartDate": None,
                "returnEndDate": str(observed_dates[end]), "returnStatus": "insufficient_history",
                "coverageReasons": [],
            }
            # These describe visible input conditions, not a fabricated prediction.
            reasons = row["coverageReasons"]
            if observed_dates[end] != date:
                reasons.append("선택일 시세 없음")
            if end + 1 < 253:
                reasons.append("모델에 필요한 253개 가격 관측 미만")
            if not np.isfinite(volume[end]) or volume[end] <= 0:
                reasons.append("당일 거래량 없음")
            if not np.isfinite(close[end]) or close[end] < (1000 if market == "kr" else 1):
                reasons.append("최소 주가 조건 미달")
            turnover = close[max(0, end - 19):end + 1] * volume[max(0, end - 19):end + 1]
            if len(turnover) < 20 or not np.isfinite(turnover).all():
                reasons.append("20일 거래대금 자료 부족")
            elif turnover.mean() < (500_000_000 if market == "kr" else 5_000_000):
                reasons.append("20일 평균 거래대금 조건 미달")
            if observed_dates[end] != date:
                row["returnStatus"] = "stale_quote"
            elif end >= HORIZON:
                window = adjusted[end - HORIZON:end + 1]
                if not np.isfinite(window).all() or not (window > 0).all():
                    row["returnStatus"] = "missing_adjusted_price"
                elif breaks[end - HORIZON:end + 1].any():
                    row["returnStatus"] = "unverified_price_continuity"
                else:
                    row.update({
                        "trailingReturn6mPct": number((window[-1] / window[0] - 1) * 100, 2),
                        "returnStartDate": str(observed_dates[end - HORIZON]),
                        "returnStatus": "available",
                    })
            by_date[date].append(row)
    # Current listings without retained prices remain searchable, with no score.
    context_hashes = {}
    for date in dates:
        existing = {r["ticker"] for r in by_date[date]}
        for ticker, info in listings.items():
            if ticker in existing:
                continue
            by_date[date].append({
                "ticker": ticker, "name": info.get("name", ticker),
                "exchange": info.get("exchange", ""), "sector": info.get("sector", ""),
                "currency": "KRW" if market == "kr" else "USD",
                "quoteDate": None, "closeRaw": None, "trailingReturn6mPct": None,
                "returnStartDate": None, "returnEndDate": None, "returnStatus": "no_prices",
                "coverageReasons": ["선택일까지 보관된 시세 없음"],
            })
        payload = {
            "schemaVersion": 1, "signalDate": date, "horizonObservations": HORIZON,
            "priceBasis": "retained_adjusted_close_with_verified_actions", "pricesSha256": price_hash,
            "priceQualityPolicy": quality["policy"], "verifiedAdjustments": quality["adjustments"],
            "listingBasis": "current_retained_listing_lookup",
            "rows": sorted(by_date[date], key=lambda r: r["ticker"]),
        }
        path = target / "price_context" / f"{date}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
        context_hashes[date] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "available": True, "path": "price_context/{date}.json", "horizonObservations": HORIZON,
        "latestCount": len(by_date[max(dates)]), "pricesSha256": price_hash,
        "priceBasis": "retained_adjusted_close_with_verified_actions", "priceQualityPolicy": quality["policy"],
        "sha256ByDate": context_hashes,
    }
