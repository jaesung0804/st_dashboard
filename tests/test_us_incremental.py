from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd
import pytest

from ai_stock_assistant.data import refresh, us


def quotes(ticker, dates, price=100):
    return pd.DataFrame([[day, ticker, price, price + 1, price - 1, price, price, 1000]
                         for day in dates], columns=us.PRICE_SCHEMA)


def setup(tmp_path, monkeypatch, tickers=("AAA", "BBB"), dates=("2026-09-03",)):
    price, listing = tmp_path / "prices.csv", tmp_path / "listings.csv"
    pd.concat([quotes(t, dates) for t in tickers]).to_csv(price, index=False)
    pd.DataFrame({"ticker": tickers}).to_csv(listing, index=False)
    monkeypatch.setattr(refresh, "ensure_project_dirs", lambda: None)
    monkeypatch.setattr(refresh, "DAILY_OUTPUT_DIR", tmp_path / "daily")
    return dict(listings_path=listing, prices_path=price, output_path=price, asof="20260904")


def test_latest_completed_us_asof_waits_for_provider_settlement():
    during_session = datetime(2026, 9, 9, 16, 30, tzinfo=timezone.utc)
    after_settlement = datetime(2026, 9, 9, 22, 30, tzinfo=timezone.utc)
    winter_schedule = datetime(2026, 1, 9, 22, 30, tzinfo=timezone.utc)
    assert refresh.latest_completed_us_asof(during_session) == "20260908"
    assert refresh.latest_completed_us_asof(after_settlement) == "20260909"
    assert refresh.latest_completed_us_asof(winter_schedule) == "20260109"


def test_default_refresh_uses_latest_completed_us_asof(tmp_path, monkeypatch):
    args = setup(tmp_path, monkeypatch, tickers=("AAA",))
    del args["asof"]
    monkeypatch.setattr(refresh, "latest_completed_us_asof", lambda: "20260908")
    ends = []
    def fetch(tickers, start, end):
        ends.append(end)
        return {"AAA": quotes("AAA", ["2026-09-03", "2026-09-08"])}
    monkeypatch.setattr(refresh, "fetch_us_ohlcv_batch", fetch)
    result = refresh.refresh_us_daily_data(**args)
    assert result.asof == "20260908" and ends == ["20260908"]


def test_total_outage_fails_before_replacing_prices(tmp_path, monkeypatch):
    args = setup(tmp_path, monkeypatch)
    before = args["prices_path"].read_bytes()
    calls = []
    def empty(tickers, **kwargs):
        calls.append(tickers)
        return {}
    monkeypatch.setattr(refresh, "fetch_us_ohlcv_batch", empty)
    with pytest.raises(RuntimeError, match="canonical US prices unchanged"):
        refresh.refresh_us_daily_data(**args)
    assert len(calls) == 2  # One bounded retry, not an endless loop.
    assert args["prices_path"].read_bytes() == before
    report = json.loads((tmp_path / "daily/20260904/us_collection.json").read_text())
    assert not report["accepted"] and report["updated_tickers"] == 0


def test_retry_only_missing_active_tickers(tmp_path, monkeypatch):
    args = setup(tmp_path, monkeypatch)
    calls = []
    def fetch(tickers, **kwargs):
        calls.append(tickers)
        return {t: quotes(t, ["2026-09-03", "2026-09-04"]) for t in tickers
                if len(calls) > 1 or t == "AAA"}
    monkeypatch.setattr(refresh, "fetch_us_ohlcv_batch", fetch)
    result = refresh.refresh_us_daily_data(**args)
    assert calls == [["AAA", "BBB"], ["BBB"]]
    assert result.updated_count == 2 and result.failed_count == 0


@pytest.mark.parametrize("asof", ["20260905", "20260906", "20260907"])
def test_weekend_and_labor_day_reuse_observed_friday_without_fabrication(tmp_path, monkeypatch, asof):
    args = setup(tmp_path, monkeypatch, dates=("2026-09-04",))
    args["asof"] = asof
    monkeypatch.setattr(refresh, "fetch_us_ohlcv_batch", lambda tickers, **k: {t: quotes(t, ["2026-09-04"]) for t in tickers})
    result = refresh.refresh_us_daily_data(**args)
    assert result.asof == "20260904"
    assert set(pd.read_csv(args["prices_path"]).date) == {"2026-09-04"}


