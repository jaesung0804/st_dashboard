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
<title>Stock Early-Warning Dashboard</title>
<style>{CSS}</style>
</head>
<body>
<main>
<h1>Stock Early-Warning Dashboard</h1>
<p>Review the latest Korea and US early-warning candidates alongside backtest results. Latest signal dates are based on the most recent available trading data.</p>
<div class="grid">
<a href="{kr}dashboard.html"><b>Korea Dashboard</b><span>Latest KOSPI/KOSDAQ candidate details</span></a>
<a href="{us}dashboard.html"><b>US Dashboard</b><span>Latest NASDAQ/NYSE candidate details</span></a>
<a href="{kr}backtest.html"><b>Korea Backtest</b><span>Candidate performance using finalized forward returns</span></a>
<a href="{us}backtest.html"><b>US Backtest</b><span>Candidate performance using finalized forward returns</span></a>
</div>
</main>
</body>
</html>
"""


def nav_html(market: str) -> str:
    if market == "kr":
        return (
            '<nav class="topnav"><a href="../index.html">Home</a>'
            '<a href="dashboard.html">Korea Dashboard</a>'
            '<a href="backtest.html">Korea Backtest</a>'
            '<a href="../lgbm_warning_dashboard_macro_us_latest/dashboard.html">US Dashboard</a>'
            '<a href="../lgbm_warning_dashboard_macro_us_latest/backtest.html">US Backtest</a></nav>'
        )
    return (
        '<nav class="topnav"><a href="../index.html">Home</a>'
        '<a href="../lgbm_warning_dashboard_macro_kr_latest/dashboard.html">Korea Dashboard</a>'
        '<a href="../lgbm_warning_dashboard_macro_kr_latest/backtest.html">Korea Backtest</a>'
        '<a href="dashboard.html">US Dashboard</a>'
        '<a href="backtest.html">US Backtest</a></nav>'
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
<title>Go to {label}</title>
<script>
const next = "{target}" + window.location.search + window.location.hash;
window.location.replace(next);
</script>
</head>
<body>
<p><a href="{target}">Go to {label}</a></p>
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
        label = "latest Korea dashboard" if "us" not in directory.name else "latest US dashboard"
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
