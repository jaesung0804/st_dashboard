from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


GROUPS = [
    ("final", "최종 후보", "isFinalCandidate"),
    ("up", "상승 후보", "isUpCandidate"),
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
    return p


def parse_pct(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip()
    text = text.where(text.str.endswith("%"), pd.NA)
    return pd.to_numeric(text.str.rstrip("%"), errors="coerce") / 100


def summarize(scores: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for group_key, group_label, flag in GROUPS:
        part = scores if flag is None else scores[scores[flag].astype(bool)]
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


def report_html(
    market_label: str,
    signal_date: str,
    latest_date: str,
    summary: pd.DataFrame,
    final_rows: pd.DataFrame,
    up_rows: pd.DataFrame,
) -> str:
    summary_view = summary.copy()
    for col in ["avgReturn", "medianReturn", "winRate", "bestReturn", "worstReturn"]:
        summary_view[col] = summary_view[col].map(fmt_pct)
    summary_view = summary_view.rename(
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
<header><h1>{market_label} 백테스트</h1><div class="sub">신호일 {signal_date} 기준, 최신 데이터 {latest_date}까지 확인 가능한 실현 수익률</div><nav class="topnav"><a href="../index.html">대시보드 홈</a><a href="dashboard.html">목록으로</a><a href="stock.html">종목 상세</a></nav></header>
<main>
<section><h2>요약</h2>{html_table(summary_view)}<p class="note">6개월 및 12개월 실현 수익률은 아직 확정되지 않은 종목이 있을 수 있습니다. 최신 신호일은 {latest_date}이며, 백테스트는 미래 수익률 확인이 가능한 {signal_date} 신호를 기준으로 계산했습니다.</p></section>
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
    summary = summarize(scores)
    summary.to_csv(backtest_dir / "backtest_summary.csv", index=False, encoding="utf-8-sig")
    (backtest_dir / "backtest_summary.json").write_text(
        json.dumps(summary.to_dict(orient="records"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    (dashboard_dir / "backtest.html").write_text(
        report_html(
            args.market_label,
            args.signal_date,
            args.latest_date,
            summary,
            top_table(scores, "isFinalCandidate"),
            top_table(scores, "isUpCandidate"),
        ),
        encoding="utf-8",
    )
    for name in ["dashboard.html", "stock.html"]:
        target = dashboard_dir / name
        if target.exists():
            add_nav_link(target)
    print(dashboard_dir / "backtest.html")


if __name__ == "__main__":
    main()