def test_small_partial_failure_and_old_delisted_history_are_retained(tmp_path, monkeypatch):
    tickers = [f"T{i:02}" for i in range(20)] + ["DELISTED"]
    args = setup(tmp_path, monkeypatch, tickers=tickers)
    old = pd.read_csv(args["prices_path"])
    old.loc[old.ticker.eq("DELISTED"), "date"] = "2021-06-01"
    old.to_csv(args["prices_path"], index=False)
    def fetch(tickers, **kwargs):
        return {t: quotes(t, ["2026-09-03", "2026-09-04"]) for t in tickers if t not in {"T19", "DELISTED"}}
    monkeypatch.setattr(refresh, "fetch_us_ohlcv_batch", fetch)
    result = refresh.refresh_us_daily_data(**args)
    assert result.updated_count == 19 and result.failed_count == 2
    merged = pd.read_csv(args["prices_path"])
    assert merged.loc[merged.ticker.eq("T19"), "date"].tolist() == ["2026-09-03"]
    assert merged.loc[merged.ticker.eq("DELISTED"), "date"].tolist() == ["2021-06-01"]


def test_widespread_missing_latest_session_rejects_canonical_replacement(tmp_path, monkeypatch):
    args = setup(tmp_path, monkeypatch)
    before = args["prices_path"].read_bytes()
    monkeypatch.setattr(refresh, "fetch_us_ohlcv_batch", lambda tickers, **k: {
        t: quotes(t, ["2026-09-03", "2026-09-04"] if t == "AAA" else ["2026-09-03"]) for t in tickers})
    with pytest.raises(RuntimeError, match="coverage"):
        refresh.refresh_us_daily_data(**args)
    assert args["prices_path"].read_bytes() == before


def test_each_ticker_recovers_its_own_long_gap(tmp_path, monkeypatch):
    args = setup(tmp_path, monkeypatch)
    old = pd.read_csv(args["prices_path"])
    old.loc[old.ticker.eq("AAA"), "date"] = "2026-08-05"
    old.to_csv(args["prices_path"], index=False)
    starts = {}
    def fetch(tickers, start, end):
        starts.update({t: start for t in tickers})
        days = pd.bdate_range(start, end).strftime("%Y-%m-%d").tolist()
        return {t: quotes(t, days) for t in tickers}
    monkeypatch.setattr(refresh, "fetch_us_ohlcv_batch", fetch)
    refresh.refresh_us_daily_data(**args)
    assert starts == {"AAA": "20260726", "BBB": "20260824"}
    merged = pd.read_csv(args["prices_path"])
    assert "2026-08-06" in set(merged.loc[merged.ticker.eq("AAA"), "date"])


def test_changed_adjustment_uses_complete_full_history(tmp_path, monkeypatch):
    args = setup(tmp_path, monkeypatch, tickers=("AAA",), dates=("2021-06-01", "2026-09-03"))
    starts = []
    def fetch(tickers, start, end):
        starts.append(start)
        days = ["2026-09-03", "2026-09-04"]
        if start == "20210601":
            days.insert(0, "2021-06-01")
        return {"AAA": quotes("AAA", days, 50)}
    monkeypatch.setattr(refresh, "fetch_us_ohlcv_batch", fetch)
    refresh.refresh_us_daily_data(**args)
    merged = pd.read_csv(args["prices_path"])
    assert merged.adjusted_close.eq(50).all()
    assert merged.adjusted_close.pct_change().dropna().eq(0).all()
    assert starts == ["20260824", "20210601"]


