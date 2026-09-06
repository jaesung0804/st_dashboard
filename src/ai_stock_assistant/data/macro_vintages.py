"""Small FRED-MD vintage features with conservative, explicit availability.

Monthly archive dates are not exact intraday release timestamps. A vintage is
therefore first usable on the first day TWO months after its named month (and
only on later signal dates). Historical revisions stay inside that vintage.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from .. import monthly_ews as live

SOURCE = "https://www.stlouisfed.org/research/economists/mccracken/fred-databases"
BASE = "https://www.stlouisfed.org/-/media/project/frbstl/stlouisfed/research/fred-md/"
ARCHIVE = BASE + "historical-vintages-of-fred-md-2015-01-to-2025-12.zip"
POLICY = "fred-md-monthly-vintage-plus-two-month-start-strict-prior-v1"
FEATURES = ["macro_policy_rate", "macro_term_spread", "macro_inflation_yoy",
            "macro_inflation_change3", "macro_unemployment", "macro_unemployment_change3",
            "macro_industrial_yoy", "macro_payroll_yoy", "macro_housing_yoy", "macro_money_yoy"]
SERIES = ["FEDFUNDS", "GS10", "TB3MS", "CPIAUCSL", "UNRATE", "INDPRO", "PAYEMS", "HOUST", "M2SL"]


def vintage_features(body: bytes, month: str, source: str) -> dict:
    period = pd.Period(month, freq="M")
    data = pd.read_csv(io.BytesIO(body))
    # The first FRED-MD row contains transformation codes, not an observation.
    dates = pd.to_datetime(data["sasdate"], format="%m/%d/%Y", errors="coerce")
    data = data.assign(date=dates).dropna(subset=["date"]).set_index("date")
    data = data.loc[data.index <= period.end_time, SERIES].apply(pd.to_numeric, errors="coerce")
    data.index = data.index.to_period("M")
    if data.index.has_duplicates:
        raise ValueError(f"Duplicate observations in vintage {month}")
    data = data.reindex(pd.period_range(data.index.min(), period, freq="M"))
    last_dates = {c: data[c].last_valid_index() for c in SERIES}
    # Official publication interruptions can leave a series several months old
    # inside a fresh vintage (e.g. housing in late 2025). Keep its real last
    # observation month; never backfill it with a later release.
    if any(d is None or period.ordinal - d.ordinal > 6 for d in last_dates.values()):
        raise ValueError(f"Missing or stale required series in vintage {month}")

    def last(series):
        return float(series.dropna().iloc[-1])

    def growth(name, lag):
        return data[name] / data[name].shift(lag) - 1

    inflation = growth("CPIAUCSL", 12) * 100
    values = [last(data["FEDFUNDS"]), last(data["GS10"] - data["TB3MS"]), last(inflation),
              last(inflation - inflation.shift(3)), last(data["UNRATE"]),
              last(data["UNRATE"] - data["UNRATE"].shift(3)),
              *[last(growth(c, 12)) for c in ("INDPRO", "PAYEMS", "HOUST", "M2SL")]]
    if not np.isfinite(values).all():
        raise ValueError(f"Non-finite macro inputs for {month}")
    return {"vintage_month": month, "available_date": str((period + 2).start_time.date()),
            "policy": POLICY, "source": source, "source_sha256": hashlib.sha256(body).hexdigest(),
            "observation_months": {c: str(d) for c, d in last_dates.items()},
            **dict(zip(FEATURES, values))}


def download(url: str, maximum: int) -> bytes:
    # Bounded requests; no per-series or per-day fallback loops.
    request = Request(url, headers={"User-Agent": "st_dashboard FRED-MD monthly research"})
    with urlopen(request, timeout=45) as response:
        body = response.read(maximum + 1)
    if len(body) > maximum:
        raise ValueError("Macro response exceeds configured size")
    if body.lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise ValueError("Macro provider returned HTML instead of data")
    return body


def collect(root: Path, asof: str | None = None, archive_file: Path | None = None) -> dict:
    today = pd.Timestamp(asof or pd.Timestamp.now(tz="UTC").date()).normalize().tz_localize(None)
    latest = today.to_period("M") - 2
    months = [str(m) for m in pd.period_range("2020-01", latest, freq="M")]
    folder = root / "macro" / "vintages"
    folder.mkdir(parents=True, exist_ok=True)
    missing = [m for m in months if not (folder / f"{m}.json").exists()]
    archived = [m for m in missing if m <= "2025-12"]
    if archived:
        body = archive_file.read_bytes() if archive_file else download(ARCHIVE, 60_000_000)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            names = {}
            for name in archive.namelist():
                match = re.search(r"(20\d{2})[-m](\d{2})", name)
                if match and name.endswith(".csv"):
                    month = f"{match[1]}-{match[2]}"
                    if month in names:
                        raise ValueError(f"Ambiguous FRED-MD archive member: {month}")
                    names[month] = name
            for month in archived:
                name = names[month]
                if archive.getinfo(name).file_size > 2_000_000:
                    raise ValueError("Unexpected archive member size")
                live.write_json(folder / f"{month}.json", vintage_features(archive.read(name), month, ARCHIVE + "#" + name))
    for month in (m for m in missing if m > "2025-12"):
        url = BASE + f"monthly/{month}-md.csv"
        live.write_json(folder / f"{month}.json", vintage_features(download(url, 2_000_000), month, url))
    rows = [live.read_json(folder / f"{month}.json") for month in months]
    if any(row["policy"] != POLICY for row in rows):
        raise ValueError("Cached macro availability policy differs")
    metadata = {"provider": "Federal Reserve Bank of St. Louis FRED-MD",
                "source": SOURCE, "policy": POLICY, "features": FEATURES, "series": SERIES,
                "scope": "US macroeconomic and global monetary context; not Korean domestic macro",
                "latest_vintage": months[-1], "vintages": len(rows),
                "collected_at": live.utc_now(), "earliest_available": rows[0]["available_date"],
                "cache_sha256": {m: live.digest(folder / f"{m}.json") for m in months}}
    live.write_json(root / "macro" / "manifest.json", metadata)
    print(f"Macro: {len(rows)} immutable monthly vintages, through {months[-1]}", flush=True)
    return metadata


def load(root: Path) -> tuple[pd.DataFrame, dict]:
    metadata = live.read_json(root / "macro" / "manifest.json")
    if metadata["policy"] != POLICY or metadata["features"] != FEATURES:
        raise ValueError("Macro feature/policy contract changed")
    rows = []
    for month, sha in metadata["cache_sha256"].items():
        file = root / "macro" / "vintages" / f"{month}.json"
        if live.digest(file) != sha:
            raise ValueError(f"Macro vintage checksum mismatch: {month}")
        rows.append(live.read_json(file))
    frame = pd.DataFrame(rows)
    frame["available_date"] = pd.to_datetime(frame["available_date"])
    return frame.sort_values("available_date"), metadata


def attach(panel: pd.DataFrame, vintages: pd.DataFrame) -> pd.DataFrame:
    result = pd.merge_asof(panel.sort_values("date"), vintages[["available_date", "vintage_month", *FEATURES]],
                           left_on="date", right_on="available_date", direction="backward", allow_exact_matches=False)
    if result[FEATURES].isna().any().any():
        raise ValueError("Price-only and macro comparisons require the same complete vintage-covered rows")
    # If a new monthly file is unavailable, fail visibly rather than forward-fill forever.
    if ((result["date"] - result["available_date"]).dt.days > 65).any():
        raise ValueError("Macro vintage cache is stale for a requested signal date")
    return result.sort_values(["date", "ticker"]).reset_index(drop=True)
