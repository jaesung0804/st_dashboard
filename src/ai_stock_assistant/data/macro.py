from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

import FinanceDataReader as fdr
import numpy as np
import pandas as pd

from ai_stock_assistant.config import RAW_DATA_DIR, ensure_project_dirs


@dataclass(frozen=True)
class MacroFetchResult:
    long_path: Path
    wide_path: Path
    feature_path: Path
    manifest_path: Path
    requested_count: int
    saved_count: int
    failed_count: int


MACRO_SERIES = [
    {"code": "^NYICDX", "name": "dollar_index", "kind": "market"},
    {"code": "US5YT", "name": "us5y_yield", "kind": "yield"},
    {"code": "US10YT", "name": "us10y_yield", "kind": "yield"},
    {"code": "US30YT", "name": "us30y_yield", "kind": "yield"},
    {"code": "FRED:T10Y2Y", "name": "us10y2y_spread", "kind": "level"},
    {"code": "USD/KRW", "name": "usd_krw", "kind": "fx"},
    {"code": "USD/EUR", "name": "usd_eur", "kind": "fx"},
    {"code": "USD/CNY", "name": "usd_cny", "kind": "fx"},
    {"code": "USD/JPY", "name": "usd_jpy", "kind": "fx"},
    {"code": "BTC/USD", "name": "btc_usd", "kind": "crypto"},
    {"code": "ETH/USD", "name": "eth_usd", "kind": "crypto"},
    {"code": "CL=F", "name": "wti_crude", "kind": "commodity"},
    {"code": "BZ=F", "name": "brent_crude", "kind": "commodity"},
    {"code": "NG=F", "name": "natural_gas", "kind": "commodity"},
    {"code": "GC=F", "name": "gold", "kind": "commodity"},
    {"code": "SI=F", "name": "silver", "kind": "commodity"},
    {"code": "HG=F", "name": "copper", "kind": "commodity"},
    {"code": "FRED:M2SL", "name": "m2_money_stock", "kind": "fred"},
    {"code": "FRED:ICSA", "name": "initial_jobless_claims", "kind": "fred"},
    {"code": "FRED:UMCSENT", "name": "consumer_sentiment", "kind": "fred"},
    {"code": "FRED:UNRATE", "name": "unemployment_rate", "kind": "fred"},
]


def _value_column(frame: pd.DataFrame, code: str) -> str | None:
    for col in ["Close", "Adj Close", code.removeprefix("FRED:")]:
        if col in frame.columns:
            return col
    numeric = frame.select_dtypes(include="number").columns.tolist()
    return numeric[0] if numeric else None


def _fetch_one_series(spec: dict[str, str], start: str, end: str) -> tuple[pd.DataFrame, dict[str, object]]:
    code = spec["code"]
    name = spec["name"]
    try:
        raw = fdr.DataReader(code, start, end)
        value_col = _value_column(raw, code)
        if raw.empty or value_col is None:
            raise ValueError("empty series")
        frame = raw[[value_col]].rename(columns={value_col: "value"}).reset_index()
        frame = frame.rename(columns={frame.columns[0]: "date"})
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        frame["series"] = name
        frame["code"] = code
        frame["kind"] = spec["kind"]
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        frame = frame.dropna(subset=["date", "value"]).sort_values("date")
        start_date = pd.Timestamp(start).normalize()
        end_date = pd.Timestamp(end).normalize()
        frame = frame[(frame["date"] >= start_date) & (frame["date"] <= end_date)]
        if frame.empty:
            raise ValueError("empty series after date filtering")
        return frame[["date", "series", "code", "kind", "value"]], {
            "code": code,
            "series": name,
            "kind": spec["kind"],
            "status": "saved",
            "rows": len(frame),
            "start": frame["date"].min().strftime("%Y-%m-%d"),
            "end": frame["date"].max().strftime("%Y-%m-%d"),
            "error": "",
        }
    except Exception as exc:  # noqa: BLE001 - keep macro collection resumable.
        return pd.DataFrame(columns=["date", "series", "code", "kind", "value"]), {
            "code": code,
            "series": name,
            "kind": spec["kind"],
            "status": "failed",
            "rows": 0,
            "start": "",
            "end": "",
            "error": repr(exc),
        }


def build_macro_feature_frame(wide: pd.DataFrame) -> pd.DataFrame:
    frame = wide.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame = frame.sort_values("date").set_index("date")
    full_index = pd.date_range(frame.index.min(), frame.index.max(), freq="D")
    frame = frame.reindex(full_index).ffill()
    feature_parts: dict[str, pd.Series] = {}
    for col in frame.columns:
        series = pd.to_numeric(frame[col], errors="coerce")
        feature_parts[f"macro_{col}"] = series
        feature_parts[f"macro_{col}_chg_1d"] = series.pct_change(1)
        feature_parts[f"macro_{col}_chg_5d"] = series.pct_change(5)
        feature_parts[f"macro_{col}_chg_20d"] = series.pct_change(20)
        feature_parts[f"macro_{col}_chg_60d"] = series.pct_change(60)
        feature_parts[f"macro_{col}_diff_20d"] = series.diff(20)
        rolling_mean = series.rolling(252, min_periods=60).mean()
        rolling_std = series.rolling(252, min_periods=60).std()
        feature_parts[f"macro_{col}_z_252d"] = (series - rolling_mean) / rolling_std.replace(0, np.nan)
    features = pd.DataFrame(feature_parts, index=frame.index)
    features = features.replace([np.inf, -np.inf], np.nan).reset_index().rename(columns={"index": "date"})
    features["date"] = pd.to_datetime(features["date"]).dt.strftime("%Y-%m-%d")
    return features


def fetch_macro_indicators(start: str, end: str, sleep_seconds: float = 0.2) -> MacroFetchResult:
    ensure_project_dirs()
    frames: list[pd.DataFrame] = []
    manifest_rows: list[dict[str, object]] = []
    for spec in MACRO_SERIES:
        frame, row = _fetch_one_series(spec, start=start, end=end)
        frames.append(frame)
        manifest_rows.append(row)
        print(f"{row['code']} {row['status']} rows={row['rows']}", flush=True)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    frames = [frame for frame in frames if not frame.empty]
    long = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series", "code", "kind", "value"])
    wide = long.pivot_table(index="date", columns="series", values="value", aggfunc="last").sort_index().reset_index()
    features = build_macro_feature_frame(wide) if not wide.empty else pd.DataFrame(columns=["date"])

    slug = f"{start.replace('-', '')}_{end.replace('-', '')}"
    long_path = RAW_DATA_DIR / f"macro_indicators_long_{slug}.csv"
    wide_path = RAW_DATA_DIR / f"macro_indicators_wide_{slug}.csv"
    feature_path = RAW_DATA_DIR / f"macro_features_{slug}.csv"
    manifest_path = RAW_DATA_DIR / f"macro_indicators_manifest_{slug}.csv"
    long.to_csv(long_path, index=False, encoding="utf-8-sig")
    wide.to_csv(wide_path, index=False, encoding="utf-8-sig")
    features.to_csv(feature_path, index=False, encoding="utf-8-sig")
    manifest = pd.DataFrame(manifest_rows)
    manifest.to_csv(manifest_path, index=False, encoding="utf-8-sig")

    return MacroFetchResult(
        long_path=long_path,
        wide_path=wide_path,
        feature_path=feature_path,
        manifest_path=manifest_path,
        requested_count=len(MACRO_SERIES),
        saved_count=int(manifest["status"].eq("saved").sum()) if not manifest.empty else 0,
        failed_count=int(manifest["status"].eq("failed").sum()) if not manifest.empty else 0,
    )
