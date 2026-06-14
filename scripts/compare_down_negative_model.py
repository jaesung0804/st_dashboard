from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


MODELS = [
    ("bottom5", "기존 하위5% 하락모델"),
    ("negative6m", "신규 6개월 음수 하락모델"),
]
MARKETS = [
    ("kr", "한국"),
    ("us", "미국"),
]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="outputs/down_negative_model_comparison")
    p.add_argument("--baseline-kr-latest", default="outputs/walkforward_warning_macro_kr_latest_20260614")
    p.add_argument("--baseline-us-latest", default="outputs/walkforward_warning_macro_us_latest_20260614")
    p.add_argument("--baseline-kr-backtest", default="outputs/walkforward_warning_macro_kr_backtest_20251128")
    p.add_argument("--baseline-us-backtest", default="outputs/walkforward_warning_macro_us_backtest_20251128")
    p.add_argument("--negative-kr-latest", default="outputs/walkforward_warning_macro_down_negative_kr_latest_20260614")
    p.add_argument("--negative-us-latest", default="outputs/walkforward_warning_macro_down_negative_us_latest_20260614")
    p.add_argument("--negative-kr-backtest", default="outputs/walkforward_warning_macro_down_negative_kr_backtest_20251128")
    p.add_argument("--negative-us-backtest", default="outputs/walkforward_warning_macro_down_negative_us_backtest_20251128")
    p.add_argument("--tail-pct", type=float, default=0.05)
    return p


def parse_pct(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip()
    text = text.where(text.str.endswith("%"), pd.NA)
    return pd.to_numeric(text.str.rstrip("%"), errors="coerce") / 100


def load_scores(path: Path) -> pd.DataFrame:
    return pd.read_csv(path / "walkforward_scores.csv", dtype={"ticker": str})


def load_validation(path: Path) -> pd.DataFrame:
    p = path / "walkforward_validation.csv"
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


def metrics(market: str, model_key: str, model_label: str, latest_dir: Path, backtest_dir: Path, tail_pct: float) -> dict[str, object]:
    latest = load_scores(latest_dir)
    backtest = load_scores(backtest_dir)
    realized = parse_pct(backtest["actRet_6m"])
    known = backtest[realized.notna()].copy()
    known["realized6m"] = realized.dropna()
    top_cut = known["realized6m"].quantile(1 - tail_pct)
    bottom_cut = known["realized6m"].quantile(tail_pct)

    final = known[known["isFinalCandidate"].astype(bool)]
    up = known[known["isUpCandidate"].astype(bool)]
    down = known[known["isDownRed"].astype(bool)]
    latest_final = latest[latest["isFinalCandidate"].astype(bool)]
    latest_down = latest[latest["isDownRed"].astype(bool)]
    val = load_validation(backtest_dir)

    def count_top(frame: pd.DataFrame) -> int:
        return int((frame["realized6m"] >= top_cut).sum())

    def count_bottom(frame: pd.DataFrame) -> int:
        return int((frame["realized6m"] <= bottom_cut).sum())

    return {
        "market": market,
        "model": model_key,
        "modelLabel": model_label,
        "latestFinalCount": int(len(latest_final)),
        "latestDownRedCount": int(len(latest_down)),
        "backtestFinalCount": int(len(final)),
        "backtestUpCount": int(len(up)),
        "backtestDownRedCount": int(len(down)),
        "finalAvg6m": float(final["realized6m"].mean()) if len(final) else None,
        "finalMedian6m": float(final["realized6m"].median()) if len(final) else None,
        "finalWinRate6m": float((final["realized6m"] > 0).mean()) if len(final) else None,
        "finalTop5Hits": count_top(final),
        "finalTop5HitRate": count_top(final) / len(final) if len(final) else None,
        "finalBottom5Hits": count_bottom(final),
        "finalBottom5Exposure": count_bottom(final) / len(final) if len(final) else None,
        "downRedNegativeRate": float((down["realized6m"] < 0).mean()) if len(down) else None,
        "downRedBottom5Hits": count_bottom(down),
        "downRedBottom5HitRate": count_bottom(down) / len(down) if len(down) else None,
        "validationDownAuc": float(val["down_auc"].mean()) if "down_auc" in val and not val.empty else None,
        "validationUpAuc": float(val["up_auc"].mean()) if "up_auc" in val and not val.empty else None,
    }


def fmt_pct(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value) * 100:.1f}%"