def test_incomplete_rebase_is_quarantined_when_market_coverage_is_safe(tmp_path, monkeypatch):
    tickers = ["AAA", *[f"T{i:02}" for i in range(19)]]
    args = setup(tmp_path, monkeypatch, tickers=tickers, dates=("2021-06-01", "2026-09-03"))
    def fetch(requested, start, end):
        if start == "20210601":
            return {"AAA": quotes("AAA", ["2026-09-03", "2026-09-04"], 50)}
        return {ticker: quotes(ticker, ["2026-09-03", "2026-09-04"], 50 if ticker == "AAA" else 100)
                for ticker in requested}
    monkeypatch.setattr(refresh, "fetch_us_ohlcv_batch", fetch)

    result = refresh.refresh_us_daily_data(**args)

    assert result.updated_count == 19 and result.failed_count == 1
    merged = pd.read_csv(args["prices_path"])
    aaa = merged.loc[merged.ticker.eq("AAA")]
    assert aaa.date.tolist() == ["2021-06-01", "2026-09-03"]
    assert aaa.close.eq(100).all()
    assert merged.loc[merged.ticker.eq("T00"), "date"].tolist()[-1] == "2026-09-04"
    report = json.loads((tmp_path / "daily/20260904/us_collection.json").read_text())
    assert report["accepted"] and report["latest_session_coverage"] == .95
    assert report["quarantined_rebase_tickers"] == ["AAA"]
    summary = pd.read_csv(tmp_path / "daily/20260904/us_daily_data_refresh_summary.csv")
    assert summary.loc[summary.ticker.eq("AAA"), "status"].item() == "rebase_quarantined"


def test_incomplete_rebase_still_fails_when_market_coverage_is_too_low(tmp_path, monkeypatch):
    args = setup(tmp_path, monkeypatch, tickers=("AAA",), dates=("2021-06-01", "2026-09-03"))
    before = args["prices_path"].read_bytes()
    def fetch(tickers, start, end):
        return {"AAA": quotes("AAA", ["2026-09-03", "2026-09-04"], 50)}
    monkeypatch.setattr(refresh, "fetch_us_ohlcv_batch", fetch)
    with pytest.raises(RuntimeError, match="empty or stale|coverage"):
        refresh.refresh_us_daily_data(**args)
    assert args["prices_path"].read_bytes() == before


def test_dividend_adjustment_alone_also_refreshes_old_history(tmp_path, monkeypatch):
    args = setup(tmp_path, monkeypatch, tickers=("AAA",), dates=("2021-06-01", "2026-09-03"))
    def fetch(tickers, start, end):
        days = ["2026-09-03", "2026-09-04"] if start == "20260824" else ["2021-06-01", "2026-09-03", "2026-09-04"]
        frame = quotes("AAA", days)
        frame["adjusted_close"] = 99.9
        return {"AAA": frame}
    monkeypatch.setattr(refresh, "fetch_us_ohlcv_batch", fetch)
    refresh.refresh_us_daily_data(**args)
    merged = pd.read_csv(args["prices_path"])
    assert merged.close.eq(100).all() and merged.adjusted_close.eq(99.9).all()


def test_truncated_period_cannot_masquerade_as_gap_recovery(tmp_path, monkeypatch):
    args = setup(tmp_path, monkeypatch, tickers=("AAA",))
    before = args["prices_path"].read_bytes()
    monkeypatch.setattr(refresh, "fetch_us_ohlcv_batch", lambda *a, **k: {"AAA": quotes("AAA", ["2026-09-04"])})
    with pytest.raises(RuntimeError, match="canonical US prices unchanged"):
        refresh.refresh_us_daily_data(**args)
    assert args["prices_path"].read_bytes() == before


def test_interrupted_atomic_write_preserves_existing_csv(tmp_path, monkeypatch):
    args = setup(tmp_path, monkeypatch, tickers=("AAA",))
    before = args["prices_path"].read_bytes()
    monkeypatch.setattr(refresh, "fetch_us_ohlcv_batch", lambda *a, **k: {"AAA": quotes("AAA", ["2026-09-03", "2026-09-04"])})
    original = pd.DataFrame.to_csv
    def fail(frame, handle, *a, **k):
        if "adjusted_close" in frame:
            handle.write("date,ticker\n2026-")
            handle.flush()
            raise OSError("simulated disk failure")
        return original(frame, handle, *a, **k)
    monkeypatch.setattr(pd.DataFrame, "to_csv", fail)
    with pytest.raises(OSError, match="disk failure"):
        refresh.refresh_us_daily_data(**args)
    assert args["prices_path"].read_bytes() == before
    assert not list(tmp_path.glob("prices.csv.*.tmp"))


