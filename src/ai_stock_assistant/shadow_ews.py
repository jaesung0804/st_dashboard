"""Independent, immutable price-vs-macro challenger. Never edits live v1 models."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import expit, logit

from . import monthly_ews as live

VERSION = "ews-smooth-macro-v2"
ARMS = ("price", "macro")
FOLDS = ("2024-10", "2025-04", "2025-10", "2026-02")
TARGET = {
    "version": "smooth-126-all-mean-v1", "horizon": 126, "terminal_days": 5,
    "minimum_return": .25, "minimum_excess": .15, "minimum_persistent_days": 4,
    "benchmark": "equal-weight arithmetic mean of signal-date active observable stocks",
    "minimum_benchmark_coverage": .95, "down_horizon": 63, "down_barrier": -.20,
    "terminal_requires_positive_volume": True,
    "price_quality": "verified-actions-and-20x-continuity-v1",
    "calendar": "market trading calendar, no forward fill of missing outcomes",
}


def market_root(state: Path, market: str) -> Path:
    # Preserve earlier experimental artifacts when the target/data contract changes.
    return state / market / VERSION


def smooth_targets(prices: pd.DataFrame, signal_dates=None) -> pd.DataFrame:
    """Same terminal market dates for every stock, including the benchmark pool.

    The benchmark contains all active observable stocks on the signal date,
    before the model's liquidity/history screen. Unobserved outcomes are counted;
    dates below 95% benchmark outcome coverage are ineligible for up labels.
    Four qualifying daily closes prevent one extreme close dominating the mean.
    """
    p = prices.pivot(index="date", columns="ticker", values="adjusted_close").sort_index()
    volume = prices.pivot(index="date", columns="ticker", values="volume").reindex(index=p.index, columns=p.columns)
    raw = prices.pivot(index="date", columns="ticker", values="close").reindex(index=p.index, columns=p.columns)
    values = p.to_numpy(dtype=float)
    valid = np.isfinite(values) & (values > 0)
    active = valid & (volume.to_numpy() > 0) & (raw.to_numpy() > 0)
    breaks = prices.assign(quality_break=prices.get("quality_break", False)).pivot(index="date", columns="ticker", values="quality_break").reindex(index=p.index, columns=p.columns).eq(True).to_numpy(dtype=bool)
    cumulative_breaks = breaks.cumsum(axis=0)
    values = np.where(valid, values, np.nan)
    dates, tickers = p.index, p.columns
    chosen = set(pd.to_datetime(signal_dates)) if signal_dates is not None else set(dates[::5])
    pieces = []
    for index, date in enumerate(dates):
        if date not in chosen:
            continue
        members = active[index]
        positions = np.flatnonzero(members)
        if not len(positions):
            continue
        row = pd.DataFrame({"date": date, "ticker": tickers[positions],
                            "end_up": pd.NaT, "end_down": pd.NaT,
                            "future_up": np.nan, "future_down": np.nan,
                            "benchmark_up": np.nan, "benchmark_coverage": np.nan,
                            "y_up": np.nan, "y_down": np.nan, "persistent_days": np.nan})
        if index + TARGET["horizon"] < len(dates):
            terminal = np.where(active[index + 122:index + 127, positions],
                                values[index + 122:index + 127, positions], np.nan)
            known = np.isfinite(terminal).all(axis=0) & ~breaks[index, positions] & (cumulative_breaks[index + 126, positions] == cumulative_breaks[index, positions])
            coverage = float(known.mean())
            returns = terminal / values[index, positions] - 1
            average = returns.mean(axis=0)
            benchmark = float(average[known].mean()) if known.any() else np.nan
            persistent = ((returns >= TARGET["minimum_return"]) &
                          (returns - benchmark >= TARGET["minimum_excess"])).sum(axis=0)
            outcome = ((average >= TARGET["minimum_return"]) &
                       (average - benchmark >= TARGET["minimum_excess"]) &
                       (persistent >= TARGET["minimum_persistent_days"]))
            row["end_up"] = dates[index + 126]
            row["future_up"] = np.where(known, average, np.nan)
            row["benchmark_up"] = benchmark
            row["benchmark_coverage"] = coverage
            row["persistent_days"] = np.where(known, persistent, np.nan)
            row["y_up"] = np.where(known & (coverage >= TARGET["minimum_benchmark_coverage"]), outcome.astype(float), np.nan)
        if index + TARGET["down_horizon"] < len(dates):
            window = values[index + 1:index + 64, positions]
            known = np.isfinite(window).all(axis=0) & ~breaks[index, positions] & (cumulative_breaks[index + 63, positions] == cumulative_breaks[index, positions])
            minimum = np.min(window, axis=0) / values[index, positions] - 1
            row["end_down"] = dates[index + 63]
            row["future_down"] = np.where(known, minimum, np.nan)
            row["y_down"] = np.where(known, (minimum <= TARGET["down_barrier"]).astype(float), np.nan)
        pieces.append(row)
    return pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()


def research_panel(prices: pd.DataFrame, market: str) -> pd.DataFrame:
    panel = live.feature_panel(prices, market, training=True)
    targets = smooth_targets(prices, panel["date"].unique())
    panel = panel.drop(columns=[c for c in ("end_up", "end_down", "future_up", "future_down", "y_up", "y_down") if c in panel])
    return panel.merge(targets, on=["date", "ticker"], how="left", validate="one_to_one")


def weights(frame: pd.DataFrame) -> np.ndarray:
    counts = frame.groupby("date")["ticker"].transform("size").to_numpy(dtype=float)
    w = 1 / counts
    return w / w.mean()


def raw_probability(frame: pd.DataFrame, head: dict, booster, features: list[str]) -> np.ndarray:
    x = frame[features].to_numpy(dtype=float)
    linear = head["linear"]
    x = np.where(np.isfinite(x), x, linear["median"])
    z = np.clip((x - linear["mean"]) / linear["scale"], -12, 12)
    return .5 * expit(z @ np.asarray(linear["coef"]) + linear["intercept"]) + .5 * booster.predict(frame[features], num_threads=1)


def fit_month(panel: pd.DataFrame, market: str, month: str, arm: str, state: Path,
              macro_features: list[str], provenance: dict, research: bool = False) -> Path:
    import lightgbm as lgb
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    if arm not in ARMS:
        raise ValueError(arm)
    boundary = pd.Timestamp(month + "-01")
    if not research and month != pd.Timestamp.now(tz="UTC").strftime("%Y-%m"):
        raise ValueError("Live shadow models may only be created for the current month")
    folder = market_root(state, market) / ("research" if research else "models") / month / arm
    if folder.exists():
        card, _ = load_model(folder)
        if card["target"] != TARGET or card["version"] != VERSION:
            raise ValueError("Immutable shadow model contract mismatch")
        return folder
    features = live.FEATURES + (macro_features if arm == "macro" else [])
    cutoff = boundary - pd.Timedelta(days=1)
    folder.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".training-", dir=folder.parent))
    card = {"version": VERSION, "id": f"{market}-{month}-{VERSION}-{arm}", "arm": arm,
            "market": market, "month": month, "cutoff": str(cutoff.date()), "research": research,
            "created_at": live.utc_now(), "features": features, "target": TARGET,
            "provenance": provenance, "code_commit": os.environ.get("GITHUB_SHA", "local-research"),
            "weighting": "equal total weight per signal date", "heads": {}, "files": {}}
    try:
        for kind in ("up", "down"):
            train, calibration = live.chronological_split(panel, kind, cutoff)
            if len(train) > 180000:
                train = train.sample(180000, random_state=live.SEED).sort_values(["date", "ticker"])
            x, y = train[features], train[f"y_{kind}"].astype(int)
            med = x.median().fillna(0).to_numpy(dtype=float)
            filled = np.where(np.isfinite(x), x, med)
            w = weights(train)
            scaler = StandardScaler().fit(filled, sample_weight=w)
            z = np.clip(scaler.transform(filled), -12, 12)
            lr = LogisticRegression(C=.1, max_iter=600, random_state=live.SEED).fit(z, y, sample_weight=w)
            linear = {"median": med.tolist(), "mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(),
                      "coef": lr.coef_[0].tolist(), "intercept": float(lr.intercept_[0])}
            tree = lgb.LGBMClassifier(n_estimators=180, learning_rate=.035, num_leaves=15,
                                     max_depth=5, min_child_samples=150, reg_lambda=10, colsample_bytree=.85,
                                     max_bin=63, random_state=live.SEED, n_jobs=1, verbosity=-1,
                                     deterministic=True, force_col_wise=True)
            tree.fit(x, y, sample_weight=w)
            head = {"linear": linear}
            raw = raw_probability(calibration, head, tree.booster_, features)
            cal = LogisticRegression(C=1, max_iter=300).fit(
                logit(np.clip(raw, 1e-6, 1-1e-6)).reshape(-1, 1), calibration[f"y_{kind}"].astype(int),
                sample_weight=weights(calibration))
            # Negative calibration is evidence of unstable ranking, never silently inverted.
            accepted = bool(cal.coef_[0, 0] >= 0)
            calibrator = {"coef": float(cal.coef_[0, 0]) if accepted else 1.,
                          "intercept": float(cal.intercept_[0]) if accepted else 0., "weight": .5 if accepted else 0.}
            head.update({"calibration": calibrator, "calibration_accepted": accepted,
                         "train_keys_sha256": hashlib.sha256(train[["date", "ticker", f"y_{kind}"]].to_csv(index=False).encode()).hexdigest(),
                         "calibration_keys_sha256": hashlib.sha256(calibration[["date", "ticker", f"y_{kind}"]].to_csv(index=False).encode()).hexdigest(),
                         "train_rows": len(train), "train_dates": int(train["date"].nunique()),
                         "train_start": str(train["date"].min().date()), "train_end": str(train["date"].max().date()),
                         "train_label_end": str(train[f"end_{kind}"].max().date()),
                         "calibration_start": str(calibration["date"].min().date()),
                         "calibration_end": str(calibration["date"].max().date()),
                         "train_event_rate": float(np.average(y, weights=w)),
                         "calibration_diagnostics_not_test": score_metrics(calibration[f"y_{kind}"], live.calibrated(raw, calibrator))})
            filename = f"{kind}.txt"
            tree.booster_.save_model(str(staged / filename))
            card["heads"][kind] = head
            card["files"][filename] = live.digest(staged / filename)
            print(f"shadow {market} {month} {arm} {kind}: train={len(train):,}, calibration={len(calibration):,}", flush=True)
        live.write_json(staged / "model.json", card)
        (staged / "model.sha256").write_text(live.digest(staged / "model.json"), encoding="ascii")
        live.publish_directory(staged, folder)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return folder


def load_model(folder: Path):
    import lightgbm as lgb
    if live.digest(folder / "model.json") != (folder / "model.sha256").read_text().strip():
        raise ValueError("Shadow model checksum mismatch")
    card = live.read_json(folder / "model.json")
    if card["version"] != VERSION or card["target"] != TARGET:
        raise ValueError("Shadow model schema/target mismatch")
    boosters = {}
    for filename, sha in card["files"].items():
        if live.digest(folder / filename) != sha:
            raise ValueError("Shadow model weights checksum mismatch")
        boosters[filename.removesuffix(".txt")] = lgb.Booster(model_file=str(folder / filename))
    return card, boosters


def predict(frame: pd.DataFrame, card: dict, boosters: dict) -> dict:
    return {kind: live.calibrated(raw_probability(frame, card["heads"][kind], boosters[kind], card["features"]), card["heads"][kind]["calibration"])
            for kind in ("up", "down")}


def score_metrics(y, p) -> dict:
    from sklearn.metrics import log_loss, roc_curve
    y, p = np.asarray(y, dtype=int), np.asarray(p, dtype=float)
    result = live.metrics(y, p)
    result["log_loss"] = float(log_loss(y, np.clip(p, 1e-8, 1-1e-8), labels=[0, 1]))
    result["ar"] = 2 * result["auc"] - 1 if result["auc"] is not None else None
    if np.unique(y).size == 2:
        fpr, tpr, _ = roc_curve(y, p)
        result["ks"] = float(np.max(tpr - fpr))
    else:
        result["ks"] = None
    result["false_alarm_rate_at_35"] = float(((p >= .35) & (y == 0)).sum() / max(1, (y == 0).sum()))
    result["recall_at_35"] = float(((p >= .35) & (y == 1)).sum() / max(1, (y == 1).sum()))
    return result


def cohort_metrics(frame: pd.DataFrame, probability: str) -> dict:
    """Equal-date top-decile outcomes, with fractional inclusion of score ties."""
    daily = []
    for _, day in frame.loc[frame.y_up.notna()].groupby("date"):
        n = max(1, int(np.ceil(len(day) * .1)))
        threshold = day[probability].nlargest(n).iloc[-1]
        above, tied = day[probability] > threshold, day[probability] == threshold
        w = above.astype(float) + tied.astype(float) * (n - above.sum()) / tied.sum()
        daily.append({"hit_rate": float(np.average(day.y_up, weights=w)),
                      "return_6m": float(np.average(day.future_up, weights=w)),
                      "excess_6m": float(np.average(day.future_up - day.benchmark_up, weights=w)),
                      "benchmark_6m": float(day.benchmark_up.iloc[0]),
                      "universe_hit_rate": float(day.y_up.mean()), "selected_weight": n})
    if not daily:
        return {"signal_dates": 0}
    result = {key: float(np.mean([d[key] for d in daily])) for key in daily[0]}
    result["signal_dates"] = len(daily)
    result["lift"] = result["hit_rate"] / result["universe_hit_rate"] if result["universe_hit_rate"] else None
    return result


def comparison_metrics(frame: pd.DataFrame) -> dict:
    result = {}
    for arm in ARMS:
        result[arm] = {}
        for kind in ("up", "down"):
            known = frame.loc[frame[f"y_{kind}"].notna()]
            if known.empty:
                result[arm][kind] = {"rows": 0, "missing": len(frame)}
                continue
            result[arm][kind] = {**score_metrics(known[f"y_{kind}"], known[f"{arm}_{kind}"]),
                                 "missing": len(frame) - len(known),
                                 "signal_dates": int(known.date.nunique())}
        result[arm]["top_decile"] = cohort_metrics(frame, f"{arm}_up")
    return result


def audit(panel: pd.DataFrame, prices_path: Path, market: str, state: Path,
          macro_features: list[str], provenance: dict) -> dict:
    root = market_root(state, market) / "audits"
    parts, folds = [], []
    for month in FOLDS:
        folder = root / month
        if folder.exists():
            report = live.read_json(folder / "report.json")
            if report["version"] != VERSION or report["target"] != TARGET:
                raise ValueError("Historical comparison contract changed")
            if live.digest(folder / "predictions.csv.gz") != report["predictions_sha256"]:
                raise ValueError("Historical comparison checksum mismatch")
            result = pd.read_csv(folder / "predictions.csv.gz", dtype={"ticker": str}, parse_dates=["date"])
        else:
            start = pd.Timestamp(month + "-01")
            test = panel.loc[(panel.date >= start) & (panel.date < start + pd.offsets.MonthBegin(1))].copy()
            if test.empty or test.end_up.isna().any():
                raise ValueError(f"Holdout month {month} has not matured")
            columns = ["date", "ticker", "y_up", "y_down", "future_up", "future_down", "benchmark_up", "benchmark_coverage"]
            result = test[columns].copy()
            models, calibration = {}, {}
            for arm in ARMS:
                path = fit_month(panel, market, month, arm, state, macro_features, provenance, research=True)
                card, boosters = load_model(path)
                models[arm] = {"id": card["id"], "sha256": live.digest(path / "model.json"),
                               "cutoff": card["cutoff"]}
                calibration[arm] = {h: card["heads"][h]["calibration_accepted"] for h in ("up", "down")}
                for kind, values in predict(test, card, boosters).items():
                    result[f"{arm}_{kind}"] = values
            # Both arms must actually use the same purged training and calibration samples.
            a, _ = load_model(market_root(state, market) / "research" / month / "price")
            b, _ = load_model(market_root(state, market) / "research" / month / "macro")
            for h in ("up", "down"):
                for key in ("train_keys_sha256", "calibration_keys_sha256"):
                    if a["heads"][h][key] != b["heads"][h][key]:
                        raise ValueError("Comparison arms used different training samples")
            folder.parent.mkdir(parents=True, exist_ok=True)
            staged = Path(tempfile.mkdtemp(prefix=".audit-", dir=folder.parent))
            try:
                result.to_csv(staged / "predictions.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
                report = {"month": month, "version": VERSION, "target": TARGET, "created_at": live.utc_now(),
                          "models": models, "calibration_accepted": calibration,
                          "price_sha256": live.digest(prices_path),
                          "predictions_sha256": live.digest(staged / "predictions.csv.gz"),
                          "signal_dates": int(result.date.nunique()), "rows": len(result),
                          "benchmark_coverage_min": float(result.benchmark_coverage.min()),
                          "metrics": comparison_metrics(result)}
                live.write_json(staged / "report.json", report)
                live.publish_directory(staged, folder)
            finally:
                if staged.exists():
                    shutil.rmtree(staged)
        parts.append(result)
        folds.append(report)
        print(f"Audit {market} {month}: {len(result):,} forward holdout rows", flush=True)
    summary = {"version": VERSION, "target": TARGET, "market": market, "created_at": live.utc_now(),
               "folds": folds, "aggregate": comparison_metrics(pd.concat(parts, ignore_index=True)),
               "test_months": list(FOLDS), "basis": "Retrospective temporal holdout, not live portfolio performance"}
    live.write_json(root / "summary.json", summary)
    return summary


def infer(prices: pd.DataFrame, market: str, state: Path, vintages: pd.DataFrame) -> Path:
    from .data import macro_vintages as macro
    signal = str(prices.date.max().date())
    if pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() - pd.Timestamp(signal) > pd.Timedelta(days=7):
        raise ValueError(f"Stale shadow signal: {signal}")
    target = market_root(state, market) / "predictions" / signal
    if target.exists():
        live.verify_prediction(target)
        print(f"Keeping immutable paired shadow prediction: {market} {signal}", flush=True)
        return target
    panel = macro.attach(live.feature_panel(prices, market, training=False), vintages)
    if panel.empty:
        raise ValueError("No eligible shadow inference rows")
    output = panel[["ticker"]].copy()
    models = {}
    kinds = []
    for arm in ARMS:
        path = market_root(state, market) / "models" / signal[:7] / arm
        if not path.exists():
            raise FileNotFoundError(f"Missing fixed shadow {arm} model for {signal[:7]}; monthly training is required")
        card, boosters = load_model(path)
        if card["month"] != signal[:7] or card["market"] != market or card["arm"] != arm or card["research"]:
            raise ValueError("Shadow model identity mismatch")
        models[arm] = {k: card[k] for k in ("id", "month", "cutoff", "created_at")}
        models[arm]["sha256"] = live.digest(path / "model.json")
        kinds.append("delayed" if card["created_at"][:10] > signal else "live")
        for kind, values in predict(panel, card, boosters).items():
            output[f"{arm}_{kind}"] = values
    rows = output.sort_values("ticker").to_dict(orient="records")
    return live.freeze_prediction(state / market, VERSION, signal, rows,
                                  {"version": VERSION, "target": TARGET, "models": models,
                                   "signal_date": signal, "created_at": live.utc_now(),
                                   "prediction_kind": "delayed" if "delayed" in kinds else "live",
                                   "vintage_month": str(panel.vintage_month.iloc[0]),
                                   "macro_available_date": str(panel.available_date.iloc[0].date())})


def export_report(state: Path, live_state: Path, market: str, output: Path) -> None:
    root = market_root(state, market)
    audit_path = root / "audits" / "summary.json"
    comparison = live.read_json(audit_path) if audit_path.exists() else None
    predictions = sorted((root / "predictions").glob("*/rows.json"))
    latest = None
    if predictions:
        meta = live.verify_prediction(predictions[-1].parent)
        latest = {"meta": meta, "rows": live.read_json(predictions[-1])}
    live_records = {"live_dates": 0, "delayed_dates": 0, "research_dates": 0}
    for file in (live_state / market / "predictions").glob("*/meta.json"):
        meta = live.verify_prediction(file.parent)
        key = meta.get("prediction_kind", "research") + "_dates"
        if key in live_records:
            live_records[key] += 1
    diagnostics = {}
    for model in (live_state / market / "models").glob("*/model.json"):
        card = live.read_json(model)
        diagnostics[card["month"]] = {h: {k: v for k, v in details.items()
                                         if k in ("diagnostics", "calibration_diagnostics_not_test", "train_rows", "calibration_rows", "calibration_start", "calibration_end")}
                                       for h, details in card["heads"].items()}
    macro_path = state / "macro" / "manifest.json"
    evaluation_path = root / "evaluations" / "latest.json"
    quality_path = root / "price_quality.json"
    report = {"schema": "ews-research-report-v1", "market": market, "generated_at": live.utc_now(),
              "target": TARGET, "comparison": comparison, "latest_shadow": latest,
              "macro": live.read_json(macro_path) if macro_path.exists() else None,
              "live_records": live_records, "production_calibration_not_test": diagnostics,
              "shadow_outcomes": live.read_json(evaluation_path) if evaluation_path.exists() else None,
              "price_quality": live.read_json(quality_path) if quality_path.exists() else None,
              "limitations": ["Historical prices/listings are a retained current vintage; delisting and survivorship bias remain.",
                              "The full benchmark means all active stocks observable in retained data, not a complete exchange census.",
                              "Five-day terminal adjusted-price return is not a tradable portfolio or a total return after costs.",
                              "Holdout dates and six-month outcomes overlap; rows are not independent trials.",
                              "Comparison specifications were chosen during development; prospective shadow validation is still required.",
                              "FRED-MD is US macro/global monetary context, with a conservative additional monthly delay."]}
    live.write_json(output / f"lgbm_warning_dashboard_macro_{market}_latest" / "model_report.json", report)


def evaluate(prices: pd.DataFrame, market: str, state: Path) -> None:
    """Evaluate saved paired forecasts, with delayed and live cohorts separate."""
    root = market_root(state, market)
    archives = sorted((root / "predictions").glob("*/rows.json"))
    dates = sorted(prices.date.unique())
    matured = {p.parent.name for p in archives if sum(d > pd.Timestamp(p.parent.name) for d in dates) >= 63}
    targets = smooth_targets(prices, list(matured)) if matured else pd.DataFrame()
    buckets = {"live": [], "delayed": []}
    counts = {"live": 0, "delayed": 0}
    for file in archives:
        meta = live.verify_prediction(file.parent)
        kind = meta["prediction_kind"]
        if kind not in buckets:
            continue
        counts[kind] += 1
        if file.parent.name not in matured:
            continue
        records = pd.DataFrame(live.read_json(file)).assign(date=pd.Timestamp(file.parent.name))
        buckets[kind].append(records.merge(targets, on=["date", "ticker"], how="left", validate="one_to_one"))
    report = {"evaluated_at": live.utc_now(), "price_cutoff": str(prices.date.max().date()), "target": TARGET,
              "forecast_dates": counts, "cohorts": {k: comparison_metrics(pd.concat(parts, ignore_index=True))
                                                     for k, parts in buckets.items() if parts},
              "basis": "Saved forecasts only; delayed separate from live; current retained adjusted-price outcomes"}
    path = root / "evaluations" / f"{report['price_cutoff']}.json"
    # Outcome snapshots have their own date; frozen prediction files stay intact.
    live.write_json(path, report)
    live.write_json(root / "evaluations" / "latest.json", report)
