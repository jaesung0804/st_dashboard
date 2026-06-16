from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
from typing import Any
from urllib.parse import quote

import pandas as pd


WF_DIR = Path("outputs/walkforward_warning")
OUT_DIR = Path("outputs/lgbm_warning_dashboard")
ALL = "전체"
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
SUBSCORES = {
    "growth_profit": "성장/수익",
    "cash_quality": "현금흐름",
    "valuation": "밸류",
    "price_volume": "가격/거래",
    "risk_overheat": "위험/과열",
}
KEEP_COLUMNS = [
    "date",
    "ticker",
    "name",
    "sector",
    "detailSector",
    "theme",
    "exchange",
    "close",
    "closeRaw",
    "currency",
    "usdKrw",
    "closeKrw",
    "upScore",
    "up_lgbm_prob",
    "upGrade",
    "downRisk",
    "down_lgbm_prob",
    "downGrade",
    "isUpCandidate",
    "isDownRed",
    "isFinalCandidate",
    "expRet_1m",
    "expClose_1m",
    "expCloseKrw_1m",
    "actRet_1m",
    "actClose_1m",
    "expRet_3m",
    "expClose_3m",
    "expCloseKrw_3m",
    "actRet_3m",
    "actClose_3m",
    "expRet_6m",
    "expClose_6m",
    "expCloseKrw_6m",
    "actRet_6m",
    "actClose_6m",
    "expRet_12m",
    "expClose_12m",
    "expCloseKrw_12m",
    "actRet_12m",
    "actClose_12m",
    *SUBSCORES.keys(),
]
HISTORY_COLUMNS = [
    "date",
    "ticker",
    "name",
    "sector",
    "detailSector",
    "exchange",
    "close",
    "closeRaw",
    "currency",
    "usdKrw",
    "closeKrw",
    "upScore",
    "upGrade",
    "downRisk",
    "downGrade",
    "isUpCandidate",
    "isDownRed",
    "isFinalCandidate",
    "expRet_1m",
    "expRet_3m",
    "expRet_6m",
    "expRet_12m",
    "expClose_1m",
    "expClose_3m",
    "expClose_6m",
    "expClose_12m",
    "expCloseKrw_1m",
    "expCloseKrw_3m",
    "expCloseKrw_6m",
    "expCloseKrw_12m",
    *SUBSCORES.keys(),
]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build a lightweight walk-forward dashboard.")
    p.add_argument("--wf-dir", default=str(WF_DIR))
    p.add_argument("--out-dir", default=str(OUT_DIR))
    p.add_argument("--html-name", default="dashboard.html")
    p.add_argument("--market-name", default="한국", help="Dashboard display name, e.g. 한국 or 미국.")
    p.add_argument("--listings-path", default=None, help="Optional listings CSV used to refresh name/sector tags.")
    p.add_argument("--home-href", default="index.html", help="Relative link to the dashboard home page.")
    p.add_argument("--kr-dashboard-href", default="dashboard.html")
    p.add_argument("--us-dashboard-href", default="../lgbm_warning_dashboard_macro_us_latest/dashboard.html")
    p.add_argument("--recent-days", type=int, default=0, help="Only write the most recent N signal dates. 0 keeps every date.")
    p.add_argument("--copy-csv", action="store_true", help="Also copy compact CSV exports into the dashboard folder.")
    return p


