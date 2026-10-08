from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from model_health_metrics import coefficient_report, label_audit, score_audit, threshold_metrics
from ai_stock_assistant import monthly_ews as live


def test_all_positive_accuracy_is_not_discrimination():
    y = np.array([0] * 25 + [1] * 75)
    p = np.ones(100)
    report = score_audit(["2026-01-01"] * 100, y, {"final": p}, .25)
    result = report["all_positive_classifier"]
    assert result["accuracy"] == .75
    assert result["balanced_accuracy"] == .5
    assert result["recall"] == 1
    assert result["specificity"] == 0
    baseline = report["components_and_baselines"]
    assert baseline["all_positive"]["auc"] == .5
    assert baseline["all_positive"]["top_decile_lift"] == 1
    assert baseline["training_prevalence"]["brier"] == pytest.approx(.4375)
    assert baseline["same_sample_prevalence_hindsight_only"]["brier"] == pytest.approx(.1875)
    assert report["deciles"] == []


def test_empty_alerts_do_not_claim_precision_and_threshold_includes_boundary():
    assert threshold_metrics([0, 1], [.1, .2], .35)["precision"] is None
    result = threshold_metrics([0, 1], [.349, .35], .35)
    assert result["tp"] == result["tn"] == 1
    assert result["balanced_accuracy"] == 1


def test_coefficient_units_clipping_and_missing_value_imputation():
    n = len(live.FEATURES)
    linear = {"coef": [2.] + [0.] * (n - 1), "mean": [10.] * n,
              "scale": [2.] * n, "median": [12.] * n, "intercept": -1.}
    frame = pd.DataFrame(np.full((3, n), 10.), columns=live.FEATURES)
    frame.iloc[:, 0] = [np.nan, 14., 100.]
    result = coefficient_report(linear, {"latest": frame})
    f = result["features"][0]
    assert f["coefficient_original_units"] == 1
    assert f["odds_ratio_per_training_sd"] == pytest.approx(np.exp(2))
    assert result["intercept_original_units_without_clipping"] == -11
    assert f["populations"]["latest"]["mean_logit_contribution"] == 10
    assert f["populations"]["latest"]["clipped_fraction"] == pytest.approx(1 / 3)


def test_forward_audit_checks_complete_window_and_signal_exclusion(monkeypatch):
    p = np.full(200, 100.)
    p[63] = 75.
    frame = pd.DataFrame({"ticker": "TEST", "date": pd.bdate_range("2025-01-01", periods=200),
                          "open": p, "high": p, "low": p, "close": p, "adjusted_close": p, "volume": 1e6})
    labels = live.ticker_features(frame, labels=True).iloc[[0, 63, 64]]
    assert labels.y_down.tolist() == [1, 0, 0]
    monkeypatch.setattr(live, "chronological_split", lambda *args: (labels, labels))
    audit = label_audit(frame, labels, str(frame.date.max().date()))
    assert audit["checked_rows"] == 3
    assert audit["implementation_mismatches"] == 0
    assert audit["first_crossing_step_median"] == 63
    corrupted = labels.copy()
    corrupted.iloc[0, corrupted.columns.get_loc("y_down")] = 0
    monkeypatch.setattr(live, "chronological_split", lambda *args: (labels, corrupted))
    assert label_audit(frame, corrupted, str(frame.date.max().date()))["implementation_mismatches"] == 1
