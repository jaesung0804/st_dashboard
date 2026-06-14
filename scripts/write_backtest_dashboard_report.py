from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


GROUPS = [
    ("final", "최종 후보", "isFinalCandidate"),
    ("up", "상승 후보", "isUpCandidate"),
    ("down_red", "하락 경보", "isDownRed"),
    ("all", "전체 점수대상", None),
]
HORIZONS = [("1m", "1개월"), ("3m", "3개월"), ("6m", "6개월")]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--market-label", required=True)
    p.add_argument("--backtest-dir", required=True)
    p.add_argument("--dashboard-dir", required=True)
    p.add_argument("--signal-date", required=True)
    p.add_argument("--latest-date", required=True)
    p.add_argument("--tail-pct", type=float, default=0.05)
    return p


def parse_pct(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip()
    text = text.where(text.str.endswith("%"), pd.NA)
    return pd.to_numeric(text.str.rstrip("%"), errors="coerce") / 100


def bool_mask(frame: pd.DataFrame, flag: str | None) -> pd.Series:
    if flag is None:
        return pd.Series(True, index=frame.index)
    return frame[flag].astype(bool)


def summarize_returns(scores: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for group_key, group_label, flag in GROUPS:
        part = scores[bool_mask(scores, flag)]
        for horizon, horizon_label in HORIZONS:
            ret = parse_pct(part[f"actRet_{horizon}"])
            known = ret.dropna()
            rows.append(
                {
                    "group": group_key,
                    "groupLabel": group_label,
                    "horizon": horizon,
                    "horizonLabel": horizon_label,
                    "count": int(len(part)),
                    "knownCount": int(len(known)),
                    "avgReturn": float(known.mean()) if len(known) else None,
                    "medianReturn": float(known.median()) if len(known) else None,
                    "winRate": float((known > 0).mean()) if len(known) else None,
                    "bestReturn": float(known.max()) if len(known) else None,
                    "worstReturn": float(known.min()) if len(known) else None,
                }
            )
    return pd.DataFrame(rows)


def summarize_target_fit(scores: pd.DataFrame, tail_pct: float) -> tuple[pd.DataFrame, dict[str, object]]:
    frame = scores.copy()
    frame["realized6m"] = parse_pct(frame["actRet_6m"])
    known = frame.dropna(subset=["realized6m"]).copy()
    if known.empty:
        return pd.DataFrame(), {"knownCount": 0}

    top_cutoff = known["realized6m"].quantile(1 - tail_pct)
    bottom_cutoff = known["realized6m"].quantile(tail_pct)
    known["actualTopTail"] = known["realized6m"] >= top_cutoff
    known["actualBottomTail"] = known["realized6m"] <= bottom_cutoff

    total_top = int(known["actualTopTail"].sum())
    total_bottom = int(known["actualBottomTail"].sum())
    rows: list[dict[str, object]] = []
    for group_key, group_label, flag in GROUPS:
        part = known[bool_mask(known, flag)]
        count = int(len(part))
        top_hits = int(part["actualTopTail"].sum())
        bottom_hits = int(part["actualBottomTail"].sum())
        rows.append(
            {
                "group": group_key,
                "groupLabel": group_label,
                "count": count,
                "topTailHits": top_hits,
                "topTailHitRate": top_hits / count if count else None,
                "topTailCaptureRate": top_hits / total_top if total_top else None,
                "bottomTailHits": bottom_hits,
                "bottomTailExposureRate": bottom_hits / count if count else None,
                "bottomTailCaptureRate": bottom_hits / total_bottom if total_bottom else None,
                "meanRealized6m": float(part["realized6m"].mean()) if count else None,
            }
        )
    meta = {
        "knownCount": int(len(known)),
        "tailPct": tail_pct,
        "topTailCutoff": float(top_cutoff),
        "bottomTailCutoff": float(bottom_cutoff),
        "topTailCount": total_top,
        "bottomTailCount": total_bottom,
    }
    return pd.DataFrame(rows), meta


def fmt_pct(value: object) -> str:
    if value is None or pd.isna(value):
        return "미확정"
    return f"{float(value) * 100:.1f}%"


def top_table(scores: pd.DataFrame, flag: str) -> pd.DataFrame:
    part = scores[scores[flag].astype(bool)].copy()
    cols = [
        "ticker",
        "name",
        "sector",
        "detailSector",
        "close",
        "upScore",
        "downRisk",
        "actRet_1m",
        "actRet_3m",
        "actRet_6m",
    ]
    return part.sort_values(["upScore", "downRisk"], ascending=[False, True])[cols].head(40)


def html_table(frame: pd.DataFrame) -> str:
    return frame.to_html(index=False, escape=True, classes="data-table", border=0)


def return_summary_view(summary: pd.DataFrame) -> pd.DataFrame:
    view = summary.copy()
    for col in ["avgReturn", "medianReturn", "winRate", "bestReturn", "worstReturn"]:
        view[col] = view[col].map(fmt_pct)
    return view.rename(
        columns={
            "groupLabel": "그룹",
            "horizonLabel": "기간",
            "count": "대상",
            "knownCount": "확정",
            "avgReturn": "평균수익률",
            "medianReturn": "중앙값",
            "winRate": "승률",
            "bestReturn": "최고",
            "worstReturn": "최저",
        }
    )[["그룹", "기간", "대상", "확정", "평균수익률", "중앙값", "승률", "최고", "최저"]]


def target_summary_view(target: pd.DataFrame) -> pd.DataFrame:
    view = target.copy()
    for col in ["topTailHitRate", "topTailCaptureRate", "bottomTailExposureRate", "bottomTailCaptureRate", "meanRealized6m"]:
        view[col] = view[col].map(fmt_pct)
    return view.rename(
        columns={
            "groupLabel": "그룹",
            "count": "대상",
            "topTailHits": "상위5% 적중",
            "topTailHitRate": "상위5% 비율",
            "topTailCaptureRate": "상위5% 포착률",
            "bottomTailHits": "하위5% 포함",
            "bottomTailExposureRate": "하위5% 노출률",
            "bottomTailCaptureRate": "하위5% 포착률",
            "meanRealized6m": "6개월 평균",
        }
    )[
        [
            "그룹",
            "대상",
            "상위5% 적중",
            "상위5% 비율",
            "상위5% 포착률",
            "하위5% 포함",
            "하위5% 노출률",
            "하위5% 포착률",
            "6개월 평균",
        ]
    ]


def report_html(
    market_label: str,
    signal_date: str,
    latest_date: str,
    summary: pd.DataFrame,
    target: pd.DataFrame,
    target_meta: dict[str, object],
    final_rows: pd.DataFrame,
    up_rows: pd.DataFrame,
) -> str:
    top_cutoff = fmt_pct(target_meta.get("topTailCutoff"))
    bottom_cutoff = fmt_pct(target_meta.get("bottomTailCutoff"))
    tail_pct = fmt_pct(target_meta.get("tailPct"))
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{market_label} 백테스트</title>
<style>
*{{box-sizing:border-box}}body{{font-family:Arial,"Malgun Gothic",sans-serif;margin:0;background:#f6f8fb;color:#172033}}
header{{background:#0f172a;color:white;padding:18px 30px}}h1{{margin:0 0 6px;font-size:25px}}.sub{{color:#cbd5e1;font-size:14px}}
.topnav{{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}}.topnav a{{color:#dbeafe;border:1px solid #334155;border-radius:4px;padding:7px 10px;text-decoration:none;font-size:13px}}.topnav a:hover{{background:#1e293b}}
main{{padding:20px 30px;max-width:1500px;margin:0 auto}}section{{background:white;border:1px solid #d8e0ea;border-radius:6px;padding:16px;margin-bottom:16px;overflow:auto}}
h2{{font-size:18px;margin:0 0 12px}}.note{{color:#475569;line-height:1.55}}table{{border-collapse:separate;border-spacing:0;width:100%;font-size:13px}}th{{background:#eef3f8;color:#334155}}th,td{{border-bottom:1px solid #e2e8f0;padding:8px 9px;text-align:right;vertical-align:top;white-space:nowrap}}td:nth-child(1),td:nth-child(2),td:nth-child(3),td:nth-child(4),th:nth-child(1),th:nth-child(2),th:nth-child(3),th:nth-child(4){{text-align:left;white-space:normal;overflow-wrap:anywhere;word-break:keep-all}}@media(max-width:800px){{main{{padding:12px}}}}
</style>
</head>
<body>
<header><h1>{market_label} 백테스트</h1><div class="sub">신호일 {signal_date} 기준, 최신 데이터 {latest_date}까지 확인 가능한 실현 수익률</div><nav class="topnav"><a href="../index.html">홈</a><a href="dashboard.html">{market_label} 대시보드</a><a href="backtest.html">{market_label} 백테스트</a></nav></header>
<main>
<section><h2>학습 타겟 검산</h2>{html_table(target_summary_view(target))}<p class="note">원래 타겟은 6개월 뒤 같은 신호일 점수대상 안에서 실현수익률 상위 {tail_pct}에 드는 종목입니다. 이 신호일의 상위 {tail_pct} 기준선은 {top_cutoff}, 하위 {tail_pct} 기준선은 {bottom_cutoff}입니다. 하위 5% 조기경보는 상승 후보의 위험 필터로 해석합니다.</p></section>
<section><h2>수익률 요약</h2>{html_table(return_summary_view(summary))}<p class="note">평균수익률, 중앙값, 승률은 절대 성과 관점입니다. 위의 타겟 검산은 모델이 실제 학습 목표인 상위 5% 선별과 하위 5% 회피에 맞게 작동했는지 보는 지표입니다.</p></section>
<section><h2>최종 후보 상위</h2>{html_table(final_rows)}</section>
<section><h2>상승 후보 상위</h2>{html_table(up_rows)}</section>
</main>
</body>
</html>
"""


def add_nav_link(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if 'href="backtest.html"' in text:
        return
    text = text.replace("</nav></header>", '<a href="backtest.html">백테스트</a></nav></header>', 1)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    args = parser().parse_args()
    backtest_dir = Path(args.backtest_dir)
    dashboard_dir = Path(args.dashboard_dir)
    scores = pd.read_csv(backtest_dir / "walkforward_scores.csv", dtype={"ticker": str})
    summary = summarize_returns(scores)
    target, target_meta = summarize_target_fit(scores, args.tail_pct)
    summary.to_csv(backtest_dir / "backtest_summary.csv", index=False, encoding="utf-8-sig")
    target.to_csv(backtest_dir / "backtest_target_fit.csv", index=False, encoding="utf-8-sig")
    (backtest_dir / "backtest_summary.json").write_text(
        json.dumps(
            {
                "returnSummary": summary.to_dict(orient="records"),
                "targetFit": target.to_dict(orient="records"),
                "targetMeta": target_meta,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    (dashboard_dir / "backtest.html").write_text(
        report_html(
            args.market_label,
            args.signal_date,
            args.latest_date,
            summary,
            target,
            target_meta,
            top_table(scores, "isFinalCandidate"),
            top_table(scores, "isUpCandidate"),
        ),
        encoding="utf-8",
    )
    for name in ["dashboard.html", "stock.html"]:
        target_path = dashboard_dir / name
        if target_path.exists():
            add_nav_link(target_path)
    print(dashboard_dir / "backtest.html")


if __name__ == "__main__":
    main()
