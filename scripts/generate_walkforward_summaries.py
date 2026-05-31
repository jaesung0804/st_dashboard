from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


OUT_DIR = Path("outputs/walkforward_warning")
SUBSCORES = [
    ("growth_profit", "성장·수익성"),
    ("cash_quality", "현금흐름 품질"),
    ("valuation", "밸류에이션"),
    ("price_volume", "가격·거래량"),
    ("risk_overheat", "위험·과열"),
]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default=None, help="YYYY-MM-DD inclusive.")
    p.add_argument("--end", default=None, help="YYYY-MM-DD inclusive.")
    p.add_argument("--month-end-only", action="store_true")
    p.add_argument("--combined-output", default=None)
    return p


def value(row: pd.Series, col: str, default: str = "") -> str:
    item = row.get(col, default)
    if pd.isna(item):
        return default
    return str(item)


def summary_line(index: int, row: pd.Series) -> str:
    return (
        f"{index}. {value(row, 'name')}({value(row, 'ticker')}) | {value(row, 'sector')} / {value(row, 'detailSector')}\n"
        f"   종가 {value(row, 'close')} | 떡상점수 {value(row, 'upScore')} | "
        f"떡락위험 {value(row, 'downRisk')} ({value(row, 'downGrade')})\n"
        f"   예상: 1M {value(row, 'expRet_1m')}({value(row, 'expClose_1m')}) / "
        f"3M {value(row, 'expRet_3m')}({value(row, 'expClose_3m')}) / "
        f"6M {value(row, 'expRet_6m')}({value(row, 'expClose_6m')}) / "
        f"12M {value(row, 'expRet_12m')}({value(row, 'expClose_12m')})"
    )


def detail_block(index: int, row: pd.Series) -> str:
    subscore_text = ", ".join(f"{label} {value(row, col)}" for col, label in SUBSCORES)
    return (
        f"[{index}] {value(row, 'name')} ({value(row, 'ticker')})\n"
        f"- 섹터: {value(row, 'sector')} / {value(row, 'detailSector')}\n"
        f"- 현재 종가: {value(row, 'close')}\n"
        f"- 조기경보: 떡상 {value(row, 'upScore')} ({value(row, 'upGrade')}), "
        f"떡락위험 {value(row, 'downRisk')} ({value(row, 'downGrade')})\n"
        f"- 예상 종가/수익: 1M {value(row, 'expClose_1m')} / {value(row, 'expRet_1m')}, "
        f"3M {value(row, 'expClose_3m')} / {value(row, 'expRet_3m')}, "
        f"6M {value(row, 'expClose_6m')} / {value(row, 'expRet_6m')}, "
        f"12M {value(row, 'expClose_12m')} / {value(row, 'expRet_12m')}\n"
        f"- 하위스코어: {subscore_text}"
    )


def validation_for_date(validation: pd.DataFrame, date: str) -> pd.Series | None:
    month = str(pd.Timestamp(date).to_period("M"))
    if "signal_month" in validation.columns:
        val = validation[validation["signal_month"].astype(str).eq(month)]
    elif "signal_date" in validation.columns:
        val = validation[validation["signal_date"].astype(str).str[:7].eq(month)]
    else:
        val = pd.DataFrame()
    return None if val.empty else val.iloc[0]


def build_text(data: pd.DataFrame, validation: pd.DataFrame, date: str) -> str:
    part = data[data["date"].astype(str).eq(date)].copy()
    part = part.sort_values(["upScore", "downRisk"], ascending=[False, True])
    val = validation_for_date(validation, date)
    lines = [
        f"[AI 주식 조기경보 / Walk-forward] {date} 기준",
        "",
        "조건: 각 월 첫 신호일 기준으로 126거래일 전까지 라벨이 확정된 과거 데이터만 학습.",
        "선별: 떡상 LGBM 상위 5% 중 떡락위험 GREEN만 통과.",
        "제외: 우선주 제외, KRX 상세 섹터 매핑 적용.",
    ]
    if val is not None:
        lines.append(
            f"검증: label_cutoff {val['label_cutoff']}, validation AUC "
            f"떡상 {float(val['up_auc']):.3f}, 떡락 {float(val['down_auc']):.3f}"
        )
    lines.extend(["주의: 투자 권유가 아니라 모델 신호 점검용.", "", f"최종 후보 총 {len(part)}개", ""])
    lines.append(f"[전체 {len(part)}개 요약]")
    for index, (_, row) in enumerate(part.iterrows(), 1):
        lines.append(summary_line(index, row))
    lines.append("")
    lines.append("[상위 10개 상세]")
    for index, (_, row) in enumerate(part.head(10).iterrows(), 1):
        lines.append(detail_block(index, row))
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def target_dates(data: pd.DataFrame, start: str | None, end: str | None, month_end_only: bool) -> list[str]:
    frame = data[["date"]].drop_duplicates().copy()
    frame["date"] = pd.to_datetime(frame["date"])
    if start:
        frame = frame[frame["date"] >= pd.Timestamp(start)]
    if end:
        frame = frame[frame["date"] <= pd.Timestamp(end)]
    if month_end_only:
        dates = frame.groupby(frame["date"].dt.to_period("M"))["date"].max()
    else:
        dates = frame["date"].sort_values()
    return [d.strftime("%Y-%m-%d") for d in dates]


def main() -> None:
    args = parser().parse_args()
    data = pd.read_csv(OUT_DIR / "walkforward_candidates.csv", dtype={"ticker": str})
    validation = pd.read_csv(OUT_DIR / "walkforward_validation.csv")
    dates = target_dates(data, args.start, args.end, args.month_end_only)
    texts = []
    for date in dates:
        text = build_text(data, validation, date)
        output = OUT_DIR / f"walkforward_summary_{date}.txt"
        output.write_text(text, encoding="utf-8")
        print(output)
        texts.append(text)
    if args.combined_output:
        combined = Path(args.combined_output)
        combined.parent.mkdir(parents=True, exist_ok=True)
        combined.write_text(("\n" + "=" * 88 + "\n\n").join(texts), encoding="utf-8")
        print(combined)


if __name__ == "__main__":
    main()
