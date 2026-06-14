from __future__ import annotations

import re
import shutil
from pathlib import Path


ROOT = Path("outputs")
KR_DIR = ROOT / "lgbm_warning_dashboard_macro_kr_latest"
US_DIR = ROOT / "lgbm_warning_dashboard_macro_us_latest"
LEGACY_REDIRECTS = {
    ROOT / "lgbm_warning_dashboard": "../lgbm_warning_dashboard_macro_kr_latest/",
    ROOT / "lgbm_warning_dashboard_us_full_daily": "../lgbm_warning_dashboard_macro_us_latest/",
}


CSS = """*{box-sizing:border-box}body{margin:0;font-family:Arial,"Malgun Gothic",sans-serif;background:#f5f7fb;color:#17202a}main{max-width:1100px;margin:0 auto;padding:40px 24px}h1{font-size:28px;margin:0 0 10px}p{color:#64748b;margin:0 0 22px;line-height:1.5}.grid{display:grid;grid-template-columns:repeat(2,minmax(240px,1fr));gap:14px}a{display:block;background:white;border:1px solid #d9e2ec;border-radius:8px;padding:18px;text-decoration:none;color:#17202a}a:hover{border-color:#1d4ed8;box-shadow:0 8px 24px rgba(15,23,42,.08)}b{display:block;font-size:18px;margin-bottom:8px}span{color:#64748b;font-size:14px;line-height:1.45}@media(max-width:720px){.grid{grid-template-columns:1fr}}"""


def home_html(prefix: str = "") -> str:
    kr = f"{prefix}lgbm_warning_dashboard_macro_kr_latest/"
    us = f"{prefix}lgbm_warning_dashboard_macro_us_latest/"
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>주식 조기경보 대시보드</title>
<style>{CSS}</style>
</head>
<body>
<main>
<h1>주식 조기경보 대시보드</h1>
<p>한국과 미국 최신 조기경보 결과와 백테스트를 같은 기준으로 확인합니다. 최신 신호일은 장 데이터가 확인된 가장 최근 거래일 기준입니다.</p>
<div class="grid">
<a href="{kr}dashboard.html"><b>한국 대시보드</b><span>KOSPI/KOSDAQ 최신 후보와 종목 상세</span></a>
<a href="{us}dashboard.html"><b>미국 대시보드</b><span>NASDAQ/NYSE 최신 후보와 종목 상세</span></a>
<a href="{kr}backtest.html"><b>한국 백테스트</b><span>확정 수익률 기준 후보 성과 검증</span></a>
<a href="{us}backtest.html"><b>미국 백테스트</b><span>확정 수익률 기준 후보 성과 검증</span></a>
</div>
</main>
</body>
</html>
"""


def nav_html(market: str) -> str:
    if market == "kr":
        return (
            '<nav class="topnav"><a href="../index.html">홈</a>'
            '<a href="dashboard.html">한국 대시보드</a>'
            '<a href="backtest.html">한국 백테스트</a>'
            '<a href="../lgbm_warning_dashboard_macro_us_latest/dashboard.html">미국 대시보드</a>'
            '<a href="../lgbm_warning_dashboard_macro_us_latest/backtest.html">미국 백테스트</a></nav>'
        )
    return (
        '<nav class="topnav"><a href="../index.html">홈</a>'
        '<a href="../lgbm_warning_dashboard_macro_kr_latest/dashboard.html">한국 대시보드</a>'
        '<a href="../lgbm_warning_dashboard_macro_kr_latest/backtest.html">한국 백테스트</a>'
        '<a href="dashboard.html">미국 대시보드</a>'
        '<a href="backtest.html">미국 백테스트</a></nav>'
    )


def sync_nav(path: Path, market: str) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'<nav class="topnav">.*?</nav>', nav_html(market), text, count=1, flags=re.S)
    path.write_text(text, encoding="utf-8")


def redirect_html(target_base: str, page: str, label: str) -> str:
    target = f"{target_base}{page}"
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="0; url={target}">
<title>{label}로 이동</title>
<script>
const next = "{target}" + window.location.search + window.location.hash;
window.location.replace(next);
</script>
</head>
<body>
<p><a href="{target}">{label}로 이동</a></p>
</body>
</html>
"""


def sync_legacy_redirects() -> None:
    for directory, target_base in LEGACY_REDIRECTS.items():
        directory.mkdir(parents=True, exist_ok=True)
        for child in directory.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        label = "최신 한국 대시보드" if "us" not in directory.name else "최신 미국 대시보드"
        for page in ["index.html", "dashboard.html", "stock.html", "backtest.html"]:
            directory.joinpath(page).write_text(redirect_html(target_base, page, label), encoding="utf-8")


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "index.html").write_text(home_html(), encoding="utf-8")
    (KR_DIR / "index.html").write_text(home_html("../"), encoding="utf-8")
    (US_DIR / "index.html").write_text(home_html("../"), encoding="utf-8")
    for path in [KR_DIR / "dashboard.html", KR_DIR / "stock.html", KR_DIR / "backtest.html"]:
        sync_nav(path, "kr")
    for path in [US_DIR / "dashboard.html", US_DIR / "stock.html", US_DIR / "backtest.html"]:
        sync_nav(path, "us")
    sync_legacy_redirects()
    print(ROOT / "index.html")


if __name__ == "__main__":
    main()
