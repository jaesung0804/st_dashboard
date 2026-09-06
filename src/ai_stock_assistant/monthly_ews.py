"""Monthly, point-in-time price models. No training is reachable from daily inference.

Artifacts and daily predictions are immutable. Historical simulations are explicitly
research records; a forecast cannot be created before its model became available.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from importlib.metadata import version as package_version
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import expit, logit

VERSION = "ews-price-v1"
SEED = 804
HORIZONS = {"up": 126, "down": 63}
FEATURES = [
    "ret20", "ret60", "ret120", "momentum252_20", "ma20", "ma60", "ma200",
    "drawdown60", "drawdown252", "vol20", "vol60", "downside60", "vol_expansion",
    "rsi14", "volume_shock", "log_turnover", "turnover_shock", "range20",
    "relative20", "relative60", "market_ret20", "market_ret60", "breadth200",
    "market_vol20", "dispersion20",
]
FEATURE_LABELS = {
    "ret20": "1개월 추세", "ret60": "3개월 추세", "ret120": "6개월 추세",
    "momentum252_20": "장기 모멘텀", "ma20": "20일선 거리", "ma60": "60일선 거리",
    "ma200": "200일선 거리", "drawdown60": "최근 고점 하락", "drawdown252": "연중 고점 하락",
    "vol20": "단기 변동성", "vol60": "중기 변동성", "downside60": "하방 변동성",
    "vol_expansion": "변동성 확대", "rsi14": "RSI", "volume_shock": "거래량 변화",
    "log_turnover": "거래대금", "turnover_shock": "거래대금 변화", "range20": "가격 진폭",
    "relative20": "단기 상대강도", "relative60": "중기 상대강도",
    "market_ret20": "시장 단기 추세", "market_ret60": "시장 중기 추세",
    "breadth200": "시장 상승 폭", "market_vol20": "시장 변동성", "dispersion20": "종목 간 격차",
}
PRICE_FILES = {"kr": "krx_ohlcv_kospi_kosdaq_state.csv", "us": "us_ohlcv_nasdaq_nyse_yfinfo_state.csv"}
LISTING_FILES = {"kr": "krx_listings_kospi_kosdaq_state.csv", "us": "us_listings_nasdaq_nyse_yfinfo_state.csv"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dumps(value))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def publish_directory(staged: Path, target: Path) -> None:
    """Same-filesystem rename, never replace a model or a published prediction."""
    if target.exists():
        raise FileExistsError(f"Immutable record already exists: {target}")
    staged.rename(target)


def read_prices(path: Path, end: str | pd.Timestamp | None = None) -> pd.DataFrame:
    numeric = ["open", "high", "low", "close", "adjusted_close", "volume"]
    df = pd.read_csv(path, dtype={"ticker": str, **{c: "float32" for c in numeric}}, usecols=["ticker", "date", *numeric])
    df["date"] = pd.to_datetime(df["date"])
    if end is not None:
        df = df.loc[df["date"] <= pd.Timestamp(end)]
    df = df.drop_duplicates(["ticker", "date"], keep="last")
    return df.sort_values(["ticker", "date"]).reset_index(drop=True)


def ticker_features(g: pd.DataFrame, labels: bool = False) -> pd.DataFrame:
    """All input features depend only on observations on or before their date."""
    g = g.reset_index(drop=True)
    p = g["adjusted_close"].where(g["adjusted_close"] > 0, g["close"]).astype(float)
    r = p.pct_change(fill_method=None)
    out = g[["date", "ticker", "close"]].copy()
    out["adjusted_close"] = p
    out["history"] = np.arange(1, len(g) + 1)
    for n in (20, 60, 120):
        out[f"ret{n}"] = p.pct_change(n, fill_method=None)
    out["momentum252_20"] = p.shift(20) / p.shift(252) - 1
    for n in (20, 60, 200):
        out[f"ma{n}"] = p / p.rolling(n, min_periods=n).mean() - 1
    for n in (60, 252):
        out[f"drawdown{n}"] = p / p.rolling(n, min_periods=n).max() - 1
    for n in (20, 60):
        out[f"vol{n}"] = r.rolling(n, min_periods=n).std() * np.sqrt(252)
    out["downside60"] = r.clip(upper=0).pow(2).rolling(60).mean().pow(.5) * np.sqrt(252)
    out["vol_expansion"] = out["vol20"] / out["vol60"].replace(0, np.nan)
    delta = p.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    out["rsi14"] = (gain / (gain + loss).replace(0, np.nan)).fillna(.5)
    volume = g["volume"].astype(float)
    value = g["close"].astype(float) * volume
    out["turnover20"] = value.rolling(20).mean()
    out["log_turnover"] = np.log1p(out["turnover20"])
    out["volume_shock"] = volume.rolling(5).mean() / volume.rolling(60).mean().replace(0, np.nan)
    out["turnover_shock"] = out["turnover20"] / value.rolling(60).mean().replace(0, np.nan)
    # Zero intraday quotes are unavailable observations, not zero volatility.
    # Keep close/volume features and close-based labels. A window with missing
    # ranges stays NaN for LightGBM / the fitted training-median imputation.
    known_ohl = g[["open", "high", "low"]].gt(0).all(axis=1)
    day_range = (g["high"] - g["low"]) / g["close"].replace(0, np.nan)
    out["range20"] = day_range.where(known_ohl).rolling(20).mean()
    out["active"] = (g["volume"] > 0) & (g["close"] > 0)
    # A stale/suspended quote must not masquerade as a liquid tradeable signal.
    out["return1"] = r.clip(-.3, .3)
    if labels:
        for head, n in HORIZONS.items():
            out[f"end_{head}"] = g["date"].shift(-n)
        out["future_up"] = p.shift(-HORIZONS["up"]) / p - 1
        n = HORIZONS["down"]
        forward_min = p.shift(-1).iloc[::-1].rolling(n, min_periods=n).min().iloc[::-1]
        out["future_down"] = forward_min / p - 1
        out["y_down"] = (out["future_down"] <= -.20).astype(float).where(out["end_down"].notna())
    return out.replace([np.inf, -np.inf], np.nan)


def feature_panel(prices: pd.DataFrame, market: str, *, training: bool, signal_date: str | None = None, signal_dates=None) -> pd.DataFrame:
    """Stream ticker features; retain weekly training rows or one inference date.

    Market context uses the same market's completed closes, never same-date US
    macro releases in a Korean signal. No financial statements with guessed dates.
    """
    dates = pd.Index(sorted(prices["date"].unique()))
    if training and signal_dates is not None:
        raise ValueError("Explicit inference dates cannot change training sampling")
    selected = set(dates[::5]) if training else (set(pd.to_datetime(signal_dates)) if signal_dates is not None else {pd.Timestamp(signal_date or dates[-1])})
    if not selected or not selected.issubset(set(dates)):
        raise ValueError("Inference dates must be observed market sessions")
    sums = np.zeros((len(dates), 4), dtype=np.float64)
    pieces = []
    for _, group in prices.groupby("ticker", sort=False, observed=True):
        # Only a year of history is needed for daily inference.
        if not training:
            if signal_dates is None:
                group = group.tail(320)
            else:
                # Keep the same 320-observation warmup for the earliest requested
                # date. Later signals never consume later prices: all rolling
                # windows remain backward-looking and contain no labels.
                group = group.loc[group.date <= max(selected)]
                start = max(0, group.date.searchsorted(min(selected), side="right") - 320)
                group = group.iloc[start:]
        f = ticker_features(group, labels=training)
        if "quality_valid" in group:
            # Optional research input only. Ordinary production price frames do
            # not contain this column, so their feature values stay unchanged.
            f["active"] &= group["quality_valid"].to_numpy(dtype=bool)
        valid = f["ma200"].notna() & f["active"]
        rows = f.loc[valid]
        ix = dates.get_indexer(rows["date"])
        r = rows["return1"].fillna(0).to_numpy()
        np.add.at(sums, ix, np.column_stack([r, r*r, (rows["ma200"] > 0).astype(float), np.ones(len(rows))]))
        keep = f["date"].isin(selected) & (f["history"] >= 253) & f["active"]
        keep &= f["turnover20"] >= (500_000_000 if market == "kr" else 5_000_000)
        keep &= f["close"] >= (1000 if market == "kr" else 1)
        pieces.append(f.loc[keep].drop(columns=["return1", "active"]))
    if not pieces:
        raise ValueError("No prices available")
    context = pd.DataFrame(index=dates)
    count = np.maximum(sums[:, 3], 1)
    mean = pd.Series(sums[:, 0] / count, index=dates)
    index = (1 + mean).cumprod()
    context["market_ret20"] = index.pct_change(20)
    context["market_ret60"] = index.pct_change(60)
    context["breadth200"] = sums[:, 2] / count
    context["market_vol20"] = mean.rolling(20).std() * np.sqrt(252)
    context["dispersion20"] = pd.Series(np.maximum(0, sums[:, 1] / count - mean.to_numpy()**2)**.5, index=dates).rolling(20).mean()
    panel = pd.concat(pieces, ignore_index=True).merge(context, left_on="date", right_index=True, validate="many_to_one")
    panel["relative20"] = panel["ret20"] - panel["market_ret20"]
    panel["relative60"] = panel["ret60"] - panel["market_ret60"]
    if training:
        # Reference universe is the liquid pool on the signal date, not today's listings.
        benchmark = panel.groupby("date")["future_up"].transform("median")
        panel["y_up"] = ((panel["future_up"] >= .20) & (panel["future_up"] - benchmark >= .10)).astype(float).where(panel["end_up"].notna())
    panel[FEATURES] = panel[FEATURES].astype("float32")
    return panel.sort_values(["date", "ticker"]).reset_index(drop=True)


def chronological_split(panel: pd.DataFrame, head: str, cutoff: pd.Timestamp, calibration_days: int = 13) -> tuple[pd.DataFrame, pd.DataFrame]:
    """13 weekly dates ~ three months; purge overlapping forward label windows."""
    known = panel.loc[panel[f"end_{head}"].le(cutoff) & panel[f"y_{head}"].notna()]
    dates = sorted(known["date"].unique())
    if len(dates) < calibration_days + 52:
        raise ValueError(f"Insufficient dated history for {head}")
    calibration_start = pd.Timestamp(dates[-calibration_days])
    train = known.loc[(known[f"end_{head}"] < calibration_start) & (known["date"] >= cutoff - pd.Timedelta(days=365 * 4))]
    calibration = known.loc[known["date"] >= calibration_start]
    for name, frame in [("train", train), ("calibration", calibration)]:
        if len(frame) < 1000 or frame[f"y_{head}"].nunique() < 2:
            raise ValueError(f"Insufficient {head} {name} examples/classes: {len(frame)}")
    return train, calibration


def probabilities(booster, linear: dict, frame: pd.DataFrame) -> np.ndarray:
    x = frame[FEATURES].to_numpy(dtype=float)
    x = np.where(np.isfinite(x), x, np.array(linear["median"]))
    x = np.clip((x - linear["mean"]) / linear["scale"], -12, 12)
    baseline = expit(x @ np.array(linear["coef"]) + linear["intercept"])
    return .5 * baseline + .5 * booster.predict(frame[FEATURES], num_threads=1)


def calibrated(raw: np.ndarray, calibration: dict) -> np.ndarray:
    adjusted = expit(calibration["coef"] * logit(np.clip(raw, 1e-6, 1-1e-6)) + calibration["intercept"])
    # A single recent regime must not completely replace the longer-run estimate.
    weight = calibration.get("weight", 1.0)
    return (1 - weight) * raw + weight * adjusted


def metrics(y, p) -> dict:
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    n = max(1, int(np.ceil(len(y) * .1)))
    threshold = np.sort(p)[-n]
    above, tied = p > threshold, p == threshold
    # Fractional inclusion of ties avoids a spurious "lift" from ticker order.
    top_rate = float((y[above].sum() + y[tied].mean() * (n - above.sum())) / n)
    prevalence = float(y.mean())
    return {
        "rows": len(y), "event_rate": prevalence,
        "brier": float(brier_score_loss(y, p)),
        "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
        "average_precision": float(average_precision_score(y, p)) if y.any() else None,
        "top_decile_event_rate": top_rate,
        "top_decile_lift": float(top_rate / prevalence) if prevalence else None,
        "reliability": [{"count": int(((p >= lo) & (p < hi)).sum()),
                         "predicted": float(p[(p >= lo) & (p < hi)].mean()),
                         "observed": float(y[(p >= lo) & (p < hi)].mean())}
                        for lo, hi in zip(np.arange(0, 1, .1), np.arange(.1, 1.01, .1))
                        if ((p >= lo) & (p < hi)).any()],
    }


def train_month(prices_path: Path, market: str, month: str, state_root: Path, *, jobs: int = 1, research: bool = False, max_rows: int = 180_000) -> Path:
    import lightgbm as lgb
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    boundary = pd.Timestamp(month + "-01")
    if boundary.strftime("%Y-%m") != month:
        raise ValueError("month must be YYYY-MM")
    if not research and month != datetime.now(timezone.utc).strftime("%Y-%m"):
        raise ValueError("Live training may only create the current month's artifact; use --research for simulations")
    target = state_root / market / "models" / month
    if target.exists():
        existing, _ = load_month(target)
        if bool(existing["research"]) != research:
            raise ValueError("Research and live artifacts must use separate state directories")
        print(f"Keeping immutable model {target}", flush=True)
        return target
    prices = read_prices(prices_path, boundary - pd.Timedelta(days=1))
    cutoff = prices["date"].max()
    if pd.isna(cutoff) or boundary - cutoff > pd.Timedelta(days=7):
        raise ValueError(f"Training prices stale at {cutoff}; refresh through the previous month first")
    panel = feature_panel(prices, market, training=True)
    del prices
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".training-", dir=target.parent))
    card = {"version": VERSION, "id": f"{market}-{month}-{VERSION}", "market": market,
            "month": month, "created_at": utc_now(), "research": research,
            "cutoff": cutoff.strftime("%Y-%m-%d"), "features": FEATURES, "seed": SEED,
            "training_price_sha256": digest(prices_path),
            "code_commit": os.environ.get("GITHUB_SHA", "local-research"),
            "packages": {name: package_version(name) for name in ("numpy", "pandas", "scipy", "scikit-learn", "lightgbm")},
            "targets": {"up": "126 sessions: return >=20% and >=10pp above signal-date liquid-pool median return",
                        "down": "63 sessions: any subsequent adjusted close <=80% of signal close"},
            "heads": {}, "files": {}}
    try:
        for head in HORIZONS:
            train, cal = chronological_split(panel, head, cutoff)
            if len(train) > max_rows:
                train = train.sample(max_rows, random_state=SEED).sort_values(["date", "ticker"])
            x, y = train[FEATURES], train[f"y_{head}"].astype(int)
            med = x.median().fillna(0).to_numpy(dtype=float)
            filled = np.where(np.isfinite(x), x, med)
            scaler = StandardScaler().fit(filled)
            z = np.clip(scaler.transform(filled), -12, 12)
            lr = LogisticRegression(C=.1, max_iter=500, random_state=SEED).fit(z, y)
            linear = {"median": med.tolist(), "mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(),
                      "coef": lr.coef_[0].tolist(), "intercept": float(lr.intercept_[0])}
            model = lgb.LGBMClassifier(n_estimators=180, learning_rate=.035, num_leaves=15,
                                      max_depth=5, min_child_samples=150, reg_lambda=10,
                                      colsample_bytree=.85, subsample=1, max_bin=63,
                                      random_state=SEED, n_jobs=max(1, jobs), verbosity=-1,
                                      deterministic=True, force_col_wise=True)
            model.fit(x, y)
            raw = probabilities(model.booster_, linear, cal)
            # No class weights: an imbalance-adjusted score is not an event probability.
            calibrator = LogisticRegression(C=1, max_iter=200).fit(logit(np.clip(raw, 1e-6, 1-1e-6)).reshape(-1, 1), cal[f"y_{head}"].astype(int))
            if calibrator.coef_[0, 0] < 0:
                raise ValueError(f"{head}: calibration reverses ranking; retain last model and investigate")
            calibration = {"coef": float(calibrator.coef_[0, 0]), "intercept": float(calibrator.intercept_[0]), "weight": .5}
            filename = f"{head}.txt"
            model.booster_.save_model(str(staged / filename))
            card["files"][filename] = digest(staged / filename)
            card["heads"][head] = {
                "linear": linear, "calibration": calibration, "train_rows": len(train),
                "train_start": str(train["date"].min().date()), "train_end": str(train["date"].max().date()),
                "train_label_end": str(train[f"end_{head}"].max().date()),
                "calibration_start": str(cal["date"].min().date()), "calibration_end": str(cal["date"].max().date()),
                "train_event_rate": float(y.mean()),
                "calibration_diagnostics_not_test": metrics(cal[f"y_{head}"], calibrated(raw, calibration)),
            }
            print(f"{market} {month} {head}: {len(train):,} train / {len(cal):,} calibration rows", flush=True)
        write_json(staged / "model.json", card)
        (staged / "model.sha256").write_text(digest(staged / "model.json"), encoding="ascii")
        publish_directory(staged, target)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return target


def load_month(path: Path) -> tuple[dict, dict]:
    import lightgbm as lgb
    if not path.exists():
        raise FileNotFoundError(f"Missing monthly model: {path}. Run Monthly Training first; daily inference never trains.")
    if digest(path / "model.json") != (path / "model.sha256").read_text(encoding="ascii").strip():
        raise ValueError(f"Model metadata checksum mismatch: {path}")
    card = read_json(path / "model.json")
    if card["version"] != VERSION or card["features"] != FEATURES:
        raise ValueError("Model feature schema differs from inference code")
    if set(card["files"]) != {f"{head}.txt" for head in HORIZONS}:
        raise ValueError("Unexpected model files in manifest")
    for name, expected in card["files"].items():
        if digest(path / name) != expected:
            raise ValueError(f"Model checksum mismatch: {name}")
    return card, {head: lgb.Booster(model_file=str(path / f"{head}.txt")) for head in HORIZONS}


def predict_panel(panel: pd.DataFrame, card: dict, boosters: dict) -> dict[str, np.ndarray]:
    return {head: calibrated(probabilities(boosters[head], card["heads"][head]["linear"], panel), card["heads"][head]["calibration"]) for head in HORIZONS}


def freeze_prediction(root: Path, market: str, signal: str, rows: list[dict], metadata: dict) -> Path:
    target = root / market / "predictions" / signal
    if target.exists():
        verify_prediction(target)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".prediction-", dir=target.parent))
    try:
        write_json(staged / "rows.json", rows)
        write_json(staged / "meta.json", {**metadata, "rows_sha256": digest(staged / "rows.json")})
        publish_directory(staged, target)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return target


def verify_prediction(path: Path) -> dict:
    meta = read_json(path / "meta.json")
    if digest(path / "rows.json") != meta["rows_sha256"]:
        raise ValueError(f"Frozen prediction checksum mismatch: {path}")
    return meta


def infer_latest(prices_path: Path, listings_path: Path, market: str, state_root: Path, *, asof: str | None = None, research: bool = False) -> Path:
    prices = read_prices(prices_path, asof)
    signal = prices["date"].max().strftime("%Y-%m-%d")
    if not research and pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() - pd.Timestamp(signal) > pd.Timedelta(days=7):
        raise ValueError(f"Refusing a stale live signal: {signal}")
    target = state_root / market / "predictions" / signal
    if target.exists():
        verify_prediction(target)
        print(f"Keeping frozen forecast for {market} {signal}", flush=True)
        return target
    if (state_root / market / "legacy" / f"{signal}.json").exists():
        print(f"Keeping previously published legacy forecast for {market} {signal}", flush=True)
        return state_root / market / "legacy" / f"{signal}.json"
    card, boosters = load_month(state_root / market / "models" / signal[:7])
    if card["month"] != signal[:7] or card["market"] != market:
        raise ValueError("Monthly model identity does not match this signal")
    if bool(card["research"]) != research:
        raise ValueError("Research and live artifacts must use separate state directories")
    if not research:
        if pd.Timestamp(signal) <= pd.Timestamp(card["cutoff"]):
            raise ValueError("A live forecast must follow the model's training cutoff")
    counts = prices.groupby("date")["ticker"].nunique()
    if counts.iloc[-1] < .8 * counts.tail(21).iloc[:-1].median():
        raise ValueError(f"Incomplete market coverage on {signal}; refusing to publish")
    panel = feature_panel(prices, market, training=False, signal_date=signal)
    if panel.empty:
        raise ValueError(f"No eligible stocks for {market} {signal}")
    probs = predict_panel(panel, card, boosters)
    raw_down = probabilities(boosters["down"], card["heads"]["down"]["linear"], panel)
    listings = pd.read_csv(listings_path, dtype={"ticker": str}).fillna("").drop_duplicates("ticker").set_index("ticker").to_dict("index")
    # Explain only the tree component; these are associations, not causal drivers.
    contrib = boosters["down"].predict(panel[FEATURES], pred_contrib=True, num_threads=1)[:, :-1]
    created = utc_now()
    kind = "research" if research else ("delayed" if signal < card["created_at"][:10] else "live")
    rows = []
    for i, (_, f) in enumerate(panel.iterrows()):
        ticker = str(f["ticker"])
        info = listings.get(ticker, {})
        up, down = float(probs["up"][i]), float(probs["down"][i])
        risk_low, risk_high = sorted([down, float(raw_down[i])])
        reasons = np.argsort(-np.abs(contrib[i]), kind="stable")[:2]
        rows.append({
            "date": signal, "ticker": ticker, "name": info.get("name", ticker),
            "sector": info.get("sector", "") or "미분류", "detailSector": info.get("industry", "") or "미분류",
            "exchange": info.get("exchange", ""), "close": round(float(f["close"]), 4),
            "closeRaw": round(float(f["close"]), 4), "currency": "KRW" if market == "kr" else "USD",
            "adjustedClose": float(f["adjusted_close"]),
            "upProb": round(up, 6), "downProb": round(down, 6),
            "upScore": round(up * 100, 2), "downRisk": round(down * 100, 2),
            "riskEstimateRange": [round(risk_low * 100, 2), round(risk_high * 100, 2)],
            "upGrade": "RED" if up >= .35 else "ORANGE" if up >= .20 else "YELLOW" if up >= .10 else "GREEN",
            "downGrade": "RED" if down >= .35 else "ORANGE" if down >= .20 else "YELLOW" if down >= .10 else "GREEN",
            "isUpCandidate": up >= .20, "isFinalCandidate": up >= .20 and risk_high < .15, "isDownRed": down >= .35,
            "modelVersion": card["id"], "modelMonth": card["month"], "trainingCutoff": card["cutoff"],
            "generatedAt": created, "predictionKind": kind,
            "evidence": {"return20": round(float(f["ret20"]) * 100, 2),
                         "relative60": round(float(f["relative60"]) * 100, 2),
                         "volatility20": round(float(f["vol20"]) * 100, 2),
                         "breadth200": round(float(f["breadth200"]) * 100, 2)},
            "riskFactors": [{"label": FEATURE_LABELS[FEATURES[j]], "direction": "위험 증가" if contrib[i, j] > 0 else "위험 감소"} for j in reasons],
        })
    rows.sort(key=lambda row: (-row["upProb"], row["downProb"], row["ticker"]))
    feature_hash = hashlib.sha256(pd.util.hash_pandas_object(panel[["date", "ticker", *FEATURES]], index=False).to_numpy().tobytes()).hexdigest()
    return freeze_prediction(state_root, market, signal, rows, {"model": card["id"], "created_at": created,
                             "prediction_kind": kind, "feature_sha256": feature_hash, "rows": len(rows)})


def migrate_legacy(pages_root: Path, state_root: Path) -> None:
    """Keep byte-identical published history, explicitly outside the live ledger."""
    for market in PRICE_FILES:
        source = pages_root / f"lgbm_warning_dashboard_macro_{market}_latest" / "walkforward_scores_by_date"
        if not source.exists():
            raise FileNotFoundError(f"Cannot preserve published history: {source}")
        for old in sorted(source.glob("*.json")):
            target = state_root / market / "legacy" / old.name
            if not target.exists() and not (state_root / market / "predictions" / old.stem).exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(old, target)
        marker = state_root / market / "legacy-manifest.json"
        if not marker.exists():
            write_json(marker, {"imported_at": utc_now(), "kind": "legacy_unversioned",
                              "files": {p.name: digest(p) for p in sorted((state_root / market / "legacy").glob("*.json"))}})


def export_dashboard(state_root: Path, market: str, output_root: Path, days: int = 60) -> None:
    root = state_root / market
    reconstructed = state_root.parent / "dashboard_research" / "reconstruction" / market
    files = {}
    kinds = {}
    for p in (reconstructed / "predictions").glob("*/rows.json"):
        meta = verify_prediction(p.parent)
        if meta["prediction_kind"] != "reconstructed":
            raise ValueError("Historical reconstruction must be labelled explicitly")
        files[p.parent.name] = p
        kinds[p.parent.name] = "reconstructed"
    # Stored contemporaneous or legacy records always take precedence.
    for p in (root / "legacy").glob("*.json"):
        files[p.stem] = p
        kinds[p.stem] = "legacy_unversioned"
    for p in (root / "predictions").glob("*/rows.json"):
        meta = verify_prediction(p.parent)
        files[p.parent.name] = p
        kinds[p.parent.name] = meta["prediction_kind"]
    dates = sorted(files, reverse=True)[:days]
    if not dates:
        raise ValueError(f"No archived predictions for {market}")
    dest = output_root / f"lgbm_warning_dashboard_macro_{market}_latest"
    folder = dest / "walkforward_scores_by_date"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    for signal in dates:
        shutil.copyfile(files[signal], folder / f"{signal}.json")
    models = {}
    for month in sorted({d[:7] for d in dates}):
        path = root / "models" / month / "model.json"
        if not path.exists():
            path = reconstructed / "models" / month / "model.json"
        if path.exists():
            card = read_json(path)
            models[month] = {k: card[k] for k in ("id", "month", "cutoff", "created_at", "targets", "research")}
    evaluations = sorted((root / "evaluations").glob("*.json"))
    assessment = read_json(evaluations[-1]) if evaluations else {}
    write_json(dest / "manifest.json", {"dates": dates, "latest": dates[0], "dateCount": len(dates),
                                        "models": models, "predictionPolicy": "immutable",
                                        "predictionKindsByDate": {d: kinds[d] for d in dates},
                                        "validation": assessment.get("results", []),
                                        "evaluatedAt": assessment.get("created_at")})


def evaluate_archive(prices_path: Path, market: str, state_root: Path) -> Path:
    """Monthly outcome snapshot, stored separately so forecasts never get edited.

    Suspended/delisted/unmatured observations remain missing, not false negatives.
    Adjusted-price outcomes use the current data vintage, recorded by its hash.
    """
    root = state_root / market
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    target = root / "evaluations" / f"{month}.json"
    if target.exists():
        return target
    archives = sorted((root / "predictions").glob("*/rows.json"))
    if not archives:
        write_json(target, {"created_at": utc_now(), "results": [], "reason": "No live forecasts yet"})
        return target
    prices = read_prices(prices_path)
    groups = {ticker: group.reset_index(drop=True) for ticker, group in prices.groupby("ticker", sort=False)}
    calendar = sorted(prices["date"].unique())
    buckets: dict[tuple[str, str], dict] = {}
    for path in archives:
        meta = verify_prediction(path.parent)
        if meta["prediction_kind"] == "research":
            continue
        signal = pd.Timestamp(path.parent.name)
        rows = read_json(path)
        for head, horizon in HORIZONS.items():
            if sum(d > signal for d in calendar) < horizon:
                continue
            known = []
            for row in rows:
                g = groups.get(row["ticker"])
                if g is None:
                    continue
                matches = g.index[g["date"] == signal]
                if len(matches) != 1 or matches[0] + horizon >= len(g):
                    continue
                i = matches[0]
                p = g["adjusted_close"].where(g["adjusted_close"] > 0, g["close"])
                value = (p.iloc[i + horizon] if head == "up" else p.iloc[i+1:i+horizon+1].min()) / p.iloc[i] - 1
                if np.isfinite(value):
                    known.append((row, float(value)))
            if not known:
                continue
            benchmark = float(np.median([value for _, value in known]))
            for row, value in known:
                key = (row["modelVersion"], head)
                bucket = buckets.setdefault(key, {"y": [], "p": [], "dates": set(), "available": 0, "total": 0})
                bucket["y"].append(int(value >= .20 and value - benchmark >= .10) if head == "up" else int(value <= -.20))
                bucket["p"].append(row["upProb" if head == "up" else "downProb"])
                bucket["dates"].add(str(signal.date()))
            # Every date uses a single model version. Explicitly report missing coverage.
            bucket["available"] += len(known)
            bucket["total"] += len(rows)
    results = [{"model": model, "head": head, "signal_dates": len(b["dates"]),
                "coverage": b["available"] / b["total"], "missing": b["total"] - b["available"],
                **metrics(b["y"], b["p"])} for (model, head), b in sorted(buckets.items())]
    write_json(target, {"created_at": utc_now(), "price_sha256": digest(prices_path),
                        "price_cutoff": str(prices["date"].max().date()), "results": results,
                        "basis": "Adjusted close, signal-close reference, no execution/cost assumptions; delayed signals included; missing outcomes excluded and counted"})
    return target
