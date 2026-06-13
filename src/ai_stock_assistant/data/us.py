from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from io import StringIO
import json
from pathlib import Path
import re
import time

import pandas as pd
import requests
import yfinance as yf

from ai_stock_assistant.config import RAW_DATA_DIR, ensure_project_dirs


NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
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
PROFILE_SCHEMA = [
    "ticker",
    "name",
    "sector",
    "industry",
    "representative_industry",
    "market_cap",
    "shares_outstanding",
    "quote_type",
    "status",
    "error",
]
INDUSTRY_REPRESENTATIVE_RULES = [
    (r"semiconductor|chip", "반도체"),
    (r"software|application|infrastructure", "소프트웨어"),
    (r"biotech|biotechnology|pharmaceutical|drug|therapeutic", "바이오/제약"),
    (r"medical|health|diagnostic|device|care", "헬스케어"),
    (r"bank|credit|mortgage|capital markets|asset management", "금융"),
    (r"insurance", "보험"),
    (r"oil|gas|energy|solar|renewable|uranium", "에너지"),
    (r"utility|utilities|water", "유틸리티"),
    (r"retail|department|specialty|internet retail", "소매/유통"),
    (r"auto|vehicle|truck|rail|airline|transport|shipping|logistics", "운송/모빌리티"),
    (r"aerospace|defense", "항공우주/방산"),
    (r"telecom|communication|entertainment|media|broadcast|publishing", "통신/미디어"),
    (r"restaurant|hotel|resort|travel|leisure|gaming", "여행/레저"),
    (r"real estate|reit", "부동산/리츠"),
    (r"metal|steel|copper|gold|silver|mining|chemical|paper|packaging", "소재"),
    (r"industrial|machinery|electrical|engineering|construction|tools", "산업재"),
    (r"food|beverage|household|personal|tobacco|grocery", "필수소비재"),
]
FINANCIAL_COLUMNS = [
    "ticker",
    "corp_code",
    "corp_name",
    "bsns_year",
    "reprt_code",
    "report_name",
    "fs_div",
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "total_assets",
    "total_liabilities",
    "total_equity",
    "cash",
    "short_term_debt",
    "long_term_debt",
    "operating_cash_flow",
    "investing_cash_flow",
    "financing_cash_flow",
    "capex",
    "eps",
    "bps",
]
FINANCIAL_FIELD_ALIASES = {
    "revenue": {"Total Revenue", "Operating Revenue"},
    "gross_profit": {"Gross Profit"},
    "operating_income": {"Operating Income", "Operating Income Loss"},
    "net_income": {"Net Income", "Net Income Common Stockholders", "Net Income Continuous Operations"},
    "total_assets": {"Total Assets"},
    "total_liabilities": {"Total Liabilities Net Minority Interest", "Total Liab"},
    "total_equity": {"Stockholders Equity", "Total Equity Gross Minority Interest", "Common Stock Equity"},
    "cash": {
        "Cash And Cash Equivalents",
        "Cash Cash Equivalents And Short Term Investments",
        "Cash Financial",
    },
    "short_term_debt": {"Current Debt", "Current Debt And Capital Lease Obligation", "Short Long Term Debt"},
    "long_term_debt": {"Long Term Debt", "Long Term Debt And Capital Lease Obligation"},
    "operating_cash_flow": {"Operating Cash Flow", "Total Cash From Operating Activities"},
    "investing_cash_flow": {"Investing Cash Flow", "Total Cashflows From Investing Activities"},
    "financing_cash_flow": {"Financing Cash Flow", "Total Cash From Financing Activities"},
    "capex": {"Capital Expenditure", "Capital Expenditures"},
    "eps": {"Basic EPS", "Diluted EPS"},
}

REPORT_CODE_BY_NAME = {
    "annual": "10-K",
    "q1": "10-Q",
    "half": "10-Q",
    "q3": "10-Q",
}
WINDOWS_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{idx}" for idx in range(1, 10)),
    *(f"LPT{idx}" for idx in range(1, 10)),
}

NON_COMMON_SECURITY_PATTERN = (
    r"warrant|right|unit|preferred|preference|depositary|note|bond|debenture|"
    r"certificate|contingent value|cvrs|etn|etv|acquisition|blank check"
)

EXCHANGE_CODE_MAP = {
    "A": "NYSEAMERICAN",
    "N": "NYSE",
    "P": "NYSEARCA",
    "Q": "NASDAQ",
    "V": "IEXG",
    "Z": "CBOE",
}


