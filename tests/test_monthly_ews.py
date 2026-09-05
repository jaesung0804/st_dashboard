from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ai_stock_assistant.monthly_ews import (
    FEATURES, calibrated, chronological_split, digest, evaluate_archive, export_dashboard, feature_panel,
    freeze_prediction, infer_latest, load_month, metrics, migrate_legacy, publish_directory,
    ticker_features, verify_prediction,
)


def prices(n=500, tickers=4):
    rng = np.random.default_rng(23)
    result = []
    for t in range(tickers):
        p = 100 * np.exp(np.cumsum(rng.normal(.0005, .025, n)))
        result.append(pd.DataFrame({"date": pd.bdate_range("2023-01-02", periods=n), "ticker": f"TEST{t}",
                                    "open": p, "high": p * 1.01, "low": p * .99,
                                    "close": p, "adjusted_close": p, "volume": 2_000_000}))
    return pd.concat(result, ignore_index=True)


def test_features_do_not_change_when_future_prices_change():
    full = prices()
    day = full["date"].unique()[400]
    before = feature_panel(full[full.date <= day], "us", training=False, signal_date=str(day))
    full.loc[full.date > day, ["open", "high", "low", "close", "adjusted_close"]] *= 10
    # Training has labels, but its input columns must match daily causal inference.
    after = feature_panel(full, "us", training=True)
    after = after[after.date == day].reset_index(drop=True)
    pd.testing.assert_frame_equal(before[FEATURES], after[FEATURES], atol=1e-6, rtol=1e-6)


def test_barrier_label_uses_future_close_and_requires_complete_horizon():
    frame = prices(n=200, tickers=1)
    frame[["close", "adjusted_close"]] = 100.
    frame.loc[10, ["close", "adjusted_close"]] = 75
    f = ticker_features(frame, labels=True)
    assert f.loc[0, "y_down"] == 1
    assert f.loc[10, "y_down"] == 0  # excludes the signal-date price itself
    assert f["y_down"].tail(63).isna().all()
    assert f["future_up"].tail(126).isna().all()


def test_purge_excludes_training_labels_crossing_calibration_start():
    dates = pd.bdate_range("2020-01-01", periods=700)
    frame = pd.DataFrame({"date": np.repeat(dates[::5], 90),
                          "end_up": np.repeat(dates[::5] + pd.offsets.BDay(126), 90),
                          "y_up": np.tile([0, 1, 0], 4200)})
    train, cal = chronological_split(frame, "up", dates[-1])
    assert train.end_up.max() < cal.date.min()
    assert cal.end_up.max() <= dates[-1]
    assert set(train.date).isdisjoint(cal.date)


def test_prediction_is_unchanged_on_rerun_and_detects_corruption(tmp_path):
    row = [{"ticker": "ABC", "upProb": .1234}]
    target = freeze_prediction(tmp_path, "us", "2026-09-04", row, {"model": "us-2026-09"})
    original = {p.name: p.read_bytes() for p in target.iterdir()}
    freeze_prediction(tmp_path, "us", "2026-09-04", [{"upProb": .9999}], {"model": "other"})
    assert original == {p.name: p.read_bytes() for p in target.iterdir()}
    (target / "rows.json").write_text("[]")
    with pytest.raises(ValueError, match="checksum"):
        verify_prediction(target)


def test_daily_rerun_returns_archive_even_if_prices_are_revised_or_model_missing(tmp_path):
    frame = prices(n=320, tickers=1)
    signal = str(frame.date.max().date())
    path = tmp_path / "price.csv"
    frame.to_csv(path, index=False)
    saved = freeze_prediction(tmp_path, "us", signal, [{"upProb": .2}], {"model": "frozen"})
    assert infer_latest(path, tmp_path / "missing-listings", "us", tmp_path, research=True) == saved


def test_missing_model_fails_without_training(tmp_path):
    frame = prices(n=320, tickers=1)
    path = tmp_path / "price.csv"
    frame.to_csv(path, index=False)
    with pytest.raises(FileNotFoundError, match="daily inference never trains"):
        infer_latest(path, tmp_path / "missing-listings", "us", tmp_path, research=True)
    assert not list(tmp_path.glob("us/models/*"))


def test_model_metadata_corruption_is_rejected_before_loading_boosters(tmp_path):
    (tmp_path / "model.json").write_text('{"version":"tampered"}')
    (tmp_path / "model.sha256").write_text("bad")
    with pytest.raises(ValueError, match="metadata checksum"):
        load_month(tmp_path)


def test_atomic_publish_cannot_replace_an_existing_directory(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    with pytest.raises(FileExistsError):
        publish_directory(new, old)
    assert new.exists() and old.exists()


def test_legacy_migration_and_export_preserve_bytes(tmp_path):
    pages, state, output = tmp_path / "pages", tmp_path / "state", tmp_path / "out"
    payload = b'[{"ticker":"ABC", "upScore":97.2}]\r\n'
    for market in ["kr", "us"]:
        path = pages / f"lgbm_warning_dashboard_macro_{market}_latest" / "walkforward_scores_by_date" / "2026-08-05.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(payload)
    migrate_legacy(pages, state)
    migrate_legacy(pages, state)
    export_dashboard(state, "us", output)
    assert (state / "us/legacy/2026-08-05.json").read_bytes() == payload
    assert (output / "lgbm_warning_dashboard_macro_us_latest/walkforward_scores_by_date/2026-08-05.json").read_bytes() == payload
    manifest = json.loads((state / "us/legacy-manifest.json").read_text())
    assert manifest["files"]["2026-08-05.json"] == digest(state / "us/legacy/2026-08-05.json")


def test_partial_calibration_is_bounded_monotone_and_limits_regime_shift():
    raw = np.linspace(.001, .999, 100)
    full = calibrated(raw, {"coef": 1.2, "intercept": -2., "weight": 1.})
    shrunk = calibrated(raw, {"coef": 1.2, "intercept": -2., "weight": .5})
    np.testing.assert_allclose(shrunk, (raw + full) / 2)
    assert ((shrunk > 0) & (shrunk < 1)).all()
    assert (np.diff(shrunk) > 0).all()


def test_constant_probability_has_no_spurious_top_decile_lift():
    result = metrics([1] * 20 + [0] * 80, [.2] * 100)
    assert result["top_decile_lift"] == pytest.approx(1.)


def test_outcomes_are_separate_and_missing_stocks_are_not_false_negatives(tmp_path):
    frame = prices(n=200, tickers=1)
    frame[["close", "adjusted_close"]] = 100.
    frame.loc[10, ["close", "adjusted_close"]] = 70.
    path = tmp_path / "prices.csv"
    frame.to_csv(path, index=False)
    rows = [{"ticker": ticker, "modelVersion": "test-model", "upProb": .2, "downProb": .6} for ticker in ["TEST0", "DELISTED"]]
    prediction = freeze_prediction(tmp_path, "us", str(frame.date.min().date()), rows, {"prediction_kind": "live"})
    before = digest(prediction / "rows.json")
    report = json.loads(evaluate_archive(path, "us", tmp_path).read_text())
    down = next(r for r in report["results"] if r["head"] == "down")
    assert down["event_rate"] == 1
    assert down["coverage"] == .5 and down["missing"] == 1
    assert digest(prediction / "rows.json") == before
