from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


WF_DIR = Path("outputs/walkforward_warning")
OUT_DIR = Path("outputs/lgbm_warning_dashboard")
SUBSCORES = ["성장·수익성", "현금흐름·품질", "밸류에이션", "가격·거래량", "위험·과열"]


def records(frame: pd.DataFrame) -> list[dict[str, object]]:
    data = frame.copy()
    rename = {"up_lgbm_prob": "upProb", "down_lgbm_prob": "downProb"}
    data = data.rename(columns=rename)
    data["rankScore"] = pd.to_numeric(data["upScore"], errors="coerce")
    keep = [
        "date", "ticker", "name", "sector", "detailSector", "close",
        "upScore", "upProb", "upGrade", "downRisk", "downProb", "downGrade", "rankScore",
        "expRet_1m", "expClose_1m", "actRet_1m", "actClose_1m",
        "expRet_3m", "expClose_3m", "actRet_3m", "actClose_3m",
        "expRet_6m", "expClose_6m", "actRet_6m", "actClose_6m",
        "expRet_12m", "expClose_12m", "actRet_12m", "actClose_12m",
        *SUBSCORES,
    ]
    for col in keep:
        if col not in data:
            data[col] = ""
    return data[keep].fillna("").to_dict("records")


def fmt_auc(value: object) -> str:
    return "" if pd.isna(value) else f"{float(value):.3f}"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candidates = pd.read_csv(WF_DIR / "walkforward_candidates.csv", dtype={"ticker": str})
    validation = pd.read_csv(WF_DIR / "walkforward_validation.csv")
    rows = records(candidates)
    (OUT_DIR / "walkforward_candidates.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    candidates.to_csv(OUT_DIR / "walkforward_candidates.csv", index=False, encoding="utf-8-sig")
    dates = sorted(candidates["date"].astype(str).unique(), reverse=True)
    sectors = sorted(candidates["sector"].dropna().astype(str).unique())
    detail_map = {
        str(sector): sorted(part["detailSector"].dropna().astype(str).unique())
        for sector, part in candidates.groupby("sector")
    }
    latest = dates[0] if dates else ""
    latest_count = int((candidates["date"].astype(str) == latest).sum()) if latest else 0
    validation_display = validation.copy()
    validation_display["up_auc"] = validation_display["up_auc"].map(fmt_auc)
    validation_display["down_auc"] = validation_display["down_auc"].map(fmt_auc)
    validation_html = validation_display.to_html(index=False, escape=False)
    css = """
:root{--ink:#17202a;--muted:#64748b;--line:#d9e2ec;--bg:#f4f7fb;--panel:#fff;--head:#0f172a;--green:#047857;--red:#b91c1c}
*{box-sizing:border-box}body{font-family:'Malgun Gothic',Arial,sans-serif;margin:0;background:var(--bg);color:var(--ink)}header{background:var(--head);color:white;padding:22px 32px}header h1{margin:0 0 6px;font-size:26px}.sub{color:#cbd5e1;font-size:14px}main{padding:22px 32px;max-width:1800px;margin:0 auto}section{background:white;border:1px solid var(--line);border-radius:6px;padding:16px;margin-bottom:18px;overflow:auto}.cards{display:grid;grid-template-columns:repeat(4,minmax(180px,1fr));gap:12px;margin-bottom:18px}.card{background:white;border:1px solid var(--line);border-radius:6px;padding:14px}.label{font-size:12px;color:var(--muted);margin-bottom:6px}.value{font-size:22px;font-weight:800}.toolbar{display:grid;grid-template-columns:repeat(5,max-content);gap:10px;align-items:end;margin:12px 0 16px}label{font-size:12px;color:#475569;display:grid;gap:4px}select,input{padding:8px 9px;border:1px solid #cbd5e1;border-radius:4px;background:white}.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(82px,1fr));gap:6px}.sub-grid{display:grid;grid-template-columns:repeat(5,minmax(82px,1fr));gap:6px}.metric{border:1px solid var(--line);border-radius:5px;background:#fafcff;padding:6px}.metric b{display:block;font-size:11px;color:#64748b}.metric span{display:block;font-weight:800}.heat{border-radius:4px;padding:3px 5px;display:inline-block;min-width:34px;text-align:center}table{border-collapse:separate;border-spacing:0;width:100%;font-size:12px}th{background:#eef3f8;position:sticky;top:0;z-index:1;color:#334155;font-weight:800}th,td{border-bottom:1px solid var(--line);padding:8px 9px;white-space:nowrap;text-align:right}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2),th:nth-child(3),td:nth-child(3),th:nth-child(4),td:nth-child(4),th:nth-child(5),td:nth-child(5){text-align:left}.badge{display:inline-block;min-width:58px;text-align:center;border-radius:4px;padding:3px 6px;font-weight:800;font-size:12px}.GREEN{background:#dcfce7;color:var(--green)}.RED{background:#fee2e2;color:var(--red)}.YELLOW{background:#fef9c3;color:#854d0e}.ORANGE{background:#ffedd5;color:#c2410c}.pos{color:#047857;font-weight:700}.neg{color:#b91c1c;font-weight:700}.pending{color:#64748b}.note{line-height:1.6;color:#475569;font-size:14px}@media(max-width:900px){main{padding:14px}.cards{grid-template-columns:1fr 1fr}.toolbar{grid-template-columns:1fr}.metric-grid,.sub-grid{grid-template-columns:1fr 1fr}}
"""
    html = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>Walk-forward 조기경보</title><style>{css}</style></head>
<body><header><h1>Walk-forward 조기경보</h1><div class="sub">각 기준일마다 미래 라벨을 보지 않고 재학습한 후보만 표시</div></header>
<main>
<div class="cards"><div class="card"><div class="label">최신 기준일</div><div class="value">{latest}</div></div><div class="card"><div class="label">최신 후보</div><div class="value">{latest_count:,}</div></div><div class="card"><div class="label">검증 월수</div><div class="value">{len(dates):,}</div></div><div class="card"><div class="label">방식</div><div class="value" style="font-size:16px">Walk-forward</div></div></div>
<section><h2>후보</h2><div class="note">기준일 - 126거래일 이전 라벨만 학습에 사용했다. 후보는 떡상 상위 5% 중 떡락위험 GREEN만 통과한 종목이다.</div><div class="toolbar"><label>기준일<select id="date"></select></label><label>섹터<select id="sector"></select></label><label>세부섹터<select id="detail"></select></label><label>기간<select id="horizon"><option value="1m">1개월</option><option value="3m">3개월</option><option value="6m" selected>6개월</option><option value="12m">12개월</option></select></label><label>상위 N<input id="topn" type="number" value="50" min="1" max="200"></label></div><div id="table"></div></section>
<section><h2>Walk-forward 검증</h2>{validation_html}</section>
</main>
<script>
const ROWS={json.dumps(rows, ensure_ascii=False)};
const dates={json.dumps(dates, ensure_ascii=False)}, sectors={json.dumps(sectors, ensure_ascii=False)}, detailMap={json.dumps(detail_map, ensure_ascii=False)};
const dateSel=document.getElementById('date'), sectorSel=document.getElementById('sector'), detailSel=document.getElementById('detail'), horizonSel=document.getElementById('horizon'), topN=document.getElementById('topn');
dates.forEach(d=>dateSel.add(new Option(d,d))); ['전체',...sectors].forEach(s=>sectorSel.add(new Option(s,s)));
function refreshDetails(){{const s=sectorSel.value; const ds=s==='전체'?[...new Set(Object.values(detailMap).flat())].sort():(detailMap[s]||[]); detailSel.innerHTML=''; ['전체',...ds].forEach(d=>detailSel.add(new Option(d,d)));}}
refreshDetails();
function pct(v){{if(!v||v==='미확정')return NaN; return parseFloat(String(v).replace('%',''));}}
function cls(v){{const n=pct(v); return Number.isNaN(n)?'pending':(n<0?'neg':'pos');}}
function heat(v,k){{const raw=parseFloat(v||0), good=k==='위험·과열'?100-raw:raw, hue=240-(Math.max(0,Math.min(100,good))*2.4);return `background:hsl(${{hue}} 78% 90%);color:hsl(${{hue}} 72% 28%)`;}}
function metric(r,h){{return '<div class="metric-grid"><div class="metric"><b>예상수익</b><span class="'+cls(r['expRet_'+h])+'">'+r['expRet_'+h]+'</span></div><div class="metric"><b>예상종가</b><span>'+r['expClose_'+h]+'</span></div><div class="metric"><b>실제수익</b><span class="'+cls(r['actRet_'+h])+'">'+r['actRet_'+h]+'</span></div><div class="metric"><b>실제종가</b><span>'+r['actClose_'+h]+'</span></div></div>';}}
function subs(r){{return '<div class="sub-grid">'+{json.dumps(SUBSCORES, ensure_ascii=False)}.map(k=>'<div class="metric"><b>'+k+'</b><span class="heat" style="'+heat(r[k],k)+'">'+r[k]+'</span></div>').join('')+'</div>';}}
function render(){{const d=dateSel.value,s=sectorSel.value,det=detailSel.value,h=horizonSel.value,n=parseInt(topN.value||50,10); const rows=ROWS.filter(r=>r.date===d&&(s==='전체'||r.sector===s)&&(det==='전체'||r.detailSector===det)).sort((a,b)=>parseFloat(b.upScore)-parseFloat(a.upScore)).slice(0,n); let html='<table><thead><tr><th>순위</th><th>티커</th><th>종목명</th><th>섹터</th><th>세부섹터</th><th>종가</th><th>떡상점수</th><th>떡락위험</th><th>선택기간</th><th>하위스코어</th></tr></thead><tbody>'; rows.forEach((r,i)=>html+='<tr><td>'+(i+1)+'</td><td>'+r.ticker+'</td><td>'+r.name+'</td><td>'+r.sector+'</td><td>'+r.detailSector+'</td><td>'+r.close+'</td><td>'+r.upScore+'</td><td>'+r.downRisk+'</td><td>'+metric(r,h)+'</td><td>'+subs(r)+'</td></tr>'); document.getElementById('table').innerHTML=html+'</tbody></table>';}}
sectorSel.onchange=()=>{{refreshDetails();render();}}; [dateSel,detailSel,horizonSel,topN].forEach(x=>x.addEventListener('input',render)); render();
</script></body></html>"""
    (OUT_DIR / "dashboard.html").write_text(html, encoding="utf-8")
    print(OUT_DIR / "dashboard.html")


if __name__ == "__main__":
    main()
