from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path("outputs")
KR_DASHBOARD = ROOT / "lgbm_warning_dashboard_macro_kr_latest"
US_DASHBOARD = ROOT / "lgbm_warning_dashboard_macro_us_latest"
KR_LATEST_WF = ROOT / "walkforward_warning_macro_kr_combined_20260614"
US_LATEST_WF = ROOT / "walkforward_warning_macro_us_combined_20260614"
KR_BACKTEST_WF = ROOT / "walkforward_warning_macro_kr_backtest_20251128"
US_BACKTEST_WF = ROOT / "walkforward_warning_macro_us_backtest_20251128"
US_LISTINGS = Path("data/raw/us_listings_nasdaq_nyse_yfinfo_20260612.csv")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Update the Korean and US macro dashboards as one paired bundle.")
    p.add_argument("--kr-wf-dir", default=str(KR_LATEST_WF))
    p.add_argument("--us-wf-dir", default=str(US_LATEST_WF))
    p.add_argument("--kr-backtest-dir", default=str(KR_BACKTEST_WF))
    p.add_argument("--us-backtest-dir", default=str(US_BACKTEST_WF))
    p.add_argument("--kr-dashboard-dir", default=str(KR_DASHBOARD))
    p.add_argument("--us-dashboard-dir", default=str(US_DASHBOARD))
    p.add_argument("--us-listings-path", default=str(US_LISTINGS))
    p.add_argument("--signal-date", default="2025-11-28")
    p.add_argument("--latest-date", default="2026-06-12")
    p.add_argument("--recent-days", type=int, default=0)
    return p


def require_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(path)


def run(args: list[str]) -> None:
    print(" ".join(args), flush=True)
    subprocess.run(args, check=True)


def main() -> None:
    args = parser().parse_args()
    py = sys.executable

    kr_wf_dir = Path(args.kr_wf_dir)
    us_wf_dir = Path(args.us_wf_dir)
    kr_backtest_dir = Path(args.kr_backtest_dir)
    us_backtest_dir = Path(args.us_backtest_dir)
    kr_dashboard_dir = Path(args.kr_dashboard_dir)
    us_dashboard_dir = Path(args.us_dashboard_dir)
    us_listings_path = Path(args.us_listings_path)

    for path in [
        kr_wf_dir / "walkforward_scores.csv",
        us_wf_dir / "walkforward_scores.csv",
        kr_backtest_dir / "walkforward_scores.csv",
        us_backtest_dir / "walkforward_scores.csv",
    ]:
        require_file(path)

    run(
        [
            py,
            "scripts/build_walkforward_dashboard.py",
            "--wf-dir",
            str(kr_wf_dir),
            "--out-dir",
            str(kr_dashboard_dir),
            "--market-name",
            "한국",
            "--home-href",
            "../index.html",
            "--kr-dashboard-href",
            "dashboard.html",
            "--us-dashboard-href",
            "../lgbm_warning_dashboard_macro_us_latest/dashboard.html",
            "--recent-days",
            str(args.recent_days),
        ]
    )

    us_build = [
        py,
        "scripts/build_walkforward_dashboard.py",
        "--wf-dir",
        str(us_wf_dir),
        "--out-dir",
        str(us_dashboard_dir),
        "--market-name",
        "미국",
        "--home-href",
        "../index.html",
        "--kr-dashboard-href",
        "../lgbm_warning_dashboard_macro_kr_latest/dashboard.html",
        "--us-dashboard-href",
        "dashboard.html",
        "--recent-days",
        str(args.recent_days),
    ]
    if us_listings_path.exists():
        us_build.extend(["--listings-path", str(us_listings_path)])
    run(us_build)

    run(
        [
            py,
            "scripts/write_backtest_dashboard_report.py",
            "--market-label",
            "한국 주식",
            "--backtest-dir",
            str(kr_backtest_dir),
            "--dashboard-dir",
            str(kr_dashboard_dir),
            "--signal-date",
            args.signal_date,
            "--latest-date",
            args.latest_date,
        ]
    )
    run(
        [
            py,
            "scripts/write_backtest_dashboard_report.py",
            "--market-label",
            "미국 주식",
            "--backtest-dir",
            str(us_backtest_dir),
            "--dashboard-dir",
            str(us_dashboard_dir),
            "--signal-date",
            args.signal_date,
            "--latest-date",
            args.latest_date,
        ]
    )
    run([py, "scripts/sync_dashboard_navigation.py"])
    print("Updated dashboard bundle: Korean and US outputs are paired.", flush=True)


if __name__ == "__main__":
    main()
