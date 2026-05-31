from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ai_stock_assistant.labels import add_model_labels


PRICE_FEATURES = [
    "return_5d",
    "return_20d",
    "return_60d",
    "return_120d",
    "return_252d",
    "return_252d_ex_20d",
    "price_to_ma20",
    "price_to_ma60",
    "price_to_ma120",
    "price_to_ma200",
    "ma20_to_ma60",
    "ma60_to_ma120",
    "price_to_52w_high",
    "price_to_52w_low",
    "breakout_20d_high",
    "breakout_60d_high",
    "breakout_252d_high",
    "avg_volume_20d",
    "avg_volume_60d",
    "volume_ratio_5d_to_60d",
    "volume_ratio_20d_to_60d",
    "avg_trading_value_20d",
    "avg_trading_value_60d",
    "trading_value_ratio_20d_to_60d",
    "volatility_20d",
    "volatility_60d",
    "volatility_expansion",
    "rsi_14",
    "rsi_28",
    "parabolic_move_score",
    "upper_wick_ratio",
    "close_position_in_day_range",
]

FINANCIAL_FEATURES = [
    "revenue_yoy",
    "operating_income_yoy",
    "net_income_yoy",
    "eps_yoy",
    "operating_margin",
    "gross_margin",
    "debt_to_equity",
    "cash_to_assets",
    "cash_to_market_cap",
    "equity_ratio",
    "cfo_margin",
    "fcf_margin",
    "cfo_to_net_income",
    "fcf_to_net_income",
    "operating_cash_flow_to_debt",
    "free_cash_flow_to_debt",
    "psr",
    "pbr",
]

FEATURE_COLUMNS = PRICE_FEATURES + FINANCIAL_FEATURES


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0, np.nan)


