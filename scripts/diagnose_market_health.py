"""Read-only reproduction of frozen calibration scores and collection failures.

Downloads only selected, checksum-verified state. Never trains, promotes models,
changes a snapshot head, or emits individual price/prediction records.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from scipy.special import expit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ai_stock_assistant import monthly_ews as live
from research_backend_client import Client, safe_path
from unpack_dashboard_state import restore_state


def emit(section, value):
    print("MARKET_DIAGNOSTIC=" + json.dumps({section: value}, allow_nan=False), flush=True)


def collection_summary(repository, artifact):
    # GitHub's token stays at api.github.com, never at the signed download host.
    url = f"https://api.github.com/repos/{repository}/actions/artifacts/{artifact}/zip"
    response = requests.get(url, headers={"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
                                         "Accept": "application/vnd.github+json"},
                            allow_redirects=False, timeout=60)
    if response.status_code != 302:
        raise RuntimeError(f"Artifact redirect failed: HTTP {response.status_code}")
    response = requests.get(response.headers["Location"], timeout=120)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        for name in archive.namelist():
            if name.endswith("us_collection.json"):
                emit("us_collection", json.loads(archive.read(name)))
            elif name.endswith("us_daily_data_refresh_summary.csv"):
                frame = pd.read_csv(io.BytesIO(archive.read(name)), dtype=str).fillna("")
                emit("us_summary", {"columns": list(frame.columns), "rows": len(frame),
                    "counts": {c: frame[c].value_counts().head(30).to_dict()
                               for c in ("status", "latest", "latest_date", "error") if c in frame},
                    "examples": frame.groupby("status", group_keys=False).head(6).to_dict("records")
                                if "status" in frame else frame.head(20).to_dict("records")})


def restore_selected(root, market, month):
    client = Client(project="investment")
    head = client.json("GET", "/snapshot-heads/pipeline-state")
    sid = head["snapshot_id"]
    entries = {e["relative_path"]: e for e in client.snapshot_entries(sid)}
    packed = root / "packed"

    def download(relative):
        e = entries[relative]
        client.download(e["sha256"], safe_path(packed, relative), e["byte_size"])

    download("state-manifest.json")
    manifest = json.loads((packed / "state-manifest.json").read_text())
    price = "data/raw/" + live.PRICE_FILES[market]
    prefix = f"data/dashboard_ews/{market}/models/{month}/"
    selected = {p: info for p, info in manifest.items() if p == price or p.startswith(prefix)}
    if price not in selected or prefix + "model.json" not in selected:
        raise ValueError("Requested frozen model or price source is absent")
    needed = {p for p, info in selected.items() if info["type"] == "file"}
    needed.update(part for info in selected.values() for part in info.get("parts", []))
    for relative in sorted(needed):
        download(relative)
    (packed / "state-manifest.json").write_text(json.dumps(selected))
    restored = root / "restored"
    restore_state(packed, restored)
    emit("source", {"snapshot_id": sid, "selected_files": len(selected),
                    "download_bytes": sum(entries[p]["byte_size"] for p in needed),
                    "price_sha256": live.digest(restored / price)})
    return restored


def short_metrics(y, p):
    return {k: v for k, v in live.metrics(y, p).items() if k != "reliability"}


def scores(frame, head, card, boosters):
    linear = card["heads"][head]["linear"]
    x = frame[live.FEATURES].to_numpy(float)
    z = np.clip((np.where(np.isfinite(x), x, linear["median"]) - linear["mean"])
                / linear["scale"], -12, 12)
    lr = expit(z @ np.asarray(linear["coef"]) + linear["intercept"])
    tree = boosters[head].predict(frame[live.FEATURES], num_threads=1)
    raw = .5 * lr + .5 * tree
    return {"linear": lr, "tree": tree, "raw": raw,
            "final": live.calibrated(raw, card["heads"][head]["calibration"])}


def diagnose(root, market, month):
    card, boosters = live.load_month(root / f"data/dashboard_ews/{market}/models/{month}")
    price_path = root / "data/raw" / live.PRICE_FILES[market]
    prices = live.read_prices(price_path, card["cutoff"])
    # Hash mismatch is expected when subsequent sessions were appended. The old
    # card hashed the entire CSV, not a cutoff-bound immutable training slice.
    emit("model", {k: card[k] for k in ("id", "cutoff", "created_at", "training_price_sha256",
                                       "code_commit", "packages", "targets")})
    emit("prices", {"rows": len(prices), "tickers": int(prices.ticker.nunique()),
                    "first": str(prices.date.min().date()), "last": str(prices.date.max().date()),
                    "nonpositive_adjusted": int(prices.adjusted_close.le(0).sum()),
                    "missing_adjusted": int(prices.adjusted_close.isna().sum())})
    panel = live.feature_panel(prices, market, training=True)
    del prices
    from sklearn.metrics import roc_auc_score
    for head in live.HORIZONS:
        train, cal = live.chronological_split(panel, head, pd.Timestamp(card["cutoff"]))
        if len(train) > 180000:
            train = train.sample(180000, random_state=live.SEED).sort_values(["date", "ticker"])
        meta = card["heads"][head]
        predictions = scores(cal, head, card, boosters)
        y = cal[f"y_{head}"].astype(int).to_numpy()
        groups = []
        for date, indices in cal.reset_index(drop=True).groupby("date").groups.items():
            idx = np.asarray(indices)
            groups.append({"date": str(date.date()), **short_metrics(y[idx], predictions["raw"][idx]),
                           "mean_score": float(predictions["raw"][idx].mean())})
        shifts = []
        for feature in live.FEATURES:
            a, b = train[feature].dropna(), cal[feature].dropna()
            valid = cal[feature].notna()
            auc = roc_auc_score(y[valid], cal.loc[valid, feature]) if len(np.unique(y[valid])) == 2 else None
            shifts.append({"feature": feature, "train_mean": float(a.mean()), "cal_mean": float(b.mean()),
                           "mean_shift_train_sd": float((b.mean() - a.mean()) / max(float(a.std()), 1e-12)),
                           "cal_missing_fraction": float(1 - valid.mean()), "cal_feature_auc": auc})
        shifts.sort(key=lambda s: -abs(s["mean_shift_train_sd"]))
        published = meta["calibration_diagnostics_not_test"]
        actual = short_metrics(y, predictions["final"])
        train_scores = scores(train, head, card, boosters)
        emit(head, {"metadata": {k: v for k, v in meta.items() if k != "linear"},
            "reproduced": actual,
            "same_calibration_rows": len(cal) == published["rows"],
            "auc_difference": actual["auc"] - published["auc"],
            "train_rows": len(train), "train_event_rate": float(train[f"y_{head}"].mean()),
            "train_in_sample": {k: short_metrics(train[f"y_{head}"], p) for k, p in train_scores.items()},
            "calibration_components": {k: short_metrics(y, p) for k, p in predictions.items()},
            "calibration_date_count": len(groups), "calibration_by_date": groups,
            "feature_shifts": shifts,
            "constant_event_rate_brier": float(y.mean() * (1 - y.mean())),
            "label_end_min": str(cal[f"end_{head}"].min().date()),
            "label_end_max": str(cal[f"end_{head}"].max().date())})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=["kr", "us"], default="kr")
    parser.add_argument("--month", default="2026-09")
    parser.add_argument("--artifact", type=int)
    parser.add_argument("--root", type=Path, default=Path(".research-backend/artifacts/market-diagnosis"))
    args = parser.parse_args()
    if args.artifact:
        collection_summary(os.environ["GITHUB_REPOSITORY"], args.artifact)
    root = restore_selected(args.root, args.market, args.month)
    diagnose(root, args.market, args.month)


if __name__ == "__main__":
    main()
