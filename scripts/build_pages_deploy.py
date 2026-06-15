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
ACTION_URL = "https://github.com/jaesung0804/st_dashboard/actions/workflows/daily-refresh.yml"
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


def home_html() -> str:
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>주식 조기경보 대시보드</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;font-family:Arial,"Malgun Gothic",sans-serif;background:#f6f8fb;color:#17202a}}main{{max-width:1040px;margin:0 auto;padding:28px 18px 42px}}h1{{font-size:26px;margin:0 0 8px}}.sub{{color:#64748b;line-height:1.55;margin-bottom:18px}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}.card{{display:block;background:#fff;border:1px solid #d9e2ec;border-radius:8px;padding:18px;text-decoration:none;color:#17202a}}.card:hover{{border-color:#1d4ed8;box-shadow:0 8px 24px rgba(15,23,42,.08)}}b{{display:block;font-size:18px;margin-bottom:7px}}span{{display:block;color:#64748b;font-size:14px;line-height:1.45}}.actions{{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}}.button{{display:inline-flex;align-items:center;justify-content:center;background:#1d4ed8;color:#fff;border-radius:7px;padding:10px 13px;text-decoration:none;font-weight:800}}.button.secondary{{background:#e8eef8;color:#1e3a8a}}@media(max-width:720px){{main{{padding:20px 12px 34px}}h1{{font-size:23px}}.grid{{grid-template-columns:1fr}}.card{{padding:15px}}.button{{width:100%}}}}
</style></head><body><main>
<h1>주식 조기경보 대시보드</h1>
<div class="sub">최근 한 달치 신호일을 공개용으로 가볍게 정리한 화면입니다. 매일 GitHub Actions가 종가와 재무 상태를 갱신하고 Pages를 다시 배포합니다.</div>
<div class="actions">
<a class="button" href="lgbm_warning_dashboard_macro_kr_latest/dashboard.html">한국 보기</a>
<a class="button" href="lgbm_warning_dashboard_macro_us_latest/dashboard.html">미국 보기</a>
<a class="button secondary" href="{ACTION_URL}">최신화 실행</a>
</div>
<div class="grid">
<a class="card" href="lgbm_warning_dashboard_macro_kr_latest/dashboard.html"><b>한국 대시보드</b><span>신호일별 KOSPI/KOSDAQ 최근 후보</span></a>
<a class="card" href="lgbm_warning_dashboard_macro_us_latest/dashboard.html"><b>미국 대시보드</b><span>신호일별 NASDAQ/NYSE 최근 후보</span></a>
<a class="card" href="down_negative_model_comparison/index.html"><b>하락 모델 비교</b><span>하락 필터별 차이를 요약한 비교 리포트</span></a>
</div>
</main></body></html>"""


def dashboard_html(label: str, subtitle: str, other_href: str, other_label: str) -> str:
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{label} 조기경보 대시보드</title>
<style>
:root{{--ink:#17202a;--muted:#64748b;--line:#d9e2ec;--bg:#f6f8fb;--head:#101827;--blue:#1d4ed8;--red:#b91c1c;--green:#047857}}
*{{box-sizing:border-box}}body{{margin:0;font-family:Arial,"Malgun Gothic",sans-serif;background:var(--bg);color:var(--ink)}}header{{background:var(--head);color:white;padding:16px 18px}}header h1{{margin:0 0 5px;font-size:22px}}.sub{{color:#cbd5e1;font-size:13px;line-height:1.45}}nav{{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}}nav a{{color:#dbeafe;text-decoration:none;border:1px solid #334155;border-radius:6px;padding:7px 10px;font-size:13px}}main{{max-width:1360px;margin:0 auto;padding:14px}}.stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:12px}}.stat,.panel{{background:white;border:1px solid var(--line);border-radius:8px}}.stat{{padding:12px}}.stat small{{display:block;color:var(--muted);margin-bottom:4px}}.stat b{{font-size:20px}}.panel{{padding:12px;margin-bottom:12px}}.guide{{line-height:1.55;color:#334155;font-size:13px}}.guide b{{color:#0f172a}}.controls{{display:grid;grid-template-columns:170px repeat(4,minmax(120px,1fr));gap:9px;align-items:end}}label{{font-size:12px;color:#475569;display:grid;gap:4px}}select,input{{width:100%;padding:9px;border:1px solid #cbd5e1;border-radius:6px;background:white}}.tabs{{display:flex;gap:7px;flex-wrap:wrap;margin:12px 0}}button{{border:1px solid #bfdbfe;background:#eff6ff;color:#1e40af;padding:8px 10px;border-radius:6px;font-weight:800;cursor:pointer}}button.active{{background:var(--blue);color:white}}.meta{{font-size:13px;color:#475569;margin:8px 0}}table{{width:100%;border-collapse:separate;border-spacing:0;font-size:12px}}th,td{{border-bottom:1px solid var(--line);padding:9px 8px;text-align:right;vertical-align:top}}th{{background:#eef3f8;color:#334155;position:sticky;top:0}}td.left,th.left{{text-align:left}}.name{{font-weight:800;color:#1d4ed8;text-decoration:none}}.name:hover{{text-decoration:underline}}.wrap{{white-space:normal;overflow-wrap:anywhere;line-height:1.35}}.badge{{display:inline-block;min-width:54px;text-align:center;border-radius:5px;padding:3px 6px;font-weight:800}}.score-grid{{display:grid;grid-template-columns:repeat(5,minmax(56px,1fr));gap:4px;min-width:310px;text-align:left}}.score{{border:1px solid #d9e2ec;border-radius:5px;padding:5px}}.score small{{display:block;color:#64748b;font-size:10px;margin-bottom:2px}}.score b{{font-size:12px}}.GREEN{{background:#dbeafe;color:#1d4ed8}}.RED{{background:#fee2e2;color:#b91c1c}}.YELLOW{{background:#fef9c3;color:#854d0e}}.ORANGE{{background:#ffedd5;color:#c2410c}}.pos{{color:var(--green);font-weight:800}}.neg{{color:var(--red);font-weight:800}}.pager{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:10px}}.scroll{{overflow:auto}}.empty{{padding:16px;color:var(--muted)}}.price{{line-height:1.35}}.price small{{display:block;color:#64748b}}@media(max-width:760px){{header{{padding:14px 12px}}header h1{{font-size:20px}}main{{padding:10px}}.stats{{grid-template-columns:1fr 1fr;gap:8px}}.stat{{padding:10px}}.stat b{{font-size:18px}}.controls{{grid-template-columns:1fr}}.panel{{padding:10px;border-radius:7px}}table,thead,tbody,tr,th,td{{display:block}}thead{{display:none}}tbody{{display:grid;gap:10px}}tr{{background:white;border:1px solid var(--line);border-radius:8px;padding:10px}}td{{border:0;display:grid;grid-template-columns:92px 1fr;gap:8px;text-align:left;padding:5px 0;white-space:normal}}td::before{{content:attr(data-label);color:#64748b;font-size:11px;font-weight:800;text-transform:uppercase}}td.left{{text-align:left}}.badge{{min-width:0}}.score-grid{{min-width:0;grid-template-columns:1fr 1fr}}.pager button{{flex:1}}}}
</style></head><body>
<header><h1>{label} 조기경보 대시보드</h1><div class="sub">{subtitle}. 최근 공개 신호일을 보여줍니다.</div>
<nav><a href="../index.html">홈</a><a href="dashboard.html">{label}</a><a href="{other_href}">{other_label}</a><a href="../down_negative_model_comparison/index.html">모델 비교</a><a href="{ACTION_URL}">최신화 실행</a></nav></header>
<main>
<div class="stats"><div class="stat"><small>최신 신호일</small><b id="latest">-</b></div><div class="stat"><small>최종 후보</small><b id="finalCount">-</b></div><div class="stat"><small>상승 후보</small><b id="upCount">-</b></div><div class="stat"><small>전체 종목</small><b id="rowCount">-</b></div></div>
<section class="panel guide"><b>해석 가이드</b><br>상승점수는 같은 날짜 종목 중 6개월 상승 확률이 높은 순위 점수이고, 하락위험은 6개월 하락 확률이 높은 순위 점수입니다. 등급은 상위 5% RED, 5-15% ORANGE, 15-35% YELLOW, 나머지 GREEN으로 나뉩니다. 최종 후보는 상승 상위 5%이면서 하락위험이 GREEN인 종목입니다. 대표점수는 모델 입력 특성을 묶어 백분위로 요약한 해석 보조 지표이며, 색이 진할수록 해당 묶음의 강도가 큽니다.</section>
<section class="panel">
<div class="controls"><label>신호일<select id="date"></select></label><label>보기<select id="mode"><option value="final">최종 후보</option><option value="up">상승 후보</option><option value="down">하락 RED</option><option value="all">전체 종목</option></select></label><label>검색<input id="query" placeholder="티커 또는 종목명"></label><label>정렬<select id="sort"><option value="upScore">상승점수</option><option value="downRisk">하락위험</option><option value="growth_profit">성장/수익</option><option value="cash_quality">현금흐름</option><option value="valuation">밸류</option><option value="price_volume">가격/거래</option><option value="risk_overheat">위험/과열</option><option value="ticker">티커</option><option value="name">종목명</option><option value="closeRaw">종가</option></select></label><label>페이지당 행<input id="pageSize" type="number" min="10" max="200" value="10"></label></div>
<div class="tabs"><button id="dir" type="button">내림차순</button></div>
<div class="meta" id="meta"></div><div class="scroll" id="table"></div>
<div class="pager"><button id="prev" type="button">이전</button><span id="pageInfo"></span><button id="next" type="button">다음</button></div>
</section></main>
<script>
let manifest={{}}, rows=[], filtered=[], page=1, asc=false;
const $=id=>document.getElementById(id);
const labels=['#','티커','종목명','섹터','대표분류','종가','상승','상승등급','하락','하락등급','대표점수','예상 6개월'];
const scoreLabels=[['growth_profit','성장'],['cash_quality','현금'],['valuation','밸류'],['price_volume','가격'],['risk_overheat','위험']];
const suffix={{NASDAQ:'.O',NYSE:'.N',NYSEAMERICAN:'.A',NYSEARCA:'.P'}};
function esc(v){{return String(v??'').replace(/[&<>"']/g,ch=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));}}
function num(v){{const n=parseFloat(String(v??'').replace(/[$,원%]/g,'').split('/')[0]);return Number.isNaN(n)?NaN:n;}}
function naver(r){{const t=String(r.ticker||''); if(String(r.currency||'').toUpperCase()==='USD') return `https://m.stock.naver.com/worldstock/stock/${{encodeURIComponent(t+(suffix[String(r.exchange||'').toUpperCase()]||'.O'))}}/total`; return `https://m.stock.naver.com/domestic/stock/${{encodeURIComponent(t.padStart(6,'0'))}}/total`;}}
function ret(v){{const n=num(v);const cls=Number.isNaN(n)?'':n<0?'neg':'pos';return `<span class="${{cls}}">${{esc(v||'-')}}</span>`;}}
function heat(v,key){{const n=Math.max(0,Math.min(100,num(v)||0));const good=key==='risk_overheat'?100-n:n;const hue=220-good*1.7;return `background:hsl(${{hue}} 84% 91%);color:hsl(${{hue}} 76% 27%)`;}}
function scoreBlock(r){{return '<div class="score-grid">'+scoreLabels.map(([key,label])=>`<div class="score" style="${{heat(r[key],key)}}"><small>${{label}}</small><b>${{esc(r[key]??'-')}}</b></div>`).join('')+'</div>';}}
function price(r){{const text=String(r.close||'-'); if(text.includes('/')){{const [usd,krw]=text.split('/'); return `<div class="price"><b>${{esc(usd.trim())}}</b><small>${{esc(krw.trim())}}</small></div>`;}} return esc(text);}}
function expected(r){{const krw=r.expCloseKrw_6m?`<small>${{esc(r.expCloseKrw_6m)}}</small>`:'';return `<div class="price">${{ret(r.expRet_6m)}}<small>${{esc(r.expClose_6m||'-')}}</small>${{krw}}</div>`;}}
function compare(a,b,k){{const an=num(a[k]),bn=num(b[k]);if(!Number.isNaN(an)&&!Number.isNaN(bn))return an-bn;return String(a[k]??'').localeCompare(String(b[k]??''),'ko');}}
function selectedRows(){{const mode=$('mode').value,q=$('query').value.trim().toLowerCase();let out=rows.filter(r=>mode==='final'?r.isFinalCandidate:mode==='up'?r.isUpCandidate:mode==='down'?r.isDownRed:true);if(q)out=out.filter(r=>String(r.ticker).toLowerCase().includes(q)||String(r.name).toLowerCase().includes(q));const key=$('sort').value;out.sort((a,b)=>(asc?1:-1)*compare(a,b,key));return out;}}
async function loadDate(){{const date=$('date').value;$('table').innerHTML='<div class="empty">불러오는 중...</div>';const res=await fetch(`walkforward_scores_by_date/${{date}}.json`);rows=await res.json();page=1;render();}}
function render(){{filtered=selectedRows();const size=Math.max(10,Math.min(200,parseInt($('pageSize').value||10,10)));const pages=Math.max(1,Math.ceil(filtered.length/size));page=Math.max(1,Math.min(page,pages));const start=(page-1)*size, shown=filtered.slice(start,start+size);$('meta').textContent=`${{filtered.length.toLocaleString()}}개 / 신호일 ${{$('date').value}}`;$('pageInfo').textContent=`${{page}} / ${{pages}}`; $('prev').disabled=page<=1;$('next').disabled=page>=pages;$('dir').textContent=asc?'오름차순':'내림차순';if(!shown.length){{$('table').innerHTML='<div class="empty">조건에 맞는 종목이 없습니다.</div>';return;}}$('table').innerHTML='<table><thead><tr>'+labels.map((x,i)=>`<th class="${{i>=1&&i<=4?'left':''}}">${{x}}</th>`).join('')+'</tr></thead><tbody>'+shown.map((r,i)=>{{const cells=[start+i+1,`<a class="name" href="${{naver(r)}}" target="_blank" rel="noopener">${{esc(r.ticker)}}</a>`,`<a class="name" href="stock.html?ticker=${{encodeURIComponent(r.ticker)}}">${{esc(r.name||r.ticker)}}</a>`,esc(r.sector),esc(r.detailSector),price(r),esc(r.upScore),`<span class="badge ${{esc(r.upGrade)}}">${{esc(r.upGrade)}}</span>`,esc(r.downRisk),`<span class="badge ${{esc(r.downGrade)}}">${{esc(r.downGrade)}}</span>`,scoreBlock(r),expected(r)];return '<tr>'+cells.map((c,idx)=>`<td data-label="${{labels[idx]}}" class="${{idx>=1&&idx<=4?'left wrap':''}}">${{c}}</td>`).join('')+'</tr>';}}).join('')+'</tbody></table>';}}
async function init(){{manifest=await fetch('manifest.json').then(r=>r.json());$('latest').textContent=manifest.latest||'-';$('finalCount').textContent=Number(manifest.latestFinal||0).toLocaleString();$('upCount').textContent=Number(manifest.latestUp||0).toLocaleString();$('rowCount').textContent=Number(manifest.latestRows||0).toLocaleString();(manifest.dates||[]).forEach(d=>$('date').add(new Option(d,d)));['date','mode','query','sort','pageSize'].forEach(id=>$(id).addEventListener(id==='query'?'input':'change',()=>{{page=1;id==='date'?loadDate():render();}}));$('dir').onclick=()=>{{asc=!asc;render();}};$('prev').onclick=()=>{{page--;render();}};$('next').onclick=()=>{{page++;render();}};await loadDate();}}
init().catch(err=>{{$('table').innerHTML=`<div class="empty">${{esc(err.message)}}</div>`;}});
</script></body></html>"""


