"""Bounded, resumable KRX collection: one period request per restored ticker.

Checkpoints are expendable caches, never authoritative dashboard state. Isolated
failures retain their history; only a sufficiently covered market may commit.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time

import numpy as np
import pandas as pd

from ai_stock_assistant.data.krx import PRICE_SCHEMA, fetch_krx_ohlcv_bounded

VERSION = "naver-adjusted-range-v1"


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
            frame.to_csv(handle, index=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _validate(frame: pd.DataFrame, ticker: str, start: str, end: str) -> pd.DataFrame:
    if not set(PRICE_SCHEMA).issubset(frame.columns):
        raise ValueError("Missing OHLCV columns")
    frame = frame[PRICE_SCHEMA].copy()
    frame["ticker"] = frame["ticker"].astype(str).str.zfill(6)
    dates = pd.to_datetime(frame["date"], errors="raise")
    if not frame["ticker"].eq(ticker).all() or not dates.between(pd.Timestamp(start), pd.Timestamp(end)).all():
        raise ValueError("Price response has the wrong ticker or date range")
    numeric = PRICE_SCHEMA[2:]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(frame[numeric].to_numpy(dtype=float)).all():
        raise ValueError("Non-finite OHLCV values")
    if (frame[numeric] < 0).any().any() or (frame[["close", "adjusted_close"]] <= 0).any().any():
        raise ValueError("Invalid OHLCV values")
    # Naver can omit all intraday quotes even with positive close and volume
    # (010780 on 2026-08-13). Preserve that raw sentinel, not invented OHLC.
    # The monthly model masks the unavailable range rather than treating it as 0.
    missing_ohl = frame[["open", "high", "low"]].eq(0).all(axis=1)
    traded = frame["volume"].gt(0) & ~missing_ohl
    if (frame.loc[traded, ["open", "high", "low"]] <= 0).any().any():
        raise ValueError("Incomplete traded OHL prices")
    if (frame.loc[traded, "high"] < frame.loc[traded, ["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("High is below traded OHLC prices")
    if (frame.loc[traded, "low"] > frame.loc[traded, ["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("Low is above traded OHLC prices")
    frame["date"] = dates.dt.strftime("%Y-%m-%d")
    if frame.duplicated(["ticker", "date"]).any():
        raise ValueError("Duplicate price observations")
    return frame.sort_values("date").reset_index(drop=True)


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _period(ticker: str, start: str, end: str, cache: Path, before_request,
            *, use_cache: bool = True) -> tuple[pd.DataFrame, bool]:
    key = {"version": VERSION, "ticker": ticker, "start": start, "end": end}
    path = cache / (hashlib.sha256(_json_bytes(key)).hexdigest() + ".json.gz")
    try:
        entry = json.loads(gzip.decompress(path.read_bytes()))
        body = entry["body"]
        age = time.time() - body["fetched_at"]
        if (use_cache and body["query"] == key and 0 <= age < 36 * 3600
                and entry["sha256"] == hashlib.sha256(_json_bytes(body)).hexdigest()):
            frame = pd.DataFrame(body["rows"], columns=PRICE_SCHEMA)
            return _validate(frame, ticker, start, end), True
    except (OSError, EOFError, ValueError, KeyError, TypeError):
        pass  # Corrupt or old caches are re-fetched, never trusted as prices.
    # The collector owns the two attempts, including validation failures.
    # Do not multiply them by the HTTP client's own retry loop.
    frame = _validate(fetch_krx_ohlcv_bounded(ticker, start, end, before_request=before_request,
                                           attempts=1), ticker, start, end)
    body = {"query": key, "fetched_at": time.time(), "rows": frame.values.tolist()}
    payload = _json_bytes({"body": body, "sha256": hashlib.sha256(_json_bytes(body)).hexdigest()})
    _atomic_bytes(path, gzip.compress(payload, mtime=0))
    return frame, False


class RequestBudget:
    def __init__(self, seconds: float, interval: float):
        self.deadline = time.monotonic() + seconds
        self.interval = interval
        self.next_request = 0.0
        self.lock = threading.Lock()
        self.stopped = threading.Event()

    def check(self) -> None:
        if self.stopped.is_set() or time.monotonic() >= self.deadline:
            raise TimeoutError("KRX collection budget exhausted; completed ticker checkpoints retained")

    def before_request(self) -> None:
        with self.lock:
            self.check()
            delay = max(0.0, self.next_request - time.monotonic())
            if delay:
                time.sleep(delay)
            self.check()
            self.next_request = time.monotonic() + self.interval


def refresh_ranges(
    *, markets: list[str], requested_asof: str, prices_path: Path,
    output_path: Path, listings_path: Path, run_dir: Path, checkpoint_dir: Path,
    overlap_days: int = 7, workers: int = 4, max_seconds: float = 3600,
    request_interval: float = .25, minimum_coverage: float = .95,
):
    from ai_stock_assistant.data.refresh import DailyRefreshResult

    if (not 1 <= workers <= 8 or overlap_days < 1 or max_seconds <= 0
            or request_interval < 0 or not 0 < minimum_coverage <= 1):
        raise ValueError("Invalid KRX collection limits")
    requested = pd.Timestamp(requested_asof).normalize()
    listings = pd.read_csv(listings_path, dtype={"ticker": str})
    listings["ticker"] = listings["ticker"].astype(str).str.zfill(6)
    listings = listings.loc[listings["exchange"].str.upper().isin(markets)].drop_duplicates("ticker")
    tickers = listings["ticker"].sort_values().tolist()
    if not tickers or not listings["ticker"].str.fullmatch(r"\d{6}").all():
        raise ValueError("No valid restored KRX universe; refresh listings explicitly first")
    existing = pd.read_csv(prices_path, dtype={"ticker": str})
    if not set(PRICE_SCHEMA).issubset(existing.columns):
        raise ValueError("Restored prices lack required columns")
    existing["ticker"] = existing["ticker"].astype(str).str.zfill(6)
    existing["date"] = pd.to_datetime(existing["date"], errors="raise").dt.strftime("%Y-%m-%d")
    if existing.duplicated(["ticker", "date"]).any():
        raise ValueError("Restored prices have duplicate keys; refusing a lossy merge")
    history = existing.loc[existing["date"] <= requested.strftime("%Y-%m-%d")]
    histories = {ticker: group for ticker, group in history.groupby("ticker", sort=False)}
    sessions = sorted(history.date.unique())
    recent_floor = (pd.Timestamp(sessions[-1]) - pd.Timedelta(days=45)).strftime("%Y-%m-%d") if sessions else ""
    recent = history.loc[history.date.isin(sessions[-21:]) & history.date.ge(recent_floor) & history.volume.gt(0)]
    # Yesterday's failures must not disappear from today's coverage denominator.
    active = (set(recent.ticker) & set(tickers)) or set(tickers)
    ends = history.groupby("ticker")["date"].max()
    starts = {
        ticker: (pd.Timestamp(ends[ticker]) - pd.Timedelta(days=overlap_days)).strftime("%Y%m%d")
        if ticker in ends else (requested - pd.DateOffset(years=5)).strftime("%Y%m%d")
        for ticker in tickers
    }
    end = requested.strftime("%Y%m%d")
    print(f"KRX range collection: {len(tickers)} restored tickers, "
          f"{min(starts.values())}..{end}, workers={workers}, "
          f"overlap={overlap_days}d, budget={max_seconds:.0f}s", flush=True)
    budget = RequestBudget(max_seconds, request_interval)

    attempts = {}

    def collect_once(ticker, use_cache):
        budget.check()
        frame, cached = _period(ticker, starts[ticker], end, checkpoint_dir, budget.before_request,
                                use_cache=use_cache)
        if frame.empty:
            raise ValueError("No provider observations; history retained")
        status = "cached" if cached else "updated"
        old = histories.get(ticker)
        if old is not None and not frame.empty:
            overlap = old[["date", "adjusted_close"]].merge(frame[["date", "adjusted_close"]], on="date")
            if not overlap.empty:
                mismatch = ~np.isclose(overlap["adjusted_close_x"], overlap["adjusted_close_y"], rtol=.005, atol=1)
                if mismatch.any():
                    # A split/rebase must not splice newly adjusted quotes onto
                    # an old-scale history. Re-read the original retained range.
                    full_start = old["date"].min().replace("-", "")
                    frame, cached = _period(ticker, full_start, end, checkpoint_dir, budget.before_request,
                                            use_cache=use_cache)
                    if not set(old["date"]).issubset(set(frame["date"])):
                        raise ValueError("Adjusted prices changed but provider did not return the full retained history")
                    status = "rebase_cached" if cached else "rebase_updated"
        return frame, status

    def collect(ticker):
        for attempt in range(1, 3):
            budget.check()
            attempts[ticker] = attempt
            try:
                return collect_once(ticker, use_cache=attempt == 1)
            except Exception:
                if attempt == 2:
                    raise

    frames, rows = [], []
    iterator = iter(tickers)
    fatal = None
    pool = ThreadPoolExecutor(max_workers=workers)
    pending = {}
    try:
        for _ in range(workers):
            ticker = next(iterator, None)
            if ticker is not None:
                pending[pool.submit(collect, ticker)] = ticker
        while pending:
            budget.check()
            done, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
            for future in done:
                ticker = pending.pop(future)
                try:
                    frame, status = future.result()
                    if not frame.empty:
                        frames.append(frame)
                    missing_ohl = frame[["open", "high", "low"]].eq(0).all(axis=1)
                    rows.append({"ticker": ticker, "status": status if not frame.empty else "empty",
                                 "rows": len(frame), "latest_date": frame["date"].max() if len(frame) else "",
                                 "missing_ohl_rows": int(missing_ohl.sum()),
                                 "missing_ohl_with_volume": int((missing_ohl & frame["volume"].gt(0)).sum()),
                                 "error": ""})
                except Exception as exc:
                    rows.append({"ticker": ticker, "status": "failed", "rows": 0, "latest_date": "",
                                 "missing_ohl_rows": 0, "missing_ohl_with_volume": 0,
                                 "error": f"{type(exc).__name__}: {str(exc).splitlines()[0]}"})
                if (len(rows) % 50 == 0 or len(rows) == len(tickers)
                        or rows[-1]["status"] == "failed" or rows[-1]["missing_ohl_with_volume"]):
                    detail = f" ({rows[-1]['error']})" if rows[-1]["error"] else ""
                    if rows[-1]["missing_ohl_with_volume"]:
                        detail += f" (missing OHL with volume: {rows[-1]['missing_ohl_with_volume']}; raw values retained)"
                    print(f"[KRX range {len(rows)}/{len(tickers)}] {ticker} {rows[-1]['status']}{detail}", flush=True)
                # Examine the final market totals, not the position of failures
                # in ticker order. A run budget still bounds provider outages.
                ticker = next(iterator, None)
                if ticker is not None:
                    pending[pool.submit(collect, ticker)] = ticker
    except Exception as exc:
        fatal = exc
    finally:
        budget.stopped.set()
        for future in pending:
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
    summary_path = run_dir / "krx_daily_data_refresh_summary.csv"
    for row in rows:
        row["attempts"] = attempts.get(row["ticker"], 0)
    _atomic_csv(summary_path, pd.DataFrame(rows))
    failed = sum(row["status"] == "failed" for row in rows)
    update = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PRICE_SCHEMA)
    latest = pd.to_datetime(update["date"]).max()
    # Check coverage on the provider's latest completed session. Weekends and
    # holidays need not equal requested_asof, but a lone fresh ticker isn't enough.
    observed = set(update.loc[update["date"].eq(latest.strftime("%Y-%m-%d")), "ticker"]) if pd.notna(latest) else set()
    coverage = len(active & observed) / len(active)
    reason = str(fatal) if fatal else ""
    if not reason and len(rows) != len(tickers):
        reason = f"KRX collection incomplete ({len(rows)}/{len(tickers)})"
    if not reason and (pd.isna(latest) or requested - latest > pd.Timedelta(days=7)):
        reason = f"KRX provider prices empty or stale at {latest}"
    if not reason and coverage < minimum_coverage:
        reason = f"KRX latest-session coverage {coverage:.1%} < {minimum_coverage:.1%}"
    combined = pd.concat([existing, update], ignore_index=True)
    combined = combined.drop_duplicates(["ticker", "date"], keep="last").sort_values(["ticker", "date"]).reset_index(drop=True)
    report = {"version": VERSION, "collected_at": datetime.now(timezone.utc).isoformat(),
              "requested_asof": end, "latest": latest.strftime("%Y-%m-%d") if pd.notna(latest) else None,
              "requested_tickers": len(tickers), "updated_rows": len(update), "failed_tickers": failed,
              "quarantined_tickers": sorted(row["ticker"] for row in rows if row["status"] == "failed"),
              "active_tickers": len(active), "missing_active_tickers": sorted(active - observed),
              "missing_active_fraction": len(active - observed) / len(active),
              "maximum_missing_fraction": 1 - minimum_coverage, "attempt_limit": 2,
              "accepted": not bool(reason), "error": reason,
              "missing_ohl_rows": sum(row["missing_ohl_rows"] for row in rows),
              "missing_ohl_with_volume": sum(row["missing_ohl_with_volume"] for row in rows),
              "latest_session_coverage": coverage, "restored_rows": len(existing), "merged_rows": len(combined),
              "listings_source": str(listings_path), "universe_refreshed": False}
    _atomic_bytes(run_dir / "krx_collection.json", _json_bytes(report))
    if reason:
        raise RuntimeError(f"{reason}; Canonical prices unchanged; {failed} tickers failed after retry") from fatal
    _atomic_csv(output_path, combined)
    print(f"KRX collection committed atomically: latest={report['latest']}, coverage={coverage:.1%}, "
          f"missing_active={len(active - observed)}/{len(active)}, quarantined={failed}, "
          f"rows={len(existing)} -> {len(combined)}", flush=True)
    return DailyRefreshResult(asof=latest.strftime("%Y%m%d"), listings_path=listings_path,
                              price_dir=output_path.parent, combined_prices_path=output_path,
                              summary_path=summary_path, updated_count=len(update), failed_count=failed,
                              excluded_tickers=tuple(sorted(set(tickers) - observed)))
