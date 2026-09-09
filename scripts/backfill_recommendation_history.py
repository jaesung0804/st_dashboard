"""Fill missing observed sessions without rewriting the live forecast ledger.

The output is explicitly retrospective.  Existing live, delayed, legacy, and
reconstructed records are immutable and always take precedence.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_stock_assistant import monthly_ews as live
try:
    from reconstruct_market_history import prediction_rows
except ModuleNotFoundError:  # Imported as scripts.backfill_recommendation_history in tests.
    from scripts.reconstruct_market_history import prediction_rows


DEFAULT_START = "2026-08-01"
LIVE_ROOT = Path("data/dashboard_ews")
RECONSTRUCTION_ROOT = Path("data/dashboard_research/reconstruction")
RAW_ROOT = Path("data/raw")


def complete_sessions(prices: pd.DataFrame, start: str, end: str | None) -> list[pd.Timestamp]:
    """Return retained market sessions, refusing a suspicious partial close."""
    start_at = pd.Timestamp(start)
    latest = prices["date"].max()
    end_at = min(pd.Timestamp(end), latest) if end else latest
    if pd.isna(latest) or end_at < start_at:
        return []
    counts = prices.groupby("date")["ticker"].nunique().sort_index()
    sessions = list(counts.loc[counts.index.to_series().between(start_at, end_at)].index)
    for day in sessions:
        prior = counts.loc[counts.index < day].tail(20)
        if not prior.empty and counts.loc[day] < 0.8 * prior.median():
            raise ValueError(f"Incomplete prices on {day.date()}: {counts.loc[day]} rows")
    return sessions


def archived_dates(market: str) -> set[str]:
    """Validate and enumerate every stored forecast kind."""
    dates = set()
    for path in (LIVE_ROOT / market / "legacy").glob("*.json"):
        live.read_json(path)
        dates.add(path.stem)
    for root, expected_kind in (
        (LIVE_ROOT / market / "predictions", None),
        (RECONSTRUCTION_ROOT / market / "predictions", "reconstructed"),
    ):
        for path in root.glob("*/rows.json"):
            meta = live.verify_prediction(path.parent)
            if expected_kind and meta.get("prediction_kind") != expected_kind:
                raise ValueError(f"Unexpected reconstruction kind: {path.parent}")
            dates.add(path.parent.name)
    return dates


def model_for(market: str, month: str):
    candidates = [
        LIVE_ROOT / market / "models" / month,
        RECONSTRUCTION_ROOT / market / "models" / month,
    ]
    for folder in candidates:
        if not folder.exists():
            continue
        card, boosters = live.load_month(folder)
        if card["market"] != market or card["month"] != month:
            raise ValueError(f"Model identity mismatch: {folder}")
        if pd.Timestamp(card["cutoff"]) >= pd.Timestamp(month + "-01"):
            raise ValueError(f"Future training cutoff in {folder}")
        return folder, card, boosters
    raise FileNotFoundError(f"No frozen {market} model for {month}; refusing to train during backfill")


def production_sentinel(market: str, months: set[str], end: pd.Timestamp) -> dict[str, Path]:
    """Find one immutable production forecast per model month for reproduction."""
    sentinels = {}
    for path in sorted((LIVE_ROOT / market / "predictions").glob("*/rows.json"), reverse=True):
        signal = path.parent.name
        if signal[:7] in months and pd.Timestamp(signal) <= end and signal[:7] not in sentinels:
            live.verify_prediction(path.parent)
            sentinels[signal[:7]] = path
    return sentinels


def assert_reproduced(rows: list[dict], stored_path: Path) -> None:
    stored = {row["ticker"]: row for row in live.read_json(stored_path)}
    calculated = {row["ticker"]: row for row in rows}
    if stored.keys() != calculated.keys():
        raise AssertionError(f"Backfill universe differs from {stored_path}")
    for ticker, row in calculated.items():
        old = stored[ticker]
        if abs(row["upProb"] - old["upProb"]) > 0.000002 or abs(row["downProb"] - old["downProb"]) > 0.000002:
            raise AssertionError(f"Backfill probabilities differ from {stored_path}: {ticker}")


def run_market(market: str, start: str, end: str | None) -> dict:
    raw = RAW_ROOT / live.PRICE_FILES[market]
    listings_path = RAW_ROOT / live.LISTING_FILES[market]
    prices = live.read_prices(raw, end)
    sessions = complete_sessions(prices, start, end)
    if not sessions:
        raise ValueError(f"No retained {market} sessions from {start}")
    covered_before = archived_dates(market)
    missing = [day for day in sessions if day.strftime("%Y-%m-%d") not in covered_before]
    result = {
        "market": market,
        "start": start,
        "requested_end": end,
        "latest_session": sessions[-1].strftime("%Y-%m-%d"),
        "observed_sessions": len(sessions),
        "covered_before": len(sessions) - len(missing),
        "created_dates": [],
        "verification_dates": [],
    }
    if not missing:
        print(f"{market}: all {len(sessions)} observed sessions already archived", flush=True)
        return result

    missing_months = {day.strftime("%Y-%m") for day in missing}
    sentinels = production_sentinel(market, missing_months, sessions[-1])
    panel_dates = sorted(set(missing) | {pd.Timestamp(path.parent.name) for path in sentinels.values()})
    models = {month: model_for(market, month) for month in {day.strftime("%Y-%m") for day in panel_dates}}
    print(f"{market}: building causal features for {len(missing)} missing session(s)", flush=True)
    panel = live.feature_panel(prices, market, training=False, signal_dates=panel_dates)
    listings = (
        pd.read_csv(listings_path, dtype={"ticker": str})
        .fillna("")
        .drop_duplicates("ticker")
        .set_index("ticker")
        .to_dict("index")
    )
    created_at = live.utc_now()
    source_hash = live.digest(raw)
    missing_names = {day.strftime("%Y-%m-%d") for day in missing}
    sentinel_paths = {path.parent.name: path for path in sentinels.values()}

    for date, frame in panel.groupby("date", sort=True):
        signal = date.strftime("%Y-%m-%d")
        folder, card, boosters = models[signal[:7]]
        frame = frame.reset_index(drop=True)
        rows, _ = prediction_rows(frame, card, boosters, listings, market, signal, created_at)
        if signal in sentinel_paths:
            assert_reproduced(rows, sentinel_paths[signal])
            result["verification_dates"].append(signal)
        if signal not in missing_names:
            continue
        feature_hash = hashlib.sha256(
            pd.util.hash_pandas_object(frame[["date", "ticker", *live.FEATURES]], index=False).to_numpy().tobytes()
        ).hexdigest()
        live.freeze_prediction(
            RECONSTRUCTION_ROOT,
            market,
            signal,
            rows,
            {
                "model": card["id"],
                "created_at": created_at,
                "prediction_kind": "reconstructed",
                "training_cutoff": card["cutoff"],
                "model_path": str(folder),
                "feature_sha256": feature_hash,
                "rows": len(rows),
                "source_price_sha256": source_hash,
                "limitations": [
                    "Retrospective generation after the signal date; not a contemporaneous live forecast.",
                    "Uses retained adjusted prices and current listings; historical constituent or revision bias may remain.",
                ],
            },
        )
        result["created_dates"].append(signal)
        print(f"{market}: reconstructed {signal} ({len(rows)} rows)", flush=True)

    missing_after = [day.strftime("%Y-%m-%d") for day in sessions if day.strftime("%Y-%m-%d") not in archived_dates(market)]
    if missing_after:
        raise AssertionError(f"Unfilled {market} sessions: {missing_after}")
    if not result["verification_dates"]:
        raise AssertionError(f"No production sentinel available for {market} backfill")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--market", choices=["all", "kr", "us"], default="all")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=None, help="Inclusive date; defaults to each market's latest retained session")
    args = parser.parse_args()
    markets = ["kr", "us"] if args.market == "all" else [args.market]
    summary = {"created_at": live.utc_now(), "markets": [run_market(market, args.start, args.end) for market in markets]}
    live.write_json(Path(".work/recommendation-history-backfill.json"), summary)
    print("RECOMMENDATION HISTORY COMPLETE", summary, flush=True)


if __name__ == "__main__":
    main()
