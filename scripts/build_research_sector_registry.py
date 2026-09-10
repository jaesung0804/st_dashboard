"""Version current research coverage without backdating sector knowledge."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import pandas as pd


GROUPS = {
    "정보기술": ["반도체", "AI/SW/인터넷", "디스플레이", "전기전자"],
    "산업재": ["건설/건자재", "기계/장비", "로봇/자동화", "방산/우주항공", "운송/물류", "전력/인프라", "조선/해운", "환경/수처리"],
    "소비재": ["교육", "농업/사료", "섬유/의류", "유통/소비플랫폼", "음식료", "자동차/모빌리티", "화장품/소비재"],
    "소재": ["2차전지", "철강/비철", "화학/소재"],
    "커뮤니케이션": ["게임/엔터/콘텐츠", "통신/네트워크"],
    "금융": ["금융", "금융/특수목적"], "부동산": ["부동산/리츠"],
    "헬스케어": ["바이오/헬스케어"], "에너지·유틸리티": ["에너지/유틸리티"],
    "복합기업": ["지주/복합기업"]
}
US_GROUPS = {"Technology": "정보기술", "Industrials": "산업재", "Consumer Cyclical": "소비재",
             "Consumer Defensive": "소비재", "Basic Materials": "소재", "Communication Services": "커뮤니케이션",
             "Financial Services": "금융", "Real Estate": "부동산", "Healthcare": "헬스케어",
             "Energy": "에너지·유틸리티", "Utilities": "에너지·유틸리티"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--state-dir", type=Path, required=True)
    p.add_argument("--observed-at", required=True)
    p.add_argument("--out", type=Path, default=Path("data/reference/research_sectors_20260910.csv.gz"))
    a = p.parse_args()
    if a.out.exists():
        raise SystemExit("Choose a new version filename; retain previous snapshots")
    broad = {value: key for key, values in GROUPS.items() for value in values}
    kr_path = a.state_dir / "data/raw/krx_kospi_kosdaq_detailed_sector_map.xlsx"
    us_path = a.state_dir / "data/raw/us_listings_nasdaq_nyse_yfinfo_state.csv"
    kr = pd.read_excel(kr_path, sheet_name="SectorMap", dtype=str).fillna("")
    us = pd.read_csv(us_path, dtype=str).fillna("")
    rows = []
    for r in kr.to_dict("records"):
        rows.append({"market": "kr", "ticker": r["ticker"].zfill(6), "name": r["company_name"],
                     "sector_l1": broad.get(r["model_sector"], "미분류"), "sector_l2": r["model_sector"] or "미분류",
                     "sector_l3": r["model_industry"] or "미분류", "themes": r["theme_tags"],
                     "available_at": a.observed_at, "source_generated_at": r["generated_at"],
                     "source_method": r["classification_method"], "source_verified": r["is_verified"],
                     "review_status": "needs_analyst_review", "source": r["source_primary"]})
    for r in us.to_dict("records"):
        rows.append({"market": "us", "ticker": r["ticker"], "name": r["name"],
                     "sector_l1": US_GROUPS.get(r["sector"], "미분류"), "sector_l2": r["representative_industry"] or "미분류",
                     "sector_l3": r["industry"] or "미분류", "themes": "", "available_at": a.observed_at,
                     "source_generated_at": "", "source_method": "retained_yfinance_listing", "source_verified": "unverified",
                     "review_status": "needs_analyst_review", "source": str(us_path.name)})
    frame = pd.DataFrame(rows).sort_values(["market", "ticker"])
    if frame.duplicated(["market", "ticker"]).any():
        raise ValueError("Duplicate securities need manual resolution")
    with a.out.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as out:
            out.write(frame.to_csv(index=False).encode())
    counts = frame.groupby(["market", "sector_l1", "sector_l2", "sector_l3"]).size().reset_index(name="securities")
    meta = {"available_at": a.observed_at, "classification": "custom three-level research taxonomy, not official GICS",
            "historical_use_before_available_at": False, "rows": len(frame),
            "sources": {str(f.name): hashlib.sha256(f.read_bytes()).hexdigest() for f in [kr_path, us_path]},
            "unclassified_l1": int(frame.sector_l1.eq("미분류").sum()), "counts": counts.to_dict("records")}
    a.out.with_suffix(".metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({"rows": len(frame), "markets": frame.groupby("market").size().to_dict(), "unclassified_l1": meta["unclassified_l1"]},ensure_ascii=False))


if __name__ == "__main__":
    main()
