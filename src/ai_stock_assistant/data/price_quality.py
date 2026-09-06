"""Research-only corporate-action reconciliation and continuity eligibility."""
from __future__ import annotations
import numpy as np
import pandas as pd

POLICY = "verified-actions-and-20x-continuity-v1"
PPCB_SOURCE = "https://ir.propanc.com/all-sec-filings/xbrl_doc_only/3605"


def prepare(prices: pd.DataFrame, market: str) -> tuple[pd.DataFrame, dict]:
    result = prices.copy()
    # Work only on the experimental adjusted series. Never edit retained raw
    # files, archived forecasts, or operational liquidity/price screen inputs.
    result["adjusted_close"] = result.adjusted_close.astype(float)
    adjustments = []
    if market == "us":
        before = result.loc[(result.ticker == "PPCB") & (result.date < "2025-01-29")].tail(1)
        after = result.loc[(result.ticker == "PPCB") & (result.date >= "2025-01-29")].head(1)
        if len(before) and len(after):
            prior, current = float(before.adjusted_close.iloc[0]), float(after.adjusted_close.iloc[0])
            ratio = current / prior if prior > 0 and np.isfinite([prior, current]).all() else None
            applied = False
            # The issuer confirms a 1-for-60,000 split effective 2025-01-29.
            # Correct it only when this known discontinuity is actually present.
            # If the upstream data later becomes split-adjusted, do not double it.
            if ratio is not None and 30000 <= ratio <= 120000:
                result.loc[(result.ticker == "PPCB") & (result.date < "2025-01-29"), "adjusted_close"] *= 60000
                applied = True
            adjustments.append({"ticker": "PPCB", "effective_date": "2025-01-29", "factor": 60000,
                                "observed_boundary_ratio": ratio, "applied": applied, "source": PPCB_SOURCE})
    previous = result.groupby("ticker", sort=False).adjusted_close.shift()
    ratio = result.adjusted_close / previous.where(previous > 0)
    invalid = ~np.isfinite(result.adjusted_close) | result.adjusted_close.le(0)
    discontinuity = (ratio.gt(20) | ratio.lt(.05)) & previous.gt(0) & ~invalid
    result["quality_break"] = invalid | discontinuity
    # A signal's feature eligibility depends only on its own preceding history.
    recent = result.groupby("ticker", sort=False).quality_break.transform(lambda s: s.rolling(253, min_periods=1).max())
    result["quality_valid"] = recent.eq(0)
    events = result.loc[discontinuity, ["date", "ticker"]].copy()
    events["ratio"] = ratio.loc[events.index]
    events["date"] = events.date.dt.strftime("%Y-%m-%d")
    latest = result.date.max()
    report = {"policy": POLICY, "adjustments": adjustments,
              "unverified_discontinuities": events.to_dict(orient="records"),
              "invalid_adjusted_price_rows": int(invalid.sum()),
              "latest_affected_tickers": sorted(result.loc[result.date.eq(latest) & ~result.quality_valid, "ticker"].tolist()),
              "policy_description": "Do not cap returns. Unverified >20x or <1/20 one-observation changes and nonpositive adjusted prices invalidate affected features/outcomes; count missing benchmark coverage."}
    return result, report
