from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
import os
from pathlib import Path
import time
from zipfile import ZipFile

import pandas as pd
import requests

from ai_stock_assistant.config import RAW_DATA_DIR, ensure_project_dirs


BASE_URL = "https://opendart.fss.or.kr/api"
REPORT_CODES = {
    "annual": "11011",
    "q1": "11013",
    "half": "11012",
    "q3": "11014",
}
REPORT_NAMES = {value: key for key, value in REPORT_CODES.items()}
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

ACCOUNT_ALIASES = {
    "revenue": {
        "ifrs-full_Revenue",
        "ifrs-full_RevenueFromContractsWithCustomers",
        "dart_OperatingRevenue",
        "매출액",
        "영업수익",
    },
    "gross_profit": {"ifrs-full_GrossProfit", "매출총이익"},
    "operating_income": {"dart_OperatingIncomeLoss", "영업이익"},
    "net_income": {"ifrs-full_ProfitLoss", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "당기순이익"},
    "total_assets": {"ifrs-full_Assets", "자산총계"},
    "total_liabilities": {"ifrs-full_Liabilities", "부채총계"},
    "total_equity": {"ifrs-full_Equity", "자본총계"},
    "cash": {"ifrs-full_CashAndCashEquivalents", "현금및현금성자산"},
    "short_term_debt": {"ifrs-full_ShorttermBorrowings", "단기차입금", "유동성장기부채"},
    "long_term_debt": {"ifrs-full_LongtermBorrowings", "장기차입금", "사채"},
    "operating_cash_flow": {
        "ifrs-full_CashFlowsFromUsedInOperatingActivities",
        "영업활동현금흐름",
        "영업활동으로 인한 현금흐름",
    },
    "investing_cash_flow": {
        "ifrs-full_CashFlowsFromUsedInInvestingActivities",
        "투자활동현금흐름",
        "투자활동으로 인한 현금흐름",
    },
    "financing_cash_flow": {
        "ifrs-full_CashFlowsFromUsedInFinancingActivities",
        "재무활동현금흐름",
        "재무활동으로 인한 현금흐름",
    },
    "capex": {
        "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        "유형자산의 취득",
    },
    "eps": {"ifrs-full_BasicEarningsLossPerShare", "기본주당이익"},
}


@dataclass(frozen=True)
class OpenDartFetchResult:
    corp_codes_path: Path
    raw_accounts_path: Path
    normalized_financials_path: Path
    manifest_path: Path
    requested_count: int
    saved_count: int
    failed_count: int


@dataclass(frozen=True)
class OpenDartCombineResult:
    raw_accounts_path: Path
    normalized_financials_path: Path
    manifest_path: Path
    raw_rows: int
    normalized_rows: int


def get_api_key(api_key: str | None = None) -> str:
    value = api_key or os.getenv("OPENDART_API_KEY") or os.getenv("DART_API_KEY")
    if not value:
        raise ValueError("OPENDART_API_KEY environment variable is required.")
    return value


