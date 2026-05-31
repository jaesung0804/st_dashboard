from __future__ import annotations

from pathlib import Path

import pandas as pd


OUT_DIR = Path("outputs/walkforward_warning")


def value(row: pd.Series, col: str, default: str = "") -> str:
    item = row.get(col, default)
    if pd.isna(item):
        return default
    return str(item)


def summary_line(index: int, row: pd.Series) -> str:
    return (
        f"{index}. {value(row, 'name')}({value(row, 'ticker')}) | {value(row, 'sector')} / {value(row, 'detailSector')}\n"
        f"   종가 {value(row, 'close')} | 떡상 {value(row, 'upScore')}점 | 떡락위험 {value(row, 'downRisk')}점(GREEN)\n"
        f"   예상: 1M {value(row, 'expRet_1m')}({value(row, 'expClose_1m')}) / "
        f"3M {value(row, 'expRet_3m')}({value(row, 'expClose_3m')}) / "
        f"6M {value(row, 'expRet_6m')}({value(row, 'expClose_6m')}) / "
        f"12M {value(row, 'expRet_12m')}({value(row, 'expClose_12m')})"
    )


def detail_block(index: int, row: pd.Series) -> str:
    return (
        f"[{index}] {value(row, 'name')} ({value(row, 'ticker')})\n"
        f"- 섹터: {value(row, 'sector')} / {value(row, 'detailSector')}\n"
        f"- 현재 종가: {value(row, 'close')}\n"
        f"- 조기경보: 떡상 {value(row, 'upScore')}점({value(row, 'upGrade')}), "
        f"떡락위험 {value(row, 'downRisk')}점({value(row, 'downGrade')})\n"
        f"- 예상 종가/수익: 1M {value(row, 'expClose_1m')} / {value(row, 'expRet_1m')}, "
        f"3M {value(row, 'expClose_3m')} / {value(row, 'expRet_3m')}, "
        f"6M {value(row, 'expClose_6m')} / {value(row, 'expRet_6m')}, "
        f"12M {value(row, 'expClose_12m')} / {value(row, 'expRet_12m')}\n"
        f"- 하위스코어: 성장·수익성 {value(row, '성장·수익성')}, "
        f"현금흐름·품질 {value(row, '현금흐름·품질')}, 밸류에이션 {value(row, '밸류에이션')}, "
        f"가격·거래량 {value(row, '가격·거래량')}, 위험·과열 {value(row, '위험·과열')} (낮을수록 좋음)"
    )


def build_one(data: pd.DataFrame, validation: pd.DataFrame, date: str) -> Path:
    part = data[data["date"].astype(str).eq(date)].copy()
    part = part.sort_values(["upScore", "downRisk"], ascending=[False, True])
    val = validation[validation["signal_date"].astype(str).eq(date)]
    lines = []
    lines.append(f"[AI 주식 조기경보 / Walk-forward] {date} 기준")
    lines.append("")
    lines.append("조건: 해당 기준일에 실제로 알 수 있었던 과거 라벨만 사용해 재학습")
    lines.append("선별: 떡상 LGBM 상위 5% 중 떡락위험 GREEN만 통과")
    lines.append("제외: 우선주 제외, KRX 상세 섹터맵 적용")
    if not val.empty:
        row = val.iloc[0]
        lines.append(
            f"검증: label_cutoff {row['label_cutoff']}, validation AUC "
            f"떡상 {float(row['up_auc']):.3f}, 떡락 {float(row['down_auc']):.3f}"
        )
    lines.append("주의: 투자 권유가 아니라 모델 신호 점검용")
    lines.append("")
    lines.append(f"후보 수: {len(part)}개")
    lines.append("")
    lines.append(f"[전체 {len(part)}개 요약]")
    for index, (_, row) in enumerate(part.iterrows(), 1):
        lines.append(summary_line(index, row))
    lines.append("")
    lines.append("[상위 10개 상세]")
    for index, (_, row) in enumerate(part.head(10).iterrows(), 1):
        lines.append(detail_block(index, row))
        lines.append("")
    lines.append("[PDF 작성용 멘트]")
    lines.append(
        f"아래 내용은 {date} 기준 walk-forward 방식의 AI 주식 조기경보 후보 요약입니다. "
        "각 기준일마다 미래 라벨을 보지 않도록 학습 컷오프를 두고 재학습했습니다. "
        "PDF에서는 전체 후보 표, 상위 10개 상세 코멘트, 섹터 분포, 검증 AUC, 유의사항 순서로 정리해 주세요."
    )
    output = OUT_DIR / f"walkforward_summary_{date}.txt"
    output.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return output


def main() -> None:
    data = pd.read_csv(OUT_DIR / "walkforward_candidates.csv", dtype={"ticker": str})
    validation = pd.read_csv(OUT_DIR / "walkforward_validation.csv")
    for date in sorted(data["date"].astype(str).unique()):
        print(build_one(data, validation, date))


if __name__ == "__main__":
    main()