def html_table(frame: pd.DataFrame) -> str:
    view = frame.copy()
    for col in [
        "finalAvg6m",
        "finalMedian6m",
        "finalWinRate6m",
        "finalTop5HitRate",
        "finalBottom5Exposure",
        "downRedNegativeRate",
        "downRedBottom5HitRate",
        "validationDownAuc",
        "validationUpAuc",
    ]:
        if col in view:
            view[col] = view[col].map(fmt_pct if "Auc" not in col else lambda x: "" if pd.isna(x) else f"{float(x):.3f}")
    view = view.rename(
        columns={
            "market": "시장",
            "modelLabel": "모델",
            "latestFinalCount": "최신 최종후보",
            "latestDownRedCount": "최신 하락경보",
            "backtestFinalCount": "BT 최종후보",
            "backtestUpCount": "BT 상승후보",
            "backtestDownRedCount": "BT 하락경보",
            "finalAvg6m": "최종 6M 평균",
            "finalMedian6m": "최종 6M 중앙",
            "finalWinRate6m": "최종 6M 승률",
            "finalTop5Hits": "최종 상위5% 적중",
            "finalTop5HitRate": "최종 상위5% 비율",
            "finalBottom5Hits": "최종 하위5% 포함",
            "finalBottom5Exposure": "최종 하위5% 노출",
            "downRedNegativeRate": "하락경보 음수비율",
            "downRedBottom5Hits": "하락경보 하위5% 적중",
            "downRedBottom5HitRate": "하락경보 하위5% 비율",
            "validationDownAuc": "하락 AUC",
            "validationUpAuc": "상승 AUC",
        }
    )
    cols = [
        "시장",
        "모델",
        "최신 최종후보",
        "최신 하락경보",
        "BT 최종후보",
        "최종 6M 평균",
        "최종 6M 승률",
        "최종 상위5% 적중",
        "최종 상위5% 비율",
        "최종 하위5% 포함",
        "최종 하위5% 노출",
        "하락경보 음수비율",
        "하락경보 하위5% 적중",
        "하락경보 하위5% 비율",
        "하락 AUC",
    ]
    return view[cols].to_html(index=False, escape=True, border=0, classes="data-table")


def report_html(summary: pd.DataFrame) -> str:
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>하락 타겟 모델 비교</title>
<style>
*{{box-sizing:border-box}}body{{font-family:Arial,"Malgun Gothic",sans-serif;margin:0;background:#f6f8fb;color:#172033}}
header{{background:#0f172a;color:white;padding:18px 30px}}h1{{margin:0 0 6px;font-size:25px}}.sub{{color:#cbd5e1;font-size:14px}}
.topnav{{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}}.topnav a{{color:#dbeafe;border:1px solid #334155;border-radius:4px;padding:7px 10px;text-decoration:none;font-size:13px}}.topnav a:hover{{background:#1e293b}}
main{{padding:20px 30px;max-width:1700px;margin:0 auto}}section{{background:white;border:1px solid #d8e0ea;border-radius:6px;padding:16px;margin-bottom:16px;overflow:auto}}
h2{{font-size:18px;margin:0 0 12px}}.note{{color:#475569;line-height:1.55}}table{{border-collapse:separate;border-spacing:0;width:100%;font-size:13px}}th{{background:#eef3f8;color:#334155}}th,td{{border-bottom:1px solid #e2e8f0;padding:8px 9px;text-align:right;vertical-align:top;white-space:nowrap}}td:nth-child(1),td:nth-child(2),th:nth-child(1),th:nth-child(2){{text-align:left;white-space:normal;overflow-wrap:anywhere;word-break:keep-all}}
</style>
</head>
<body>
<header><h1>하락 타겟 모델 비교</h1><div class="sub">기존 하위 5% 하락모델과 신규 6개월 음수수익률 하락모델의 후보 필터링 차이</div><nav class="topnav"><a href="../index.html">홈</a><a href="../lgbm_warning_dashboard_macro_kr_latest/dashboard.html">한국 대시보드</a><a href="../lgbm_warning_dashboard_macro_us_latest/dashboard.html">미국 대시보드</a></nav></header>
<main>
<section><h2>요약</h2>{html_table(summary)}<p class="note">최신 후보 수는 2026-06-12 기준입니다. BT 지표는 2025-11-28 신호일 기준 6개월 실현수익률로 계산했습니다. 신규 모델은 하락 확률의 학습 타겟만 '6개월 뒤 수익률 &lt; 0'으로 바꾼 별도 산출물이며, 기존 모델 결과는 건드리지 않았습니다.</p></section>
</main>
</body>
</html>
"""


def main() -> None:
    args = parser().parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        ("kr", "bottom5", "latest"): Path(args.baseline_kr_latest),
        ("us", "bottom5", "latest"): Path(args.baseline_us_latest),
        ("kr", "bottom5", "backtest"): Path(args.baseline_kr_backtest),
        ("us", "bottom5", "backtest"): Path(args.baseline_us_backtest),
        ("kr", "negative6m", "latest"): Path(args.negative_kr_latest),
        ("us", "negative6m", "latest"): Path(args.negative_us_latest),
        ("kr", "negative6m", "backtest"): Path(args.negative_kr_backtest),
        ("us", "negative6m", "backtest"): Path(args.negative_us_backtest),
    }
    rows = []
    for market, market_label in MARKETS:
        for model_key, model_label in MODELS:
            rows.append(
                metrics(
                    market_label,
                    model_key,
                    model_label,
                    paths[(market, model_key, "latest")],
                    paths[(market, model_key, "backtest")],
                    args.tail_pct,
                )
            )
    summary = pd.DataFrame(rows)
    summary.to_csv(out_dir / "summary.csv", index=False, encoding="utf-8-sig")
    (out_dir / "summary.json").write_text(json.dumps(summary.to_dict(orient="records"), ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "index.html").write_text(report_html(summary), encoding="utf-8")
    print(out_dir / "index.html")


if __name__ == "__main__":
    main()
