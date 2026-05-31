from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.metrics import roc_auc_score

from ai_stock_assistant.features import FEATURE_COLUMNS, FINANCIAL_FEATURES


FEATURES_PATH = Path("outputs/daily_features_full/training_features_daily.csv")
SECTOR_MAP_PATH = Path("data/raw/krx_kospi_kosdaq_detailed_sector_map.xlsx")
OUT_DIR = Path("outputs/walkforward_warning")

MIN_TRADING_VALUE = 500_000_000
MIN_CLOSE = 1_000
UP_TAIL = 0.05
DOWN_GREEN_CUTOFF = 0.35
HORIZONS = [("1m", 20), ("3m", 63), ("6m", 126), ("12m", 252)]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2025-12-01")
    p.add_argument("--end", default="2026-05-31")
    return p


def load_sector() -> pd.DataFrame:
    sec = pd.read_excel(
        SECTOR_MAP_PATH,
        sheet_name="SectorMap",
        dtype={"ticker": str},
        usecols=["ticker", "company_name", "security_type", "model_sector", "model_industry", "theme_tags"],
    )
    sec["ticker"] = sec["ticker"].astype(str).str.zfill(6)
    return sec.rename(
        columns={
            "company_name": "name",
            "model_sector": "sector",
            "model_industry": "detailSector",
            "theme_tags": "theme",
        }
    )


def add_targets(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.sort_values(["ticker", "date"]).copy()
    for _, days in HORIZONS:
        col = f"future_return_{days}d"
        if col not in frame.columns:
            frame[col] = frame.groupby("ticker")["adj_close"].shift(-days) / frame["adj_close"] - 1
    frame["up_cutoff_6m"] = frame.groupby("date")["future_return_126d"].transform(lambda x: x.quantile(1 - UP_TAIL))
    frame["down_cutoff_6m"] = frame.groupby("date")["future_return_126d"].transform(lambda x: x.quantile(UP_TAIL))
    frame["up_label_6m_top5"] = (frame["future_return_126d"] >= frame["up_cutoff_6m"]).astype(int)
    frame["down_label_6m_bottom5"] = (frame["future_return_126d"] <= frame["down_cutoff_6m"]).astype(int)
    return frame


def static_pool(frame: pd.DataFrame, require_target: bool) -> pd.DataFrame:
    mask = (
        ~frame["security_type"].eq("preferred")
        & (pd.to_numeric(frame["avg_trading_value_20d"], errors="coerce").fillna(0) >= MIN_TRADING_VALUE)
        & (pd.to_numeric(frame["adj_close"], errors="coerce").fillna(0) > MIN_CLOSE)
    )
    if require_target:
        mask &= frame["future_return_126d"].notna()
    return frame.loc[mask].copy()


def signal_dates(frame: pd.DataFrame, start: str, end: str) -> list[pd.Timestamp]:
    dates = pd.Series(pd.to_datetime(frame["date"].drop_duplicates())).sort_values()
    mask = (dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))
    monthly = dates[mask].groupby(dates[mask].dt.to_period("M")).max()
    return [pd.Timestamp(x) for x in monthly.tolist()]


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
    values = pd.to_numeric(frame[col], errors="coerce") if col in frame.columns else pd.Series(np.nan, index=frame.index)
    return values.rank(pct=True, ascending=not high_good) * 100


