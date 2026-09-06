from pathlib import Path
from types import SimpleNamespace
import gzip
import json

import pandas as pd
import pytest
import requests

from ai_stock_assistant.data import krx, krx_incremental as inc


def quotes(ticker, dates, price=100):
    return pd.DataFrame([
        [day, ticker, price, price + 2, price - 2, price, price, 1000]
        for day in dates
    ], columns=krx.PRICE_SCHEMA)


def setup(tmp_path, tickers=("000001", "000002")):
    prices = tmp_path / "prices.csv"
    listings = tmp_path / "listings.csv"
    frames = [quotes(t, ["2026-08-05"]) for t in tickers]
    frames.append(quotes("999999", ["2021-06-01"]))  # Delisted history must survive.
    pd.concat(frames).to_csv(prices, index=False)
    pd.DataFrame({"ticker": tickers, "name": tickers, "exchange": ["KOSPI"] * len(tickers)}).to_csv(listings, index=False)
    return dict(markets=["KOSPI"], requested_asof="20260905", prices_path=prices,
                output_path=prices, listings_path=listings, run_dir=tmp_path / "run",
                checkpoint_dir=tmp_path / "cache", workers=1, request_interval=0)


def test_one_request_per_ticker_for_month_gap_and_preserve_history(tmp_path, monkeypatch):
    args = setup(tmp_path)
    calls = []
    def fetch(ticker, start, end, **kwargs):
        calls.append((ticker, start, end))
        return quotes(ticker, ["2026-08-05", "2026-08-06", "2026-08-31", "2026-09-04"])
    monkeypatch.setattr(inc, "fetch_krx_ohlcv_bounded", fetch)
    result = inc.refresh_ranges(**args)
    assert calls == [("000001", "20260729", "20260905"), ("000002", "20260729", "20260905")]
    merged = pd.read_csv(args["prices_path"], dtype={"ticker": str})
    assert len(merged) == 9
    assert "999999" in set(merged.ticker)
    assert not merged.duplicated(["date", "ticker"]).any()
    assert result.asof == "20260904"  # Weekend is not fabricated as a signal date.


def test_partial_failure_does_not_replace_state_and_retry_reuses_checkpoint(tmp_path, monkeypatch):
    args = setup(tmp_path)
    original = args["prices_path"].read_bytes()
    calls = []
    def fail_second(ticker, start, end, **kwargs):
        calls.append(ticker)
        if ticker == "000002":
            raise RuntimeError("temporary provider timeout")
        return quotes(ticker, ["2026-08-05", "2026-09-04"])
    monkeypatch.setattr(inc, "fetch_krx_ohlcv_bounded", fail_second)
    with pytest.raises(RuntimeError, match="Canonical prices unchanged"):
        inc.refresh_ranges(**args)
    assert args["prices_path"].read_bytes() == original
    assert len(list(args["checkpoint_dir"].glob("*.gz"))) == 1
    calls.clear()
    def succeed(ticker, start, end, **kwargs):
        calls.append(ticker)
        return quotes(ticker, ["2026-08-05", "2026-09-04"])
    monkeypatch.setattr(inc, "fetch_krx_ohlcv_bounded", succeed)
    inc.refresh_ranges(**args)
    assert calls == ["000002"]


def test_each_ticker_recovers_its_own_gap(tmp_path, monkeypatch):
    args = setup(tmp_path)
    prices = pd.read_csv(args["prices_path"], dtype={"ticker": str})
    prices.loc[prices.ticker.eq("000002"), "date"] = "2026-07-01"
    prices.to_csv(args["prices_path"], index=False)
    calls = {}
    def fetch(ticker, start, end, **kwargs):
        calls[ticker] = start
        return quotes(ticker, ["2026-09-04"])
    monkeypatch.setattr(inc, "fetch_krx_ohlcv_bounded", fetch)
    inc.refresh_ranges(**args)
    assert calls == {"000001": "20260729", "000002": "20260624"}


