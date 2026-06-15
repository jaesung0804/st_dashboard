from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote


ROOT = Path("outputs")
DEPLOY_DIR = Path(".pages-deploy")
DASHBOARDS = {
    "kr": {
        "source": ROOT / "lgbm_warning_dashboard_macro_kr_latest",
        "target": "lgbm_warning_dashboard_macro_kr_latest",
        "label": "Korea",
        "subtitle": "KOSPI/KOSDAQ early-warning candidates",
    },
    "us": {
        "source": ROOT / "lgbm_warning_dashboard_macro_us_latest",
        "target": "lgbm_warning_dashboard_macro_us_latest",
        "label": "US",
        "subtitle": "NASDAQ/NYSE early-warning candidates",
    },
}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build the lightweight GitHub Pages deployment bundle.")
    p.add_argument("--deploy-dir", default=str(DEPLOY_DIR))
    p.add_argument("--days", type=int, default=22, help="Trading dates to publish, roughly one month by default.")
    p.add_argument("--push", action="store_true", help="Commit and force-push the deploy bundle to gh-pages.")
    p.add_argument("--repo", default="https://github.com/jaesung0804/st_dashboard.git")
    return p


def json_load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def json_dump(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def selected_date_files(source: Path, days: int) -> list[Path]:
    by_date = source / "walkforward_scores_by_date"
    files = sorted(by_date.glob("*.json"), key=lambda p: p.stem, reverse=True)
    if not files:
        raise FileNotFoundError(f"No date JSON files found in {by_date}")
    return files[:days]


def home_html() -> str:
    return """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stock Early-Warning Dashboard</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:Arial,"Malgun Gothic",sans-serif;background:#f6f8fb;color:#17202a}main{max-width:1040px;margin:0 auto;padding:28px 18px 42px}h1{font-size:26px;margin:0 0 8px}.sub{color:#64748b;line-height:1.5;margin-bottom:18px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.card{display:block;background:#fff;border:1px solid #d9e2ec;border-radius:8px;padding:18px;text-decoration:none;color:#17202a}.card:hover{border-color:#1d4ed8;box-shadow:0 8px 24px rgba(15,23,42,.08)}b{display:block;font-size:18px;margin-bottom:7px}span{display:block;color:#64748b;font-size:14px;line-height:1.45}.actions{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}.button{display:inline-flex;align-items:center;justify-content:center;background:#1d4ed8;color:#fff;border-radius:7px;padding:10px 13px;text-decoration:none;font-weight:800}.button.secondary{background:#e8eef8;color:#1e3a8a}@media(max-width:720px){main{padding:20px 12px 34px}h1{font-size:23px}.grid{grid-template-columns:1fr}.card{padding:15px}.button{width:100%}}
</style>
</head>
<body>
<main>
<h1>Stock Early-Warning Dashboard</h1>
<div class="sub">Public lightweight view with the latest month of signal dates. This page is informational research output, not investment advice.</div>
<div class="actions">
<a class="button" href="lgbm_warning_dashboard_macro_kr_latest/dashboard.html">Open Korea</a>
<a class="button" href="lgbm_warning_dashboard_macro_us_latest/dashboard.html">Open US</a>
<a class="button secondary" href="https://github.com/jaesung0804/st_dashboard/actions/workflows/daily-refresh.yml">Refresh Workflow</a>
</div>
<div class="grid">
<a class="card" href="lgbm_warning_dashboard_macro_kr_latest/dashboard.html"><b>Korea Dashboard</b><span>Recent KOSPI/KOSDAQ candidates by signal date</span></a>
<a class="card" href="lgbm_warning_dashboard_macro_us_latest/dashboard.html"><b>US Dashboard</b><span>Recent NASDAQ/NYSE candidates by signal date</span></a>
<a class="card" href="down_negative_model_comparison/index.html"><b>Downside Model Comparison</b><span>Compact comparison report for downside filters</span></a>
</div>
</main>
</body>
</html>
"""


def dashboard_html(label: str, subtitle: str, other_href: str, other_label: str) -> str:
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{label} Early-Warning Dashboard</title>
<style>
:root{{--ink:#17202a;--muted:#64748b;--line:#d9e2ec;--bg:#f6f8fb;--head:#101827;--blue:#1d4ed8;--red:#b91c1c;--green:#047857}}
*{{box-sizing:border-box}}body{{margin:0;font-family:Arial,"Malgun Gothic",sans-serif;background:var(--bg);color:var(--ink)}}header{{background:var(--head);color:white;padding:16px 18px}}header h1{{margin:0 0 5px;font-size:22px}}.sub{{color:#cbd5e1;font-size:13px;line-height:1.45}}nav{{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}}nav a{{color:#dbeafe;text-decoration:none;border:1px solid #334155;border-radius:6px;padding:7px 10px;font-size:13px}}main{{max-width:1280px;margin:0 auto;padding:14px}}.stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:12px}}.stat,.panel{{background:white;border:1px solid var(--line);border-radius:8px}}.stat{{padding:12px}}.stat small{{display:block;color:var(--muted);margin-bottom:4px}}.stat b{{font-size:20px}}.panel{{padding:12px;margin-bottom:12px}}.controls{{display:grid;grid-template-columns:170px repeat(4,minmax(120px,1fr));gap:9px;align-items:end}}label{{font-size:12px;color:#475569;display:grid;gap:4px}}select,input{{width:100%;padding:9px;border:1px solid #cbd5e1;border-radius:6px;background:white}}.tabs{{display:flex;gap:7px;flex-wrap:wrap;margin:12px 0}}button{{border:1px solid #bfdbfe;background:#eff6ff;color:#1e40af;padding:8px 10px;border-radius:6px;font-weight:800;cursor:pointer}}button.active{{background:var(--blue);color:white}}.meta{{font-size:13px;color:#475569;margin:8px 0}}table{{width:100%;border-collapse:separate;border-spacing:0;font-size:12px}}th,td{{border-bottom:1px solid var(--line);padding:9px 8px;text-align:right;vertical-align:top}}th{{background:#eef3f8;color:#334155;position:sticky;top:0}}td.left,th.left{{text-align:left}}.name{{font-weight:800;color:#1d4ed8;text-decoration:none}}.wrap{{white-space:normal;overflow-wrap:anywhere;line-height:1.35}}.badge{{display:inline-block;min-width:54px;text-align:center;border-radius:5px;padding:3px 6px;font-weight:800}}.score-grid{{display:grid;grid-template-columns:repeat(5,minmax(54px,1fr));gap:4px;min-width:300px;text-align:left}}.score{{border:1px solid #d9e2ec;border-radius:5px;padding:5px;background:#fafcff}}.score small{{display:block;color:#64748b;font-size:10px;margin-bottom:2px}}.score b{{font-size:12px}}.GREEN{{background:#dbeafe;color:#1d4ed8}}.RED{{background:#fee2e2;color:#b91c1c}}.YELLOW{{background:#fef9c3;color:#854d0e}}.ORANGE{{background:#ffedd5;color:#c2410c}}.pos{{color:var(--green);font-weight:800}}.neg{{color:var(--red);font-weight:800}}.pager{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:10px}}.scroll{{overflow:auto}}.empty{{padding:16px;color:var(--muted)}}@media(max-width:760px){{header{{padding:14px 12px}}header h1{{font-size:20px}}main{{padding:10px}}.stats{{grid-template-columns:1fr 1fr;gap:8px}}.stat{{padding:10px}}.stat b{{font-size:18px}}.controls{{grid-template-columns:1fr}}.panel{{padding:10px;border-radius:7px}}table,thead,tbody,tr,th,td{{display:block}}thead{{display:none}}tbody{{display:grid;gap:10px}}tr{{background:white;border:1px solid var(--line);border-radius:8px;padding:10px}}td{{border:0;display:grid;grid-template-columns:92px 1fr;gap:8px;text-align:left;padding:5px 0;white-space:normal}}td::before{{content:attr(data-label);color:#64748b;font-size:11px;font-weight:800;text-transform:uppercase}}td.left{{text-align:left}}.badge{{min-width:0}}.score-grid{{min-width:0;grid-template-columns:1fr 1fr}}.pager button{{flex:1}}}}
</style>
</head>
<body>
<header>
<h1>{label} Early-Warning Dashboard</h1>
<div class="sub">{subtitle}. Showing the latest published month of signal dates.</div>
<nav><a href="../index.html">Home</a><a href="dashboard.html">{label}</a><a href="{other_href}">{other_label}</a><a href="../down_negative_model_comparison/index.html">Model Compare</a><a href="https://github.com/jaesung0804/st_dashboard/actions/workflows/daily-refresh.yml">Refresh Workflow</a></nav>
</header>
<main>
<div class="stats"><div class="stat"><small>Latest Date</small><b id="latest">-</b></div><div class="stat"><small>Final Candidates</small><b id="finalCount">-</b></div><div class="stat"><small>Up Candidates</small><b id="upCount">-</b></div><div class="stat"><small>Rows</small><b id="rowCount">-</b></div></div>
<section class="panel">
<div class="controls"><label>Signal Date<select id="date"></select></label><label>Mode<select id="mode"><option value="final">Final</option><option value="up">Up candidates</option><option value="down">Downside RED</option><option value="all">All rows</option></select></label><label>Search<input id="query" placeholder="Ticker or name"></label><label>Sort<select id="sort"><option value="upScore">Up score</option><option value="downRisk">Down risk</option><option value="growth_profit">Growth/Profit</option><option value="cash_quality">Cash Flow</option><option value="valuation">Valuation</option><option value="price_volume">Price/Volume</option><option value="risk_overheat">Risk/Overheat</option><option value="ticker">Ticker</option><option value="name">Name</option><option value="close">Close</option></select></label><label>Rows per page<input id="pageSize" type="number" min="10" max="200" value="50"></label></div>
<div class="tabs"><button id="dir" type="button">Descending</button></div>
<div class="meta" id="meta"></div>
<div class="scroll" id="table"></div>
<div class="pager"><button id="prev" type="button">Prev</button><span id="pageInfo"></span><button id="next" type="button">Next</button></div>
</section>
</main>
<script>
let manifest={{}}, rows=[], filtered=[], page=1, asc=false;
const $=id=>document.getElementById(id);
const labels=['#','Ticker','Name','Sector','Detail','Close','Up','Up Grade','Down','Down Grade','Representative Scores','Expected 6M','Actual 6M'];
const scoreLabels=[['growth_profit','Growth'],['cash_quality','Cash'],['valuation','Value'],['price_volume','Price'],['risk_overheat','Risk']];
function esc(v){{return String(v??'').replace(/[&<>"']/g,ch=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));}}
function num(v){{const n=parseFloat(String(v??'').replace(/,/g,'').replace('%',''));return Number.isNaN(n)?NaN:n;}}
function ret(v){{const n=num(v);const cls=Number.isNaN(n)?'':n<0?'neg':'pos';return `<span class="${{cls}}">${{esc(v||'-')}}</span>`;}}
function scoreBlock(r){{return '<div class="score-grid">'+scoreLabels.map(([key,label])=>`<div class="score"><small>${{label}}</small><b>${{esc(r[key]??'-')}}</b></div>`).join('')+'</div>';}}
function compare(a,b,k){{const an=num(a[k]),bn=num(b[k]);if(!Number.isNaN(an)&&!Number.isNaN(bn))return an-bn;return String(a[k]??'').localeCompare(String(b[k]??''));}}
function selectedRows(){{const mode=$('mode').value,q=$('query').value.trim().toLowerCase();let out=rows.filter(r=>mode==='final'?r.isFinalCandidate:mode==='up'?r.isUpCandidate:mode==='down'?r.isDownRed:true);if(q)out=out.filter(r=>String(r.ticker).toLowerCase().includes(q)||String(r.name).toLowerCase().includes(q));const key=$('sort').value;out.sort((a,b)=>(asc?1:-1)*compare(a,b,key));return out;}}
async function loadDate(){{const date=$('date').value;$('table').innerHTML='<div class="empty">Loading...</div>';const res=await fetch(`walkforward_scores_by_date/${{date}}.json`);rows=await res.json();page=1;render();}}
function render(){{filtered=selectedRows();const size=Math.max(10,Math.min(200,parseInt($('pageSize').value||50,10)));const pages=Math.max(1,Math.ceil(filtered.length/size));page=Math.max(1,Math.min(page,pages));const start=(page-1)*size, shown=filtered.slice(start,start+size);$('meta').textContent=`${{filtered.length.toLocaleString()}} rows / date ${{$('date').value}}`;$('pageInfo').textContent=`${{page}} / ${{pages}}`; $('prev').disabled=page<=1;$('next').disabled=page>=pages;$('dir').textContent=asc?'Ascending':'Descending';if(!shown.length){{$('table').innerHTML='<div class="empty">No rows for this filter.</div>';return;}}$('table').innerHTML='<table><thead><tr>'+labels.map((x,i)=>`<th class="${{i>=1&&i<=4?'left':''}}">${{x}}</th>`).join('')+'</tr></thead><tbody>'+shown.map((r,i)=>{{const cells=[start+i+1,esc(r.ticker),`<a class="name" href="stock.html?ticker=${{encodeURIComponent(r.ticker)}}">${{esc(r.name||r.ticker)}}</a>`,esc(r.sector),esc(r.detailSector),esc(r.close),esc(r.upScore),`<span class="badge ${{esc(r.upGrade)}}">${{esc(r.upGrade)}}</span>`,esc(r.downRisk),`<span class="badge ${{esc(r.downGrade)}}">${{esc(r.downGrade)}}</span>`,scoreBlock(r),ret(r.expRet_6m),ret(r.actRet_6m)];return '<tr>'+cells.map((c,idx)=>`<td data-label="${{labels[idx]}}" class="${{idx>=1&&idx<=4?'left wrap':''}}">${{c}}</td>`).join('')+'</tr>';}}).join('')+'</tbody></table>';}}
async function init(){{manifest=await fetch('manifest.json').then(r=>r.json());$('latest').textContent=manifest.latest||'-';$('finalCount').textContent=Number(manifest.latestFinal||0).toLocaleString();$('upCount').textContent=Number(manifest.latestUp||0).toLocaleString();$('rowCount').textContent=Number(manifest.latestRows||0).toLocaleString();(manifest.dates||[]).forEach(d=>$('date').add(new Option(d,d)));['date','mode','query','sort','pageSize'].forEach(id=>$(id).addEventListener(id==='query'?'input':'change',()=>{{page=1;id==='date'?loadDate():render();}}));$('dir').onclick=()=>{{asc=!asc;render();}};$('prev').onclick=()=>{{page--;render();}};$('next').onclick=()=>{{page++;render();}};await loadDate();}}
init().catch(err=>{{$('table').innerHTML=`<div class="empty">${{esc(err.message)}}</div>`;}});
</script>
</body>
</html>
"""


def stock_html(label: str, other_href: str, other_label: str) -> str:
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{label} Stock Detail</title>
<style>*{{box-sizing:border-box}}body{{margin:0;font-family:Arial,"Malgun Gothic",sans-serif;background:#f6f8fb;color:#17202a}}header{{background:#101827;color:white;padding:16px 18px}}nav{{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}}nav a{{color:#dbeafe;text-decoration:none;border:1px solid #334155;border-radius:6px;padding:7px 10px;font-size:13px}}main{{max-width:1100px;margin:0 auto;padding:14px}}.panel{{background:white;border:1px solid #d9e2ec;border-radius:8px;padding:12px;overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{border-bottom:1px solid #d9e2ec;padding:8px;text-align:right}}th{{background:#eef3f8}}th:first-child,td:first-child{{text-align:left}}.scores{{display:grid;grid-template-columns:repeat(5,minmax(48px,1fr));gap:4px;min-width:270px;text-align:left}}.score{{border:1px solid #d9e2ec;border-radius:5px;padding:5px;background:#fafcff}}.score small{{display:block;color:#64748b;font-size:10px}}.score b{{font-size:12px}}.pos{{color:#047857;font-weight:800}}.neg{{color:#b91c1c;font-weight:800}}@media(max-width:720px){{main{{padding:10px}}table,thead,tbody,tr,th,td{{display:block}}thead{{display:none}}tr{{border:1px solid #d9e2ec;border-radius:8px;margin-bottom:9px;padding:8px}}td{{border:0;display:grid;grid-template-columns:92px 1fr;text-align:left;padding:5px}}td::before{{content:attr(data-label);font-size:11px;color:#64748b;font-weight:800;text-transform:uppercase}}.scores{{min-width:0;grid-template-columns:1fr 1fr}}}}</style></head>
<body><header><h1 id="title">{label} Stock Detail</h1><nav><a href="../index.html">Home</a><a href="dashboard.html">{label}</a><a href="{other_href}">{other_label}</a></nav></header><main><section class="panel" id="content">Loading...</section></main>
<script>
const params=new URLSearchParams(location.search),ticker=params.get('ticker')||'';const labels=['Date','Close','Up','Up Grade','Down','Down Grade','Final','Representative Scores','Expected 6M'];
const scoreLabels=[['growth_profit','Growth'],['cash_quality','Cash'],['valuation','Value'],['price_volume','Price'],['risk_overheat','Risk']];
function esc(v){{return String(v??'').replace(/[&<>"']/g,ch=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));}}
function cls(v){{const n=parseFloat(String(v??'').replace('%',''));return Number.isNaN(n)?'':n<0?'neg':'pos';}}
function scoreBlock(r){{return '<div class="scores">'+scoreLabels.map(([key,label])=>`<div class="score"><small>${{label}}</small><b>${{esc(r[key]??'-')}}</b></div>`).join('')+'</div>';}}
async function init(){{if(!ticker)throw new Error('Ticker is missing.');const data=await fetch(`stock_history/${{encodeURIComponent(ticker)}}.json`).then(r=>{{if(!r.ok)throw new Error(`${{r.status}} ${{r.statusText}}`);return r.json();}});document.getElementById('title').textContent=`{label} Stock Detail: ${{data.ticker}} ${{data.name||''}}`;const rows=(data.rows||[]).slice().reverse();document.getElementById('content').innerHTML='<table><thead><tr>'+labels.map(x=>`<th>${{x}}</th>`).join('')+'</tr></thead><tbody>'+rows.map(r=>{{const cells=[r.date,r.close,r.upScore,r.upGrade,r.downRisk,r.downGrade,r.isFinalCandidate?'Yes':'No',scoreBlock(r),`<span class="${{cls(r.expRet_6m)}}">${{esc(r.expRet_6m||'-')}}</span>`];return '<tr>'+cells.map((c,i)=>`<td data-label="${{labels[i]}}">${{c}}</td>`).join('')+'</tr>';}}).join('')+'</tbody></table>';}}
init().catch(err=>{{document.getElementById('content').textContent=err.message;}});
</script></body></html>"""


def build_dashboard(source: Path, target: Path, days: int, label: str, subtitle: str, other_href: str, other_label: str) -> None:
    date_files = selected_date_files(source, days)
    dates = [path.stem for path in date_files]
    by_date_target = target / "walkforward_scores_by_date"
    by_date_target.mkdir(parents=True, exist_ok=True)
    latest_rows: list[dict] = []
    history: dict[str, list[dict]] = defaultdict(list)
    sectors: set[str] = set()
    detail_map: dict[str, set[str]] = defaultdict(set)
    stock_index: dict[str, dict[str, str]] = {}

    for src_file in date_files:
        rows = json_load(src_file)
        if not isinstance(rows, list):
            rows = []
        shutil.copy2(src_file, by_date_target / src_file.name)
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
                }

    target.mkdir(parents=True, exist_ok=True)
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
                "growth_profit": "Growth/Profit",
                "cash_quality": "Cash Flow",
                "valuation": "Valuation",
                "price_volume": "Price/Volume",
                "risk_overheat": "Risk/Overheat",
            },
            "validation": [],
            "marketName": label,
        },
    )
    json_dump(target / "stock_index.json", sorted(stock_index.values(), key=lambda row: row["ticker"]))
    for ticker, rows in history.items():
        rows.sort(key=lambda row: str(row.get("date", "")))
        json_dump(
            target / "stock_history" / f"{quote(ticker, safe='')}.json",
            {
                "ticker": ticker,
                "name": stock_index[ticker]["name"],
                "sector": stock_index[ticker]["sector"],
                "detailSector": stock_index[ticker]["detailSector"],
                "rows": rows,
            },
        )
    (target / "dashboard.html").write_text(dashboard_html(label, subtitle, other_href, other_label), encoding="utf-8")
    (target / "index.html").write_text(dashboard_html(label, subtitle, other_href, other_label), encoding="utf-8")
    (target / "stock.html").write_text(stock_html(label, other_href, other_label), encoding="utf-8")


def copy_comparison(deploy_dir: Path) -> None:
    source = ROOT / "down_negative_model_comparison"
    if source.exists():
        shutil.copytree(source, deploy_dir / "down_negative_model_comparison")


def run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True)


def remove_tree(path: Path) -> None:
    def onexc(func, target, exc_info):  # noqa: ANN001
        os.chmod(target, 0o700)
        func(target)

    shutil.rmtree(path, onexc=onexc)


def push_pages(deploy_dir: Path, repo: str) -> None:
    run_git(["init"], deploy_dir)
    run_git(["checkout", "-B", "gh-pages"], deploy_dir)
    run_git(["add", "-A"], deploy_dir)
    run_git(["commit", "-m", "Deploy dashboard to GitHub Pages"], deploy_dir)
    try:
        run_git(["remote", "add", "origin", repo], deploy_dir)
    except subprocess.CalledProcessError:
        run_git(["remote", "set-url", "origin", repo], deploy_dir)
    run_git(["push", "-f", "origin", "gh-pages"], deploy_dir)


def main() -> None:
    args = parser().parse_args()
    deploy_dir = Path(args.deploy_dir)
    if deploy_dir.exists():
        remove_tree(deploy_dir)
    deploy_dir.mkdir(parents=True)
    (deploy_dir / ".nojekyll").write_text("", encoding="utf-8")
    (deploy_dir / "index.html").write_text(home_html(), encoding="utf-8")
    copy_comparison(deploy_dir)

    build_dashboard(
        DASHBOARDS["kr"]["source"],
        deploy_dir / DASHBOARDS["kr"]["target"],
        args.days,
        DASHBOARDS["kr"]["label"],
        DASHBOARDS["kr"]["subtitle"],
        "../lgbm_warning_dashboard_macro_us_latest/dashboard.html",
        "US",
    )
    build_dashboard(
        DASHBOARDS["us"]["source"],
        deploy_dir / DASHBOARDS["us"]["target"],
        args.days,
        DASHBOARDS["us"]["label"],
        DASHBOARDS["us"]["subtitle"],
        "../lgbm_warning_dashboard_macro_kr_latest/dashboard.html",
        "Korea",
    )
    total = sum(path.stat().st_size for path in deploy_dir.rglob("*") if path.is_file())
    print(f"Built {deploy_dir} with {sum(1 for _ in deploy_dir.rglob('*') if _.is_file())} files, {total / 1024 / 1024:.1f} MB.")
    if args.push:
        push_pages(deploy_dir, args.repo)


if __name__ == "__main__":
    main()