def json_dump(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def safe_stock_filename(ticker: str) -> str:
    name = quote(str(ticker), safe="")
    stem = name.rsplit(".", 1)[0].upper()
    if stem in WINDOWS_RESERVED_NAMES:
        name = f"{name}_"
    return f"{name}.json"


def load_scores(wf_dir: Path) -> pd.DataFrame:
    source = wf_dir / "walkforward_scores.csv"
    if not source.exists():
        source = wf_dir / "walkforward_candidates.csv"
    data = pd.read_csv(source, dtype={"ticker": str}, low_memory=False)
    for col in KEEP_COLUMNS:
        if col not in data:
            data[col] = ""
    data = data[KEEP_COLUMNS].rename(columns={"up_lgbm_prob": "upProb", "down_lgbm_prob": "downProb"})
    for col in ["isUpCandidate", "isDownRed", "isFinalCandidate"]:
        data[col] = data[col].astype(str).str.lower().isin(["true", "1", "yes"])
    return data.fillna("")


def apply_listing_tags(scores: pd.DataFrame, listings_path: Path | None) -> pd.DataFrame:
    if listings_path is None or not listings_path.exists():
        return scores
    listings = pd.read_csv(listings_path, dtype={"ticker": str})
    listings["ticker"] = listings["ticker"].astype(str).str.strip()
    tag_cols = ["ticker"]
    for col in ["name", "sector", "industry", "representative_industry", "exchange"]:
        if col in listings:
            tag_cols.append(col)
    tags = listings[tag_cols].drop_duplicates("ticker", keep="first")
    merged = scores.merge(tags, on="ticker", how="left", suffixes=("", "_listing"))

    def usable(series: pd.Series) -> pd.Series:
        return series.notna() & series.astype(str).str.strip().ne("")

    if "name_listing" in merged:
        merged["name"] = merged["name_listing"].where(usable(merged["name_listing"]), merged["name"])
    if "sector_listing" in merged:
        merged["sector"] = merged["sector_listing"].where(usable(merged["sector_listing"]), merged["sector"])
    if "representative_industry" in merged:
        merged["detailSector"] = merged["representative_industry"].where(usable(merged["representative_industry"]), merged["detailSector"])
    elif "industry" in merged:
        merged["detailSector"] = merged["industry"].where(usable(merged["industry"]), merged["detailSector"])
    if "industry" in merged:
        merged["theme"] = merged["industry"].where(usable(merged["industry"]), merged["theme"])
    if "exchange_listing" in merged:
        merged["exchange"] = merged["exchange_listing"].where(usable(merged["exchange_listing"]), merged.get("exchange", ""))
    drop_cols = [c for c in merged.columns if c.endswith("_listing") or c in {"representative_industry", "industry"}]
    return merged.drop(columns=drop_cols, errors="ignore").fillna("")


def format_auc(value: object) -> str:
    return "" if pd.isna(value) else f"{float(value):.3f}"


def validation_records(wf_dir: Path) -> list[dict[str, object]]:
    path = wf_dir / "walkforward_validation.csv"
    if not path.exists():
        return []
    validation = pd.read_csv(path)
    for col in ["up_auc", "down_auc"]:
        if col in validation:
            validation[col] = validation[col].map(format_auc)
    return validation.fillna("").to_dict("records")


def selected_dates(scores: pd.DataFrame, recent_days: int) -> list[str]:
    dates = sorted(scores["date"].astype(str).unique(), reverse=True)
    return dates[:recent_days] if recent_days and recent_days > 0 else dates


def write_date_files(scores: pd.DataFrame, out_dir: Path, dates: list[str]) -> None:
    by_date_dir = out_dir / "walkforward_scores_by_date"
    if by_date_dir.exists():
        shutil.rmtree(by_date_dir)
    by_date_dir.mkdir(parents=True, exist_ok=True)
    selected = set(dates)
    for date, part in scores.groupby(scores["date"].astype(str), sort=False):
        safe_date = str(date)
        if safe_date in selected:
            json_dump(by_date_dir / f"{safe_date}.json", part.to_dict("records"))


def write_stock_history(scores: pd.DataFrame, out_dir: Path, dates: list[str]) -> None:
    history_dir = out_dir / "stock_history"
    if history_dir.exists():
        shutil.rmtree(history_dir)
    history_dir.mkdir(parents=True, exist_ok=True)

    selected = scores[scores["date"].astype(str).isin(set(dates))].copy()
    for col in ["upScore", "downRisk"]:
        selected[col] = pd.to_numeric(selected[col], errors="coerce")
    sector_avg = (
        selected.groupby(["date", "sector"], dropna=False)[["upScore", "downRisk"]]
        .mean()
        .rename(columns={"upScore": "sectorUpScore", "downRisk": "sectorDownRisk"})
        .reset_index()
    )
    history = selected[HISTORY_COLUMNS].merge(sector_avg, on=["date", "sector"], how="left")
    history["sectorUpScore"] = history["sectorUpScore"].round(1)
    history["sectorDownRisk"] = history["sectorDownRisk"].round(1)
    history = history.sort_values(["ticker", "date"])
    index_rows: list[dict[str, str]] = []
    for ticker, part in history.groupby("ticker", sort=False):
        latest = part.iloc[-1]
        index_rows.append(
            {
                "ticker": str(ticker),
                "name": str(latest.get("name", "")),
                "sector": str(latest.get("sector", "")),
                "detailSector": str(latest.get("detailSector", "")),
                "exchange": str(latest.get("exchange", "")),
            }
        )
        payload = {
            "ticker": str(ticker),
            "name": latest.get("name", ""),
            "sector": latest.get("sector", ""),
            "detailSector": latest.get("detailSector", ""),
            "exchange": latest.get("exchange", ""),
            "rows": part.fillna("").to_dict("records"),
        }
        json_dump(history_dir / safe_stock_filename(str(ticker)), payload)
    json_dump(out_dir / "stock_index.json", sorted(index_rows, key=lambda row: row["ticker"]))


def write_csv_exports(scores: pd.DataFrame, out_dir: Path) -> None:
    scores.to_csv(out_dir / "walkforward_scores.csv", index=False, encoding="utf-8-sig")
    scores[scores["isFinalCandidate"].astype(bool)].to_csv(out_dir / "walkforward_candidates.csv", index=False, encoding="utf-8-sig")


def build_manifest(scores: pd.DataFrame, dates: list[str], wf_dir: Path, market_name: str) -> dict[str, object]:
    sectors = sorted(x for x in scores["sector"].astype(str).unique() if x)
    detail_map = {
        str(sector): sorted(x for x in part["detailSector"].astype(str).unique() if x)
        for sector, part in scores.groupby("sector", dropna=False)
    }
    latest = dates[0] if dates else ""
    latest_rows = scores[scores["date"].astype(str).eq(latest)] if latest else scores.iloc[0:0]
    return {
        "dates": dates,
        "sectors": sectors,
        "detailMap": detail_map,
        "latest": latest,
        "latestFinal": int(latest_rows["isFinalCandidate"].sum()) if latest else 0,
        "latestUp": int(latest_rows["isUpCandidate"].sum()) if latest else 0,
        "latestRows": int(len(latest_rows)) if latest else 0,
        "dateCount": len(dates),
        "subLabels": SUBSCORES,
        "validation": validation_records(wf_dir),
        "marketName": market_name,
    }


def common_css() -> str:
    return """
:root{--ink:#17202a;--muted:#64748b;--line:#d9e2ec;--bg:#f4f7fb;--head:#0f172a;--blue:#1d4ed8;--red:#b91c1c}
*{box-sizing:border-box}body{font-family:Arial,"Malgun Gothic",sans-serif;margin:0;background:var(--bg);color:var(--ink)}header{background:var(--head);color:white;padding:18px 30px}h1{margin:0 0 6px;font-size:25px}.sub{color:#cbd5e1;font-size:14px}.topnav{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.topnav a{color:#dbeafe;border:1px solid #334155;border-radius:4px;padding:7px 10px;text-decoration:none;font-size:13px}.topnav a:hover{background:#1e293b}main{padding:20px 30px;max-width:1900px;margin:0 auto}.cards{display:grid;grid-template-columns:repeat(4,minmax(160px,1fr));gap:10px;margin-bottom:14px}.card,section{background:white;border:1px solid var(--line);border-radius:6px}.card{padding:13px}.label{font-size:12px;color:var(--muted);margin-bottom:5px}.value{font-size:21px;font-weight:800}section{padding:14px;margin-bottom:16px;overflow:auto}.tabs,.pager{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:12px}.tab,.pager button{border:1px solid #bfdbfe;background:#eff6ff;color:#1e40af;padding:8px 11px;border-radius:4px;font-weight:800;cursor:pointer}.tab.active{background:#1d4ed8;color:white}.pager button:disabled{opacity:.45;cursor:not-allowed}.toolbar{display:grid;grid-template-columns:repeat(8,max-content);gap:9px;align-items:end;margin:10px 0 14px}label{font-size:12px;color:#475569;display:grid;gap:4px}select,input{padding:8px 9px;border:1px solid #cbd5e1;border-radius:4px;background:white}.sort-note,.page-info{font-size:12px;color:#475569}.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(80px,1fr));gap:6px}.sub-grid{display:grid;grid-template-columns:repeat(5,minmax(76px,1fr));gap:6px}.metric{border:1px solid var(--line);border-radius:5px;background:#fafcff;padding:6px}.metric b{display:block;font-size:11px;color:#64748b}.metric span{display:block;font-weight:800}.heat{border-radius:4px;padding:3px 5px;display:inline-block;min-width:34px;text-align:center}table{border-collapse:separate;border-spacing:0;width:100%;font-size:12px;table-layout:auto}th{background:#eef3f8;position:sticky;top:0;z-index:1;color:#334155;font-weight:800}th,td{border-bottom:1px solid var(--line);padding:8px 9px;white-space:nowrap;text-align:right;vertical-align:top}.left{text-align:left}.wrap{white-space:normal;overflow-wrap:anywhere;word-break:keep-all;line-height:1.35;min-width:90px;max-width:220px}.name-col{min-width:130px;max-width:260px}.stock-link{font-weight:800;color:#1d4ed8;text-decoration:none}.stock-link:hover{text-decoration:underline}.badge{display:inline-block;min-width:58px;text-align:center;border-radius:4px;padding:3px 6px;font-weight:800;font-size:12px}.GREEN{background:#dbeafe;color:#1d4ed8}.RED{background:#fee2e2;color:#b91c1c}.YELLOW{background:#fef9c3;color:#854d0e}.ORANGE{background:#ffedd5;color:#c2410c}.pos{color:#047857;font-weight:700}.neg{color:#b91c1c;font-weight:700}.pending{color:#64748b}.note{line-height:1.55;color:#475569;font-size:14px}.error{color:#b91c1c;font-weight:700}.chart{width:100%;height:320px;border:1px solid var(--line);border-radius:6px;background:#fff}.legend{display:flex;gap:10px 14px;flex-wrap:wrap;font-size:12px;color:#475569;margin:8px 0 12px}.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px}.triple-grid{display:grid;grid-template-columns:repeat(3,minmax(260px,1fr));gap:14px;align-items:start}.triple-grid h3{margin:6px 0 4px;font-size:16px}@media(max-width:1100px){.triple-grid{grid-template-columns:1fr}}@media(max-width:950px){main{padding:12px}.cards{grid-template-columns:1fr 1fr}.toolbar{grid-template-columns:1fr}.metric-grid,.sub-grid{grid-template-columns:1fr 1fr}}
"""


def dashboard_html(market_name: str, home_href: str) -> str:
    script = """
let ROWS = [];
let MANIFEST = {};
let mode = 'final';
let sortAsc = false;
let page = 1;
let filteredRows = [];
const cache = new Map();
const $ = id => document.getElementById(id);
const dateSel = $('date'), sectorSel = $('sector'), detailSel = $('detail'), horizonSel = $('horizon');
const pageSize = $('pageSize'), query = $('query'), sortSel = $('sort'), sortNote = $('sortNote'), dirBtn = $('dir');
const table = $('table'), validation = $('validation'), pageInfo = $('pageInfo'), prevBtn = $('prevPage'), nextBtn = $('nextPage');
function esc(value){return String(value ?? '').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));}
function num(value){const n=parseFloat(String(value??'').replace(/,/g,'').replace('%',''));return Number.isNaN(n)?NaN:n;}
function cls(value){const n=num(value);return Number.isNaN(n)?'pending':(n<0?'neg':'pos');}
function cmp(a,b,key){const an=num(a[key]),bn=num(b[key]);if(!Number.isNaN(an)&&!Number.isNaN(bn))return an-bn;return String(a[key]??'').localeCompare(String(b[key]??''),'ko');}
function heat(value,key){const raw=num(value),good=key==='risk_overheat'?100-raw:raw;const n=Math.max(0,Math.min(100,Number.isNaN(good)?50:good));if(n>=75)return 'background:#ecfdf5;color:#065f46;border-color:#bbf7d0';if(n>=55)return 'background:#f0fdfa;color:#0f766e;border-color:#99f6e4';if(n>=35)return 'background:#fffbeb;color:#92400e;border-color:#fde68a';return 'background:#fff1f2;color:#9f1239;border-color:#fecdd3';}
function metric(r,h){return `<div class="metric-grid"><div class="metric"><b>예상수익</b><span class="${cls(r['expRet_'+h])}">${esc(r['expRet_'+h])}</span></div><div class="metric"><b>예상종가</b><span>${esc(r['expClose_'+h])}</span></div><div class="metric"><b>실제수익</b><span class="${cls(r['actRet_'+h])}">${esc(r['actRet_'+h])}</span></div><div class="metric"><b>실제종가</b><span>${esc(r['actClose_'+h])}</span></div></div>`;}
function subs(r){return '<div class="sub-grid">'+Object.entries(MANIFEST.subLabels).map(([k,label])=>`<div class="metric"><b>${esc(label)}</b><span class="heat" style="${heat(r[k],k)}">${esc(r[k])}</span></div>`).join('')+'</div>';}
function modeRows(rows){if(mode==='final')return rows.filter(r=>r.isFinalCandidate);if(mode==='up')return rows.filter(r=>r.isUpCandidate);if(mode==='down')return rows.filter(r=>r.isDownRed);return rows;}
function resetPage(){page=1;}
function refreshDetails(){const sector=sectorSel.value;const details=sector==='전체'?[...new Set(Object.values(MANIFEST.detailMap).flat())].sort():(MANIFEST.detailMap[sector]||[]);detailSel.innerHTML='';['전체',...details].forEach(value=>detailSel.add(new Option(value,value)));}
function computeRows(){const sector=sectorSel.value,detail=detailSel.value,q=query.value.trim().toLowerCase();let rows=ROWS.filter(r=>(sector==='전체'||r.sector===sector)&&(detail==='전체'||r.detailSector===detail));rows=modeRows(rows);if(q)rows=rows.filter(r=>String(r.name).toLowerCase().includes(q)||String(r.ticker).toLowerCase().includes(q));const sortKey=sortSel.value;rows.sort((a,b)=>(sortAsc?1:-1)*cmp(a,b,sortKey));filteredRows=rows;}
function render(){computeRows();const size=Math.max(1,Math.min(500,parseInt(pageSize.value||50,10)));const totalPages=Math.max(1,Math.ceil(filteredRows.length/size));page=Math.max(1,Math.min(page,totalPages));const start=(page-1)*size;const rows=filteredRows.slice(start,start+size);const h=horizonSel.value;const arrow=sortAsc?'오름차순':'내림차순';const from=filteredRows.length?start+1:0;const to=Math.min(start+size,filteredRows.length);dirBtn.textContent=arrow;sortNote.textContent=`정렬: ${sortSel.options[sortSel.selectedIndex].text} ${arrow} / 전체 ${filteredRows.length.toLocaleString()}개`;pageInfo.textContent=`${page} / ${totalPages} 페이지 (${from}-${to}개 표시)`;prevBtn.disabled=page<=1;nextBtn.disabled=page>=totalPages;table.innerHTML=`<table><thead><tr><th>순위</th><th class="left">티커</th><th class="left wrap name-col">종목명</th><th class="left wrap">섹터</th><th class="left wrap">대표분야</th><th>종가</th><th>상승점수</th><th>상승등급</th><th>하락위험</th><th>하락등급</th><th>선택기간</th><th>하위점수</th></tr></thead><tbody>`+rows.map((r,i)=>`<tr><td>${start+i+1}</td><td class="left"><a class="stock-link" href="stock.html?ticker=${encodeURIComponent(r.ticker)}">${esc(r.ticker)}</a></td><td class="left wrap name-col"><a class="stock-link" href="stock.html?ticker=${encodeURIComponent(r.ticker)}">${esc(r.name)}</a></td><td class="left wrap">${esc(r.sector)}</td><td class="left wrap">${esc(r.detailSector)}</td><td>${esc(r.close)}</td><td>${esc(r.upScore)}</td><td><span class="badge ${esc(r.upGrade)}">${esc(r.upGrade)}</span></td><td>${esc(r.downRisk)}</td><td><span class="badge ${esc(r.downGrade)}">${esc(r.downGrade)}</span></td><td>${metric(r,h)}</td><td>${subs(r)}</td></tr>`).join('')+'</tbody></table>';}
async function loadDate(){table.innerHTML='<div class="note">선택한 신호일 데이터를 불러오는 중...</div>';try{const date=dateSel.value;if(!cache.has(date)){const res=await fetch(`walkforward_scores_by_date/${date}.json`);if(!res.ok)throw new Error(`${res.status} ${res.statusText}`);cache.set(date,await res.json());}ROWS=cache.get(date);resetPage();render();}catch(err){table.innerHTML=`<div class="note error">${esc(err.message)}</div>`;}}
function renderValidation(){const rows=MANIFEST.validation||[];if(!rows.length){validation.innerHTML='<div class="note">검증 파일이 없습니다.</div>';return;}const cols=Object.keys(rows[0]);validation.innerHTML='<table><thead><tr>'+cols.map(c=>`<th>${esc(c)}</th>`).join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+cols.map(c=>`<td>${esc(r[c])}</td>`).join('')+'</tr>').join('')+'</tbody></table>';}
async function init(){MANIFEST=await fetch('manifest.json').then(r=>r.json());$('latest').textContent=MANIFEST.latest||'-';$('latestFinal').textContent=Number(MANIFEST.latestFinal||0).toLocaleString();$('latestUp').textContent=Number(MANIFEST.latestUp||0).toLocaleString();$('latestRows').textContent=Number(MANIFEST.latestRows||0).toLocaleString();(MANIFEST.dates||[]).forEach(d=>dateSel.add(new Option(d,d)));['전체',...(MANIFEST.sectors||[])].forEach(s=>sectorSel.add(new Option(s,s)));refreshDetails();renderValidation();document.querySelectorAll('.mode-tab').forEach(btn=>btn.onclick=()=>{document.querySelectorAll('.mode-tab').forEach(x=>x.classList.remove('active'));btn.classList.add('active');mode=btn.dataset.mode;resetPage();render();});sectorSel.onchange=()=>{refreshDetails();resetPage();render();};dateSel.onchange=loadDate;[detailSel,horizonSel,pageSize,query,sortSel].forEach(x=>x.addEventListener('input',()=>{resetPage();render();}));sortSel.addEventListener('change',()=>{resetPage();render();});dirBtn.onclick=()=>{sortAsc=!sortAsc;resetPage();render();};prevBtn.onclick=()=>{page--;render();};nextBtn.onclick=()=>{page++;render();};await loadDate();}
init().catch(err=>{table.innerHTML=`<div class="note error">대시보드를 초기화하지 못했습니다: ${esc(err.message)}</div>`;});
"""
    return f"""<!doctype html>
<html lang="ko">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{market_name} 주식 조기경보 대시보드</title><style>{common_css()}</style></head>
<body>
<header><h1>{market_name} 주식 조기경보 대시보드</h1><div class="sub">선택한 신호일만 불러오고, 종목 상세에서는 점수와 섹터 평균 흐름을 확인합니다.</div><nav class="topnav"><a href="{home_href}">대시보드 홈</a><a href="dashboard.html">현재 대시보드</a></nav></header>
<main>
<div class="cards"><div class="card"><div class="label">최신 신호일</div><div class="value" id="latest">-</div></div><div class="card"><div class="label">최신 최종 후보</div><div class="value" id="latestFinal">-</div></div><div class="card"><div class="label">최신 상승 후보</div><div class="value" id="latestUp">-</div></div><div class="card"><div class="label">최신 전체 종목</div><div class="value" id="latestRows">-</div></div></div>
<section>
<div class="tabs"><button class="tab mode-tab active" data-mode="final">최종 후보</button><button class="tab mode-tab" data-mode="up">상승 후보</button><button class="tab mode-tab" data-mode="down">하락 RED</button><button class="tab mode-tab" data-mode="search">전체 검색</button></div>
<div class="toolbar"><label>신호일<select id="date"></select></label><label>섹터<select id="sector"></select></label><label>대표분야<select id="detail"></select></label><label>기간<select id="horizon"><option value="1m">1개월</option><option value="3m">3개월</option><option value="6m" selected>6개월</option><option value="12m">12개월</option></select></label><label>페이지당 N개<input id="pageSize" type="number" value="50" min="1" max="500"></label><label>검색<input id="query" placeholder="종목명 또는 티커"></label><label>정렬<select id="sort"><option value="upScore">상승점수</option><option value="downRisk">하락위험</option><option value="ticker">티커</option><option value="name">종목명</option><option value="sector">섹터</option><option value="detailSector">대표분야</option><option value="close">종가</option><option value="upGrade">상승등급</option><option value="downGrade">하락등급</option><option value="expRet_1m">예상 1개월</option><option value="actRet_1m">실제 1개월</option><option value="expRet_3m">예상 3개월</option><option value="actRet_3m">실제 3개월</option><option value="expRet_6m">예상 6개월</option><option value="actRet_6m">실제 6개월</option><option value="expRet_12m">예상 12개월</option><option value="actRet_12m">실제 12개월</option><option value="growth_profit">성장/수익</option><option value="cash_quality">현금흐름</option><option value="valuation">밸류</option><option value="price_volume">가격/거래</option><option value="risk_overheat">위험/과열</option></select></label><label>방향<button id="dir" type="button" class="tab">내림차순</button></label></div>
<div class="pager"><button id="prevPage" type="button">이전</button><span class="page-info" id="pageInfo"></span><button id="nextPage" type="button">다음</button></div>
<div class="sort-note" id="sortNote"></div><div id="table"></div>
</section>
<section><h2>Walk-forward 검증</h2><div id="validation"></div></section>
</main><script>{script}</script></body></html>"""


def stock_html(market_name: str, home_href: str) -> str:
    script = """
const params = new URLSearchParams(location.search);
const ticker = params.get('ticker') || '';
const $ = id => document.getElementById(id);
let primary = null;
let compareList = [];
let tablePage = 1;
let stockIndex = [];
const palette = ['#1d4ed8','#dc2626','#059669','#7c3aed','#ea580c','#0891b2','#be123c','#4d7c0f','#9333ea','#0f766e'];
const reservedNames = new Set(['CON','PRN','AUX','NUL','COM1','COM2','COM3','COM4','COM5','COM6','COM7','COM8','COM9','LPT1','LPT2','LPT3','LPT4','LPT5','LPT6','LPT7','LPT8','LPT9']);

function esc(value){return String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));}
function num(value){const n=parseFloat(String(value??'').replace(/,/g,'').replace('%',''));return Number.isNaN(n)?null:n;}
function badge(v){return `<span class="badge ${esc(v)}">${esc(v)}</span>`;}
function safeTickerFile(symbol){
  let name = encodeURIComponent(String(symbol || ''));
  const stem = name.split('.')[0].toUpperCase();
  if(reservedNames.has(stem)) name += '_';
  return name;
}
function loadHistory(symbol){
  return fetch(`stock_history/${safeTickerFile(symbol)}.json`).then(r=>{
    if(!r.ok) throw new Error(`${symbol}: ${r.status} ${r.statusText}`);
    return r.json();
  });
}
function normalizeSearch(value){
  return String(value || '').trim().toLowerCase();
}
function resolveTicker(value){
  const q = normalizeSearch(value);
  if(!q) return null;
  const exact = stockIndex.find(item => item.ticker.toLowerCase() === q);
  if(exact) return exact;
  const exactName = stockIndex.find(item => item.name.toLowerCase() === q);
  if(exactName) return exactName;
  const starts = stockIndex.find(item => item.ticker.toLowerCase().startsWith(q) || item.name.toLowerCase().startsWith(q));
  if(starts) return starts;
  return stockIndex.find(item => item.ticker.toLowerCase().includes(q) || item.name.toLowerCase().includes(q)) || null;
}
function rangeRows(rows){
  const range = $('range').value;
  if(range === 'all') return rows;
  const count = parseInt(range, 10);
  return rows.slice(Math.max(0, rows.length - count));
}
function activeRows(){
  return primary ? rangeRows(primary.rows || []) : [];
}
function monthKey(date){
  return String(date || '').slice(0, 7);
}
function draw(canvas, datasets, options={}){
  const ctx=canvas.getContext('2d');
  canvas.width=canvas.clientWidth*devicePixelRatio;
  canvas.height=canvas.clientHeight*devicePixelRatio;
  ctx.scale(devicePixelRatio,devicePixelRatio);
  const cw=canvas.clientWidth,ch=canvas.clientHeight,pad=38;
  ctx.clearRect(0,0,cw,ch);
  ctx.font='12px Arial';
  const dateAxis = [...new Set(datasets.flatMap(ds => (ds.rows || []).map(r => r.date)))].sort();
  if(!dateAxis.length){
    ctx.fillStyle='#64748b';
    ctx.fillText('표시할 데이터가 없습니다.', pad, pad);
    return;
  }
  const values = datasets.flatMap(ds => (ds.rows || []).map(r => num(r[ds.key])).filter(v => v !== null));
  const rawMin = values.length ? Math.min(...values) : 0;
  const rawMax = values.length ? Math.max(...values) : 100;
  const span = Math.max(1, rawMax - rawMin);
  const minY = options.fixedScale ? 0 : Math.max(0, rawMin - span * 0.08);
  const maxY = options.fixedScale ? 100 : rawMax + span * 0.08;
  function yPos(v){return ch-pad-(ch-pad*2)*((v-minY)/(maxY-minY));}
  ctx.strokeStyle='#d9e2ec';
  ctx.lineWidth=1;
  for(let i=0;i<=4;i++){
    const y=pad+(ch-pad*2)*i/4;
    ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(cw-pad,y);ctx.stroke();
    const label = maxY - (maxY-minY)*i/4;
    ctx.fillStyle='#64748b';ctx.fillText(options.fixedScale ? String(Math.round(label)) : label.toFixed(0),6,y+4);
  }
  const monthTicks = [];
  let seenMonth = '';
  dateAxis.forEach((date, index) => {
    const key = monthKey(date);
    if(key && key !== seenMonth){
      monthTicks.push({date, index, label:key});
      seenMonth = key;
    }
  });
  monthTicks.forEach(tick => {
    const x=pad+(cw-pad*2)*(dateAxis.length===1?0:tick.index/(dateAxis.length-1));
    ctx.strokeStyle='#eef2f7';
    ctx.beginPath();ctx.moveTo(x,pad);ctx.lineTo(x,ch-pad);ctx.stroke();
    ctx.save();
    ctx.translate(x+3,ch-8);
    ctx.rotate(-Math.PI/7);
    ctx.fillStyle='#64748b';
    ctx.fillText(tick.label,0,0);
    ctx.restore();
  });
  datasets.forEach(ds=>{
    const valueByDate = new Map((ds.rows || []).map(r => [r.date, num(r[ds.key])]));
    ctx.strokeStyle=ds.color;
    ctx.lineWidth=ds.dash ? 1.5 : 2.3;
    ctx.setLineDash(ds.dash ? [5,4] : []);
    ctx.beginPath();
    let started=false;
    dateAxis.forEach((date,i)=>{
      const v=valueByDate.get(date); if(v===null || v===undefined) return;
      const x=pad+(cw-pad*2)*(dateAxis.length===1?0:i/(dateAxis.length-1));
      const y=yPos(v);
      if(!started){ctx.moveTo(x,y);started=true;} else ctx.lineTo(x,y);
    });
    ctx.stroke();
    ctx.setLineDash([]);
    monthTicks.forEach(tick => {
      const v=valueByDate.get(tick.date); if(v===null || v===undefined) return;
      const x=pad+(cw-pad*2)*(dateAxis.length===1?0:tick.index/(dateAxis.length-1));
      const y=yPos(v);
      ctx.beginPath();
      ctx.fillStyle=ds.color;
      ctx.arc(x,y,ds.dash?2.5:3.5,0,Math.PI*2);
      ctx.fill();
      ctx.strokeStyle='#fff';
      ctx.stroke();
    });
  });
  ctx.textAlign='left';
}
function renderSummary(){
  const rows = activeRows();
  const latest = rows[rows.length-1] || {};
  $('title').textContent = primary ? `${primary.name || primary.ticker} (${primary.ticker})` : '종목 상세';
  $('sub').textContent = primary ? `${primary.sector || ''} / ${primary.detailSector || ''}` : '점수와 등급의 날짜별 변화';
  $('latestDate').textContent = latest.date || '-';
  $('latestUp').textContent = latest.upScore ?? '-';
  $('latestDown').textContent = latest.downRisk ?? '-';
  $('latestGrade').innerHTML = `${badge(latest.upGrade || '')} ${badge(latest.downGrade || '')}`;
}
function legend(items){
  return items.map(item=>`<span><i class="dot" style="background:${item.color}"></i>${esc(item.label)}${item.dash?' 섹터':''}</span>`).join('');
}
function datasetsFor(items, key){
  return items.map((item, idx) => {
    const color = palette[idx % palette.length];
    const rows = rangeRows(item.rows || []);
    return {rows, key, color, label:item.ticker};
  });
}
function renderCharts(){
  const items = primary ? [primary, ...compareList.filter(item => item.ticker !== primary.ticker)] : compareList;
  $('compareTitle').textContent = `비교 그래프 (${items.length}개)`;
  $('compareSub').textContent = items.map(item => `${item.ticker}: ${item.name || ''}`).join(' / ') || '비교 검색에 여러 종목을 쉼표로 넣어보세요';
  const upDatasets = datasetsFor(items, 'upScore');
  const downDatasets = datasetsFor(items, 'downRisk');
  const closeDatasets = datasetsFor(items, 'close');
  $('compareUpLegend').innerHTML = legend(upDatasets);
  $('compareDownLegend').innerHTML = legend(downDatasets);
  $('compareCloseLegend').innerHTML = legend(closeDatasets);
  draw($('compareUpChart'), upDatasets, {fixedScale:true});
  draw($('compareDownChart'), downDatasets, {fixedScale:true});
  draw($('compareCloseChart'), closeDatasets, {fixedScale:false});
}
function renderTable(){
  const rows = activeRows().slice().reverse();
  const size = Math.max(1, Math.min(200, parseInt($('historyPageSize').value || 30, 10)));
  const totalPages = Math.max(1, Math.ceil(rows.length / size));
  tablePage = Math.max(1, Math.min(tablePage, totalPages));
  const start = (tablePage - 1) * size;
  const pageRows = rows.slice(start, start + size);
  const from = rows.length ? start + 1 : 0;
  const to = Math.min(start + size, rows.length);
  $('historyPageInfo').textContent = `${tablePage} / ${totalPages} 페이지 (${from}-${to}개 표시)`;
  $('historyPrev').disabled = tablePage <= 1;
  $('historyNext').disabled = tablePage >= totalPages;
  $('historyTable').innerHTML='<table><thead><tr><th>날짜</th><th>종가</th><th>상승점수</th><th>상승등급</th><th>섹터 평균 상승</th><th>하락위험</th><th>하락등급</th><th>섹터 평균 하락</th><th>최종</th></tr></thead><tbody>'+
    pageRows.map(r=>`<tr><td>${esc(r.date)}</td><td>${esc(r.close)}</td><td>${esc(r.upScore)}</td><td>${badge(r.upGrade)}</td><td>${esc(r.sectorUpScore)}</td><td>${esc(r.downRisk)}</td><td>${badge(r.downGrade)}</td><td>${esc(r.sectorDownRisk)}</td><td>${r.isFinalCandidate?'Y':''}</td></tr>`).join('')+
    '</tbody></table>';
}
function renderAll(){renderSummary();renderCharts();renderTable();}
async function loadCompare(){
  const raw = $('compareTicker').value.trim();
  if(!raw){compareList=[];renderCharts();return;}
  $('compareStatus').textContent='불러오는 중...';
  try{
    const tokens = raw.split(/[,\\r\\n]+/).map(x=>x.trim()).filter(Boolean);
    const matches = [];
    const misses = [];
    tokens.forEach(token => {
      const match = resolveTicker(token);
      if(match && !matches.some(item => item.ticker === match.ticker)) matches.push(match);
      else if(!match) misses.push(token);
    });
    if(!matches.length) throw new Error(`검색 결과가 없습니다: ${raw}`);
    compareList = await Promise.all(matches.map(match => loadHistory(match.ticker)));
    $('compareTicker').value = matches.map(match => match.ticker).join(', ');
    $('compareStatus').textContent=`${matches.length}개 로드 완료${misses.length ? ` / 실패: ${misses.join(', ')}` : ''}`;
    renderCharts();
  }catch(err){
    compareList=[];
    $('compareStatus').textContent=`불러오기 실패: ${err.message}`;
    renderCharts();
  }
}
async function init(){
  if(!ticker){$('content').innerHTML='<section class="error">티커가 없습니다.</section>';return;}
  try{
    stockIndex = await fetch('stock_index.json').then(r=>r.json());
    const datalist = $('stockOptions');
    stockIndex.slice(0, 4000).forEach(item => {
      const option = document.createElement('option');
      option.value = item.ticker;
      option.label = `${item.name} / ${item.sector}`;
      datalist.appendChild(option);
    });
    primary = await loadHistory(ticker);
    $('compareTicker').placeholder = ticker === 'AAPL' ? 'NVDA' : 'AAPL';
    $('range').onchange=()=>{tablePage=1;renderAll();};
    $('historyPageSize').oninput=()=>{tablePage=1;renderTable();};
    $('historyPrev').onclick=()=>{tablePage--;renderTable();};
    $('historyNext').onclick=()=>{tablePage++;renderTable();};
    $('loadCompare').onclick=loadCompare;
    $('compareTicker').addEventListener('keydown', e=>{if(e.key==='Enter')loadCompare();});
    renderAll();
  }catch(err){
    $('content').innerHTML=`<section class="note error">종목 이력을 불러오지 못했습니다: ${esc(err.message)}</section>`;
  }
}
addEventListener('resize',()=>renderCharts());
init();
"""
    return f"""<!doctype html>
<html lang="ko">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>종목 상세</title><style>{common_css()}</style></head>
<body>
<header><h1 id="title">{market_name} 종목 상세</h1><div class="sub" id="sub">점수와 등급의 날짜별 변화</div><nav class="topnav"><a href="{home_href}">대시보드 홈</a><a href="dashboard.html">목록으로</a></nav></header>
<main id="content">
<div class="cards"><div class="card"><div class="label">최근 신호일</div><div class="value" id="latestDate">-</div></div><div class="card"><div class="label">최근 상승점수</div><div class="value" id="latestUp">-</div></div><div class="card"><div class="label">최근 하락위험</div><div class="value" id="latestDown">-</div></div><div class="card"><div class="label">최근 등급</div><div class="value" id="latestGrade">-</div></div></div>
<section>
<div class="toolbar"><label>그래프 기간<select id="range"><option value="20">최근 20 신호일</option><option value="60">최근 60 신호일</option><option value="120">최근 120 신호일</option><option value="all">전체</option></select></label><label>비교 검색<input id="compareTicker" list="stockOptions" placeholder="AAPL, NVDA, TSLA"></label><datalist id="stockOptions"></datalist><label>비교 불러오기<button id="loadCompare" type="button" class="tab">로드</button></label><div class="sort-note" id="compareStatus"></div></div>
<h2 id="compareTitle">비교 그래프</h2><div class="note" id="compareSub">비교 검색에 여러 종목을 쉼표로 넣어보세요</div>
<div class="triple-grid">
<div><h3>상승점수</h3><div class="legend" id="compareUpLegend"></div><canvas class="chart" id="compareUpChart"></canvas></div>
<div><h3>하락위험</h3><div class="legend" id="compareDownLegend"></div><canvas class="chart" id="compareDownChart"></canvas></div>
<div><h3>종가</h3><div class="legend" id="compareCloseLegend"></div><canvas class="chart" id="compareCloseChart"></canvas></div>
</div>
</section>
<section><h2>일자별 주요 점수 및 등급</h2><div class="pager"><label>페이지당 N개<input id="historyPageSize" type="number" value="30" min="1" max="200"></label><button id="historyPrev" type="button">이전</button><span class="page-info" id="historyPageInfo"></span><button id="historyNext" type="button">다음</button></div><div id="historyTable"></div></section>
</main><script>{script}</script></body></html>"""


def home_html(kr_dashboard_href: str, us_dashboard_href: str) -> str:
    css = """*{box-sizing:border-box}body{margin:0;font-family:Arial,"Malgun Gothic",sans-serif;background:#f5f7fb;color:#17202a}main{max-width:980px;margin:0 auto;padding:40px 24px}h1{font-size:28px;margin:0 0 10px}p{color:#64748b;margin:0 0 22px;line-height:1.5}.grid{display:grid;grid-template-columns:repeat(2,minmax(240px,1fr));gap:14px}a{display:block;background:white;border:1px solid #d9e2ec;border-radius:8px;padding:18px;text-decoration:none;color:#17202a}a:hover{border-color:#1d4ed8;box-shadow:0 8px 24px rgba(15,23,42,.08)}b{display:block;font-size:18px;margin-bottom:8px}span{color:#64748b;font-size:14px;line-height:1.45}@media(max-width:720px){.grid{grid-template-columns:1fr}}"""
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>주식 조기경보 대시보드</title><style>{css}</style></head><body><main><h1>주식 조기경보 대시보드</h1><p>한국과 미국 walk-forward 조기경보 결과를 나눠서 봅니다. 각 대시보드는 선택한 신호일 데이터만 불러오고, 종목 상세 페이지에서 점수와 섹터 평균 흐름을 확인합니다.</p><div class="grid"><a href="{kr_dashboard_href}"><b>한국 대시보드</b><span>KOSPI/KOSDAQ 조기경보 후보</span></a><a href="{us_dashboard_href}"><b>미국 대시보드</b><span>NASDAQ/NYSE 또는 미국 전체 후보</span></a></div></main></body></html>"""


def main() -> None:
    args = parser().parse_args()
    wf_dir = Path(args.wf_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    scores = apply_listing_tags(load_scores(wf_dir), Path(args.listings_path) if args.listings_path else None)
    dates = selected_dates(scores, args.recent_days)
    write_date_files(scores, out_dir, dates)
    write_stock_history(scores, out_dir, dates)
    json_dump(out_dir / "manifest.json", build_manifest(scores, dates, wf_dir, args.market_name))
    if args.copy_csv:
        write_csv_exports(scores, out_dir)
    else:
        for stale in ["walkforward_scores.csv", "walkforward_candidates.csv"]:
            path = out_dir / stale
            if path.exists():
                path.unlink()
    (out_dir / args.html_name).write_text(dashboard_html(args.market_name, args.home_href), encoding="utf-8")
    (out_dir / "stock.html").write_text(stock_html(args.market_name, args.home_href), encoding="utf-8")
    home = home_html(args.kr_dashboard_href, args.us_dashboard_href)
    (out_dir / "index.html").write_text(home, encoding="utf-8")
    if out_dir.parent.name == "outputs":
        root_kr = f"{out_dir.name}/{args.kr_dashboard_href}" if not args.kr_dashboard_href.startswith("../") else "lgbm_warning_dashboard/dashboard.html"
        root_us = f"{out_dir.name}/{args.us_dashboard_href}" if not args.us_dashboard_href.startswith("../") else "lgbm_warning_dashboard_macro_us_latest/dashboard.html"
        (out_dir.parent / "index.html").write_text(home_html(root_kr, root_us), encoding="utf-8")
    print(out_dir / args.html_name)


if __name__ == "__main__":
    main()
