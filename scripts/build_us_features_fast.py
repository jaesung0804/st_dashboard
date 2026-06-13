from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from ai_stock_assistant.features import FINANCIAL_FEATURES, PRICE_FEATURES, _safe_div, normalize_ticker, prepare_financial_asof
from ai_stock_assistant.labels import add_adjusted_ohlc


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--prices-path", required=True)
    p.add_argument("--financials-path", required=True)
    p.add_argument("--listings-path", required=True)
    p.add_argument("--output-path", required=True)
    return p


def add_price_features_for_group(group: pd.DataFrame) -> pd.DataFrame:
    group = add_adjusted_ohlc(group.sort_values("date").copy())
    close = group["adj_close"]
    high = group["adj_high"]
    low = group["adj_low"]
    volume = group["volume"]
    value = close * volume
    returns = close.pct_change()

    for window in [5, 20, 60, 120, 252]:
        group[f"return_{window}d"] = close / close.shift(window) - 1
    for window in [20, 63, 126, 252]:
        group[f"future_return_{window}d"] = close.shift(-window) / close - 1
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


def merge_ticker_financials(group: pd.DataFrame, fin: pd.DataFrame) -> pd.DataFrame:
    ticker = str(group["ticker"].iloc[0])
    ticker_fin = fin[fin["ticker"].eq(ticker)].sort_values("announcement_date")
    if ticker_fin.empty:
        return group
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
    joined = pd.merge_asof(
        group.sort_values("date"),
        ticker_fin[fin_cols],
        left_on="date",
        right_on="announcement_date",
        by="ticker",
        direction="backward",
    )
    shares = pd.to_numeric(joined.get("shares_outstanding", np.nan), errors="coerce")
    static_market_cap = pd.to_numeric(joined.get("market_cap", np.nan), errors="coerce")
    market_cap = joined["adj_close"] * shares
    market_cap = market_cap.where(market_cap.notna(), static_market_cap)
    joined["cash_to_market_cap"] = _safe_div(joined["cash"], market_cap)
    joined["psr"] = _safe_div(market_cap, joined["revenue"])
    joined["pbr"] = _safe_div(market_cap, joined["total_equity"])
    return joined


def main() -> None:
    args = parser().parse_args()
    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    prices = pd.read_csv(args.prices_path, dtype={"ticker": str})
    prices["ticker"] = normalize_ticker(prices["ticker"])
    prices["date"] = pd.to_datetime(prices["date"])
    listings = pd.read_csv(args.listings_path, dtype={"ticker": str})
    listings["ticker"] = normalize_ticker(listings["ticker"])
    for col in ["exchange", "industry", "shares_outstanding", "market_cap"]:
        if col not in listings:
            listings[col] = np.nan
    listings = listings[["ticker", "exchange", "industry", "shares_outstanding", "market_cap"]]
    prices = prices.merge(listings, on="ticker", how="left")

    financials = pd.read_csv(args.financials_path, dtype={"ticker": str, "corp_code": str})
    financials["ticker"] = normalize_ticker(financials["ticker"])
    fin = prepare_financial_asof(financials) if not financials.empty else pd.DataFrame()

    base_cols = [
        "date",
        "ticker",
        "adj_close",
        "exchange",
        "industry",
        "shares_outstanding",
        "market_cap",
        "future_return_20d",
        "future_return_63d",
        "future_return_126d",
        "future_return_252d",
    ]
    out_cols = base_cols + PRICE_FEATURES + FINANCIAL_FEATURES + [
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

    first = True
    grouped = prices.sort_values(["ticker", "date"]).groupby("ticker", sort=False)
    for idx, (ticker, group) in enumerate(grouped, start=1):
        features = add_price_features_for_group(group)
        if not fin.empty:
            features = merge_ticker_financials(features, fin)
        for col in out_cols:
            if col not in features.columns:
                features[col] = np.nan
        features["date"] = pd.to_datetime(features["date"]).dt.strftime("%Y-%m-%d")
        features[out_cols].to_csv(
            output_path,
            mode="w" if first else "a",
            index=False,
            header=first,
            encoding="utf-8-sig",
        )
        first = False
        if idx % 100 == 0:
            print(f"{idx}/{len(grouped)} {ticker}", flush=True)
    print(output_path, flush=True)


if __name__ == "__main__":
    main()
