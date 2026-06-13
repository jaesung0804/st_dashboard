from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.metrics import roc_auc_score

from ai_stock_assistant.features import FEATURE_COLUMNS


FEATURES_PATH = Path("outputs/daily_features_full/training_features_daily.csv")
SECTOR_MAP_PATH = Path("data/raw/krx_kospi_kosdaq_detailed_sector_map.xlsx")
OUT_DIR = Path("outputs/walkforward_warning")

MIN_TRADING_VALUE = 500_000_000
MIN_CLOSE = 1_000
UP_TAIL = 0.05
DOWN_TAIL = 0.05
DOWN_GREEN_CUTOFF = 0.35
HORIZONS = [("1m", 20), ("3m", 63), ("6m", 126), ("12m", 252)]
SUBSCORES = {
    "growth_profit": {
        "label": "성장·수익성",
        "high_good": True,
        "features": ["revenue_yoy", "operating_income_yoy", "eps_yoy", "operating_margin", "gross_margin"],
    },
    "cash_quality": {
        "label": "현금흐름 품질",
        "high_good": True,
        "features": ["cfo_margin", "fcf_margin", "cfo_to_net_income", "fcf_to_net_income", "operating_cash_flow_to_debt"],
    },
    "valuation": {
        "label": "밸류에이션",
        "high_good": True,
        "features": ["psr_low", "pbr_low", "cash_to_market_cap", "price_to_52w_low"],
    },
    "price_volume": {
        "label": "가격·거래량",
        "high_good": True,
        "features": ["return_20d", "return_60d", "return_120d", "breakout_20d_high", "volume_ratio_20d_to_60d"],
    },
    "risk_overheat": {
        "label": "위험·과열",
        "high_good": False,
        "features": [
            "debt_to_equity",
            "equity_ratio_low",
            "fcf_margin_low",
            "parabolic_move_score",
            "upper_wick_ratio",
            "volatility_expansion",
            "avg_trading_value_20d_low",
        ],
    },
}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="auto", help="YYYY-MM-DD, or auto for earliest inferable date.")
    p.add_argument("--end", default="auto", help="YYYY-MM-DD, or auto for latest available date.")
    p.add_argument("--frequency", choices=["daily", "month-end"], default="daily")
    p.add_argument("--market", choices=["kr", "us"], default="kr")
    p.add_argument("--features-path", default=str(FEATURES_PATH))
    p.add_argument("--listings-path", default=None)
    p.add_argument("--sector-map-path", default=str(SECTOR_MAP_PATH))
    p.add_argument("--out-dir", default=str(OUT_DIR))
    p.add_argument("--min-trading-value", type=float, default=None)
    p.add_argument("--min-close", type=float, default=None)
    return p


