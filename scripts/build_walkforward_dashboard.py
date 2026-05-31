from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


WF_DIR = Path("outputs/walkforward_warning")
OUT_DIR = Path("outputs/lgbm_warning_dashboard")
SUBSCORES = {
    "growth_profit": "성장·수익성",
    "cash_quality": "현금흐름 품질",
    "valuation": "밸류에이션",
    "price_volume": "가격·거래량",
    "risk_overheat": "위험·과열",
}


def load_scores() -> pd.DataFrame:
    path = WF_DIR / "walkforward_scores.csv"
    if path.exists():
        return pd.read_csv(path, dtype={"ticker": str})
    return pd.read_csv(WF_DIR / "walkforward_candidates.csv", dtype={"ticker": str})


def records(frame: pd.DataFrame) -> list[dict[str, object]]:
    data = frame.copy()
    data = data.rename(columns={"up_lgbm_prob": "upProb", "down_lgbm_prob": "downProb"})
    keep = [
        "date", "ticker", "name", "sector", "detailSector", "theme", "close",
        "upScore", "upProb", "upGrade", "downRisk", "downProb", "downGrade",
        "isUpCandidate", "isDownRed", "isFinalCandidate",
        "expRet_1m", "expClose_1m", "actRet_1m", "actClose_1m",
        "expRet_3m", "expClose_3m", "actRet_3m", "actClose_3m",
        "expRet_6m", "expClose_6m", "actRet_6m", "actClose_6m",
        "expRet_12m", "expClose_12m", "actRet_12m", "actClose_12m",
        *SUBSCORES.keys(),
    ]
    for col in keep:
        if col not in data:
            data[col] = ""
    for col in ["isUpCandidate", "isDownRed", "isFinalCandidate"]:
        data[col] = data[col].astype(str).str.lower().isin(["true", "1", "yes"])
    return data[keep].fillna("").to_dict("records")


