"""Offline, chronological paper portfolios. No LLM, broker or network calls.

Nine rule-based desks are *proxies for mandates*, not historical LLM agents.
Orders are frozen after signal close and may fill at the next session's open.
Valuation retains missing holdings; it never retrospectively removes losers.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

from .data.accounting_pit import attach


DESKS = {
    "pulse_day": (1, "momentum_short"),
    "pulse_week": (5, "momentum_medium"),
    "pulse_month": (21, "momentum_long"),
    "compound_quarter": (63, "growth_quality"),
    "compound_half": (126, "cash_quality"),
    "compound_year": (252, "durable_quality"),
    "adaptive_tactical": (5, "balanced_momentum"),
    "adaptive_quality": (21, "balanced_quality"),
    "adaptive_defensive": (21, "defensive"),
    "baseline_momentum": (21, "momentum_medium"),
    "baseline_equal_weight": (21, "equal_weight"),
}


def read_prices(path: Path, tickers: list[str], market: str) -> pd.DataFrame:
    """Read only retained symbols; keep all their dates, including missing quotes."""
    keep = set(tickers)
    pieces = []
    cols = ["date", "ticker", "open", "high", "low", "close", "adjusted_close", "volume"]
    for chunk in pd.read_csv(path, dtype={"ticker": str}, usecols=cols, chunksize=200000):
        if market == "kr":
            chunk["ticker"] = chunk.ticker.str.zfill(6)
        pieces.append(chunk.loc[chunk.ticker.isin(keep)].copy())
    result = pd.concat(pieces, ignore_index=True)
    result["date"] = pd.to_datetime(result.date)
    if result.duplicated(["date", "ticker"]).any():
        raise ValueError("Duplicate price observations: resolve source versions first")
    return result.sort_values(["date", "ticker"]).reset_index(drop=True)


@dataclass
class ReplayData:
    dates: pd.DatetimeIndex
    tickers: list[str]
    close: np.ndarray
    opening: np.ndarray
    valid_close: np.ndarray
    valid_open: np.ndarray
    capacity: np.ndarray
    features: dict[str, np.ndarray]
    audit: dict


def prepare(prices: pd.DataFrame, filings: pd.DataFrame, policy: dict, market: str) -> ReplayData:
    p = prices.copy().sort_values(["date", "ticker"])
    p["date"] = pd.to_datetime(p.date)
    p = p.loc[p.date <= pd.Timestamp(policy["end"])]
    if p.empty or p.duplicated(["date", "ticker"]).any():
        raise ValueError("Empty or duplicate price panel")
    dates = pd.DatetimeIndex(sorted(p.date.unique()))
    tickers = sorted(p.ticker.unique())
    def pivot(col):
        return p.pivot(index="date", columns="ticker", values=col).reindex(index=dates, columns=tickers)
    raw = pivot("close")
    close = pivot("adjusted_close").where(lambda x: x > 0)
    vol = pivot("volume")
    ratio = close / raw.where(raw > 0)
    opening = pivot("open").where(lambda x: x > 0) * ratio
    returns = close.pct_change(fill_method=None)
    features = {}
    for days in [5, 20, 63, 126]:
        features[f"mom{days}"] = close / close.shift(days) - 1
    features["vol20"] = returns.rolling(20, min_periods=20).std() * np.sqrt(252)
    features["trend"] = close / close.rolling(60, min_periods=60).mean() - 1
    adv = (raw * vol).rolling(20, min_periods=20).mean()
    # A historical break is counted when it occurs, never by screening on its future.
    breaks = (returns > 4) | (returns < -.8)
    bad_history = breaks.rolling(126, min_periods=1).max().astype(bool)
    eligible = (adv >= policy["minimum_adv20"][market]) & vol.gt(0)
    eligible &= close.rolling(126, min_periods=126).count().eq(126) & ~bad_history
    features["eligible"] = eligible
    # One row for every asset/session is required for proper dated financial joins.
    grid = pd.MultiIndex.from_product([dates, tickers], names=["date", "ticker"]).to_frame(index=False)
    f = filings.copy()
    for name in ["available_at", "filed", "period_end"]:
        f[name] = pd.to_datetime(f[name])
    if (f.available_at <= f.filed).any():
        raise ValueError("Filing available_at must be later than its filed date")
    joined = attach(grid, f)
    joined = joined.loc[joined.date <= pd.Timestamp(policy["end"])]
    for col in ["fin_revenue_growth", "fin_roa", "fin_cfo_assets", "fin_accruals_assets",
                "fin_liabilities_assets", "fin_cash_assets", "fin_cfo_after_ppe_assets"]:
        features[col] = joined.pivot(index="date", columns="ticker", values=col).reindex(index=dates, columns=tickers)
    # Preserve the precise event identifier/date behind every company decision.
    features["filing_available_at"] = joined.pivot(index="date", columns="ticker", values="available_at").reindex(index=dates, columns=tickers)
    features["filing_id"] = joined.pivot(index="date", columns="ticker", values="filing_id").reindex(index=dates, columns=tickers)
    coverage = joined.groupby("date").fin_observed_fraction.apply(lambda x: float(x.gt(0).mean()))
    return ReplayData(
        dates, tickers, close.to_numpy(), opening.to_numpy(),
        close.notna().to_numpy(), opening.notna().to_numpy(),
        (vol * policy["max_previous_day_volume_fraction"] / ratio).to_numpy(),
        {k: v.to_numpy() for k, v in features.items()},
        {"rows": len(p), "tickers": len(tickers), "price_start": str(dates.min().date()),
         "price_end": str(dates.max().date()), "filing_tickers": int(f.ticker.nunique()),
         "filing_coverage_at_end": float(coverage.iloc[-1]),
         "large_price_breaks": int(breaks.sum().sum()),
         "survivorship_bias": "retained current-listing cohort; historical delistings incomplete",
         "sector_limit_verified": False, "official_market_benchmark_available": False,
         "dividends": "US vendor adjusted close; KR retained adjusted price, cash dividends not separately available",
         "fx": "separate local-currency leagues; no consolidated KRW return",
         "historical_llm_decisions": False, "historical_model_scores_used": False},
    )


def ranks(x: np.ndarray, eligible: np.ndarray) -> np.ndarray:
    return pd.Series(np.where(eligible, x, np.nan)).rank(pct=True).to_numpy()


def mean_available(*arrays: np.ndarray, minimum: int = 2) -> np.ndarray:
    a = np.vstack(arrays)
    count = np.isfinite(a).sum(axis=0)
    result = np.nansum(a, axis=0) / np.maximum(count, 1)
    return np.where(count >= minimum, result, np.nan)


def target_weights(data: ReplayData, day: int, desk: str, max_weight: float) -> tuple[np.ndarray, np.ndarray, str]:
    """Only today's already-computed trailing/as-of values are exposed to rules."""
    f = {k: v[day] for k, v in data.features.items()}
    eligible = f["eligible"].astype(bool)
    r = lambda key, sign=1: ranks(sign * f[key].astype(float), eligible)
    quality = mean_available(r("fin_roa"), r("fin_cfo_assets"),
                             r("fin_accruals_assets", -1), r("fin_liabilities_assets", -1), minimum=3)
    growth = mean_available(quality, r("fin_revenue_growth"), minimum=2)
    cash = mean_available(r("fin_cfo_assets"), r("fin_cfo_after_ppe_assets"), r("fin_cash_assets"), minimum=2)
    durable = mean_available(quality, r("fin_roa"), r("fin_liabilities_assets", -1), minimum=3)
    rule = DESKS[desk][1]
    scores = {
        "momentum_short": .5 * r("mom5") + .5 * r("mom20"),
        "momentum_medium": r("mom20"), "momentum_long": r("mom63"),
        "growth_quality": growth, "cash_quality": mean_available(quality, cash),
        "durable_quality": durable,
        "balanced_momentum": .5 * r("mom20") + .5 * r("vol20", -1),
        "balanced_quality": mean_available(quality, r("mom63")),
        "defensive": r("vol20", -1), "equal_weight": np.ones(len(eligible)),
    }
    score = scores[rule]
    allowed = eligible & np.isfinite(score)
    if rule.startswith("momentum") or rule == "balanced_momentum":
        allowed &= f["trend"] > 0
    breadth = float(np.mean(f["trend"][eligible] > 0)) if eligible.any() else 0.
    exposure = .5 if desk.startswith("adaptive") and breadth < .45 else 1.
    weights = np.zeros(len(eligible))
    candidates = np.flatnonzero(allowed)
    # The equal-weight control holds ALL eligible assets, not an alphabetic top ten.
    selected = candidates if desk == "baseline_equal_weight" else candidates[np.argsort(-score[candidates], kind="stable")[:10]]
    if len(selected):
        weights[selected] = min(exposure / len(selected), max_weight)
    return weights, score, f"{rule}; breadth={breadth:.3f}; target_exposure={exposure:.2f}"


