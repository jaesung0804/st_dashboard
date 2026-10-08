from __future__ import annotations

import argparse
import base64
import hashlib
import html as html_lib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

from build_price_context import build_price_context
from build_simulation_pages import build as build_simulation_pages


ROOT = Path("outputs")
DEPLOY_DIR = Path(".pages-deploy")
BUILD_VERSION = os.environ.get("PAGES_BUILD_VERSION", str(int(time.time())))
ACTION_URLS = {
    "all": "https://github.com/jaesung0804/st_dashboard/actions",
    "kr": "https://github.com/jaesung0804/st_dashboard/actions/workflows/daily-refresh-kr.yml",
    "us": "https://github.com/jaesung0804/st_dashboard/actions/workflows/daily-refresh-us.yml",
}
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
DASHBOARDS = {
    "kr": {
        "source": ROOT / "lgbm_warning_dashboard_macro_kr_latest",
        "target": "lgbm_warning_dashboard_macro_kr_latest",
        "label": "한국",
        "subtitle": "KOSPI/KOSDAQ 조기경보 후보",
    },
    "us": {
        "source": ROOT / "lgbm_warning_dashboard_macro_us_latest",
        "target": "lgbm_warning_dashboard_macro_us_latest",
        "label": "미국",
        "subtitle": "NASDAQ/NYSE 조기경보 후보",
    },
}

DASHBOARDS["kr"]["label"] = "한국"
DASHBOARDS["kr"]["subtitle"] = "KOSPI/KOSDAQ 조기경보 후보"
DASHBOARDS["us"]["label"] = "미국"
DASHBOARDS["us"]["subtitle"] = "NASDAQ/NYSE 조기경보 후보"


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build the lightweight GitHub Pages deployment bundle.")
    p.add_argument("--deploy-dir", default=str(DEPLOY_DIR))
    p.add_argument("--days", type=int, default=60, help="Trading dates to publish, roughly three months by default.")
    p.add_argument("--raw-dir", type=Path, default=Path("data/raw"), help="Retained prices for read-only past-return and unscored-stock lookup.")
    p.add_argument("--push", action="store_true", help="Publish the bundle while preserving gh-pages history and other pages.")
    p.add_argument("--repo", default="https://github.com/jaesung0804/st_dashboard.git")
    p.add_argument(
        "--preserve-existing-pages",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Copy any missing dashboard directories from the current gh-pages branch. Defaults to on when --push is used.",
    )
    return p