def test_real_missing_ohl_row_is_preserved_and_other_ticker_cache_reused(tmp_path, monkeypatch):
    args = setup(tmp_path, ("000001", "010780"))
    calls = []
    def fetch(ticker, start, end, **kwargs):
        calls.append(ticker)
        frame = quotes(ticker, ["2026-08-05", "2026-09-04"])
        if ticker == "010780":
            # Actual Naver response observed on 2026-09-06, not a fabricated bar.
            missing = pd.DataFrame([["2026-08-13", ticker, 0, 0, 0, 18000, 18000, 199329]],
                                   columns=krx.PRICE_SCHEMA)
            frame = pd.concat([frame, missing], ignore_index=True)
        return frame
    monkeypatch.setattr(inc, "fetch_krx_ohlcv_bounded", fetch)
    inc._period("000001", "20260729", "20260905", args["checkpoint_dir"], lambda: None)
    calls.clear()
    inc.refresh_ranges(**args)
    assert calls == ["010780"]
    merged = pd.read_csv(args["prices_path"], dtype={"ticker": str})
    row = merged.loc[merged.ticker.eq("010780") & merged.date.eq("2026-08-13")].iloc[0]
    assert row[krx.PRICE_SCHEMA[2:]].tolist() == [0, 0, 0, 18000, 18000, 199329]
    assert "999999" in set(merged.ticker)
    report = json.loads((args["run_dir"] / "krx_collection.json").read_text())
    assert report["failed_tickers"] == 0 and report["missing_ohl_with_volume"] == 1
    _, cached = inc._period("010780", "20260729", "20260905", args["checkpoint_dir"], lambda: None)
    assert cached and calls == ["010780"]


@pytest.mark.parametrize("change", [
    {"high": 99}, {"low": 101}, {"open": 0}, {"high": 0}, {"low": 0},
    {"open": 0, "high": 0, "low": 0, "close": 0},
    {"open": 0, "high": 0, "low": 0, "volume": -1},
])
def test_missing_ohl_exception_does_not_accept_other_invalid_prices(tmp_path, monkeypatch, change):
    args = setup(tmp_path, ("010780",))
    original = args["prices_path"].read_bytes()
    def fetch(ticker, start, end, **kwargs):
        frame = quotes(ticker, ["2026-09-04"])
        for column, value in change.items():
            frame[column] = value
        return frame
    monkeypatch.setattr(inc, "fetch_krx_ohlcv_bounded", fetch)
    with pytest.raises(RuntimeError, match="Canonical prices unchanged"):
        inc.refresh_ranges(**args)
    assert args["prices_path"].read_bytes() == original


@pytest.mark.parametrize("bad", ["stale", "coverage", "wrong_ticker", "negative", "nan", "duplicate"])
def test_bad_prices_fail_closed(tmp_path, monkeypatch, bad):
    args = setup(tmp_path)
    original = args["prices_path"].read_bytes()
    def fetch(ticker, start, end, **kwargs):
        day = "2026-08-05" if bad == "stale" or (bad == "coverage" and ticker == "000002") else "2026-09-04"
        frame = quotes("000003" if bad == "wrong_ticker" else ticker, [day])
        if bad == "negative":
            frame["close"] = -1
        if bad == "nan":
            frame["close"] = float("nan")
        if bad == "duplicate":
            frame = pd.concat([frame, frame])
        return frame
    monkeypatch.setattr(inc, "fetch_krx_ohlcv_bounded", fetch)
    with pytest.raises((ValueError, RuntimeError)):
        inc.refresh_ranges(**args)
    assert args["prices_path"].read_bytes() == original