@dataclass(frozen=True)
class UniversePriceFetchResult:
    listings_path: Path
    price_dir: Path
    combined_prices_path: Path | None
    manifest_path: Path
    requested_count: int
    saved_count: int
    failed_count: int


@dataclass(frozen=True)
class YFinanceFinancialFetchResult:
    raw_accounts_path: Path
    normalized_financials_path: Path
    manifest_path: Path
    requested_count: int
    saved_count: int
    failed_count: int


@dataclass(frozen=True)
class YFinanceProfileFetchResult:
    output_path: Path
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


def _yyyymmdd_to_yfinance_date(value: str) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def _exclusive_end_date(value: str) -> str:
    end = pd.to_datetime(value).date() + timedelta(days=1)
    return end.strftime("%Y-%m-%d")


def _to_yfinance_ticker(ticker: str) -> str:
    return str(ticker).strip().upper().replace(".", "-")


def _ticker_filename(ticker: str) -> str:
    clean = _to_yfinance_ticker(ticker)
    stem = f"{clean}_" if clean.upper() in WINDOWS_RESERVED_FILENAMES else clean
    return f"{stem}.csv"


def _parse_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def representative_us_industry(sector: object, industry: object) -> str:
    """Collapse yfinance's detailed industry strings into dashboard-friendly buckets."""
    text = f"{sector or ''} {industry or ''}".lower()
    for pattern, label in INDUSTRY_REPRESENTATIVE_RULES:
        if re.search(pattern, text):
            return label
    if industry and not pd.isna(industry):
        return str(industry).split("-")[0].split(",")[0].strip()[:32]
    if sector and not pd.isna(sector):
        return str(sector).strip()[:32]
    return "미분류"


def _profile_cache_path(cache_dir: Path, ticker: str) -> Path:
    return cache_dir / f"{_ticker_filename(ticker).removesuffix('.csv')}.json"


def fetch_us_profile_for_ticker(ticker: str, name: str | None = None) -> dict[str, object]:
    ticker = _to_yfinance_ticker(ticker)
    info = yf.Ticker(ticker).info or {}
    sector = info.get("sector")
    industry = info.get("industry")
    return {
        "ticker": ticker,
        "name": info.get("shortName") or info.get("longName") or name or ticker,
        "sector": sector,
        "industry": industry,
        "representative_industry": representative_us_industry(sector, industry),
        "market_cap": info.get("marketCap"),
        "shares_outstanding": info.get("sharesOutstanding") or info.get("impliedSharesOutstanding"),
        "quote_type": info.get("quoteType"),
        "status": "saved",
        "error": "",
    }


def _financial_report_name(period_end: pd.Timestamp, frequency: str) -> str:
    if frequency == "annual":
        return "annual"
    quarter = int(period_end.quarter)
    return {1: "q1", 2: "half", 3: "q3", 4: "annual"}[quarter]


def _statement_value(statements: list[pd.DataFrame], aliases: set[str], period: pd.Timestamp) -> float | None:
    for statement in statements:
        if statement.empty or period not in statement.columns:
            continue
        for alias in aliases:
            if alias in statement.index:
                value = _parse_float(statement.loc[alias, period])
                if value is not None:
                    return value
    return None


