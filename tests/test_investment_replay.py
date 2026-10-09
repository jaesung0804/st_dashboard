import json
from pathlib import Path
import numpy as np
import pandas as pd

from ai_stock_assistant.investment_replay import prepare, replay, metrics, target_weights


def fixture_data():
    dates = pd.bdate_range("2022-01-03", periods=175)
    records = []
    for k in range(12):
        for i, d in enumerate(dates):
            price = 100 * (1.0002 + k * .00002) ** i
            records.append({"date": d, "ticker": f"T{k:02d}", "open": price, "high": price, "low": price,
                            "close": price, "adjusted_close": price, "volume": 1e7})
    filings = pd.DataFrame([{"ticker": f"T{k:02d}", "available_at": "2022-01-04", "filed": "2022-01-03", "period_end": "2021-12-31", "filing_id": f"F{k}",
                             "fin_revenue_growth": .1+k*.01, "fin_roa": .05, "fin_cfo_assets": .1, "fin_accruals_assets": 0,
                             "fin_liabilities_assets": .4, "fin_cash_assets": .2, "fin_cfo_after_ppe_assets": .08,
                             "fin_observed_fraction": .8} for k in range(12)])
    policy = json.loads(Path("data/reference/investment_replay_policy.json").read_text(encoding="utf-8"))
    policy.update(start=str(dates[130].date()), end=str(dates[-1].date()), review_split=str(dates[155].date()))
    return pd.DataFrame(records), filings, policy, dates


def test_future_price_and_filing_cannot_change_past_decisions_or_nav():
    prices, filings, policy, dates = fixture_data()
    original = replay(prepare(prices, filings, policy, "us"), policy, "us")
    cutoff = dates[155]
    prices.loc[prices.date > cutoff, ["open", "high", "low", "close", "adjusted_close"]] *= 3
    later = filings.copy()
    later["available_at"], later["filed"] = str(dates[156].date()), str(dates[155].date())
    later["fin_roa"] = 999
    later["filing_id"] = "FUTURE"
    updated = replay(prepare(prices, pd.concat([filings, later]), policy, "us"), policy, "us")
    past = lambda rows: [r for r in rows if r["signal_date"] <= str(cutoff.date())]
    assert past(original["decisions"]) == past(updated["decisions"])
    count = sum(d <= str(cutoff.date()) for d in original["dates"])
    for desk in original["series"]:
        np.testing.assert_array_equal(original["series"][desk][:count], updated["series"][desk][:count])
    assert all(t["fill_date"] > t["signal_date"] for t in original["trades"])


def test_costs_cash_and_initial_drawdown_are_accounted_for():
    prices, filings, policy, dates = fixture_data()
    for col in ["open", "high", "low", "close", "adjusted_close"]:
        prices[col] = 100.
    data = prepare(prices, filings, policy, "us")
    normal = replay(data, policy, "us")
    stress = replay(data, policy, "us", 2.)
    for desk in ["baseline_equal_weight", "compound_quarter"]:
        assert stress["summary"][desk]["net_return"] < normal["summary"][desk]["net_return"] < 0
        assert normal["summary"][desk]["cost_paid"] > 0
    assert np.isclose(metrics(np.array([90., 95.]), 100.)["max_drawdown"], -.1)
    for company, config in policy["companies"].items():
        expected = sum(np.asarray(normal["series"][t]) for t in config["teams"]) + policy["initial_capital"]["us"]*.1
        np.testing.assert_allclose(normal["series"][company], expected)


def test_missing_held_quotes_remain_in_valuation_and_are_flagged():
    prices, filings, policy, dates = fixture_data()
    prices = prices.loc[~((prices.ticker == "T11") & (prices.date >= dates[150]))]
    result = replay(prepare(prices, filings, policy, "us"), policy, "us")
    assert result["summary"]["compound_quarter"]["stale_exposure_days"] > 0
    assert result["summary"]["compound_quarter"]["max_stale_weight"] > 0
    assert not any(t["ticker"] == "T11" and t["fill_date"] >= str(dates[150].date()) for t in result["trades"])


def test_filing_clock_and_position_limits():
    prices, filings, policy, dates = fixture_data()
    data = prepare(prices, filings, policy, "us")
    w, _, _ = target_weights(data, 140, "compound_quarter", .1)
    assert np.max(w) <= .1 and np.sum(w) <= 1 + 1e-12
    filings["available_at"] = filings["filed"]
    try:
        prepare(prices, filings, policy, "us")
    except ValueError as exc:
        assert "later" in str(exc)
    else:
        raise AssertionError("Same-day availability must fail")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(len(tests), "checks passed")