def add_price_features(prices: pd.DataFrame) -> pd.DataFrame:
    frame = add_model_labels(prices)
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values(["ticker", "date"]).reset_index(drop=True)

    def apply_group(group: pd.DataFrame) -> pd.DataFrame:
        close = group["adj_close"]
        high = group["adj_high"]
        low = group["adj_low"]
        volume = group["volume"]
        value = close * volume
        returns = close.pct_change()

        for window in [5, 20, 60, 120, 252]:
            group[f"return_{window}d"] = close / close.shift(window) - 1
        group["return_252d_ex_20d"] = close.shift(20) / close.shift(252) - 1
        for window in [20, 60, 120, 200]:
            ma = close.rolling(window, min_periods=window).mean()
            group[f"price_to_ma{window}"] = _safe_div(close, ma) - 1
        ma20 = close.rolling(20, min_periods=20).mean()
        ma60 = close.rolling(60, min_periods=60).mean()
        ma120 = close.rolling(120, min_periods=120).mean()
        group["ma20_to_ma60"] = _safe_div(ma20, ma60) - 1
        group["ma60_to_ma120"] = _safe_div(ma60, ma120) - 1

        high_52w = high.shift(1).rolling(252, min_periods=60).max()
        low_52w = low.shift(1).rolling(252, min_periods=60).min()
        group["price_to_52w_high"] = _safe_div(close, high_52w) - 1
        group["price_to_52w_low"] = _safe_div(close, low_52w) - 1
        for window in [20, 60, 252]:
            prev_high = high.shift(1).rolling(window, min_periods=max(5, window // 4)).max()
            group[f"breakout_{window}d_high"] = (close > prev_high).astype(float)

        avg_vol_5 = volume.rolling(5, min_periods=5).mean()
        avg_vol_20 = volume.rolling(20, min_periods=20).mean()
        avg_vol_60 = volume.rolling(60, min_periods=60).mean()
        group["avg_volume_20d"] = avg_vol_20
        group["avg_volume_60d"] = avg_vol_60
        group["volume_ratio_5d_to_60d"] = _safe_div(avg_vol_5, avg_vol_60)
        group["volume_ratio_20d_to_60d"] = _safe_div(avg_vol_20, avg_vol_60)

        avg_value_20 = value.rolling(20, min_periods=20).mean()
        avg_value_60 = value.rolling(60, min_periods=60).mean()
        group["avg_trading_value_20d"] = avg_value_20
        group["avg_trading_value_60d"] = avg_value_60
        group["trading_value_ratio_20d_to_60d"] = _safe_div(avg_value_20, avg_value_60)

        group["volatility_20d"] = returns.rolling(20, min_periods=20).std() * np.sqrt(252)
        group["volatility_60d"] = returns.rolling(60, min_periods=60).std() * np.sqrt(252)
        group["volatility_expansion"] = _safe_div(group["volatility_20d"], group["volatility_60d"])
        group["upper_wick_ratio"] = _safe_div(high - np.maximum(group["adj_open"], close), high - low)
        group["close_position_in_day_range"] = _safe_div(close - low, high - low)
        group["parabolic_move_score"] = group["return_20d"].clip(lower=0) * group["volume_ratio_5d_to_60d"]

        for period in [14, 28]:
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(period, min_periods=period).mean()
            loss = (-delta.clip(upper=0)).rolling(period, min_periods=period).mean()
            rs = _safe_div(gain, loss)
            group[f"rsi_{period}"] = 100 - (100 / (1 + rs))
        return group

    return frame.groupby("ticker", group_keys=False).apply(apply_group).reset_index(drop=True)


def prepare_financial_asof(financials: pd.DataFrame) -> pd.DataFrame:
    frame = financials.copy()
    frame["ticker"] = frame["ticker"].astype(str).str.zfill(6)
    frame["bsns_year"] = frame["bsns_year"].astype(int)
    report_month_day = {
        "q1": ("03-31", 45),
        "half": ("06-30", 45),
        "q3": ("09-30", 45),
        "annual": ("12-31", 90),
    }
    period_end = []
    announcement = []
    for _, row in frame.iterrows():
        report_name = str(row["report_name"])
        month_day, lag = report_month_day.get(report_name, ("12-31", 90))
        end = pd.Timestamp(f"{int(row['bsns_year'])}-{month_day}")
        period_end.append(end)
        announcement.append(end + pd.Timedelta(days=lag))
    frame["period_end"] = period_end
    frame["announcement_date"] = announcement
    frame = frame.sort_values(["ticker", "announcement_date", "report_name"]).reset_index(drop=True)

    for col in ["revenue", "operating_income", "net_income", "eps"]:
        frame[f"{col}_yoy"] = frame.groupby(["ticker", "report_name"])[col].pct_change(1)
    frame["operating_margin"] = _safe_div(frame["operating_income"], frame["revenue"])
    frame["gross_margin"] = _safe_div(frame["gross_profit"], frame["revenue"])
    debt = frame["short_term_debt"].fillna(0) + frame["long_term_debt"].fillna(0)
    frame["total_debt"] = debt
    frame["debt_to_equity"] = _safe_div(debt, frame["total_equity"])
    frame["cash_to_assets"] = _safe_div(frame["cash"], frame["total_assets"])
    frame["equity_ratio"] = _safe_div(frame["total_equity"], frame["total_assets"])
    fcf = frame["operating_cash_flow"] - frame["capex"].abs()
    frame["free_cash_flow"] = fcf
    frame["cfo_margin"] = _safe_div(frame["operating_cash_flow"], frame["revenue"])
    frame["fcf_margin"] = _safe_div(fcf, frame["revenue"])
    frame["cfo_to_net_income"] = _safe_div(frame["operating_cash_flow"], frame["net_income"])
    frame["fcf_to_net_income"] = _safe_div(fcf, frame["net_income"])
    frame["operating_cash_flow_to_debt"] = _safe_div(frame["operating_cash_flow"], debt)
    frame["free_cash_flow_to_debt"] = _safe_div(fcf, debt)
    return frame


def merge_financial_features(price_features: pd.DataFrame, financials: pd.DataFrame) -> pd.DataFrame:
    if financials.empty:
        return price_features
    price = price_features.sort_values(["ticker", "date"]).copy()
    fin = prepare_financial_asof(financials)
    merged = []
    post_merge_features = {"cash_to_market_cap", "psr", "pbr"}
    fin_feature_cols = [col for col in FINANCIAL_FEATURES if col not in post_merge_features]
    fin_cols = ["ticker", "announcement_date"] + fin_feature_cols + [
        "revenue",
        "gross_profit",
        "operating_income",
        "net_income",
        "total_assets",
        "total_liabilities",
        "total_equity",
        "cash",
        "total_debt",
        "free_cash_flow",
    ]
    for ticker, group in price.groupby("ticker", sort=False):
        ticker_fin = fin.loc[fin["ticker"].eq(ticker), fin_cols].sort_values("announcement_date")
        if ticker_fin.empty:
            merged.append(group)
            continue
        joined = pd.merge_asof(
            group.sort_values("date"),
            ticker_fin,
            left_on="date",
            right_on="announcement_date",
            by="ticker",
            direction="backward",
        )
        merged.append(joined)
    frame = pd.concat(merged, ignore_index=True)
    market_cap = frame["adj_close"] * frame.get("shares_outstanding", np.nan)
    if "shares_outstanding" not in frame.columns:
        market_cap = np.nan
    frame["cash_to_market_cap"] = _safe_div(frame["cash"], market_cap)
    frame["psr"] = _safe_div(market_cap, frame["revenue"])
    frame["pbr"] = _safe_div(market_cap, frame["total_equity"])
    return frame


def build_feature_matrix(
    prices_path: Path,
    financials_path: Path | None = None,
    listings_path: Path | None = None,
    output_path: Path | None = None,
) -> pd.DataFrame:
    prices = pd.read_csv(prices_path, dtype={"ticker": str})
    if listings_path is not None and listings_path.exists():
        listings = pd.read_csv(listings_path, dtype={"ticker": str})
        prices = prices.merge(listings[["ticker", "exchange", "industry", "shares_outstanding"]], on="ticker", how="left")
    features = add_price_features(prices)
    if financials_path is not None and financials_path.exists():
        financials = pd.read_csv(financials_path, dtype={"ticker": str, "corp_code": str})
        features = merge_financial_features(features, financials)
    features = features.sort_values(["date", "ticker"]).reset_index(drop=True)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        features.to_csv(output_path, index=False, encoding="utf-8-sig")
    return features