def _fetch_yfinance_statement_frames(ticker: str, frequency: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    stock = yf.Ticker(ticker)
    if frequency == "annual":
        income = stock.income_stmt
        balance = stock.balance_sheet
        cashflow = stock.cashflow
    else:
        income = stock.quarterly_income_stmt
        balance = stock.quarterly_balance_sheet
        cashflow = stock.quarterly_cashflow
    return income, balance, cashflow


def fetch_us_financials_for_ticker(ticker: str, frequency: str = "annual", name: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch and normalize yfinance statements for one US ticker."""
    ticker = _to_yfinance_ticker(ticker)
    income, balance, cashflow = _fetch_yfinance_statement_frames(ticker, frequency=frequency)
    statements = [income, balance, cashflow]
    periods = sorted(
        {
            pd.Timestamp(period)
            for statement in statements
            for period in statement.columns
            if not statement.empty
        }
    )
    if not periods:
        return pd.DataFrame(), pd.DataFrame(columns=FINANCIAL_COLUMNS)

    raw_rows: list[dict[str, object]] = []
    normalized_rows: list[dict[str, object]] = []
    for period in periods:
        report_name = _financial_report_name(period, frequency=frequency)
        row = {column: None for column in FINANCIAL_COLUMNS}
        row.update(
            {
                "ticker": ticker,
                "corp_code": ticker,
                "corp_name": name or ticker,
                "bsns_year": int(period.year),
                "reprt_code": REPORT_CODE_BY_NAME[report_name],
                "report_name": report_name,
                "fs_div": "YF",
            }
        )
        for field, aliases in FINANCIAL_FIELD_ALIASES.items():
            value = _statement_value(statements, aliases, period)
            if field == "eps" and value is not None and abs(value) > 1_000_000:
                value = None
            row[field] = value
            raw_rows.append(
                {
                    "ticker": ticker,
                    "corp_code": ticker,
                    "corp_name": name or ticker,
                    "bsns_year": int(period.year),
                    "reprt_code": row["reprt_code"],
                    "report_name": report_name,
                    "fs_div": "YF",
                    "period_end": period.strftime("%Y-%m-%d"),
                    "account_id": field,
                    "account_nm": "|".join(sorted(aliases)),
                    "amount": value,
                }
            )
        normalized_rows.append(row)

    raw = pd.DataFrame(raw_rows)
    normalized = pd.DataFrame(normalized_rows, columns=FINANCIAL_COLUMNS)
    return raw, normalized.sort_values(["ticker", "bsns_year", "reprt_code"]).reset_index(drop=True)


def fetch_us_listings(markets: list[str] | None = None, include_etfs: bool = False) -> pd.DataFrame:
    """Fetch US listed symbols from Nasdaq Trader and normalize to company schema.

    Nasdaq Trader's symbol directories include NASDAQ plus other US exchanges.
    The returned ticker format is yfinance-compatible, e.g. BRK.B becomes BRK-B.
    """
    requested_markets = {market.upper() for market in (markets or ["NASDAQ", "NYSE", "NYSEARCA", "NYSEAMERICAN"])}

    nasdaq = pd.read_csv(NASDAQ_LISTED_URL, sep="|")
    nasdaq = nasdaq[nasdaq["Symbol"].notna() & nasdaq["Symbol"].ne("File Creation Time")]
    nasdaq = nasdaq[nasdaq["Test Issue"].eq("N")]
    if not include_etfs and "ETF" in nasdaq.columns:
        nasdaq = nasdaq[nasdaq["ETF"].eq("N")]
    nasdaq_frame = pd.DataFrame(
        {
            "ticker": nasdaq["Symbol"].map(_to_yfinance_ticker),
            "name": nasdaq["Security Name"],
            "exchange": "NASDAQ",
            "sector": None,
            "industry": None,
            "market_cap": None,
            "shares_outstanding": None,
            "listing_date": None,
        }
    )

    other = pd.read_csv(OTHER_LISTED_URL, sep="|")
    other = other[other["ACT Symbol"].notna() & other["ACT Symbol"].ne("File Creation Time")]
    other = other[other["Test Issue"].eq("N")]
    if not include_etfs and "ETF" in other.columns:
        other = other[other["ETF"].eq("N")]
    other_frame = pd.DataFrame(
        {
            "ticker": other["ACT Symbol"].map(_to_yfinance_ticker),
            "name": other["Security Name"],
            "exchange": other["Exchange"].map(EXCHANGE_CODE_MAP).fillna(other["Exchange"]),
            "sector": None,
            "industry": None,
            "market_cap": None,
            "shares_outstanding": None,
            "listing_date": None,
        }
    )

    listings = pd.concat([nasdaq_frame, other_frame], ignore_index=True)
    listings = listings[listings["exchange"].str.upper().isin(requested_markets)]
    listings = listings[~listings["name"].fillna("").str.contains(NON_COMMON_SECURITY_PATTERN, case=False, regex=True)]
    listings = listings.drop_duplicates("ticker", keep="first")
    return listings[COMPANY_SCHEMA].sort_values(["exchange", "ticker"]).reset_index(drop=True)


def fetch_sp500_listings() -> pd.DataFrame:
    response = requests.get(SP500_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    response.raise_for_status()
    table = pd.read_html(StringIO(response.text))[0]
    frame = pd.DataFrame(
        {
            "ticker": table["Symbol"].map(_to_yfinance_ticker),
            "name": table["Security"],
            "exchange": "SP500",
            "sector": table["GICS Sector"],
            "industry": table["GICS Sub-Industry"],
            "market_cap": None,
            "shares_outstanding": None,
            "listing_date": table.get("Date added"),
        }
    )
    return frame[COMPANY_SCHEMA].sort_values("ticker").reset_index(drop=True)


def save_us_listings(
    markets: list[str] | None = None,
    asof: str | None = None,
    include_etfs: bool = False,
    universe: str = "listed",
) -> Path:
    ensure_project_dirs()
    asof = asof or today_yyyymmdd()
    if universe == "sp500":
        market_slug = "sp500"
        listings = fetch_sp500_listings()
    else:
        markets = markets or ["NASDAQ", "NYSE", "NYSEARCA", "NYSEAMERICAN"]
        market_slug = "_".join(market.lower() for market in markets)
        listings = fetch_us_listings(markets=markets, include_etfs=include_etfs)
    output_path = RAW_DATA_DIR / f"us_listings_{market_slug}_{asof}.csv"
    listings.to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_path


def enrich_us_listings_with_yfinance_info(
    listings_path: Path,
    output_path: Path | None = None,
    limit: int | None = None,
    offset: int = 0,
    sleep_seconds: float = 0.05,
    force: bool = False,
    workers: int = 4,
) -> YFinanceProfileFetchResult:
    """Attach yfinance .info sector/industry tags to an existing US listings CSV."""
    ensure_project_dirs()
    listings_path = Path(listings_path)
    listings = pd.read_csv(listings_path, dtype={"ticker": str})
    listings["ticker"] = listings["ticker"].map(_to_yfinance_ticker)
    work = listings.iloc[offset:].copy() if offset > 0 else listings.copy()
    if limit is not None:
        work = work.head(limit)

    output_path = output_path or listings_path.with_name(f"{listings_path.stem}_yfinfo.csv")
    cache_dir = RAW_DATA_DIR / "yfinance_profiles"
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict[str, object]] = []

    def collect_one(row: pd.Series) -> dict[str, object]:
        ticker = _to_yfinance_ticker(str(row["ticker"]))
        cache_path = _profile_cache_path(cache_dir, ticker)
        try:
            if cache_path.exists() and not force:
                profile = json.loads(cache_path.read_text(encoding="utf-8"))
                profile["status"] = "cached"
            else:
                profile = fetch_us_profile_for_ticker(ticker=ticker, name=row.get("name", ticker))
                cache_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
            profile["ticker"] = ticker
            profile["cache_path"] = str(cache_path)
            return profile
        except Exception as exc:  # noqa: BLE001 - keep the universe fetch resumable.
            return {
                "ticker": ticker,
                "name": row.get("name", ticker),
                "sector": row.get("sector"),
                "industry": row.get("industry"),
                "representative_industry": representative_us_industry(row.get("sector"), row.get("industry")),
                "market_cap": row.get("market_cap"),
                "shares_outstanding": row.get("shares_outstanding"),
                "quote_type": "",
                "status": "failed",
                "error": repr(exc),
                "cache_path": str(cache_path),
            }

    workers = max(1, workers)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(collect_one, row) for _, row in work.iterrows()]
        for counter, future in enumerate(as_completed(futures), start=1):
            profile = future.result()
            manifest_rows.append(profile)
            print(f"[{counter}/{len(work)}] {profile['ticker']} {profile['status']}", flush=True)

    profiles = pd.DataFrame(manifest_rows)
    for col in PROFILE_SCHEMA:
        if col not in profiles:
            profiles[col] = None
    profiles = profiles[PROFILE_SCHEMA].drop_duplicates("ticker", keep="last")

    enriched = listings.merge(
        profiles.drop(columns=["status", "error"], errors="ignore"),
        on="ticker",
        how="left",
        suffixes=("", "_yf"),
    )
    for col in ["name", "sector", "industry", "market_cap", "shares_outstanding"]:
        yf_col = f"{col}_yf"
        if yf_col in enriched:
            base = enriched[col] if col in enriched else pd.Series("", index=enriched.index)
            enriched[col] = base.where(base.notna() & base.astype(str).str.len().gt(0), enriched[yf_col])
            enriched = enriched.drop(columns=[yf_col])
    if "representative_industry" not in enriched:
        enriched["representative_industry"] = ""
    enriched["representative_industry"] = enriched["representative_industry"].where(
        enriched["representative_industry"].notna() & enriched["representative_industry"].astype(str).str.len().gt(0),
        [
            representative_us_industry(sector, industry)
            for sector, industry in zip(enriched.get("sector", ""), enriched.get("industry", ""))
        ],
    )
    enriched = enriched.drop_duplicates("ticker", keep="first")
    enriched.to_csv(output_path, index=False, encoding="utf-8-sig")

    manifest_path = output_path.with_name(f"{output_path.stem}_manifest.csv")
    profiles.to_csv(manifest_path, index=False, encoding="utf-8-sig")
    saved_count = int(profiles["sector"].notna().sum())
    failed_count = int(profiles["status"].eq("failed").sum()) if "status" in profiles else 0
    return YFinanceProfileFetchResult(
        output_path=output_path,
        manifest_path=manifest_path,
        requested_count=len(work),
        saved_count=saved_count,
        failed_count=failed_count,
    )


def fetch_us_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch daily US OHLCV from yfinance and normalize to project price schema."""
    yf_ticker = _to_yfinance_ticker(ticker)
    raw = yf.download(
        yf_ticker,
        start=_yyyymmdd_to_yfinance_date(start),
        end=_exclusive_end_date(end),
        auto_adjust=False,
        progress=False,
        actions=False,
        threads=False,
    )
    if raw.empty:
        return pd.DataFrame(columns=PRICE_SCHEMA)

    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)

    frame = raw.reset_index()
    frame = frame.rename(
        columns={
            "Date": "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Adj Close": "adjusted_close",
            "Volume": "volume",
        }
    )
    if "adjusted_close" not in frame.columns:
        frame["adjusted_close"] = frame["close"]
    frame["ticker"] = yf_ticker
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    return frame[PRICE_SCHEMA].sort_values(["ticker", "date"]).reset_index(drop=True)


def _normalize_yfinance_history(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=PRICE_SCHEMA)
    frame = raw.reset_index()
    frame = frame.rename(
        columns={
            "Date": "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Adj Close": "adjusted_close",
            "Volume": "volume",
        }
    )
    if "adjusted_close" not in frame.columns and "close" in frame.columns:
        frame["adjusted_close"] = frame["close"]
    required = {"date", "open", "high", "low", "close", "adjusted_close", "volume"}
    if not required.issubset(frame.columns):
        return pd.DataFrame(columns=PRICE_SCHEMA)
    frame = frame.dropna(subset=["open", "high", "low", "close"], how="all")
    if frame.empty:
        return pd.DataFrame(columns=PRICE_SCHEMA)
    frame["ticker"] = ticker
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    return frame[PRICE_SCHEMA].sort_values(["ticker", "date"]).reset_index(drop=True)


def fetch_us_ohlcv_batch(tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    tickers = [_to_yfinance_ticker(ticker) for ticker in tickers]
    raw = yf.download(
        tickers,
        start=_yyyymmdd_to_yfinance_date(start),
        end=_exclusive_end_date(end),
        auto_adjust=False,
        progress=False,
        actions=False,
        threads=True,
        group_by="ticker",
    )
    result: dict[str, pd.DataFrame] = {}
    if raw.empty:
        return {ticker: pd.DataFrame(columns=PRICE_SCHEMA) for ticker in tickers}
    if not isinstance(raw.columns, pd.MultiIndex):
        ticker = tickers[0]
        return {ticker: _normalize_yfinance_history(raw, ticker)}
    for ticker in tickers:
        if ticker in raw.columns.get_level_values(0):
            result[ticker] = _normalize_yfinance_history(raw[ticker].dropna(how="all"), ticker)
        else:
            result[ticker] = pd.DataFrame(columns=PRICE_SCHEMA)
    return result


def save_us_ohlcv(tickers: list[str], start: str, end: str) -> Path:
    ensure_project_dirs()
    frames = [fetch_us_ohlcv(ticker=ticker, start=start, end=end) for ticker in tickers]
    prices = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PRICE_SCHEMA)
    output_path = RAW_DATA_DIR / f"us_ohlcv_{start}_{end}.csv"
    prices.to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_path


def save_us_universe_ohlcv(
    start: str | None = None,
    end: str | None = None,
    markets: list[str] | None = None,
    listings_path: Path | None = None,
    sleep_seconds: float = 0.1,
    limit: int | None = None,
    offset: int = 0,
    force: bool = False,
    combine: bool = True,
    include_etfs: bool = False,
    batch_size: int = 100,
) -> UniversePriceFetchResult:
    ensure_project_dirs()
    end = end or today_yyyymmdd()
    start = start or five_years_ago_yyyymmdd(end)
    markets = markets or ["NASDAQ", "NYSE", "NYSEARCA", "NYSEAMERICAN"]
    using_external_listings = listings_path is not None
    if listings_path is not None:
        listings = pd.read_csv(listings_path, dtype={"ticker": str})
        market_slug = Path(listings_path).stem.removeprefix("us_listings_")
        if market_slug.endswith(f"_{end}"):
            market_slug = market_slug[: -(len(end) + 1)]
    else:
        market_slug = "_".join(market.lower() for market in markets)
        listings = fetch_us_listings(markets=markets, include_etfs=include_etfs)
    if offset > 0:
        listings = listings.iloc[offset:]
    if limit is not None:
        listings = listings.head(limit)

    output_listings_path = RAW_DATA_DIR / f"us_listings_{market_slug}_{end}.csv"
    if not using_external_listings:
        listings.to_csv(output_listings_path, index=False, encoding="utf-8-sig")

    price_dir = RAW_DATA_DIR / "us_ohlcv_daily" / f"{market_slug}_{start}_{end}"
    price_dir.mkdir(parents=True, exist_ok=True)

    manifest_rows: list[dict[str, object]] = []
    saved_count = 0
    failed_count = 0

    pending_rows: list[tuple[int, pd.Series, Path]] = []
    for idx, row in listings.reset_index(drop=True).iterrows():
        ticker = str(row["ticker"])
        output_path = price_dir / _ticker_filename(ticker)
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
        pending_rows.append((idx, row, output_path))

    for batch_start in range(0, len(pending_rows), batch_size):
        batch = pending_rows[batch_start : batch_start + batch_size]
        batch_tickers = [str(row["ticker"]) for _, row, _ in batch]
        try:
            batch_prices = fetch_us_ohlcv_batch(batch_tickers, start=start, end=end)
        except Exception as exc:  # noqa: BLE001 - retry individually if a batch fails.
            batch_prices = {}
            batch_error = repr(exc)
        else:
            batch_error = ""
        for idx, row, output_path in batch:
            ticker = str(row["ticker"])
            try:
                prices = batch_prices.get(ticker)
                if prices is None:
                    prices = fetch_us_ohlcv(ticker=ticker, start=start, end=end)
                prices.to_csv(output_path, index=False, encoding="utf-8-sig")
                saved_count += 1
                status = "empty" if prices.empty else "saved"
                error = batch_error if prices.empty else ""
                manifest_rows.append(
                    {
                        "ticker": ticker,
                        "name": row["name"],
                        "exchange": row["exchange"],
                        "status": status,
                        "rows": len(prices),
                        "path": str(output_path),
                        "error": error,
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
    manifest_path = RAW_DATA_DIR / f"us_ohlcv_manifest_{market_slug}_{start}_{end}.csv"
    manifest.to_csv(manifest_path, index=False, encoding="utf-8-sig")

    combined_prices_path: Path | None = None
    if combine:
        requested_paths = [Path(path) for path in manifest["path"].tolist()]
        frames = [pd.read_csv(path, dtype={"ticker": str}) for path in requested_paths if path.exists()]
        combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PRICE_SCHEMA)
        combined_prices_path = RAW_DATA_DIR / f"us_ohlcv_{market_slug}_{start}_{end}.csv"
        combined.to_csv(combined_prices_path, index=False, encoding="utf-8-sig")

    return UniversePriceFetchResult(
        listings_path=listings_path if listings_path is not None else output_listings_path,
        price_dir=price_dir,
        combined_prices_path=combined_prices_path,
        manifest_path=manifest_path,
        requested_count=len(listings),
        saved_count=saved_count,
        failed_count=failed_count,
    )


def save_us_financials(
    listings_path: Path,
    reports: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
    sleep_seconds: float = 0.1,
    force: bool = False,
    workers: int = 1,
) -> YFinanceFinancialFetchResult:
    ensure_project_dirs()
    reports = reports or ["annual", "quarterly"]
    frequencies = ["annual" if report == "annual" else "quarterly" for report in reports]
    report_slug = "_".join(dict.fromkeys(frequencies))
    listings = pd.read_csv(listings_path, dtype={"ticker": str})
    if offset > 0:
        listings = listings.iloc[offset:]
    if limit is not None:
        listings = listings.head(limit)

    cache_dir = RAW_DATA_DIR / "yfinance_financials" / report_slug
    cache_dir.mkdir(parents=True, exist_ok=True)

    tasks = [
        {
            "ticker": _to_yfinance_ticker(str(row["ticker"])),
            "name": row.get("name", row["ticker"]),
            "frequency": frequency,
        }
        for _, row in listings.iterrows()
        for frequency in frequencies
    ]

    def collect_one(task: dict[str, object]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
        ticker = str(task["ticker"])
        name = str(task["name"])
        frequency = str(task["frequency"])
        cache_path = cache_dir / f"{ticker}_{frequency}.csv"
        raw_cache_path = cache_dir / f"{ticker}_{frequency}_raw.csv"
        try:
            if cache_path.exists() and raw_cache_path.exists() and not force:
                normalized = pd.read_csv(cache_path, dtype={"ticker": str, "corp_code": str})
                raw = pd.read_csv(raw_cache_path, dtype={"ticker": str, "corp_code": str})
                status = "cached_empty" if normalized.empty else "cached"
            else:
                raw, normalized = fetch_us_financials_for_ticker(ticker=ticker, frequency=frequency, name=name)
                normalized.to_csv(cache_path, index=False, encoding="utf-8-sig")
                raw.to_csv(raw_cache_path, index=False, encoding="utf-8-sig")
                status = "empty" if normalized.empty else "saved"
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
            return raw, normalized, {
                "ticker": ticker,
                "name": name,
                "frequency": frequency,
                "status": status,
                "rows": len(normalized),
                "path": str(cache_path),
                "raw_path": str(raw_cache_path),
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001 - keep all-universe collection resumable.
            return pd.DataFrame(), pd.DataFrame(columns=FINANCIAL_COLUMNS), {
                "ticker": ticker,
                "name": name,
                "frequency": frequency,
                "status": "failed",
                "rows": 0,
                "path": str(cache_path),
                "raw_path": str(raw_cache_path),
                "error": repr(exc),
            }

    raw_frames: list[pd.DataFrame] = []
    normalized_frames: list[pd.DataFrame] = []
    manifest_rows: list[dict[str, object]] = []
    workers = max(1, workers)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(collect_one, task) for task in tasks]
        for counter, future in enumerate(as_completed(futures), start=1):
            raw, normalized, manifest_row = future.result()
            if not raw.empty:
                raw_frames.append(raw)
            if not normalized.empty:
                normalized_frames.append(normalized)
            manifest_rows.append(manifest_row)
            print(
                f"[{counter}/{len(tasks)}] {manifest_row['ticker']} "
                f"{manifest_row['frequency']} {manifest_row['status']}",
                flush=True,
            )

    raw_accounts = pd.concat(raw_frames, ignore_index=True) if raw_frames else pd.DataFrame()
    normalized = pd.concat(normalized_frames, ignore_index=True) if normalized_frames else pd.DataFrame(columns=FINANCIAL_COLUMNS)
    if not normalized.empty:
        normalized = normalized.drop_duplicates(["ticker", "bsns_year", "reprt_code", "report_name"], keep="last")
        normalized = normalized.sort_values(["ticker", "bsns_year", "reprt_code", "report_name"]).reset_index(drop=True)

    raw_accounts_path = RAW_DATA_DIR / f"yfinance_accounts_{report_slug}.csv"
    normalized_path = RAW_DATA_DIR / f"yfinance_financials_{report_slug}.csv"
    manifest_path = RAW_DATA_DIR / f"yfinance_financials_manifest_{report_slug}.csv"
    raw_accounts.to_csv(raw_accounts_path, index=False, encoding="utf-8-sig")
    normalized.to_csv(normalized_path, index=False, encoding="utf-8-sig")
    pd.DataFrame(manifest_rows).to_csv(manifest_path, index=False, encoding="utf-8-sig")

    saved_count = sum(1 for row in manifest_rows if row["rows"] > 0)
    failed_count = sum(1 for row in manifest_rows if row["status"] == "failed")
    return YFinanceFinancialFetchResult(
        raw_accounts_path=raw_accounts_path,
        normalized_financials_path=normalized_path,
        manifest_path=manifest_path,
        requested_count=len(tasks),
        saved_count=saved_count,
        failed_count=failed_count,
    )