def test_corrupt_checkpoint_is_refetched(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    calls = []
    def fetch(ticker, start, end, **kwargs):
        calls.append(ticker)
        return quotes(ticker, ["2026-09-04"])
    monkeypatch.setattr(inc, "fetch_krx_ohlcv_bounded", fetch)
    inc._period("000001", "20260801", "20260905", cache, lambda: None)
    path = next(cache.glob("*.gz"))
    envelope = json.loads(gzip.decompress(path.read_bytes()))
    envelope["body"]["rows"][0][5] = 999999
    path.write_bytes(gzip.compress(json.dumps(envelope).encode()))
    frame, cached = inc._period("000001", "20260801", "20260905", cache, lambda: None)
    assert not cached and len(calls) == 2
    assert frame.close.iloc[0] == 100


def test_changed_adjustment_refetches_full_retained_history(tmp_path, monkeypatch):
    args = setup(tmp_path, ("000001",))
    existing = pd.read_csv(args["prices_path"], dtype={"ticker": str})
    pd.concat([existing, quotes("000001", ["2021-06-01"])]).to_csv(args["prices_path"], index=False)
    calls = []
    def fetch(ticker, start, end, **kwargs):
        calls.append(start)
        days = ["2026-08-05", "2026-09-04"]
        if start == "20210601":
            days.insert(0, "2021-06-01")
        return quotes(ticker, days, price=50)
    monkeypatch.setattr(inc, "fetch_krx_ohlcv_bounded", fetch)
    inc.refresh_ranges(**args)
    assert calls == ["20260729", "20210601"]
    merged = pd.read_csv(args["prices_path"], dtype={"ticker": str})
    assert merged.loc[merged.ticker.eq("000001"), "adjusted_close"].eq(50).all()
    assert merged.loc[merged.ticker.eq("999999"), "adjusted_close"].eq(100).all()


def test_circuit_breaker_stops_provider_outage(tmp_path, monkeypatch):
    args = setup(tmp_path, tuple(f"{i:06d}" for i in range(1, 21)))
    original = args["prices_path"].read_bytes()
    calls = []
    def fail(ticker, *args, **kwargs):
        calls.append(ticker)
        raise RuntimeError("service unavailable")
    monkeypatch.setattr(inc, "fetch_krx_ohlcv_bounded", fail)
    with pytest.raises(RuntimeError, match="Eight consecutive"):
        inc.refresh_ranges(**args)
    assert len(calls) == 8
    assert args["prices_path"].read_bytes() == original


def test_budget_stops_before_next_request(tmp_path, monkeypatch):
    budget = inc.RequestBudget(1, 0)
    budget.deadline = 0
    with pytest.raises(TimeoutError):
        budget.before_request()


def response(xml):
    return SimpleNamespace(content=xml.encode(), raise_for_status=lambda: None)


def test_http_uses_explicit_timeout_and_filters_period(monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return response('<protocol><chartdata symbol="005930"><item data="20260903|100|102|98|100|10"/>'
                        '<item data="20260904|100|102|98|100|20"/></chartdata></protocol>')
    monkeypatch.setattr(krx.requests, "get", get)
    frame = krx.fetch_krx_ohlcv_bounded("005930", "20260904", "20260905")
    assert frame.date.tolist() == ["2026-09-04"]
    assert calls[0][0].startswith("https://")
    assert calls[0][1]["timeout"] == (5.0, 15.0)
    assert calls[0][1]["params"]["symbol"] == "005930"


@pytest.mark.parametrize("xml", ["<html>maintenance</html>", "not XML", '<protocol><chartdata symbol="005930"><item data="broken"/></chartdata></protocol>'])
def test_invalid_provider_payload_is_not_empty_success(monkeypatch, xml):
    monkeypatch.setattr(krx.requests, "get", lambda *a, **k: response(xml))
    with pytest.raises(ValueError):
        krx.fetch_krx_ohlcv_bounded("005930", "20260904", "20260905")


def test_http_retries_are_finite(monkeypatch):
    calls = []
    def timeout(*args, **kwargs):
        calls.append(kwargs["timeout"])
        raise requests.Timeout("provider took too long")
    monkeypatch.setattr(krx.requests, "get", timeout)
    monkeypatch.setattr(krx.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="Timeout"):
        krx.fetch_krx_ohlcv_bounded("005930", "20260801", "20260905")
    assert len(calls) == 2


def test_real_provider_euc_kr_encoding_is_decoded_before_xml_parse(monkeypatch):
    xml = ('<?xml version="1.0" encoding="euc-kr"?><protocol>'
           '<chartdata symbol="005930" name="삼성전자">'
           '<item data="20260904|100|102|98|100|20"/></chartdata></protocol>')
    r = SimpleNamespace(content=xml.encode("euc-kr"), raise_for_status=lambda: None)
    monkeypatch.setattr(krx.requests, "get", lambda *a, **k: r)
    frame = krx.fetch_krx_ohlcv_bounded("005930", "20260904", "20260905")
    assert frame.date.tolist() == ["2026-09-04"]
    assert frame.ticker.tolist() == ["005930"]


def test_http_403_is_not_retried(monkeypatch):
    calls = []
    def denied(*args, **kwargs):
        calls.append(1)
        r = requests.Response()
        r.status_code = 403
        raise requests.HTTPError(response=r)
    monkeypatch.setattr(krx.requests, "get", denied)
    with pytest.raises(RuntimeError, match="403"):
        krx.fetch_krx_ohlcv_bounded("005930", "20260801", "20260905")
    assert len(calls) == 1
