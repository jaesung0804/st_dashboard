"""Audit public saved account snapshots without training, solving or backend writes.

Usage: python scripts/audit_public_portfolio_feasibility.py portfolio.json
Also accepts the public portfolio/index.html with its embedded portfolio-data.
Scope: historical guarded fractional-share experiments, not the separate
261-day whole-share experiment. Missing risk inputs never imply a pass.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

def audit(source: Path):
    raw = source.read_bytes()
    text = raw.decode("utf-8-sig")
    if text.lstrip().startswith("<"):
        match = re.search(r'<script[^>]*id="portfolio-data"[^>]*>(.*?)</script>', text, re.S)
        if not match:
            raise ValueError("No embedded portfolio-data")
        text = match.group(1)
    data = json.loads(text)
    rows = []
    for group in data["guarded_holdings"]["sets"]:
        for snapshot in group["snapshots"]:
            if not snapshot.get("solver_error"):
                continue
            positions = snapshot["positions"]
            before = snapshot["before"]
            nav = before["nav_krw"]
            equity = (before["kr_stock_krw"] + before["us_stock_krw"]) / nav
            assert abs(sum(p["current_weight"] for p in positions) - equity) < 1e-10
            nominal_excess = [{"asset": p["asset"], "weight": p["current_weight"], "target": .05}
                              for p in positions if p["current_weight"] > .05 + 1e-8]
            zero_shares = all(abs(p["accepted_change"]) < 1e-12 for p in positions)
            zero_fx = abs(snapshot["accepted_fx_delta_usd"]) < 1e-12
            zero_cost = snapshot["trade_cost_krw"] == 0 and snapshot["fx_cost_krw"] == 0
            unchanged_weights = all(abs(p["accepted_weight"]-p["current_weight"]) < 1e-12 for p in positions)
            no_trade = zero_shares and zero_fx and zero_cost and unchanged_weights
            rows.append({
                "account": group["id"], "date": snapshot["date"],
                "error": snapshot["solver_error"], "action": snapshot["action"],
                "final_order": snapshot["final_order"],
                "recorded_plan_check_passed": snapshot["plan_check_passed"],
                "recorded_plan_unresolved": snapshot["plan_unresolved"],
                "accepted_no_trade": no_trade,
                "recorded_verified_no_trade": no_trade and snapshot["plan_check_passed"],
                "normal_name_target_excess": nominal_excess,
                "name_emergency_8pct_exceeded": any(p["current_weight"]>.08+1e-8 for p in positions),
                "equity_weight": equity, "equity_95pct_exceeded": equity>.95+1e-8,
                "country_65pct_exceeded": max(before["kr_stock_krw"],before["us_stock_krw"])/nav>.65+1e-8,
                "usd_75pct_exceeded": (before["us_stock_krw"]+before["usd_total_krw"])/nav>.75+1e-8,
                "negative_settled_cash": min(before["settled_krw"],before["settled_usd"])<-1e-8,
                "independent_full_feasibility": "unknown_missing_risk_model_and_checker",
            })
    return {
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "scope": "Public last-25 snapshots per guarded account; error field present; not all experiment dates",
        "error_account_dates": len(rows),
        "recorded_verified_no_trade_dates": sum(r["recorded_verified_no_trade"] for r in rows),
        "recorded_final_plan_pass_dates": sum(r["recorded_plan_check_passed"] for r in rows),
        "normal_5pct_target_excess_dates": sum(bool(r["normal_name_target_excess"]) for r in rows),
        "limitations": [
            "5pct is the normal target; 8pct is a separate emergency monitor.",
            "Recorded checker flags are not an independent rerun of the missing checker.",
            "Full pretrade covariance, loss scenarios, clusters and exact recovery policy are unavailable.",
            "Postfill risks use later marks and are not substituted for pretrade no-trade risks.",
            "Accepted zero stock trades alone is not no-trade: FX changes must also be zero.",
            "An intermediate solver_error can coexist with a passing final plan.",
        ],
        "rows": rows,
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.source), ensure_ascii=True, indent=2))