def json_load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def json_dump(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def safe_stock_filename(ticker: str) -> str:
    name = quote(str(ticker), safe="")
    stem = name.rsplit(".", 1)[0].upper()
    if stem in WINDOWS_RESERVED_NAMES:
        name = f"{name}_"
    return f"{name}.json"


def selected_date_files(source: Path, days: int) -> list[Path]:
    files = sorted((source / "walkforward_scores_by_date").glob("*.json"), key=lambda p: p.stem, reverse=True)
    if not files:
        raise FileNotFoundError(f"No date JSON files found under {source}")
    return files[:days]


def has_date_files(source: Path) -> bool:
    return any((source / "walkforward_scores_by_date").glob("*.json"))


def load_us_exchange_map() -> dict[str, str]:
    candidates = [
        Path("data/raw/us_listings_nasdaq_nyse_yfinfo_state.csv"),
        *sorted(Path("data/raw").glob("us_listings_nasdaq_nyse_yfinfo_*.csv"), reverse=True),
    ]
    for path in candidates:
        if not path.exists() or "manifest" in path.stem:
            continue
        try:
            import pandas as pd

            frame = pd.read_csv(path, dtype={"ticker": str})
            if {"ticker", "exchange"}.issubset(frame.columns):
                return dict(zip(frame["ticker"].astype(str).str.upper(), frame["exchange"].astype(str)))
        except Exception:
            continue
    return {}


def normalized_row(row: dict, label: str, exchange_map: dict[str, str]) -> dict:
    out = dict(row)
    if out.get("modelVersion"):
        return out  # The live ledger already contains the signal-date metadata.
    ticker = str(out.get("ticker", "")).upper()
    if label == "미국":
        out.setdefault("currency", "USD")
        out["exchange"] = out.get("exchange") or exchange_map.get(ticker, "")
        if "closeRaw" not in out or out.get("closeRaw") in {"", None}:
            try:
                out["closeRaw"] = float(str(out.get("close", "")).replace(",", "").replace("$", ""))
            except ValueError:
                out["closeRaw"] = ""
        if out.get("closeRaw") not in {"", None} and ("/" not in str(out.get("close", ""))):
            out["close"] = f"${float(out['closeRaw']):,.2f}"
        for horizon in ["1m", "3m", "6m", "12m"]:
            key = f"expClose_{horizon}"
            value = str(out.get(key, ""))
            if value and value != "미확인" and not value.startswith("$"):
                try:
                    out[key] = f"${float(value.replace(',', '')):,.2f}"
                except ValueError:
                    pass
    else:
        out.setdefault("currency", "KRW")
    for key in list(out):
        if key.startswith("actRet_") or key.startswith("actClose_"):
            out.pop(key, None)
    return out


def theme_head_script() -> str:
    return """<script>
(function(){try{if(localStorage.getItem('dashboardTheme')==='dark')document.documentElement.dataset.theme='dark';}catch(e){}})();
</script>"""


def theme_toggle_script() -> str:
    return """<script>
(function(){
  const button=document.getElementById('themeToggle');
  if(!button)return;
  function apply(theme){
    const dark=theme==='dark';
    document.documentElement.dataset.theme=dark?'dark':'';
    button.textContent=dark?'라이트모드':'다크모드';
    button.setAttribute('aria-pressed',String(dark));
    try{localStorage.setItem('dashboardTheme',dark?'dark':'light');}catch(e){}
  }
  button.addEventListener('click',()=>apply(document.documentElement.dataset.theme==='dark'?'light':'dark'));
  apply(document.documentElement.dataset.theme==='dark'?'dark':'light');
})();
</script>"""


def header_html(market: str = "") -> str:
    assets = Path(__file__).resolve().parent / "dashboard_web"
    html = (assets / "header.html").read_text(encoding="utf-8")
    html = html.replace("@@ROOT@@", "../" if market else "")
    for key, value in (("HOME", ""), ("KR", "kr"), ("US", "us")):
        html = html.replace(f"@@{key}_CURRENT@@", 'aria-current="page"' if market == value else "")
    return html


def home_html() -> str:
    assets = Path(__file__).resolve().parent / "dashboard_web"
    html = (assets / "home.html").read_text(encoding="utf-8")
    for key, value in {
        "@@HEADER@@": header_html(), "@@BUILD@@": BUILD_VERSION,
        "@@THEME_HEAD@@": theme_head_script(), "@@THEME_TOGGLE@@": theme_toggle_script(),
        "@@ACTION_URL@@": ACTION_URLS["all"],
    }.items():
        html = html.replace(key, value)
    return html


def investment_entry_html(market: str = "all") -> str:
    """Move saved entry URLs to the current explorer, preserving only search."""
    prefix = "" if market == "all" else "../"
    target = f"{prefix}investment/?market={market}&sort=score&v={quote(BUILD_VERSION, safe='')}"
    template = (Path(__file__).resolve().parent / "dashboard_web" / "entry.html").read_text(encoding="utf-8")
    return (template.replace("@@TARGET_JSON@@", json.dumps(target).replace("<", "\\u003c"))
            .replace("@@TARGET_HTML@@", html_lib.escape(target, quote=True))
            .replace("@@HISTORY@@", "history.html" if market == "all" else "legacy.html"))


def publish_investment_entrypoints(deploy_dir: Path) -> None:
    """Only migrate when a verified explorer has actually been built."""
    if not (deploy_dir / "investment" / "index.html").is_file():
        return
    home = deploy_dir / "index.html"
    if home.is_file() and 'data-investment-entry="v1"' not in home.read_text(encoding="utf-8"):
        shutil.copyfile(home, deploy_dir / "history.html")
    home.write_bytes(investment_entry_html().encode("utf-8"))
    for market, config in DASHBOARDS.items():
        folder = deploy_dir / config["target"]
        dashboard = folder / "dashboard.html"
        if not dashboard.is_file():
            continue
        if 'data-investment-entry="v1"' not in dashboard.read_text(encoding="utf-8"):
            shutil.copyfile(dashboard, folder / "legacy.html")
        for name in ("dashboard.html", "index.html"):
            (folder / name).write_bytes(investment_entry_html(market).encode("utf-8"))
        manifest_path = folder / "manifest.json"
        if manifest_path.is_file():
            manifest = json_load(manifest_path)
            ui_assets = manifest.setdefault("uiAssets", {})
            for name in ("dashboard.html", "index.html", "legacy.html"):
                if (folder / name).is_file():
                    ui_assets[name] = hashlib.sha256((folder / name).read_bytes()).hexdigest()
            json_dump(manifest_path, manifest)


def render_dashboard_template(template: str, label: str, other_href: str, other_label: str) -> str:
    assets = Path(__file__).resolve().parent / "dashboard_web"
    html = (assets / template).read_text(encoding="utf-8")
    replacements = {
        "@@HEADER@@": header_html("kr" if label == "한국" else "us"),
        "@@LABEL@@": label, "@@OTHER_HREF@@": other_href, "@@OTHER_LABEL@@": other_label,
        "@@BUILD@@": BUILD_VERSION, "@@THEME_HEAD@@": theme_head_script(),
        "@@THEME_TOGGLE@@": theme_toggle_script(),
        "@@ACTION_URL@@": ACTION_URLS["kr" if label == "한국" else "us"],
        "@@GUIDE@@": (assets / "model-guide.html").read_text(encoding="utf-8"),
        "@@SEARCH_EXAMPLE@@": "예: 삼성전자, 005930" if label == "한국" else "예: Apple, AAPL",
    }
    for key, value in replacements.items():
        html = html.replace(key, value)
    return html


def dashboard_html(label: str, subtitle: str, other_href: str, other_label: str) -> str:
    return render_dashboard_template("dashboard.html", label, other_href, other_label)


def stock_html(label: str, other_href: str, other_label: str) -> str:
    return render_dashboard_template("stock.html", label, other_href, other_label)


def build_dashboard(source: Path, target: Path, days: int, label: str, subtitle: str, other_href: str, other_label: str, raw_dir: Path = Path("data/raw")) -> None:
    source_manifest = json_load(source / "manifest.json") if (source / "manifest.json").exists() else {}
    date_files = selected_date_files(source, days)
    dates = [path.stem for path in date_files]
    by_date_target = target / "walkforward_scores_by_date"
    if by_date_target.exists():
        shutil.rmtree(by_date_target)
    by_date_target.mkdir(parents=True, exist_ok=True)
    latest_rows: list[dict] = []
    history: dict[str, list[dict]] = defaultdict(list)
    sectors: set[str] = set()
    detail_map: dict[str, set[str]] = defaultdict(set)
    stock_index: dict[str, dict[str, str]] = {}
    exchange_map = load_us_exchange_map() if label == "미국" else {}
    for src_file in date_files:
        rows = json_load(src_file)
        rows = rows if isinstance(rows, list) else []
        rows = [normalized_row(row, label, exchange_map) for row in rows]
        json_dump(by_date_target / src_file.name, rows)
        if src_file == date_files[0]:
            latest_rows = rows
        for row in rows:
            ticker = str(row.get("ticker", ""))
            sector = str(row.get("sector", ""))
            detail = str(row.get("detailSector", ""))
            if sector:
                sectors.add(sector)
                if detail:
                    detail_map[sector].add(detail)
            if ticker:
                history[ticker].append(row)
                stock_index[ticker] = {
                    "ticker": ticker,
                    "name": str(row.get("name", "")),
                    "sector": sector,
                    "detailSector": detail,
                    "exchange": str(row.get("exchange", "")),
                }
    target.mkdir(parents=True, exist_ok=True)
    # Presentation files are separate from archived prediction rows and models.
    price_context = build_price_context(raw_dir, target, "kr" if label == "한국" else "us", dates)
    (target / "dashboard.html").write_bytes(dashboard_html(label, subtitle, other_href, other_label).encode("utf-8"))
    (target / "index.html").write_bytes(dashboard_html(label, subtitle, other_href, other_label).encode("utf-8"))
    (target / "stock.html").write_bytes(stock_html(label, other_href, other_label).encode("utf-8"))
    assets = Path(__file__).resolve().parent / "dashboard_web"
    (target / "model.html").write_bytes(render_dashboard_template("model.html", label, other_href, other_label).encode("utf-8"))
    # Rebuild the report from restored state on every publisher, so an ordinary
    # daily refresh cannot discard the independent macro experiment.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from ai_stock_assistant.shadow_ews import export_report
    export_report(raw_dir.parent / "dashboard_ews_shadow", raw_dir.parent / "dashboard_ews",
                  "kr" if label == "한국" else "us", source.parent)
    shutil.copyfile(source / "model_report.json", target / "model_report.json")
    from ai_stock_assistant.research_report import export_report as export_accounting_report
    export_accounting_report(raw_dir.parent / "dashboard_research", raw_dir,
                             "kr" if label == "한국" else "us", target / "research_report.json")
    for filename in ("dashboard.css", "common.js", "dashboard.js", "stock.js", "model.js", "research.js"):
        # Canonical LF before hashing, independent of a Windows app checkout.
        (target / filename).write_bytes((assets / filename).read_text(encoding="utf-8").encode("utf-8"))
    ui_assets = {name: hashlib.sha256((target / name).read_bytes()).hexdigest()
                 for name in ("dashboard.html", "index.html", "stock.html", "model.html", "dashboard.css", "common.js", "dashboard.js", "stock.js", "model.js", "model_report.json", "research.js", "research_report.json")}
    json_dump(
        target / "manifest.json",
        {
            "dates": dates,
            "sectors": sorted(sectors),
            "detailMap": {key: sorted(value) for key, value in sorted(detail_map.items())},
            "latest": dates[0],
            "latestFinal": sum(bool(row.get("isFinalCandidate")) for row in latest_rows),
            "latestUp": sum(bool(row.get("isUpCandidate")) for row in latest_rows),
            "latestRows": len(latest_rows),
            "dateCount": len(dates),
            "subLabels": {
                "growth_profit": "성장/수익",
                "cash_quality": "현금흐름",
                "valuation": "밸류",
                "price_volume": "가격/거래",
                "risk_overheat": "위험/과열",
            },
            "validation": source_manifest.get("validation", []),
            "models": source_manifest.get("models", {}),
            "predictionPolicy": source_manifest.get("predictionPolicy", "legacy_unversioned"),
            "predictionKindsByDate": source_manifest.get("predictionKindsByDate", {}),
            "marketName": label,
            "uiVersion": "ews-research-workspace-v3",
            "presentationBuild": BUILD_VERSION,
            "uiAssets": ui_assets,
            "priceContext": price_context,
        },
    )
    json_dump(target / "stock_index.json", sorted(stock_index.values(), key=lambda row: row["ticker"]))
    history_dir = target / "stock_history"
    if history_dir.exists():
        shutil.rmtree(history_dir)
    for ticker, rows in history.items():
        rows.sort(key=lambda row: str(row.get("date", "")))
        index = stock_index[ticker]
        json_dump(
            history_dir / safe_stock_filename(ticker),
            {"ticker": ticker, "name": index["name"], "sector": index["sector"], "detailSector": index["detailSector"], "exchange": index["exchange"], "rows": rows},
        )


def run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True)