def add_subscores(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["성장·수익성"] = pd.concat([pct_rank(out, c) for c in ["revenue_yoy", "operating_income_yoy", "eps_yoy", "operating_margin", "gross_margin"]], axis=1).mean(axis=1).fillna(50).round(0)
    out["현금흐름·품질"] = pd.concat([pct_rank(out, c) for c in ["cfo_margin", "fcf_margin", "cfo_to_net_income", "fcf_to_net_income", "operating_cash_flow_to_debt"]], axis=1).mean(axis=1).fillna(50).round(0)
    out["밸류에이션"] = pd.concat([pct_rank(out, "psr", False), pct_rank(out, "pbr", False), pct_rank(out, "cash_to_market_cap"), pct_rank(out, "price_to_52w_low")], axis=1).mean(axis=1).fillna(50).round(0)
    out["가격·거래량"] = pd.concat([pct_rank(out, c) for c in ["return_20d", "return_60d", "return_120d", "breakout_20d_high", "volume_ratio_20d_to_60d"]], axis=1).mean(axis=1).fillna(50).round(0)
    out["위험·과열"] = pd.concat([pct_rank(out, "debt_to_equity"), pct_rank(out, "equity_ratio", False), pct_rank(out, "fcf_margin", False), pct_rank(out, "parabolic_move_score"), pct_rank(out, "upper_wick_ratio"), pct_rank(out, "volatility_expansion"), pct_rank(out, "avg_trading_value_20d", False)], axis=1).mean(axis=1).fillna(50).round(0)
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


def main() -> None:
    args = parser().parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sector = load_sector()
    usecols = set(["date", "ticker", "adj_close", "avg_trading_value_20d", "future_return_20d", "future_return_63d", "future_return_126d"]) | set(FEATURE_COLUMNS)
    raw = pd.read_csv(FEATURES_PATH, dtype={"ticker": str}, usecols=lambda c: c in usecols)
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw.merge(sector, on="ticker", how="left")
    raw["security_type"] = raw["security_type"].fillna("common")
    raw["sector"] = raw["sector"].fillna("미분류")
    raw["detailSector"] = raw["detailSector"].fillna("미분류")
    raw["theme"] = raw["theme"].fillna("")
    raw = add_targets(raw)
    pool = static_pool(raw, require_target=False)
    labeled = static_pool(raw, require_target=True)
    all_dates = sorted(pd.to_datetime(pool["date"].drop_duplicates()).tolist())
    signals = signal_dates(pool, args.start, args.end)
    all_candidates = []
    validations = []
    for fold, sig in enumerate(signals, 1):
        cutoff_6m = cutoff_for_signal(all_dates, sig, 126)
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
                "signal_date": sig.date(),
                "label_cutoff": cutoff_6m.date(),
                "train_rows": len(train_core),
                "validation_rows": len(val),
                "up_auc": validation_auc(up_val_model, val, up_features, "up_label_6m_top5"),
                "down_auc": validation_auc(down_val_model, val, down_features, "down_label_6m_bottom5"),
            }
        )
        up_model = fit_classifier(train_all, "up_label_6m_top5", up_features, seed=fold + 200)
        down_model = fit_classifier(train_all, "down_label_6m_bottom5", down_features, seed=fold + 300)
        score = pool[pool["date"].eq(sig)].copy()
        score["up_lgbm_prob"] = up_model.predict_proba(score[up_features].replace([np.inf, -np.inf], np.nan))[:, 1]
        score["down_lgbm_prob"] = down_model.predict_proba(score[down_features].replace([np.inf, -np.inf], np.nan))[:, 1]
        score["up_rank_pct"] = score["up_lgbm_prob"].rank(pct=True, ascending=False, method="first")
        score["down_rank_pct"] = score["down_lgbm_prob"].rank(pct=True, ascending=False, method="first")
        score["upGrade"] = grade(score["up_rank_pct"])
        score["downGrade"] = grade(score["down_rank_pct"])
        score["upScore"] = ((1 - score["up_rank_pct"]) * 100).round(1)
        score["downRisk"] = ((1 - score["down_rank_pct"]) * 100).round(1)
        for key, days in HORIZONS:
            cutoff_h = cutoff_for_signal(all_dates, sig, days)
            train_h = labeled[labeled["date"] <= cutoff_h].copy()
            reg = fit_regressor(train_h, ret_features, f"future_return_{days}d", seed=days + fold)
            pred = reg.predict(score[ret_features].replace([np.inf, -np.inf], np.nan)).clip(-0.85, 2.5)
            score[f"expRet_{key}"] = [fmt_pct(x) for x in pred]
            score[f"expClose_{key}"] = [fmt_price(c * (1 + r)) for c, r in zip(score["adj_close"], pred)]
            actual = score.get(f"future_return_{days}d", pd.Series(np.nan, index=score.index))
            score[f"actRet_{key}"] = [fmt_pct(x) for x in actual]
            score[f"actClose_{key}"] = [fmt_price(c * (1 + r) if not pd.isna(r) else np.nan) for c, r in zip(score["adj_close"], actual)]
        score = add_subscores(score)
        candidates = score[(score["up_rank_pct"] <= UP_TAIL) & (score["down_rank_pct"] > DOWN_GREEN_CUTOFF)].copy()
        candidates["close"] = candidates["adj_close"].map(fmt_price)
        candidates["date"] = candidates["date"].dt.strftime("%Y-%m-%d")
        cols = [
            "date", "ticker", "name", "sector", "detailSector", "close", "upScore", "up_lgbm_prob", "upGrade",
            "downRisk", "down_lgbm_prob", "downGrade", "expRet_1m", "expClose_1m", "actRet_1m", "actClose_1m",
            "expRet_3m", "expClose_3m", "actRet_3m", "actClose_3m", "expRet_6m", "expClose_6m", "actRet_6m",
            "actClose_6m", "expRet_12m", "expClose_12m", "actRet_12m", "actClose_12m", "성장·수익성",
            "현금흐름·품질", "밸류에이션", "가격·거래량", "위험·과열",
        ]
        all_candidates.append(candidates[cols].sort_values(["upScore", "downRisk"], ascending=[False, True]))
        print(f"{sig.date()} cutoff={cutoff_6m.date()} candidates={len(candidates)}")
    result = pd.concat(all_candidates, ignore_index=True)
    result.to_csv(OUT_DIR / "walkforward_candidates.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(validations).to_csv(OUT_DIR / "walkforward_validation.csv", index=False, encoding="utf-8-sig")
    print(OUT_DIR / "walkforward_candidates.csv")
    print(OUT_DIR / "walkforward_validation.csv")


if __name__ == "__main__":
    main()