def fmt_auc(value: object) -> str:
    return "" if pd.isna(value) else f"{float(value):.3f}"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    scores = load_scores()
    validation = pd.read_csv(WF_DIR / "walkforward_validation.csv")
    rows = records(scores)
    (OUT_DIR / "walkforward_scores.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    scores.to_csv(OUT_DIR / "walkforward_scores.csv", index=False, encoding="utf-8-sig")
    final = scores[scores.get("isFinalCandidate", False).astype(bool)] if "isFinalCandidate" in scores else scores
    final.to_csv(OUT_DIR / "walkforward_candidates.csv", index=False, encoding="utf-8-sig")

    dates = sorted(scores["date"].astype(str).unique(), reverse=True)
    sectors = sorted(scores["sector"].dropna().astype(str).unique())
    detail_map = {
        str(sector): sorted(part["detailSector"].dropna().astype(str).unique())
        for sector, part in scores.groupby("sector")
    }
    latest = dates[0] if dates else ""
    latest_final = int((scores["date"].astype(str).eq(latest) & scores["isFinalCandidate"].astype(bool)).sum()) if latest else 0
    latest_up = int((scores["date"].astype(str).eq(latest) & scores["isUpCandidate"].astype(bool)).sum()) if latest else 0
    validation_display = validation.copy()
    for col in ["up_auc", "down_auc"]:
        if col in validation_display:
            validation_display[col] = validation_display[col].map(fmt_auc)
    validation_html = validation_display.to_html(index=False, escape=False)
    css = """
:root{--ink:#17202a;--muted:#64748b;--line:#d9e2ec;--bg:#f4f7fb;--panel:#fff;--head:#0f172a;--blue:#1d4ed8;--red:#b91c1c}
*{box-sizing:border-box}body{font-family:'Malgun Gothic',Arial,sans-serif;margin:0;background:var(--bg);color:var(--ink)}header{background:var(--head);color:white;padding:20px 30px}header h1{margin:0 0 6px;font-size:25px}.sub{color:#cbd5e1;font-size:14px}main{padding:20px 30px;max-width:1900px;margin:0 auto}.cards{display:grid;grid-template-columns:repeat(4,minmax(160px,1fr));gap:10px;margin-bottom:14px}.card,section{background:white;border:1px solid var(--line);border-radius:6px}.card{padding:13px}.label{font-size:12px;color:var(--muted);margin-bottom:5px}.value{font-size:21px;font-weight:800}section{padding:14px;margin-bottom:16px;overflow:auto}.tabs{display:flex;gap:6px;margin-bottom:12px}.tab{border:1px solid #bfdbfe;background:#eff6ff;color:#1e40af;padding:8px 11px;border-radius:4px;font-weight:800;cursor:pointer}.tab.active{background:#1d4ed8;color:white}.toolbar{display:grid;grid-template-columns:repeat(7,max-content);gap:9px;align-items:end;margin:10px 0 14px}label{font-size:12px;color:#475569;display:grid;gap:4px}select,input{padding:8px 9px;border:1px solid #cbd5e1;border-radius:4px;background:white}.sort-note{font-size:12px;color:#475569;margin:4px 0 10px}.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(80px,1fr));gap:6px}.sub-grid{display:grid;grid-template-columns:repeat(5,minmax(76px,1fr));gap:6px}.metric{border:1px solid var(--line);border-radius:5px;background:#fafcff;padding:6px}.metric b{display:block;font-size:11px;color:#64748b}.metric span{display:block;font-weight:800}.heat{border-radius:4px;padding:3px 5px;display:inline-block;min-width:34px;text-align:center}table{border-collapse:separate;border-spacing:0;width:100%;font-size:12px}th{background:#eef3f8;position:sticky;top:0;z-index:1;color:#334155;font-weight:800;cursor:pointer}th,td{border-bottom:1px solid var(--line);padding:8px 9px;white-space:nowrap;text-align:right}th.left,td.left{text-align:left}.badge{display:inline-block;min-width:58px;text-align:center;border-radius:4px;padding:3px 6px;font-weight:800;font-size:12px}.GREEN{background:#dbeafe;color:#1d4ed8}.RED{background:#fee2e2;color:#b91c1c}.YELLOW{background:#fef9c3;color:#854d0e}.ORANGE{background:#ffedd5;color:#c2410c}.pos{color:#047857;font-weight:700}.neg{color:#b91c1c;font-weight:700}.pending{color:#64748b}.note{line-height:1.55;color:#475569;font-size:14px}@media(max-width:950px){main{padding:12px}.cards{grid-template-columns:1fr 1fr}.toolbar{grid-template-columns:1fr}.metric-grid,.sub-grid{grid-template-columns:1fr 1fr}}
"""
    html = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>일별 주식 조기경보</title><style>{css}</style></head>
<body><header><h1>일별 주식 조기경보</h1><div class="sub">떡상 조기경보, 떡락 조기경보, 떡락 GREEN 제외 최종 후보를 매 거래일 기준으로 확인</div></header>
<main>
<div class="cards"><div class="card"><div class="label">최신 기준일</div><div class="value">{latest}</div></div><div class="card"><div class="label">최신 최종 후보</div><div class="value">{latest_final:,}</div></div><div class="card"><div class="label">최신 떡상 후보</div><div class="value">{latest_up:,}</div></div><div class="card"><div class="label">신호 일수</div><div class="value">{len(dates):,}</div></div></div>
<section>
<div class="tabs"><button class="tab active" data-mode="final">떡상-떡락 제외</button><button class="tab" data-mode="up">떡상 전체</button><button class="tab" data-mode="down">떡락 RED</button><button class="tab" data-mode="search">종목 검색</button></div>
<div class="note">학습은 각 월의 첫 신호일 기준 126거래일 이전까지 라벨이 확정된 데이터만 사용한다. 신호는 매 거래일 산출한다. 최종 후보는 떡상 상위 5% 중 떡락 위험 등급이 GREEN인 종목만 남긴다.</div>
<div class="toolbar"><label>기준일<select id="date"></select></label><label>섹터<select id="sector"></select></label><label>세부섹터<select id="detail"></select></label><label>기간<select id="horizon"><option value="1m">1개월</option><option value="3m">3개월</option><option value="6m" selected>6개월</option><option value="12m">12개월</option></select></label><label>상위 N<input id="topn" type="number" value="50" min="1" max="500"></label><label>검색<input id="query" placeholder="종목명/티커"></label><label>정렬<select id="sort"><option value="upScore">떡상점수</option><option value="downRisk">떡락위험</option><option value="ticker">티커</option><option value="name">종목명</option><option value="sector">섹터</option><option value="detailSector">세부섹터</option><option value="close">종가</option><option value="upGrade">떡상등급</option><option value="downGrade">떡락등급</option><option value="expRet_1m">예상1M수익</option><option value="actRet_1m">실제1M수익</option><option value="expRet_3m">예상3M수익</option><option value="actRet_3m">실제3M수익</option><option value="expRet_6m">예상6M수익</option><option value="actRet_6m">실제6M수익</option><option value="expRet_12m">예상12M수익</option><option value="actRet_12m">실제12M수익</option><option value="growth_profit">성장·수익성</option><option value="cash_quality">현금흐름 품질</option><option value="valuation">밸류에이션</option><option value="price_volume">가격·거래량</option><option value="risk_overheat">위험·과열</option></select></label><label>방향<button id="dir" type="button" class="tab">내림차순 ▼</button></label></div>
<div class="sort-note" id="sortNote"></div><div id="table"></div>
</section>
<section><h2>Walk-forward 검증</h2>{validation_html}</section>
</main>
<script>
let ROWS=[];
const dates={json.dumps(dates, ensure_ascii=False)}, sectors={json.dumps(sectors, ensure_ascii=False)}, detailMap={json.dumps(detail_map, ensure_ascii=False)};
const subLabels={json.dumps(SUBSCORES, ensure_ascii=False)};
const dateSel=document.getElementById('date'), sectorSel=document.getElementById('sector'), detailSel=document.getElementById('detail'), horizonSel=document.getElementById('horizon'), topN=document.getElementById('topn'), query=document.getElementById('query'), sortSel=document.getElementById('sort'), sortNote=document.getElementById('sortNote'), dirBtn=document.getElementById('dir');
let mode='final', sortAsc=false;
dates.forEach(d=>dateSel.add(new Option(d,d))); ['전체',...sectors].forEach(s=>sectorSel.add(new Option(s,s)));
function refreshDetails(){{const s=sectorSel.value; const ds=s==='전체'?[...new Set(Object.values(detailMap).flat())].sort():(detailMap[s]||[]); detailSel.innerHTML=''; ['전체',...ds].forEach(d=>detailSel.add(new Option(d,d)));}}
function pct(v){{if(!v||v==='미확정')return NaN; return parseFloat(String(v).replace('%',''));}}
function cls(v){{const n=pct(v); return Number.isNaN(n)?'pending':(n<0?'neg':'pos');}}
function num(v){{const n=parseFloat(String(v??'').replace(/,/g,'').replace('%','')); return Number.isNaN(n)?NaN:n;}}
function cmp(a,b,key){{const av=a[key], bv=b[key], an=num(av), bn=num(bv); if(!Number.isNaN(an)&&!Number.isNaN(bn))return an-bn; return String(av??'').localeCompare(String(bv??''),'ko');}}
function heat(v,key){{const raw=num(v), good=key==='risk_overheat'?100-raw:raw; const hue=240-(Math.max(0,Math.min(100,good))*2.4); return `background:hsl(${{hue}} 80% 91%);color:hsl(${{hue}} 74% 28%)`;}}
function metric(r,h){{return '<div class="metric-grid"><div class="metric"><b>예상수익</b><span class="'+cls(r['expRet_'+h])+'">'+r['expRet_'+h]+'</span></div><div class="metric"><b>예상종가</b><span>'+r['expClose_'+h]+'</span></div><div class="metric"><b>실제수익</b><span class="'+cls(r['actRet_'+h])+'">'+r['actRet_'+h]+'</span></div><div class="metric"><b>실제종가</b><span>'+r['actClose_'+h]+'</span></div></div>';}}
function subs(r){{return '<div class="sub-grid">'+Object.entries(subLabels).map(([k,label])=>'<div class="metric"><b>'+label+'</b><span class="heat" style="'+heat(r[k],k)+'">'+r[k]+'</span></div>').join('')+'</div>';}}
function modeRows(rows){{if(mode==='final')return rows.filter(r=>r.isFinalCandidate); if(mode==='up')return rows.filter(r=>r.isUpCandidate); if(mode==='down')return rows.filter(r=>r.isDownRed); return rows;}}
function render(){{const d=dateSel.value,s=sectorSel.value,det=detailSel.value,h=horizonSel.value,n=parseInt(topN.value||50,10),q=query.value.trim().toLowerCase(); let rows=ROWS.filter(r=>r.date===d&&(s==='전체'||r.sector===s)&&(det==='전체'||r.detailSector===det)); rows=modeRows(rows); if(q)rows=rows.filter(r=>String(r.name).toLowerCase().includes(q)||String(r.ticker).includes(q)); const sortKey=sortSel.value; rows.sort((a,b)=>(sortAsc?1:-1)*cmp(a,b,sortKey)); rows=rows.slice(0,n); const arrow=sortAsc?'오름차순 ▲':'내림차순 ▼'; dirBtn.textContent=arrow; sortNote.textContent='정렬 기준: '+sortSel.options[sortSel.selectedIndex].text+' '+arrow+' / 표시 '+rows.length+'개'; let html='<table><thead><tr><th>순위</th><th class="left">티커</th><th class="left">종목명</th><th class="left">섹터</th><th class="left">세부섹터</th><th>종가</th><th>떡상점수</th><th>떡상등급</th><th>떡락위험</th><th>떡락등급</th><th>선택기간</th><th>하위스코어</th></tr></thead><tbody>'; rows.forEach((r,i)=>html+='<tr><td>'+(i+1)+'</td><td class="left">'+r.ticker+'</td><td class="left">'+r.name+'</td><td class="left">'+r.sector+'</td><td class="left">'+r.detailSector+'</td><td>'+r.close+'</td><td>'+r.upScore+'</td><td><span class="badge '+r.upGrade+'">'+r.upGrade+'</span></td><td>'+r.downRisk+'</td><td><span class="badge '+r.downGrade+'">'+r.downGrade+'</span></td><td>'+metric(r,h)+'</td><td>'+subs(r)+'</td></tr>'); document.getElementById('table').innerHTML=html+'</tbody></table>';}}
document.querySelectorAll('.tab').forEach(btn=>btn.onclick=()=>{{document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active')); btn.classList.add('active'); mode=btn.dataset.mode; render();}});
async function init(){{document.getElementById('table').innerHTML='<div class="note">일별 스코어 데이터를 불러오는 중...</div>'; ROWS=await fetch('walkforward_scores.json').then(r=>r.json()); refreshDetails(); sectorSel.onchange=()=>{{refreshDetails();render();}}; [dateSel,detailSel,horizonSel,topN,query,sortSel].forEach(x=>x.addEventListener('input',render)); sortSel.addEventListener('change',render); dirBtn.onclick=()=>{{sortAsc=!sortAsc;render();}}; render();}}
init();
</script></body></html>"""
    (OUT_DIR / "dashboard.html").write_text(html, encoding="utf-8")
    print(OUT_DIR / "dashboard.html")


if __name__ == "__main__":
    main()