def stock_html(label: str, other_href: str, other_label: str) -> str:
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{label} 종목 상세</title>
<style>*{{box-sizing:border-box}}body{{margin:0;font-family:Arial,"Malgun Gothic",sans-serif;background:#f6f8fb;color:#17202a}}header{{background:#101827;color:white;padding:16px 18px}}nav{{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}}nav a{{color:#dbeafe;text-decoration:none;border:1px solid #334155;border-radius:6px;padding:7px 10px;font-size:13px}}main{{max-width:1100px;margin:0 auto;padding:14px}}.panel{{background:white;border:1px solid #d9e2ec;border-radius:8px;padding:12px;overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{border-bottom:1px solid #d9e2ec;padding:8px;text-align:right}}th{{background:#eef3f8}}th:first-child,td:first-child{{text-align:left}}.scores{{display:grid;grid-template-columns:repeat(5,minmax(48px,1fr));gap:4px;min-width:270px;text-align:left}}.score{{border:1px solid #d9e2ec;border-radius:5px;padding:5px}}.score small{{display:block;color:#64748b;font-size:10px}}.score b{{font-size:12px}}.pos{{color:#047857;font-weight:800}}.neg{{color:#b91c1c;font-weight:800}}.ext{{color:#dbeafe}}@media(max-width:720px){{main{{padding:10px}}table,thead,tbody,tr,th,td{{display:block}}thead{{display:none}}tr{{border:1px solid #d9e2ec;border-radius:8px;margin-bottom:9px;padding:8px}}td{{border:0;display:grid;grid-template-columns:92px 1fr;text-align:left;padding:5px}}td::before{{content:attr(data-label);font-size:11px;color:#64748b;font-weight:800;text-transform:uppercase}}.scores{{min-width:0;grid-template-columns:1fr 1fr}}}}</style></head>
<body><header><h1 id="title">{label} 종목 상세</h1><nav><a href="../index.html">홈</a><a href="dashboard.html">{label}</a><a href="{other_href}">{other_label}</a><a id="naver" class="ext" target="_blank" rel="noopener">네이버 증권</a></nav></header><main><section class="panel" id="content">불러오는 중...</section></main>
<script>
const params=new URLSearchParams(location.search),ticker=params.get('ticker')||'';const labels=['신호일','종가','상승','상승등급','하락','하락등급','최종','대표점수','예상 6개월'];
const scoreLabels=[['growth_profit','성장'],['cash_quality','현금'],['valuation','밸류'],['price_volume','가격'],['risk_overheat','위험']];
const suffix={{NASDAQ:'.O',NYSE:'.N',NYSEAMERICAN:'.A',NYSEARCA:'.P'}};
const reservedNames=new Set(['CON','PRN','AUX','NUL','COM1','COM2','COM3','COM4','COM5','COM6','COM7','COM8','COM9','LPT1','LPT2','LPT3','LPT4','LPT5','LPT6','LPT7','LPT8','LPT9']);
function esc(v){{return String(v??'').replace(/[&<>"']/g,ch=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));}}
function num(v){{const n=parseFloat(String(v??'').replace(/[$,원%]/g,'').split('/')[0]);return Number.isNaN(n)?NaN:n;}}
function safeTickerFile(t){{let name=encodeURIComponent(String(t||''));const stem=name.split('.')[0].toUpperCase();if(reservedNames.has(stem))name+='_';return name;}}
function cls(v){{const n=num(v);return Number.isNaN(n)?'':n<0?'neg':'pos';}}
function heat(v,key){{const n=Math.max(0,Math.min(100,num(v)||0));const good=key==='risk_overheat'?100-n:n;const hue=220-good*1.7;return `background:hsl(${{hue}} 84% 91%);color:hsl(${{hue}} 76% 27%)`;}}
function scoreBlock(r){{return '<div class="scores">'+scoreLabels.map(([key,label])=>`<div class="score" style="${{heat(r[key],key)}}"><small>${{label}}</small><b>${{esc(r[key]??'-')}}</b></div>`).join('')+'</div>';}}
function naverUrl(r){{const t=String(r.ticker||ticker); if(String(r.currency||'').toUpperCase()==='USD') return `https://m.stock.naver.com/worldstock/stock/${{encodeURIComponent(t+(suffix[String(r.exchange||'').toUpperCase()]||'.O'))}}/total`; return `https://m.stock.naver.com/domestic/stock/${{encodeURIComponent(t.padStart(6,'0'))}}/total`;}}
function expected(r){{const krw=r.expCloseKrw_6m?` / ${{esc(r.expCloseKrw_6m)}}`:'';return `<span class="${{cls(r.expRet_6m)}}">${{esc(r.expRet_6m||'-')}}</span><br>${{esc(r.expClose_6m||'-')}}${{krw}}`;}}
async function init(){{if(!ticker)throw new Error('티커가 없습니다.');const data=await fetch(`stock_history/${{safeTickerFile(ticker)}}.json`).then(r=>{{if(!r.ok)throw new Error(`${{r.status}} ${{r.statusText}}`);return r.json();}});const rows=(data.rows||[]).slice().reverse();document.getElementById('title').textContent=`{label} 종목 상세: ${{data.ticker}} ${{data.name||''}}`;document.getElementById('naver').href=naverUrl(rows[0]||data);document.getElementById('content').innerHTML='<table><thead><tr>'+labels.map(x=>`<th>${{x}}</th>`).join('')+'</tr></thead><tbody>'+rows.map(r=>{{const cells=[esc(r.date),esc(r.close),esc(r.upScore),esc(r.upGrade),esc(r.downRisk),esc(r.downGrade),r.isFinalCandidate?'Y':'',scoreBlock(r),expected(r)];return '<tr>'+cells.map((c,i)=>`<td data-label="${{labels[i]}}">${{c}}</td>`).join('')+'</tr>';}}).join('')+'</tbody></table>';}}
init().catch(err=>{{document.getElementById('content').textContent=err.message;}});
</script></body></html>"""


def build_dashboard(source: Path, target: Path, days: int, label: str, subtitle: str, other_href: str, other_label: str) -> None:
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
            "validation": [],
            "marketName": label,
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
        "미국",
    )
    build_dashboard(
        DASHBOARDS["us"]["source"],
        deploy_dir / DASHBOARDS["us"]["target"],
        args.days,
        DASHBOARDS["us"]["label"],
        DASHBOARDS["us"]["subtitle"],
        "../lgbm_warning_dashboard_macro_kr_latest/dashboard.html",
        "한국",
    )
    total = sum(path.stat().st_size for path in deploy_dir.rglob("*") if path.is_file())
    print(f"Built {deploy_dir} with {sum(1 for _ in deploy_dir.rglob('*') if _.is_file())} files, {total / 1024 / 1024:.1f} MB.")
    if args.push:
        push_pages(deploy_dir, args.repo)


if __name__ == "__main__":
    main()
