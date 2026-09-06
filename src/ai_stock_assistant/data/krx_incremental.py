"""Bounded, resumable KRX collection: one period request per restored ticker.

Checkpoints are expendable caches, never authoritative dashboard state. A failed
collection must not replace the canonical CSV or allow training/publication.
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


def _period(ticker: str, start: str, end: str, cache: Path, before_request) -> tuple[pd.DataFrame, bool]:
    key = {"version": VERSION, "ticker": ticker, "start": start, "end": end}
    path = cache / (hashlib.sha256(_json_bytes(key)).hexdigest() + ".json.gz")
    try:
        entry = json.loads(gzip.decompress(path.read_bytes()))
        body = entry["body"]
        age = time.time() - body["fetched_at"]
        if (body["query"] == key and 0 <= age < 36 * 3600
                and entry["sha256"] == hashlib.sha256(_json_bytes(body)).hexdigest()):
            frame = pd.DataFrame(body["rows"], columns=PRICE_SCHEMA)
            return _validate(frame, ticker, start, end), True
    except (OSError, EOFError, ValueError, KeyError, TypeError):
        pass  # Corrupt or old caches are re-fetched, never trusted as prices.
    frame = _validate(fetch_krx_ohlcv_bounded(ticker, start, end, before_request=before_request), ticker, start, end)
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

    def collect(ticker):
        budget.check()
        frame, cached = _period(ticker, starts[ticker], end, checkpoint_dir, budget.before_request)
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
                    frame, cached = _period(ticker, full_start, end, checkpoint_dir, budget.before_request)
                    if not set(old["date"]).issubset(set(frame["date"])):
                        raise ValueError("Adjusted prices changed but provider did not return the full retained history")
                    status = "rebase_cached" if cached else "rebase_updated"
        return frame, status

    frames, rows = [], []
    iterator = iter(tickers)
    consecutive_failures = 0
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
                    consecutive_failures = 0
                    missing_ohl = frame[["open", "high", "low"]].eq(0).all(axis=1)
                    rows.append({"ticker": ticker, "status": status if not frame.empty else "empty",
                                 "rows": len(frame), "latest_date": frame["date"].max() if len(frame) else "",
                                 "missing_ohl_rows": int(missing_ohl.sum()),
                                 "missing_ohl_with_volume": int((missing_ohl & frame["volume"].gt(0)).sum()),
                                 "error": ""})
                except Exception as exc:
                    consecutive_failures += 1
                    rows.append({"ticker": ticker, "status": "failed", "rows": 0, "latest_date": "",
                                 "missing_ohl_rows": 0, "missing_ohl_with_volume": 0,
                                 "error": f"{type(exc).__name__}: {str(exc).splitlines()[0]}"})
                if (len(rows) % 50 == 0 or len(rows) == len(tickers)
                        or rows[-1]["status"] == "failed" or rows[-1]["missing_ohl_with_volume"]):
                    detail = f" ({rows[-1]['error']})" if rows[-1]["error"] else ""
                    if rows[-1]["missing_ohl_with_volume"]:
                        detail += f" (missing OHL with volume: {rows[-1]['missing_ohl_with_volume']}; raw values retained)"
                    print(f"[KRX range {len(rows)}/{len(tickers)}] {ticker} {rows[-1]['status']}{detail}", flush=True)
                if consecutive_failures >= 8:
                    raise RuntimeError("Eight consecutive provider failures; stopping requests and retaining checkpoints")
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
    _atomic_csv(summary_path, pd.DataFrame(rows))
    failed = sum(row["status"] == "failed" for row in rows)
    if fatal is not None or failed or len(rows) != len(tickers):
        raise RuntimeError(f"KRX collection incomplete ({len(rows)}/{len(tickers)}, {failed} failed). "
                           f"Canonical prices unchanged; retry reuses {checkpoint_dir}. {fatal or ''}") from fatal
    update = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PRICE_SCHEMA)
    latest = pd.to_datetime(update["date"]).max()
    if pd.isna(latest) or requested - latest > pd.Timedelta(days=7):
        raise ValueError(f"KRX provider prices stale at {latest}; canonical prices unchanged")
    # Check coverage on the provider's latest completed session. Weekends and
    # holidays need not equal requested_asof, but a lone fresh ticker isn't enough.
    last_existing = history["date"].max()
    active = set(history.loc[(history["date"] == last_existing) & (history["volume"] > 0), "ticker"]) & set(tickers)
    active = active or set(tickers)
    observed = set(update.loc[update["date"].eq(latest.strftime("%Y-%m-%d")), "ticker"])
    coverage = len(active & observed) / len(active)
    if coverage < minimum_coverage:
        raise ValueError(f"KRX latest-session coverage {coverage:.1%} < {minimum_coverage:.1%}; canonical prices unchanged")
    combined = pd.concat([existing, update], ignore_index=True)
    combined = combined.drop_duplicates(["ticker", "date"], keep="last").sort_values(["ticker", "date"]).reset_index(drop=True)
    report = {"version": VERSION, "collected_at": datetime.now(timezone.utc).isoformat(),
              "requested_asof": end, "latest": latest.strftime("%Y-%m-%d"),
              "requested_tickers": len(tickers), "updated_rows": len(update), "failed_tickers": failed,
              "missing_ohl_rows": sum(row["missing_ohl_rows"] for row in rows),
              "missing_ohl_with_volume": sum(row["missing_ohl_with_volume"] for row in rows),
              "latest_session_coverage": coverage, "restored_rows": len(existing), "merged_rows": len(combined),
              "listings_source": str(listings_path), "universe_refreshed": False}
    _atomic_bytes(run_dir / "krx_collection.json", _json_bytes(report))
    _atomic_csv(output_path, combined)
    print(f"KRX collection committed atomically: latest={report['latest']}, coverage={coverage:.1%}, "
          f"rows={len(existing)} -> {len(combined)}", flush=True)
    return DailyRefreshResult(asof=latest.strftime("%Y%m%d"), listings_path=listings_path,
                              price_dir=output_path.parent, combined_prices_path=output_path,
                              summary_path=summary_path, updated_count=len(update), failed_count=failed)