def git_auth_prefix(repo: str) -> list[str]:
    token = os.environ.get("GITHUB_TOKEN")
    if not token or not repo.startswith("https://github.com/"):
        return ["git"]
    credential = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    return ["git", "-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {credential}"]


def manifest_latest(path: Path) -> str:
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        return ""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(manifest.get("latest", ""))


def restore_existing_dashboards(deploy_dir: Path, repo: str, targets: list[str]) -> int:
    targets = sorted(set(targets))
    if not targets:
        return 0

    with tempfile.TemporaryDirectory(prefix="pages-existing-") as tmp:
        checkout = Path(tmp) / "gh-pages"
        result = subprocess.run(
            [
                *git_auth_prefix(repo),
                "clone",
                "--depth",
                "1",
                "--branch",
                "gh-pages",
                repo,
                str(checkout),
            ],
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            print(f"Could not restore existing pages from gh-pages: {result.stderr.strip()}", flush=True)
            return 0

        restored = 0
        for target in targets:
            source = checkout / target
            if not source.exists():
                print(f"No existing {target} found on gh-pages.", flush=True)
                continue

            destination = deploy_dir / target
            existing_latest = manifest_latest(source)
            local_latest = manifest_latest(destination)
            if destination.exists() and existing_latest <= local_latest:
                continue

            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(source, destination)
            restored += 1
            reason = "missing locally" if not local_latest else f"newer than local {local_latest}"
            print(f"Restored existing {target} from gh-pages ({existing_latest}; {reason}).", flush=True)
        return restored


def remove_tree(path: Path) -> None:
    def onexc(func, target, exc_info):  # noqa: ANN001
        os.chmod(target, 0o700)
        func(target)

    shutil.rmtree(path, onexc=onexc)


def push_pages(deploy_dir: Path, repo: str) -> None:
    # Overlay the verified bundle onto current Pages. Other teams' pages and the
    # branch history survive publication; concurrent updates fail safely.
    (deploy_dir / ".gitattributes").write_bytes(b"* -text\n")
    with tempfile.TemporaryDirectory(prefix="pages-publish-") as tmp:
        checkout = Path(tmp) / "pages"
        subprocess.run([*git_auth_prefix(repo), "clone", "--depth", "1", "--branch", "gh-pages", repo, str(checkout)], check=True)
        run_git(["config", "core.autocrlf", "false"], checkout)
        run_git(["config", "user.name", "github-actions[bot]"], checkout)
        run_git(["config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], checkout)
        for source in deploy_dir.iterdir():
            if source.name == ".git":
                continue
            target = checkout / source.name
            if source.is_dir():
                # Rebuilt market data is a complete bounded window, not an
                # indefinitely growing collection. Unknown sibling pages remain.
                if source.name in {d["target"] for d in DASHBOARDS.values()} and target.exists():
                    if manifest_latest(source) < manifest_latest(target):
                        print(f"Preserving newer published market data: {source.name}", flush=True)
                        continue
                    shutil.rmtree(target)
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                shutil.copyfile(source, target)
        run_git(["add", "-A"], checkout)
        unchanged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=checkout).returncode == 0
        if unchanged:
            print("Pages already matches the verified bundle.", flush=True)
            return
        run_git(["commit", "-m", "Publish verified dashboard and simulation pages"], checkout)
        subprocess.run([*git_auth_prefix(repo), "push", "origin", "HEAD:gh-pages"], cwd=checkout, check=True)


def main() -> None:
    args = parser().parse_args()
    deploy_dir = Path(args.deploy_dir)
    if deploy_dir.exists():
        remove_tree(deploy_dir)
    deploy_dir.mkdir(parents=True)
    (deploy_dir / ".nojekyll").write_text("", encoding="utf-8")
    (deploy_dir / "index.html").write_bytes(home_html().encode("utf-8"))
    assets = Path(__file__).resolve().parent / "dashboard_web"
    for filename in ("dashboard.css", "common.js", "home.js"):
        (deploy_dir / filename).write_bytes((assets / filename).read_text(encoding="utf-8").encode("utf-8"))
    from export_investment_experiment import export as export_investment_experiment
    export_investment_experiment(deploy_dir)
    (deploy_dir / ".gitattributes").write_bytes(b"* -text\n")
    simulation_result = Path("docs/replays/2026-09-10-v2/manifest.json")
    if simulation_result.exists():
        build_simulation_pages(deploy_dir / "simulation")
    built_dashboards = 0
    missing_targets: list[str] = []
    dashboard_jobs = [
        (
            "kr",
            "../lgbm_warning_dashboard_macro_us_latest/dashboard.html",
            "미국",
        ),
        (
            "us",
            "../lgbm_warning_dashboard_macro_kr_latest/dashboard.html",
            "한국",
        ),
    ]
    dashboard_targets = [dashboard["target"] for dashboard in DASHBOARDS.values()]
    for key, other_href, other_label in dashboard_jobs:
        dashboard = DASHBOARDS[key]
        source = dashboard["source"]
        if not has_date_files(source):
            print(f"Skipping {key} pages: no date JSON files found under {source}", flush=True)
            missing_targets.append(dashboard["target"])
            continue
        build_dashboard(
            source,
            deploy_dir / dashboard["target"],
            args.days,
            dashboard["label"],
            dashboard["subtitle"],
            other_href,
            other_label,
            args.raw_dir,
        )
        built_dashboards += 1
    preserve_existing_pages = args.preserve_existing_pages if args.preserve_existing_pages is not None else args.push
    restored_dashboards = 0
    if preserve_existing_pages:
        restored_dashboards = restore_existing_dashboards(deploy_dir, args.repo, dashboard_targets)
    if built_dashboards + restored_dashboards == 0:
        raise FileNotFoundError("No dashboard date JSON files found under outputs")
    publish_investment_entrypoints(deploy_dir)
    total = sum(path.stat().st_size for path in deploy_dir.rglob("*") if path.is_file())
    print(f"Built {deploy_dir} with {sum(1 for _ in deploy_dir.rglob('*') if _.is_file())} files, {total / 1024 / 1024:.1f} MB.")
    if args.push:
        push_pages(deploy_dir, args.repo)


if __name__ == "__main__":
    main()
