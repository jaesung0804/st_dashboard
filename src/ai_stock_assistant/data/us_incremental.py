"""Validate US range updates before atomically replacing retained prices.

An unavailable ticker is not proof of a market-wide outage. Keep its history,
but require fresh provider coverage of the previously active universe. Never
invent holiday/suspension bars or splice a changed adjustment scale into history.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import time

import numpy as np
import pandas as pd

from ai_stock_assistant.data.krx_incremental import _atomic_bytes, _atomic_csv, _json_bytes
from ai_stock_assistant.data.us import PRICE_SCHEMA, _to_yfinance_ticker


def validate_prices(frame: pd.DataFrame, ticker: str, start: str, end: str,
                    *, diagnostics: dict | None = None) -> pd.DataFrame:
    if not set(PRICE_SCHEMA).issubset(frame.columns):
        raise ValueError("Missing US OHLCV columns")
    frame = frame[PRICE_SCHEMA].copy()
    frame["ticker"] = frame["ticker"].map(_to_yfinance_ticker)
    dates = pd.to_datetime(frame["date"], errors="raise")
    if not frame.ticker.eq(ticker).all() or not dates.between(pd.Timestamp(start), pd.Timestamp(end)).all():
        raise ValueError("Wrong US ticker or price date range")
    numeric = PRICE_SCHEMA[2:]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="raise")

    # Yahoo can publish the newest raw close before its adjusted-close field is
    # populated.  The latest adjusted close is on its own current scale, so the
    # raw close is a safe temporary value for that row only.  Older missing
    # adjusted values are withheld rather than filled across a corporate action.
    finite_close = np.isfinite(frame["close"].to_numpy(dtype=float)) & frame["close"].gt(0)
    latest = dates.max()
    latest_adjusted_missing = dates.eq(latest) & finite_close & ~np.isfinite(
        frame["adjusted_close"].to_numpy(dtype=float)
    )
    frame.loc[latest_adjusted_missing, "adjusted_close"] = frame.loc[latest_adjusted_missing, "close"]

    # A row without a usable close/adjusted close/volume cannot contribute a
    # tradeable observation.  Dropping it lets the overlap and market-coverage
    # guards below decide whether the ticker or the whole batch must be withheld.
    required = frame[["close", "adjusted_close", "volume"]]
    invalid_required = (~np.isfinite(required.to_numpy(dtype=float))).any(axis=1)
    invalid_required |= frame["close"].le(0) | frame["adjusted_close"].le(0) | frame["volume"].lt(0)
    if diagnostics is not None and pd.notna(latest):
        invalid_fields = []
        for column in required:
            values = frame.loc[dates.eq(latest), column]
            invalid = ~np.isfinite(values.to_numpy(dtype=float))
            invalid |= (values.lt(0) if column == "volume" else values.le(0)).to_numpy()
            if invalid.any():
                invalid_fields.append(column)
        diagnostics.update(received_latest_date=latest.strftime("%Y-%m-%d"),
                           latest_invalid_fields=invalid_fields,
                           discarded_required_rows=int(invalid_required.sum()),
                           latest_adjusted_close_filled=int(latest_adjusted_missing.sum()))
    if invalid_required.any():
        frame = frame.loc[~invalid_required].copy()
        dates = dates.loc[~invalid_required]
    if frame.empty:
        raise ValueError("No usable US close/adjusted-close/volume observations")

    ohl = frame[["open", "high", "low"]]
    finite_ohl = np.isfinite(ohl.to_numpy(dtype=float))
    if ((ohl < 0) & finite_ohl).any().any():
        raise ValueError("Negative US intraday price")
    # Partial intraday fields are unavailable observations, not a reason to
    # discard an otherwise valid close.  Preserve them as NaN so range features
    # remain missing instead of fabricating a zero-volatility session.
    frame[["open", "high", "low"]] = ohl.where(finite_ohl, np.nan)
    ohl = frame[["open", "high", "low"]]
    complete_ohl = ohl.gt(0).all(axis=1) & ohl.notna().all(axis=1)
    traded = frame.volume.gt(0) & complete_ohl
    # Allow rounding noise in independently represented OHLC fields.
    tolerance = frame.close.abs() * 1e-5 + 1e-6
    if ((ohl.loc[traded] <= 0).any().any()
            or (frame.high[traded] + tolerance[traded] < frame.loc[traded, ["open", "close", "low"]].max(axis=1)).any()
            or (frame.low[traded] - tolerance[traded] > frame.loc[traded, ["open", "close", "high"]].min(axis=1)).any()):
        raise ValueError("Invalid traded US price range")
    frame["date"] = dates.dt.strftime("%Y-%m-%d")
    if frame.duplicated(["ticker", "date"]).any():
        raise ValueError("Duplicate US price observations")
    return frame.sort_values("date").reset_index(drop=True)


def refresh_ranges(*, listings_path: Path, prices_path: Path, output_path: Path,
                   requested_asof: str, run_dir: Path, fetch, overlap_days: int = 10,
                   batch_size: int = 100, minimum_coverage: float = .95,
                   max_seconds: float = 3600):
    from ai_stock_assistant.data.refresh import USDailyRefreshResult

    if overlap_days < 1 or not 1 <= batch_size <= 100 or not 0 < minimum_coverage <= 1 or max_seconds <= 0:
        raise ValueError("Invalid US collection limits")
    requested = pd.Timestamp(requested_asof).normalize()
    end = requested.strftime("%Y%m%d")
    listings = pd.read_csv(listings_path, dtype={"ticker": str})
    tickers = sorted(set(listings.ticker.dropna().map(_to_yfinance_ticker)) - {""})
    if not tickers:
        raise ValueError("No restored US universe")
    existing = pd.read_csv(prices_path, dtype={"ticker": str})
    if not set(PRICE_SCHEMA).issubset(existing.columns):
        raise ValueError("Restored US prices lack required columns")
    existing["ticker"] = existing.ticker.map(_to_yfinance_ticker)
    existing["date"] = pd.to_datetime(existing.date, errors="raise").dt.strftime("%Y-%m-%d")
    if existing.duplicated(["ticker", "date"]).any():
        raise ValueError("Restored US prices have duplicate keys; refusing a lossy merge")
    history = existing.loc[existing.date.le(requested.strftime("%Y-%m-%d"))]
    histories = dict(tuple(history.groupby("ticker", sort=False)))
    sessions = sorted(history.date.unique())
    # Repeated partial updates must not shrink coverage to yesterday's survivors.
    recent_floor = (pd.Timestamp(sessions[-1]) - pd.Timedelta(days=45)).strftime("%Y-%m-%d") if sessions else ""
    recent = history.loc[history.date.isin(sessions[-21:]) & history.date.ge(recent_floor) & history.volume.gt(0)]
    active = set(recent.ticker) & set(tickers)
    active = active or set(tickers)
    starts = {
        ticker: (pd.Timestamp(histories[ticker].date.max()) - pd.Timedelta(days=overlap_days)).strftime("%Y%m%d")
        if ticker in histories else (requested - pd.DateOffset(years=5)).strftime("%Y%m%d")
        for ticker in tickers
    }
    deadline = time.monotonic() + max_seconds
    updates, errors, received = {}, {}, {}
    attempts = defaultdict(int)
    adjustment_detected, rebase_updated, rebase_quarantined = set(), set(), set()
    fatal = None

    def download(queries, result=None, *, full_history=False):
        groups = defaultdict(list)
        for ticker, start in queries.items():
            groups[start].append(ticker)
        result = {} if result is None else result
        for start, symbols in sorted(groups.items()):
            for offset in range(0, len(symbols), batch_size):
                pending = symbols[offset:offset + batch_size]
                recovered = 0
                for attempt in range(2):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("US collection scheduling budget exhausted")
                    try:
                        for ticker in pending:
                            attempts[ticker] += 1
                        response = fetch(pending, start=start, end=end)
                    except Exception as exc:
                        response = {}
                        for ticker in pending:
                            errors[ticker] = f"Provider request failed: {type(exc).__name__}"
                    retry = []
                    for ticker in pending:
                        frame = response.get(ticker)
                        if frame is None or frame.empty:
                            errors.setdefault(ticker, "No provider observations; history retained")
                            retry.append(ticker)
                            continue
                        diagnostic = {}
                        try:
                            validated = validate_prices(frame, ticker, start, end, diagnostics=diagnostic)
                            old = histories.get(ticker)
                            if old is not None:
                                required = old if full_history else old.loc[old.date.ge(pd.Timestamp(start).strftime("%Y-%m-%d"))]
                                if not set(required.date).issubset(set(validated.date)):
                                    raise ValueError("Incomplete overlapping history; ticker update withheld")
                            result[ticker] = validated
                        except ValueError as exc:
                            errors[ticker] = str(exc)
                            retry.append(ticker)
                        else:
                            recovered += 1
                            errors.pop(ticker, None)
                        finally:
                            # Retain evidence of a newer incomplete session even
                            # when validation removes its unusable price row.
                            # A retry at the same date can clear an earlier defect.
                            if diagnostic and diagnostic["received_latest_date"] >= received.get(ticker, {}).get("received_latest_date", ""):
                                received[ticker] = diagnostic
                    pending = retry
                    if not pending:
                        break
                print(f"US range {start}..{end}: {recovered}/{min(batch_size, len(symbols) - offset)} tickers", flush=True)
        return result

    try:
        download(starts, updates)
        rebases = {}
        for ticker, frame in list(updates.items()):
            old = histories.get(ticker)
            if old is None or old.empty:
                continue
            overlap = old.merge(frame, on="date", suffixes=("_old", "_new"))
            changed = any(
                (~np.isclose(overlap[f"{column}_old"], overlap[f"{column}_new"], rtol=1e-4, atol=1e-6)).any()
                for column in ("close", "adjusted_close")
            )
            if changed:
                # Never leave retained future rows on a different scale after a
                # manually requested historical refresh.
                if existing.loc[existing.ticker.eq(ticker), "date"].max() > requested.strftime("%Y-%m-%d"):
                    raise ValueError(f"{ticker}: rebase must include all retained dates")
                adjustment_detected.add(ticker)
                if not set(old.date).issubset(set(frame.date)):
                    rebases[ticker] = old.date.min().replace("-", "")
                else:
                    rebase_updated.add(ticker)
        full = download(rebases, full_history=True) if rebases else {}
        for ticker in rebases:
            frame = full.get(ticker)
            if frame is None or not set(histories[ticker].date).issubset(set(frame.date)):
                # Never merge a partial rebase.  Retaining the old internally
                # consistent history and omitting this ticker from today's
                # update is safe; the market-wide coverage guard still rejects
                # the batch if this is more than an isolated exception.
                updates.pop(ticker, None)
                rebase_quarantined.add(ticker)
                errors[ticker] = "Adjusted prices changed; complete rebase unavailable; retained history quarantined"
                continue
            updates[ticker] = frame
            rebase_updated.add(ticker)
        if time.monotonic() >= deadline:
            raise TimeoutError("US collection scheduling budget exhausted")
    except Exception as exc:
        fatal = exc

    update = pd.concat(updates.values(), ignore_index=True) if updates else pd.DataFrame(columns=PRICE_SCHEMA)
    validated_latest = pd.to_datetime(update.date).max()
    observed_dates = [pd.Timestamp(item["received_latest_date"]) for item in received.values()]
    if pd.notna(validated_latest):
        observed_dates.append(validated_latest)
    provider_latest = max(observed_dates) if observed_dates else pd.NaT
    latest = provider_latest
    deferred_tickers = []
    # Yahoo can expose the newest session for a minority while most symbols
    # have only the preceding close. Select a consistently covered session,
    # using the fixed active universe, only if canonical history advances.
    # An outage must not become a successful replay of existing data.
    if not update.empty and not fatal:
        counts = update.loc[update.ticker.isin(active)].groupby("date").ticker.nunique()
        complete = counts.loc[counts / len(active) >= minimum_coverage]
        newest_coverage = counts.get(provider_latest.strftime("%Y-%m-%d"), 0) / len(active)
        if newest_coverage < minimum_coverage and not complete.empty:
            candidate = pd.Timestamp(complete.index.max())
            canonical_latest = pd.to_datetime(existing.date).max()
            if pd.isna(canonical_latest) or candidate > canonical_latest:
                latest = candidate
                boundary = latest.strftime("%Y-%m-%d")
                deferred_tickers = sorted(set(update.loc[update.date.gt(boundary), "ticker"]) |
                                          {ticker for ticker, item in received.items()
                                           if item["received_latest_date"] > boundary})
                update = update.loc[update.date.le(boundary)].copy()
                updates = {ticker: frame.loc[frame.date.le(boundary)].copy()
                           for ticker, frame in updates.items() if frame.date.le(boundary).any()}
                print(f"US provider session {provider_latest.date()} incomplete; "
                      f"advance consistently through {latest.date()}, "
                      f"defer newer bars for {len(deferred_tickers)} tickers", flush=True)

    summary = []
    for ticker in tickers:
        frame = updates.get(ticker)
        diagnostic = received.get(ticker, {})
        summary.append({"ticker": ticker, "requested_start": starts[ticker],
                        "status": "rebase_quarantined" if ticker in rebase_quarantined else
                                  "rebase_updated" if ticker in rebase_updated and not fatal else
                                  "updated" if frame is not None else "unavailable",
                        "rows": len(frame) if frame is not None else 0,
                        "latest_date": frame.date.max() if frame is not None else "",
                        "received_latest_date": diagnostic.get("received_latest_date", ""),
                        "latest_invalid_fields": ",".join(diagnostic.get("latest_invalid_fields", [])),
                        "discarded_required_rows": diagnostic.get("discarded_required_rows", 0),
                        "latest_adjusted_close_filled": diagnostic.get("latest_adjusted_close_filled", 0),
                        "attempts": attempts[ticker],
                        "error": errors.get(ticker, "")})
    summary_path = run_dir / "us_daily_data_refresh_summary.csv"
    _atomic_csv(summary_path, pd.DataFrame(summary))
    observed = set(update.loc[update.date.eq(latest.strftime("%Y-%m-%d")), "ticker"]) if pd.notna(latest) else set()
    coverage = len(active & observed) / len(active)
    reason = str(fatal) if fatal else ""
    if not reason and (pd.isna(latest) or requested - latest > pd.Timedelta(days=7)):
        reason = f"US provider prices empty or stale at {latest}"
    if not reason and coverage < minimum_coverage:
        reason = f"US latest-session coverage {coverage:.1%} < {minimum_coverage:.1%}"
    report = {"requested_asof": end, "latest": str(latest.date()) if pd.notna(latest) else None,
              "provider_latest": str(provider_latest.date()) if pd.notna(provider_latest) else None,
              "provider_latest_validated": str(validated_latest.date()) if pd.notna(validated_latest) else None,
              "provider_latest_invalid_field_counts": {
                  column: sum(ticker in active and item["received_latest_date"] == str(provider_latest.date())
                              and column in item["latest_invalid_fields"] for ticker, item in received.items())
                  for column in ("close", "adjusted_close", "volume")},
              "deferred_partial_session_tickers": deferred_tickers,
              "requested_tickers": len(tickers), "updated_tickers": len(updates),
              "unavailable_tickers": sorted(set(tickers) - set(updates)),
              "adjustment_detected_tickers": sorted(adjustment_detected),
              "rebased_tickers": sorted(rebase_updated),
              "quarantined_rebase_tickers": sorted(rebase_quarantined),
              "active_tickers": len(active), "latest_session_coverage": coverage,
              "missing_active_tickers": sorted(active - observed),
              "missing_active_fraction": len(active - observed) / len(active),
              "maximum_missing_fraction": 1 - minimum_coverage, "attempt_limit_per_range": 2,
              "accepted": not bool(reason), "error": reason}
    _atomic_bytes(run_dir / "us_collection.json", _json_bytes(report))
    if reason:
        raise RuntimeError(f"{reason}; canonical US prices unchanged") from fatal
    combined = pd.concat([existing, update], ignore_index=True)
    combined = combined.drop_duplicates(["ticker", "date"], keep="last").sort_values(["ticker", "date"]).reset_index(drop=True)
    _atomic_csv(output_path, combined)
    print(f"US collection committed: latest={report['latest']}, coverage={coverage:.1%}, "
          f"missing_active={len(active - observed)}/{len(active)}, "
          f"rebased={len(rebase_updated)}, quarantined={len(rebase_quarantined)}", flush=True)
    return USDailyRefreshResult(asof=latest.strftime("%Y%m%d"), listings_path=listings_path,
                                combined_prices_path=output_path, summary_path=summary_path,
                                updated_count=len(updates), failed_count=len(tickers) - len(updates),
                                excluded_tickers=tuple(sorted(set(tickers) - observed)))
