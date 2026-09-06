from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_price_context.py"
spec = importlib.util.spec_from_file_location("build_price_context", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_returns_stop_at_signal_and_lookup_keeps_unscored_and_missing(tmp_path):
    dates = pd.bdate_range("2025-01-01", periods=140).strftime("%Y-%m-%d").tolist()
    rows = []
    for ticker, values in {"005930": [100.] * 126 + [120.] + [900.] * 13,
                           "NEW": [50.] * 5,
                           "HALT": [100.] * 126,
                           "NOADJ": [100.] * 126 + [float("nan")]}.items():
        for i, adjusted in enumerate(values):
            rows.append({"ticker": ticker, "date": dates[i], "close": 10000., "adjusted_close": adjusted, "volume": 100.})
    raw = tmp_path / "raw"
    raw.mkdir()
    pd.DataFrame(rows).to_csv(raw / module.PRICE_FILES["kr"], index=False)
    pd.DataFrame([{"ticker": t, "name": t + " name", "exchange": "KOSPI"}
                  for t in ["005930", "NEW", "HALT", "NOADJ", "NOPRICE"]]).to_csv(raw / module.LISTING_FILES["kr"], index=False)
    original_hash = hashlib.sha256((raw / module.PRICE_FILES["kr"]).read_bytes()).hexdigest()
    manifest = module.build_price_context(raw, tmp_path / "site", "kr", [dates[126], dates[4]])
    data = json.loads((tmp_path / "site/price_context" / f"{dates[126]}.json").read_text())
    lookup = {row["ticker"]: row for row in data["rows"]}
    assert len(lookup) == 5  # Listing with no quote is still discoverable.
    assert lookup["005930"]["trailingReturn6mPct"] == pytest.approx(20.)
    assert lookup["005930"]["returnStartDate"] == dates[0]
    assert lookup["005930"]["returnEndDate"] == dates[126]
    assert lookup["HALT"]["trailingReturn6mPct"] is None
    assert lookup["HALT"]["returnStatus"] == "stale_quote"
    assert lookup["NOADJ"]["returnStatus"] == "missing_adjusted_price"
    assert lookup["NOPRICE"]["returnStatus"] == "no_prices"
    early = json.loads((tmp_path / "site/price_context" / f"{dates[4]}.json").read_text())
    assert next(r for r in early["rows"] if r["ticker"] == "NEW")["returnStatus"] == "insufficient_history"
    assert all(r["trailingReturn6mPct"] is None for r in early["rows"])
    assert hashlib.sha256((raw / module.PRICE_FILES["kr"]).read_bytes()).hexdigest() == original_hash
    assert manifest["sha256ByDate"][dates[126]] == hashlib.sha256((tmp_path / "site/price_context" / f"{dates[126]}.json").read_bytes()).hexdigest()


def test_missing_price_archive_is_explicit(tmp_path):
    assert module.build_price_context(tmp_path, tmp_path / "site", "kr", ["2026-09-04"])["available"] is False


def test_unverified_jump_stays_searchable_without_a_fabricated_past_return(tmp_path):
    dates = pd.bdate_range('2025-01-01', periods=127).strftime('%Y-%m-%d')
    raw = tmp_path / 'raw'
    raw.mkdir()
    pd.DataFrame({'ticker':'BROKEN', 'date':dates, 'close':100., 'volume':100.,
                  'adjusted_close':[100.]*100 + [10000.]*27}).to_csv(raw / module.PRICE_FILES['us'], index=False)
    module.build_price_context(raw, tmp_path / 'site', 'us', [dates[-1]])
    data = json.loads((tmp_path / 'site/price_context' / f'{dates[-1]}.json').read_text())
    assert data['rows'][0]['ticker'] == 'BROKEN'
    assert data['rows'][0]['trailingReturn6mPct'] is None
    assert data['rows'][0]['returnStatus'] == 'unverified_price_continuity'
