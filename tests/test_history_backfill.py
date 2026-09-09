import pandas as pd
import pytest

import scripts.backfill_recommendation_history as backfill


def prices(counts):
    rows = []
    for date, count in counts:
        rows.extend({"date": pd.Timestamp(date), "ticker": str(i)} for i in range(count))
    return pd.DataFrame(rows)


def test_complete_sessions_uses_observed_market_dates_only():
    frame = prices([("2026-08-03", 10), ("2026-08-04", 10), ("2026-08-06", 10)])

    assert backfill.complete_sessions(frame, "2026-08-01", "2026-08-06") == [
        pd.Timestamp("2026-08-03"),
        pd.Timestamp("2026-08-04"),
        pd.Timestamp("2026-08-06"),
    ]


def test_complete_sessions_rejects_partial_market_close():
    frame = prices([("2026-08-03", 10), ("2026-08-04", 10), ("2026-08-05", 7)])

    with pytest.raises(ValueError, match="Incomplete prices"):
        backfill.complete_sessions(frame, "2026-08-01", None)


def test_archived_dates_validates_and_combines_all_ledgers(tmp_path, monkeypatch):
    live_root = tmp_path / "live"
    reconstruction_root = tmp_path / "research" / "reconstruction"
    monkeypatch.setattr(backfill, "LIVE_ROOT", live_root)
    monkeypatch.setattr(backfill, "RECONSTRUCTION_ROOT", reconstruction_root)
    legacy = live_root / "kr" / "legacy" / "2026-09-04.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("[]", encoding="utf-8")
    backfill.live.freeze_prediction(
        reconstruction_root,
        "kr",
        "2026-09-07",
        [{"ticker": "A"}],
        {"prediction_kind": "reconstructed"},
    )

    assert backfill.archived_dates("kr") == {"2026-09-04", "2026-09-07"}