def _parse_amount(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "nan", "NaN"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _sanitize_message(message: object, api_key: str) -> str:
    return str(message).replace(api_key, "<redacted>")


def fetch_corp_codes(api_key: str | None = None) -> pd.DataFrame:
    key = get_api_key(api_key)
    response = requests.get(f"{BASE_URL}/corpCode.xml", params={"crtfc_key": key}, timeout=60)
    response.raise_for_status()
    with ZipFile(BytesIO(response.content)) as archive:
        xml_name = archive.namelist()[0]
        with archive.open(xml_name) as file:
            frame = pd.read_xml(file, dtype={"corp_code": str, "stock_code": str})
    frame["stock_code"] = frame["stock_code"].fillna("").astype(str).str.zfill(6)
    frame = frame[frame["stock_code"].str.fullmatch(r"\d{6}")]
    return frame.rename(columns={"stock_code": "ticker"})


def save_corp_codes(api_key: str | None = None) -> Path:
    ensure_project_dirs()
    output_path = RAW_DATA_DIR / "opendart_corp_codes.csv"
    fetch_corp_codes(api_key=api_key).to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_path


def load_or_fetch_corp_codes(api_key: str | None = None, force: bool = False) -> tuple[pd.DataFrame, Path]:
    path = RAW_DATA_DIR / "opendart_corp_codes.csv"
    if path.exists() and not force:
        return pd.read_csv(path, dtype={"corp_code": str, "ticker": str}), path
    path = save_corp_codes(api_key=api_key)
    return pd.read_csv(path, dtype={"corp_code": str, "ticker": str}), path


def fetch_financial_statement(
    corp_code: str,
    year: int,
    reprt_code: str,
    fs_div: str,
    api_key: str | None = None,
) -> tuple[pd.DataFrame, str, str]:
    key = get_api_key(api_key)
    params = {
        "crtfc_key": key,
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": reprt_code,
        "fs_div": fs_div,
    }
    response = requests.get(f"{BASE_URL}/fnlttSinglAcntAll.json", params=params, timeout=60)
    response.raise_for_status()
    payload = response.json()
    status = str(payload.get("status", ""))
    message = str(payload.get("message", ""))
    rows = payload.get("list") or []
    return pd.DataFrame(rows), status, message


def fetch_financial_statement_with_fallback(
    corp_code: str,
    year: int,
    reprt_code: str,
    api_key: str | None = None,
) -> tuple[pd.DataFrame, str, str, str]:
    messages = []
    for fs_div in ("CFS", "OFS"):
        frame, status, message = fetch_financial_statement(
            corp_code=corp_code,
            year=year,
            reprt_code=reprt_code,
            fs_div=fs_div,
            api_key=api_key,
        )
        if not frame.empty:
            return frame, fs_div, status, message
        messages.append(f"{fs_div}:{status}:{message}")
    return pd.DataFrame(), "NONE", status, " | ".join(messages)


def normalize_financial_accounts(raw_accounts: pd.DataFrame, listings: pd.DataFrame | None = None) -> pd.DataFrame:
    if raw_accounts.empty:
        return pd.DataFrame(columns=FINANCIAL_COLUMNS)

    frames = []
    alias_lookup = {
        alias: field
        for field, aliases in ACCOUNT_ALIASES.items()
        for alias in aliases
    }
    raw = raw_accounts.copy()
    raw["amount"] = raw["thstrm_amount"].map(_parse_amount)
    raw["field"] = raw["account_id"].map(alias_lookup).fillna(raw["account_nm"].map(alias_lookup))
    raw = raw[raw["field"].notna()]

    group_columns = ["ticker", "corp_code", "corp_name", "bsns_year", "reprt_code", "fs_div"]
    for keys, group in raw.groupby(group_columns, dropna=False):
        row = dict(zip(group_columns, keys, strict=True))
        row["report_name"] = REPORT_NAMES.get(str(row["reprt_code"]), str(row["reprt_code"]))
        for column in FINANCIAL_COLUMNS:
            row.setdefault(column, None)
        for field, values in group.groupby("field")["amount"]:
            non_null = values.dropna()
            if not non_null.empty:
                row[field] = float(non_null.iloc[0])
        frames.append(row)

    normalized = pd.DataFrame(frames, columns=FINANCIAL_COLUMNS)
    if listings is not None and "shares_outstanding" in listings.columns:
        shares = listings[["ticker", "shares_outstanding"]].copy()
        shares["ticker"] = shares["ticker"].astype(str).str.zfill(6)
        normalized = normalized.merge(shares, on="ticker", how="left")
        missing_bps = normalized["bps"].isna()
        normalized.loc[missing_bps, "bps"] = (
            normalized.loc[missing_bps, "total_equity"] / normalized.loc[missing_bps, "shares_outstanding"]
        )
        normalized = normalized.drop(columns=["shares_outstanding"])

    return normalized.sort_values(["ticker", "bsns_year", "reprt_code"]).reset_index(drop=True)


def save_korean_financials(
    listings_path: Path,
    years: list[int],
    reports: list[str] | None = None,
    api_key: str | None = None,
    sleep_seconds: float = 0.05,
    limit: int | None = None,
    force_corp_codes: bool = False,
    workers: int = 1,
) -> OpenDartFetchResult:
    ensure_project_dirs()
    key = get_api_key(api_key)
    reports = reports or ["annual"]
    report_codes = [REPORT_CODES.get(report, report) for report in reports]
    year_slug = f"{min(years)}_{max(years)}"
    report_slug = "_".join(REPORT_NAMES.get(code, code) for code in report_codes)
    cache_dir = RAW_DATA_DIR / "opendart_accounts" / f"{report_slug}_{year_slug}"
    cache_dir.mkdir(parents=True, exist_ok=True)

    listings = pd.read_csv(listings_path, dtype={"ticker": str})
    listings["ticker"] = listings["ticker"].astype(str).str.zfill(6)
    if limit is not None:
        listings = listings.head(limit)

    corp_codes, corp_codes_path = load_or_fetch_corp_codes(api_key=key, force=force_corp_codes)
    universe = listings.merge(corp_codes[["corp_code", "ticker", "corp_name"]], on="ticker", how="left")
    universe = universe[universe["corp_code"].notna()].copy()

    tasks = [
        {
            "ticker": str(stock_row["ticker"]).zfill(6),
            "corp_code": str(stock_row["corp_code"]).zfill(8),
            "corp_name": stock_row["corp_name"],
            "year": year,
            "reprt_code": reprt_code,
        }
        for _, stock_row in universe.iterrows()
        for year in years
        for reprt_code in report_codes
    ]

    def collect_one(task: dict[str, object]) -> tuple[pd.DataFrame, dict[str, object]]:
        ticker = str(task["ticker"])
        corp_code = str(task["corp_code"])
        corp_name = str(task["corp_name"])
        year = int(task["year"])
        reprt_code = str(task["reprt_code"])
        cache_path = cache_dir / f"{ticker}_{year}_{reprt_code}.csv"
        try:
            if cache_path.exists():
                accounts = pd.read_csv(cache_path, dtype={"ticker": str, "corp_code": str})
                fs_div = str(accounts["fs_div"].iloc[0]) if not accounts.empty else "NONE"
                status = "cached"
                message = ""
                row_status = "cached_empty" if accounts.empty else "cached"
            else:
                accounts, fs_div, status, message = fetch_financial_statement_with_fallback(
                    corp_code=corp_code,
                    year=year,
                    reprt_code=reprt_code,
                    api_key=key,
                )
                if not accounts.empty:
                    accounts["ticker"] = ticker
                    accounts["corp_code"] = corp_code
                    accounts["corp_name"] = corp_name
                    accounts["bsns_year"] = year
                    accounts["reprt_code"] = reprt_code
                    accounts["fs_div"] = fs_div
                    row_status = "saved"
                else:
                    if status not in {"013"}:
                        return pd.DataFrame(), {
                            "ticker": ticker,
                            "corp_code": corp_code,
                            "corp_name": corp_name,
                            "bsns_year": year,
                            "reprt_code": reprt_code,
                            "report_name": REPORT_NAMES.get(reprt_code, reprt_code),
                            "fs_div": fs_div,
                            "status": "failed",
                            "dart_status": status,
                            "message": _sanitize_message(message, key),
                            "rows": 0,
                            "path": str(cache_path),
                        }
                    accounts = pd.DataFrame(
                        columns=["ticker", "corp_code", "corp_name", "bsns_year", "reprt_code", "fs_div"]
                    )
                    row_status = "empty"
                accounts.to_csv(cache_path, index=False, encoding="utf-8-sig")
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
            return accounts, {
                "ticker": ticker,
                "corp_code": corp_code,
                "corp_name": corp_name,
                "bsns_year": year,
                "reprt_code": reprt_code,
                "report_name": REPORT_NAMES.get(reprt_code, reprt_code),
                "fs_div": fs_div,
                "status": row_status,
                "dart_status": status,
                "message": message,
                "rows": len(accounts),
                "path": str(cache_path),
            }
        except Exception as exc:  # noqa: BLE001 - keep all-universe collection resumable.
            return pd.DataFrame(), {
                "ticker": ticker,
                "corp_code": corp_code,
                "corp_name": corp_name,
                "bsns_year": year,
                "reprt_code": reprt_code,
                "report_name": REPORT_NAMES.get(reprt_code, reprt_code),
                "fs_div": "NONE",
                "status": "failed",
                "dart_status": "",
                "message": _sanitize_message(repr(exc), key),
                "rows": 0,
                "path": str(cache_path),
            }

    raw_frames: list[pd.DataFrame] = []
    manifest_rows: list[dict[str, object]] = []
    workers = max(1, workers)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(collect_one, task) for task in tasks]
        for counter, future in enumerate(as_completed(futures), start=1):
            accounts, manifest_row = future.result()
            if not accounts.empty:
                raw_frames.append(accounts)
            manifest_rows.append(manifest_row)
            print(
                f"[{counter}/{len(tasks)}] {manifest_row['ticker']} {manifest_row['bsns_year']} "
                f"{manifest_row['report_name']} {manifest_row['status']}",
                flush=True,
            )

    saved_count = sum(1 for row in manifest_rows if row["rows"] > 0)
    failed_count = sum(1 for row in manifest_rows if row["status"] == "failed")

    raw_accounts_path = RAW_DATA_DIR / f"opendart_accounts_{report_slug}_{year_slug}.csv"
    normalized_path = RAW_DATA_DIR / f"opendart_financials_{report_slug}_{year_slug}.csv"
    manifest_path = RAW_DATA_DIR / f"opendart_manifest_{report_slug}_{year_slug}.csv"

    raw_accounts = pd.concat(raw_frames, ignore_index=True) if raw_frames else pd.DataFrame()
    raw_accounts.to_csv(raw_accounts_path, index=False, encoding="utf-8-sig")
    normalize_financial_accounts(raw_accounts, listings=listings).to_csv(
        normalized_path,
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame(manifest_rows).to_csv(manifest_path, index=False, encoding="utf-8-sig")

    return OpenDartFetchResult(
        corp_codes_path=corp_codes_path,
        raw_accounts_path=raw_accounts_path,
        normalized_financials_path=normalized_path,
        manifest_path=manifest_path,
        requested_count=len(tasks),
        saved_count=saved_count,
        failed_count=failed_count,
    )


def combine_korean_financials(
    listings_path: Path,
    account_paths: list[Path],
    manifest_paths: list[Path],
    output_slug: str = "all_reports_2021_2025",
) -> OpenDartCombineResult:
    ensure_project_dirs()
    listings = pd.read_csv(listings_path, dtype={"ticker": str})
    raw_frames = [pd.read_csv(path, dtype={"ticker": str, "corp_code": str}) for path in account_paths if path.exists()]
    manifest_frames = [
        pd.read_csv(path, dtype={"ticker": str, "corp_code": str}) for path in manifest_paths if path.exists()
    ]
    raw_accounts = pd.concat(raw_frames, ignore_index=True) if raw_frames else pd.DataFrame()
    manifest = pd.concat(manifest_frames, ignore_index=True) if manifest_frames else pd.DataFrame()

    if not raw_accounts.empty:
        raw_accounts["ticker"] = raw_accounts["ticker"].astype(str).str.zfill(6)
        raw_accounts = raw_accounts.drop_duplicates(
            ["ticker", "bsns_year", "reprt_code", "fs_div", "account_id", "account_nm"],
            keep="last",
        )

    normalized = normalize_financial_accounts(raw_accounts, listings=listings)
    raw_accounts_path = RAW_DATA_DIR / f"opendart_accounts_{output_slug}.csv"
    normalized_path = RAW_DATA_DIR / f"opendart_financials_{output_slug}.csv"
    manifest_path = RAW_DATA_DIR / f"opendart_manifest_{output_slug}.csv"
    raw_accounts.to_csv(raw_accounts_path, index=False, encoding="utf-8-sig")
    normalized.to_csv(normalized_path, index=False, encoding="utf-8-sig")
    manifest.to_csv(manifest_path, index=False, encoding="utf-8-sig")

    return OpenDartCombineResult(
        raw_accounts_path=raw_accounts_path,
        normalized_financials_path=normalized_path,
        manifest_path=manifest_path,
        raw_rows=len(raw_accounts),
        normalized_rows=len(normalized),
    )


def combine_korean_financial_cache(
    listings_path: Path,
    cache_dir: Path,
    output_slug: str,
) -> OpenDartCombineResult:
    ensure_project_dirs()
    listings = pd.read_csv(listings_path, dtype={"ticker": str})
    paths = sorted(cache_dir.glob("*.csv"))
    raw_frames = []
    manifest_rows = []
    for path in paths:
        parts = path.stem.split("_")
        ticker = parts[0] if len(parts) >= 3 else ""
        year = parts[1] if len(parts) >= 3 else ""
        reprt_code = parts[2] if len(parts) >= 3 else ""
        frame = pd.read_csv(path, dtype={"ticker": str, "corp_code": str})
        if not frame.empty:
            raw_frames.append(frame)
        manifest_rows.append(
            {
                "ticker": ticker,
                "bsns_year": year,
                "reprt_code": reprt_code,
                "report_name": REPORT_NAMES.get(reprt_code, reprt_code),
                "status": "cached_empty" if frame.empty else "cached",
                "rows": len(frame),
                "path": str(path),
            }
        )

    raw_accounts = pd.concat(raw_frames, ignore_index=True) if raw_frames else pd.DataFrame()
    if not raw_accounts.empty:
        raw_accounts["ticker"] = raw_accounts["ticker"].astype(str).str.zfill(6)
        raw_accounts = raw_accounts.drop_duplicates(
            ["ticker", "bsns_year", "reprt_code", "fs_div", "account_id", "account_nm"],
            keep="last",
        )
    normalized = normalize_financial_accounts(raw_accounts, listings=listings)

    raw_accounts_path = RAW_DATA_DIR / f"opendart_accounts_{output_slug}.csv"
    normalized_path = RAW_DATA_DIR / f"opendart_financials_{output_slug}.csv"
    manifest_path = RAW_DATA_DIR / f"opendart_manifest_{output_slug}.csv"
    raw_accounts.to_csv(raw_accounts_path, index=False, encoding="utf-8-sig")
    normalized.to_csv(normalized_path, index=False, encoding="utf-8-sig")
    pd.DataFrame(manifest_rows).to_csv(manifest_path, index=False, encoding="utf-8-sig")

    return OpenDartCombineResult(
        raw_accounts_path=raw_accounts_path,
        normalized_financials_path=normalized_path,
        manifest_path=manifest_path,
        raw_rows=len(raw_accounts),
        normalized_rows=len(normalized),
    )