@pytest.mark.parametrize("bad", ["negative", "nan", "duplicate", "wrong_ticker", "out_of_range", "ohl"])
def test_invalid_provider_rows_do_not_replace_history(tmp_path, monkeypatch, bad):
    args = setup(tmp_path, monkeypatch, tickers=("AAA",))
    before = args["prices_path"].read_bytes()
    frame = quotes("AAA", ["2026-09-03", "2026-09-04"])
    if bad == "negative": frame["volume"] = -1
    if bad == "nan": frame["adjusted_close"] = float("nan")
    if bad == "duplicate": frame = pd.concat([frame, frame])
    if bad == "wrong_ticker": frame["ticker"] = "BBB"
    if bad == "out_of_range": frame["date"] = "2026-09-08"
    if bad == "ohl": frame["high"] = 50
    monkeypatch.setattr(refresh, "fetch_us_ohlcv_batch", lambda *a, **k: {"AAA": frame})
    with pytest.raises(RuntimeError, match="canonical US prices unchanged"):
        refresh.refresh_us_daily_data(**args)
    assert args["prices_path"].read_bytes() == before


@pytest.mark.parametrize("missing", [0, float("nan")])
def test_unavailable_intraday_prices_remain_missing(tmp_path, monkeypatch, missing):
    args = setup(tmp_path, monkeypatch, tickers=("AAA",))
    frame = quotes("AAA", ["2026-09-03", "2026-09-04"])
    frame.loc[1, ["open", "high", "low"]] = missing
    monkeypatch.setattr(refresh, "fetch_us_ohlcv_batch", lambda *a, **k: {"AAA": frame})
    refresh.refresh_us_daily_data(**args)
    latest = pd.read_csv(args["prices_path"]).iloc[-1]
    assert latest.close == 100 and latest.volume == 1000
    assert latest[["open", "high", "low"]].isna().all() if pd.isna(missing) else latest[["open", "high", "low"]].eq(0).all()


def test_partially_missing_intraday_prices_do_not_discard_valid_close(tmp_path, monkeypatch):
    args = setup(tmp_path, monkeypatch, tickers=("AAA",))
    frame = quotes("AAA", ["2026-09-03", "2026-09-04"])
    frame.loc[1, "high"] = float("nan")
    monkeypatch.setattr(refresh, "fetch_us_ohlcv_batch", lambda *a, **k: {"AAA": frame})
    refresh.refresh_us_daily_data(**args)
    latest = pd.read_csv(args["prices_path"]).iloc[-1]
    assert latest.close == 100 and latest.volume == 1000 and pd.isna(latest.high)


def test_latest_missing_adjusted_close_uses_same_session_raw_close(tmp_path, monkeypatch):
    args = setup(tmp_path, monkeypatch, tickers=("AAA",))
    frame = quotes("AAA", ["2026-09-03", "2026-09-04"])
    frame.loc[1, "adjusted_close"] = float("nan")
    monkeypatch.setattr(refresh, "fetch_us_ohlcv_batch", lambda *a, **k: {"AAA": frame})
    refresh.refresh_us_daily_data(**args)
    latest = pd.read_csv(args["prices_path"]).iloc[-1]
    assert latest.adjusted_close == latest.close == 100


def test_yfinance_batch_uses_bounded_threads_and_timeout(monkeypatch):
    calls = []
    def download(tickers, **kwargs):
        calls.append(kwargs)
        return pd.DataFrame()
    monkeypatch.setattr(us.yf, "download", download)
    us.fetch_us_ohlcv_batch([f"T{i}" for i in range(100)], "20260801", "20260904")
    assert calls[0]["threads"] == 8 and calls[0]["timeout"] == 15
    assert calls[0]["end"] == "2026-09-05"  # Yahoo's end is exclusive.