def metrics(nav: np.ndarray, initial: float) -> dict:
    curve = np.r_[initial, nav]
    ret = curve[1:] / curve[:-1] - 1
    peak = np.maximum.accumulate(curve)
    sd = float(np.std(ret, ddof=1)) if len(ret) > 1 else 0.
    return {"net_return": float(curve[-1] / initial - 1),
            "max_drawdown": float(np.min(curve / peak - 1)),
            "annualized_volatility": sd * np.sqrt(252),
            "sharpe_zero_cash_rate": float(np.mean(ret) / sd * np.sqrt(252)) if sd > 0 else None,
            "final_nav": float(curve[-1]), "sessions": len(ret)}


def replay(data: ReplayData, policy: dict, market: str, cost_multiplier: float = 1.) -> dict:
    initial = float(policy["initial_capital"][market])
    cost_rate = float(policy["all_in_cost_bps"][market]) * cost_multiplier / 10000
    begin = int(data.dates.searchsorted(policy["start"]))
    if begin >= len(data.dates):
        raise ValueError("No sessions in replay interval")
    n = len(data.tickers)
    desk_to_company = {t: c for c, v in policy["companies"].items() for t in v["teams"]}
    allocated = (1. - policy["company_cash_fraction"]) / 3
    books = {name: {"units": np.zeros(n), "cash": initial * (allocated if name in desk_to_company else 1.),
                    "pending": None, "pending_day": None, "nav": [], "cost": 0., "turnover": 0.,
                    "stale_exposure_days": 0, "max_stale_weight": 0., "price_break_exposure_days": 0}
             for name in DESKS}
    last_close = np.full(n, np.nan)
    stale = np.zeros(n, dtype=int)
    trades, decisions, requests, fills = [], [], [], []
    for day, date in enumerate(data.dates):
        valid_close = data.valid_close[day]
        opening = np.where(data.valid_open[day], data.opening[day], last_close)
        if day < begin:
            last_close = np.where(valid_close, data.close[day], last_close)
            continue
        for name, b in books.items():
            if b["pending"] is not None:
                desired = b["pending"] - b["units"]
                cap = data.capacity[day - 1] if day else np.zeros(n)
                valid = data.valid_open[day] & np.isfinite(cap) & (cap > 0)
                delta = np.where(valid, np.clip(desired, -cap, cap), 0.)
                delta = np.maximum(delta, -b["units"])
                sells = np.minimum(delta, 0.)
                sell_notional = float(np.nansum(-sells * opening))
                b["cash"] += sell_notional * (1. - cost_rate)
                buys = np.maximum(delta, 0.)
                purchase = float(np.nansum(buys * opening))
                scale = min(1., max(0., b["cash"]) / (purchase * (1. + cost_rate))) if purchase else 1.
                buys *= scale
                delta = sells + buys
                buy_notional = float(np.nansum(buys * opening))
                fees = (sell_notional + buy_notional) * cost_rate
                b["cash"] -= buy_notional * (1. + cost_rate)
                b["units"] += delta
                b["cost"] += fees
                b["turnover"] += sell_notional + buy_notional
                if b["cash"] < -1e-7 or np.min(b["units"]) < -1e-7:
                    raise AssertionError("Cash/position conservation failed")
                for j in np.flatnonzero(np.abs(delta) > 1e-9):
                    trades.append({"desk": name, "signal_date": b["pending_day"], "fill_date": str(date.date()),
                                   "ticker": data.tickers[j], "side": "buy" if delta[j] > 0 else "sell",
                                   "adjusted_units": float(abs(delta[j])), "adjusted_open": float(opening[j]),
                                   "notional": float(abs(delta[j] * opening[j])),
                                   "all_in_cost": float(abs(delta[j] * opening[j]) * cost_rate)})
                residual = np.abs(desired - delta)
                if np.any(residual > 1e-8):
                    fills.append({"desk": name, "date": str(date.date()), "unfilled_assets": int((residual > 1e-8).sum()),
                                  "reason": "missing_open_or_previous_volume_capacity_or_cash"})
                b["pending"] = None  # Unfilled residuals expire; no hidden retrospective fills.
        old_close = last_close.copy()
        last_close = np.where(valid_close, data.close[day], last_close)
        stale = np.where(valid_close, 0, stale + 1)
        price_break = np.isfinite(old_close) & ((last_close / old_close > 5) | (last_close / old_close < .2))
        for name, b in books.items():
            values = np.where(b["units"] > 0, b["units"] * last_close, 0.)
            nav = b["cash"] + float(np.nansum(values))
            b["nav"].append(nav)
            stale_value = float(np.nansum(values[stale > policy["max_stale_sessions"]]))
            if stale_value > 0:
                b["stale_exposure_days"] += 1
                b["max_stale_weight"] = max(b["max_stale_weight"], stale_value / nav)
            if np.any(price_break & (b["units"] > 0)):
                b["price_break_exposure_days"] += 1
            interval = DESKS[name][0]
            if (day - begin) % interval == 0:
                weights, score, reason = target_weights(data, day, name, policy["max_position_weight_per_team"])
                # Quantity is frozen using SIGNAL close, never tomorrow's price.
                desired = np.divide(nav * weights, last_close, out=np.zeros(n), where=np.isfinite(last_close) & (last_close > 0))
                # Keep an unquoted holding until a real price permits liquidation.
                desired = np.where(~valid_close & (b["units"] > 0), b["units"], desired)
                b["pending"], b["pending_day"] = desired, str(date.date())
                records = []
                for j in np.flatnonzero(weights):
                    available = pd.Timestamp(data.features["filing_available_at"][day, j])
                    records.append({"ticker": data.tickers[j], "weight": round(float(weights[j]), 6),
                                    "score": round(float(score[j]), 6),
                                    "filing_available_at": None if pd.isna(available) else str(available.date()),
                                    "filing_id": None if pd.isna(data.features["filing_id"][day,j]) else str(data.features["filing_id"][day,j])})
                decisions.append({"desk": name, "company": desk_to_company.get(name, "control"),
                                  "signal_date": str(date.date()), "status": "pending_next_open" if day == len(data.dates)-1 else "recorded",
                                  "rule": reason, "cash_target": round(1.-float(weights.sum()), 6), "positions": records})
                if name.startswith("compound") and not records:
                    requests.append({"from": name, "to": "research", "date": str(date.date()),
                                     "request": "No eligible dated financial candidates: retain cash and inspect source coverage"})
    run_dates = [str(d.date()) for d in data.dates[begin:]]
    series, summary = {}, {}
    for name, b in books.items():
        start_nav = initial * (allocated if name in desk_to_company else 1.)
        series[name] = b["nav"]
        summary[name] = metrics(np.asarray(b["nav"]), start_nav)
        liquidation = float(np.nansum(b["units"] * last_close)) * cost_rate
        summary[name].update({"cost_paid": b["cost"], "traded_notional_over_initial": b["turnover"] / start_nav,
                              "liquidation_adjusted_return": (b["nav"][-1] - liquidation) / start_nav - 1,
                              "stale_exposure_days": b["stale_exposure_days"], "max_stale_weight": b["max_stale_weight"],
                              "price_break_exposure_days": b["price_break_exposure_days"]})
    for company, info in policy["companies"].items():
        nav = np.sum([series[t] for t in info["teams"]], axis=0) + initial * policy["company_cash_fraction"]
        series[company] = nav.tolist()
        summary[company] = metrics(nav, initial)
        summary[company].update({"cost_paid": sum(summary[t]["cost_paid"] for t in info["teams"]),
                                 "stale_team_days": sum(summary[t]["stale_exposure_days"] for t in info["teams"]),
                                 "price_break_team_days": sum(summary[t]["price_break_exposure_days"] for t in info["teams"])})
        for label, mask in [("early", np.array(run_dates) < policy["review_split"]),
                            ("later_review", np.array(run_dates) >= policy["review_split"])]:
            indexes = np.flatnonzero(mask)
            if len(indexes):
                prev = initial if indexes[0] == 0 else nav[indexes[0]-1]
                summary[company][label] = metrics(nav[indexes], prev)
    return {"market": market, "cost_multiplier": cost_multiplier, "dates": run_dates, "series": series,
            "summary": summary, "audit": data.audit, "decisions": decisions, "trades": trades,
            "unfilled_orders": fills, "research_requests": requests}


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()