def normalize_ticker(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip()
    return text.where(~text.str.fullmatch(r"\d{1,6}"), text.str.zfill(6))


def load_kr_sector(path: Path) -> pd.DataFrame:
    sec = pd.read_excel(
        path,
        sheet_name="SectorMap",
        dtype={"ticker": str},
        usecols=["ticker", "company_name", "security_type", "model_sector", "model_industry", "theme_tags"],
    )
    sec["ticker"] = normalize_ticker(sec["ticker"])
    return sec.rename(
        columns={
            "company_name": "name",
            "model_sector": "sector",
            "model_industry": "detailSector",
            "theme_tags": "theme",
        }
    )


def load_us_sector(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame(columns=["ticker", "name", "security_type", "sector", "detailSector", "theme"])
    listings = pd.read_csv(path, dtype={"ticker": str})
    listings["ticker"] = normalize_ticker(listings["ticker"])
    return pd.DataFrame(
        {
            "ticker": listings["ticker"],
            "name": listings.get("name", listings["ticker"]),
            "security_type": "common",
            "sector": listings.get("sector", listings.get("exchange", "")),
            "detailSector": listings.get("industry", listings.get("exchange", "")),
            "theme": "",
        }
    )


def add_targets(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.sort_values(["ticker", "date"]).copy()
    for _, days in HORIZONS:
        col = f"future_return_{days}d"
        if col not in frame.columns:
            frame[col] = frame.groupby("ticker")["adj_close"].shift(-days) / frame["adj_close"] - 1
    frame["up_cutoff_6m"] = frame.groupby("date")["future_return_126d"].transform(lambda x: x.quantile(1 - UP_TAIL))
    frame["down_cutoff_6m"] = frame.groupby("date")["future_return_126d"].transform(lambda x: x.quantile(DOWN_TAIL))
    frame["up_label_6m_top5"] = (frame["future_return_126d"] >= frame["up_cutoff_6m"]).astype(int)
    frame["down_label_6m_bottom5"] = (frame["future_return_126d"] <= frame["down_cutoff_6m"]).astype(int)
    return frame


def static_pool(frame: pd.DataFrame, require_target: bool, min_trading_value: float, min_close: float) -> pd.DataFrame:
    mask = (
        ~frame["security_type"].eq("preferred")
        & (pd.to_numeric(frame["avg_trading_value_20d"], errors="coerce").fillna(0) >= min_trading_value)
        & (pd.to_numeric(frame["adj_close"], errors="coerce").fillna(0) > min_close)
    )
    if require_target:
        mask &= frame["future_return_126d"].notna()
    return frame.loc[mask].copy()


def signal_dates(frame: pd.DataFrame, start: str, end: str, frequency: str) -> list[pd.Timestamp]:
    dates = pd.Series(pd.to_datetime(frame["date"].drop_duplicates())).sort_values()
    start_date = dates.min() if str(start).lower() == "auto" else pd.Timestamp(start)
    end_date = dates.max() if str(end).lower() == "auto" else pd.Timestamp(end)
    dates = dates[(dates >= start_date) & (dates <= end_date)]
    if frequency == "month-end":
        dates = dates.groupby(dates.dt.to_period("M")).max()
    return [pd.Timestamp(x) for x in dates.tolist()]


def cutoff_for_signal(all_dates: list[pd.Timestamp], signal: pd.Timestamp, horizon: int) -> pd.Timestamp:
    idx = max(i for i, d in enumerate(all_dates) if d <= signal)
    return all_dates[max(0, idx - horizon)]


def select_features(train: pd.DataFrame, target: str, top_n: int = 35) -> list[str]:
    cols = [c for c in FEATURE_COLUMNS if c in train.columns]
    x_all = train[cols].replace([np.inf, -np.inf], np.nan)
    usable = [c for c in cols if x_all[c].notna().mean() >= 0.60 and x_all[c].nunique(dropna=True) > 5]
    y = train[target].astype(int)
    scores = []
    for col in usable:
        x = pd.to_numeric(train[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
        auc = np.nan
        if x.notna().sum() >= 500 and x.nunique(dropna=True) > 5 and y.nunique() == 2:
            try:
                raw = roc_auc_score(y, x.fillna(x.median()))
                auc = max(raw, 1 - raw)
            except ValueError:
                pass
        scores.append((col, auc))
    ranked = [c for c, a in sorted(scores, key=lambda x: -1 if pd.isna(x[1]) else x[1], reverse=True)[:top_n]]
    return ranked or usable[:top_n]


def fit_classifier(train: pd.DataFrame, target: str, features: list[str], seed: int) -> LGBMClassifier:
    y = train[target].astype(int)
    pos = y.sum()
    neg = len(y) - pos
    model = LGBMClassifier(
        objective="binary",
        n_estimators=160,
        learning_rate=0.04,
        num_leaves=31,
        min_child_samples=45,
        subsample=0.82,
        colsample_bytree=0.82,
        scale_pos_weight=(neg / pos if pos else 1.0),
        n_jobs=-1,
        random_state=seed,
        verbosity=-1,
    )
    model.fit(train[features].replace([np.inf, -np.inf], np.nan), y)
    return model


def fit_regressor(train: pd.DataFrame, features: list[str], target: str, seed: int) -> LGBMRegressor:
    data = train.dropna(subset=[target]).copy()
    model = LGBMRegressor(
        objective="regression",
        n_estimators=120,
        learning_rate=0.04,
        num_leaves=31,
        min_child_samples=45,
        subsample=0.82,
        colsample_bytree=0.82,
        n_jobs=-1,
        random_state=seed,
        verbosity=-1,
    )
    model.fit(data[features].replace([np.inf, -np.inf], np.nan), data[target].clip(-0.85, 2.5))
    return model


def grade(rank: pd.Series) -> pd.Series:
    return np.select([rank <= 0.05, rank <= 0.15, rank <= 0.35], ["RED", "ORANGE", "YELLOW"], default="GREEN")


def pct_rank(frame: pd.DataFrame, col: str, high_good: bool = True) -> pd.Series:
    if col.endswith("_low"):
        base = col.removesuffix("_low")
        values = pd.to_numeric(frame[base], errors="coerce") if base in frame.columns else pd.Series(np.nan, index=frame.index)
        return values.rank(pct=True, ascending=True) * 100
    values = pd.to_numeric(frame[col], errors="coerce") if col in frame.columns else pd.Series(np.nan, index=frame.index)
    return values.rank(pct=True, ascending=not high_good) * 100


def add_subscores(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for key, spec in SUBSCORES.items():
        parts = [pct_rank(out, col, high_good=bool(spec["high_good"])) for col in spec["features"]]
        out[key] = pd.concat(parts, axis=1).mean(axis=1).fillna(50).round(0)
    return out


def fmt_pct(x: float | None) -> str:
    return "미확정" if pd.isna(x) else f"{x * 100:.1f}%"


def fmt_price(x: float | None) -> str:
    return "미확정" if pd.isna(x) else f"{int(round(float(x))):,}"


def validation_auc(model: LGBMClassifier, val: pd.DataFrame, features: list[str], target: str) -> float:
    if val.empty or val[target].nunique() < 2:
        return np.nan
    p = model.predict_proba(val[features].replace([np.inf, -np.inf], np.nan))[:, 1]
    return roc_auc_score(val[target].astype(int), p)


def score_one_date(
    score: pd.DataFrame,
    sig: pd.Timestamp,
    all_dates: list[pd.Timestamp],
    labeled: pd.DataFrame,
    up_model: LGBMClassifier,
    down_model: LGBMClassifier,
    regressors: dict[str, LGBMRegressor],
    up_features: list[str],
    down_features: list[str],
    ret_features: list[str],
) -> pd.DataFrame:
    score = score.copy()
    score["up_lgbm_prob"] = up_model.predict_proba(score[up_features].replace([np.inf, -np.inf], np.nan))[:, 1]
    score["down_lgbm_prob"] = down_model.predict_proba(score[down_features].replace([np.inf, -np.inf], np.nan))[:, 1]
    score["up_rank_pct"] = score["up_lgbm_prob"].rank(pct=True, ascending=False, method="first")
    score["down_rank_pct"] = score["down_lgbm_prob"].rank(pct=True, ascending=False, method="first")
    score["upGrade"] = grade(score["up_rank_pct"])
    score["downGrade"] = grade(score["down_rank_pct"])
    score["upScore"] = ((1 - score["up_rank_pct"]) * 100).round(1)
    score["downRisk"] = ((1 - score["down_rank_pct"]) * 100).round(1)
    score["isUpCandidate"] = score["up_rank_pct"] <= UP_TAIL
    score["isDownRed"] = score["down_rank_pct"] <= DOWN_TAIL
    score["isFinalCandidate"] = score["isUpCandidate"] & (score["down_rank_pct"] > DOWN_GREEN_CUTOFF)
    for key, _ in HORIZONS:
        reg = regressors[key]
        pred = reg.predict(score[ret_features].replace([np.inf, -np.inf], np.nan)).clip(-0.85, 2.5)
        score[f"expRet_{key}"] = [fmt_pct(x) for x in pred]
        score[f"expClose_{key}"] = [fmt_price(c * (1 + r)) for c, r in zip(score["adj_close"], pred)]
        actual = score.get(f"future_return_{dict(HORIZONS)[key]}d", pd.Series(np.nan, index=score.index))
        score[f"actRet_{key}"] = [fmt_pct(x) for x in actual]
        score[f"actClose_{key}"] = [fmt_price(c * (1 + r) if not pd.isna(r) else np.nan) for c, r in zip(score["adj_close"], actual)]
    score = add_subscores(score)
    score["close"] = score["adj_close"].map(fmt_price)
    score["date"] = sig.strftime("%Y-%m-%d")
    return score


def main() -> None:
    args = parser().parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    min_trading_value = args.min_trading_value if args.min_trading_value is not None else (MIN_TRADING_VALUE if args.market == "kr" else 5_000_000)
    min_close = args.min_close if args.min_close is not None else (MIN_CLOSE if args.market == "kr" else 1)
    if args.market == "kr":
        sector = load_kr_sector(Path(args.sector_map_path))
    else:
        sector = load_us_sector(Path(args.listings_path) if args.listings_path else None)
    usecols = (
        {"date", "ticker", "adj_close", "avg_trading_value_20d", "future_return_20d", "future_return_63d", "future_return_126d", "future_return_252d"}
        | set(FEATURE_COLUMNS)
    )
    raw = pd.read_csv(Path(args.features_path), dtype={"ticker": str}, usecols=lambda c: c in usecols)
    raw["ticker"] = normalize_ticker(raw["ticker"])
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw.merge(sector, on="ticker", how="left")
    raw["security_type"] = raw["security_type"].fillna("common")
    raw["sector"] = raw["sector"].fillna("미분류")
    raw["detailSector"] = raw["detailSector"].fillna("미분류")
    raw["theme"] = raw["theme"].fillna("")
    raw = add_targets(raw)
    pool = static_pool(raw, require_target=False, min_trading_value=min_trading_value, min_close=min_close)
    labeled = static_pool(raw, require_target=True, min_trading_value=min_trading_value, min_close=min_close)
    all_dates = sorted(pd.to_datetime(pool["date"].drop_duplicates()).tolist())
    signals = signal_dates(pool, args.start, args.end, args.frequency)
    if not signals:
        raise RuntimeError("No signal dates found.")

    all_scores = []
    validations = []
    for fold, (period, period_signals) in enumerate(pd.Series(signals).groupby(pd.Series(signals).dt.to_period("M")), 1):
        month_signals = [pd.Timestamp(x) for x in period_signals.tolist()]
        train_signal = min(month_signals)
        cutoff_6m = cutoff_for_signal(all_dates, train_signal, 126)
        train_all = labeled[labeled["date"] <= cutoff_6m].copy()
        val_start = cutoff_6m - pd.DateOffset(months=6)
        train_core = train_all[train_all["date"] < val_start].copy()
        val = train_all[train_all["date"] >= val_start].copy()
        if len(train_core) < 50_000 or val.empty:
            continue

        up_features = select_features(train_core, "up_label_6m_top5")
        down_features = select_features(train_core, "down_label_6m_bottom5")
        ret_features = sorted(set(up_features + down_features))
        up_val_model = fit_classifier(train_core, "up_label_6m_top5", up_features, seed=fold)
        down_val_model = fit_classifier(train_core, "down_label_6m_bottom5", down_features, seed=fold + 100)
        validations.append(
            {
                "signal_month": str(period),
                "label_cutoff": cutoff_6m.date(),
                "train_rows": len(train_core),
                "validation_rows": len(val),
                "up_auc": validation_auc(up_val_model, val, up_features, "up_label_6m_top5"),
                "down_auc": validation_auc(down_val_model, val, down_features, "down_label_6m_bottom5"),
                "signal_days": len(month_signals),
            }
        )

        up_model = fit_classifier(train_all, "up_label_6m_top5", up_features, seed=fold + 200)
        down_model = fit_classifier(train_all, "down_label_6m_bottom5", down_features, seed=fold + 300)
        regressors = {}
        for key, days in HORIZONS:
            cutoff_h = cutoff_for_signal(all_dates, train_signal, days)
            train_h = labeled[labeled["date"] <= cutoff_h].copy()
            regressors[key] = fit_regressor(train_h, ret_features, f"future_return_{days}d", seed=days + fold)

        for sig in month_signals:
            score = pool[pool["date"].eq(sig)].copy()
            scored = score_one_date(
                score,
                sig,
                all_dates,
                labeled,
                up_model,
                down_model,
                regressors,
                up_features,
                down_features,
                ret_features,
            )
            all_scores.append(scored)
            print(
                f"{sig.date()} trained_month={period} cutoff={cutoff_6m.date()} "
                f"up={int(scored['isUpCandidate'].sum())} down_red={int(scored['isDownRed'].sum())} "
                f"final={int(scored['isFinalCandidate'].sum())}",
                flush=True,
            )

    scores = pd.concat(all_scores, ignore_index=True)
    cols = [
        "date", "ticker", "name", "sector", "detailSector", "theme", "close", "upScore", "up_lgbm_prob", "upGrade",
        "downRisk", "down_lgbm_prob", "downGrade", "isUpCandidate", "isDownRed", "isFinalCandidate",
        "expRet_1m", "expClose_1m", "actRet_1m", "actClose_1m",
        "expRet_3m", "expClose_3m", "actRet_3m", "actClose_3m",
        "expRet_6m", "expClose_6m", "actRet_6m", "actClose_6m",
        "expRet_12m", "expClose_12m", "actRet_12m", "actClose_12m",
        *SUBSCORES.keys(),
    ]
    for col in cols:
        if col not in scores:
            scores[col] = ""
    scores[cols].to_csv(out_dir / "walkforward_scores.csv", index=False, encoding="utf-8-sig")
    scores.loc[scores["isFinalCandidate"], cols].sort_values(["date", "upScore"], ascending=[True, False]).to_csv(
        out_dir / "walkforward_candidates.csv",
        index=False,
        encoding="utf-8-sig",
    )
    scores.loc[scores["isUpCandidate"], cols].sort_values(["date", "upScore"], ascending=[True, False]).to_csv(
        out_dir / "walkforward_up_candidates.csv",
        index=False,
        encoding="utf-8-sig",
    )
    scores.loc[scores["isDownRed"], cols].sort_values(["date", "downRisk"], ascending=[True, False]).to_csv(
        out_dir / "walkforward_down_red.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame(validations).to_csv(out_dir / "walkforward_validation.csv", index=False, encoding="utf-8-sig")
    print(out_dir / "walkforward_scores.csv")
    print(out_dir / "walkforward_candidates.csv")


if __name__ == "__main__":
    main()
