'use strict';
const data = JSON.parse(document.getElementById('investment-data').textContent);
const $ = id => document.getElementById(id);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct = v => Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : '—';
const pp = v => Number.isFinite(v) ? `${(v * 100).toFixed(1)}%p` : '—';
const num = v => Number.isFinite(v) ? v.toFixed(3) : '—';
const marketName = m => m === 'kr' ? '한국' : '미국';
let page = 1, selected = null;
const pageSize = 40;
const params = new URLSearchParams(location.search);
for (const id of ['market','fx','sort','opinion']) if ([...$(id).options].some(o => o.value === params.get(id))) $(id).value = params.get(id);
$('query').value = params.get('q') || '';
$('created').textContent = `연구 생성 ${data.created_at.slice(0,16).replace('T',' ')} UTC`;
function scenario(row) { return row.scenarios[String(Number($('fx').value))] || row.scenarios[Number($('fx').value).toFixed(1)]; }
function renderMarkets() {
  $('markets').innerHTML = ['kr','us'].map(m => {
    const d = data.markets[m].latest, rows = data.rows.filter(r=>r.market===m);
    const risk = rows.filter(r=>r.down>=.4).length / Math.max(1,rows.length);
    return `<article class="investment-market"><span class="eyebrow">${m.toUpperCase()} MARKET</span><h2>${marketName(m)} 시장</h2><p>가격·신호 기준 ${esc(d.asof)} · 모델 결과는 이후 생성한 연구치</p><dl><div><dt>최근 60거래일 시장 추세</dt><dd>${pct(rows[0]?.market_trend_60)}</dd></div><div><dt>상대 하락 확률 ≥40%</dt><dd>${pct(risk)}</dd></div><div><dt>재무 근거 보유</dt><dd>${d.financial_covered} / ${d.scored}</dd></div></dl><p>시장 추세는 활성 종목 일별 동일가중 가격지수의 과거 변화입니다. 공인 지수 수익률이나 미래 예측치가 아닙니다. 40%는 기초 사건 비중 20%의 두 배인 검토용 기준입니다.</p></article>`;
  }).join('');
}
function render() {
  const q = $('query').value.trim().toLocaleLowerCase();
  let rows = data.rows.filter(r=>($('market').value==='all'||r.market===$('market').value) && ($('opinion').value==='all'||r.opinion.status===$('opinion').value) && `${r.ticker} ${r.name}`.toLocaleLowerCase().includes(q));
  const key=$('sort').value;
  const value = r => key === 'score' ? scenario(r)?.score : r[key];
  rows.sort((a,b)=>(value(b) ?? -Infinity)-(value(a) ?? -Infinity)||a.market.localeCompare(b.market)||a.ticker.localeCompare(b.ticker));
  const pages = Math.max(1,Math.ceil(rows.length/pageSize)); page=Math.min(page,pages);
  $('counts').textContent=`${rows.length.toLocaleString()}개 종목`;
  $('scenario').textContent=`환율 가정: USD/KRW ${Number($('fx').value)>0?'+':''}${pct(Number($('fx').value))} · 한국 점수는 환율 가정에 영향받지 않습니다. 비용은 양국 공통 0.3%p 가정이며 실제 수수료·세금은 다를 수 있습니다.`;
  $('rows').innerHTML=rows.slice((page-1)*pageSize,page*pageSize).map(r=>{
    const s=scenario(r);
    return `<tr><td><button class="stock-button" data-market="${esc(r.market)}" data-ticker="${esc(r.ticker)}">${esc(r.name)}</button><span class="stock-meta">${r.market.toUpperCase()} · ${esc(r.ticker)} · ${esc(r.asof)}</span></td><td><span class="score-pill">${s?.score.toFixed(1) ?? '—'}</span></td><td>${pct(s?.median_krw)}</td><td>${pct(s?.q10_krw)}</td><td>${pct(r.up)}</td><td>${pct(r.down)}</td><td class="opinion-${esc(r.opinion.status)}">${esc(r.opinion.label)}</td></tr>`;
  }).join('') || '<tr><td colspan="7" class="investment-empty">조건에 맞는 종목이 없습니다.</td></tr>';
  $('page').textContent=`${page} / ${pages}`; $('previous').disabled=page===1; $('next').disabled=page===pages;
  const state=new URLSearchParams({market:$('market').value,fx:$('fx').value,sort:$('sort').value,opinion:$('opinion').value,q:$('query').value});
  try { history.replaceState(null,'',`${location.pathname}?${state}`); } catch (_) {}
  if(selected) detail(selected);
}
const labels={fin_revenue_growth:'매출 증가율 (TTM 전년 대비)',fin_operating_margin:'영업이익률 (TTM)',fin_cfo_assets:'영업현금흐름 / 자산',fin_cfo_after_ppe_assets:'설비투자 후 영업현금 / 자산',fin_liabilities_assets:'부채 / 자산',fin_interest_coverage:'이자보상배율'};
function detail(row) {
  selected=row; const s=scenario(row), o=row.opinion;
  $('detail').hidden=false;
  $('detail').innerHTML=`<h3>${esc(row.name)} <span class="muted">${esc(row.ticker)} · ${marketName(row.market)}</span></h3><p>시장 내 상대 상승 ${pct(row.up)} · 상대 하락 ${pct(row.down)}<br>현지 통화 중위 전망 ${pct(row.q50)} → 선택한 환율 가정의 원화 중위 전망 ${pct(s?.median_krw)}<br>과거 60거래일 시장 대비 수익률 차이 ${pct(row.relative_trend_60)} · 미래 상대수익 예측치가 아닙니다.</p><h4>${esc(o.label)} · 모델과 분리된 재무 의견</h4><p>${o.reasons.map(esc).join('<br>')}</p><div class="evidence-grid">${o.evidence.map(e=>`<article><h4>${esc(e.period_end)} 기준 재무</h4><p>이용 가능일 ${esc(e.available_at)}<br>공시 식별자 ${esc(e.filing_id)}</p><dl>${Object.entries(e.metrics).map(([k,v])=>`<dt>${esc(labels[k]||k)}</dt><dd>${k==='fin_interest_coverage'?(Number.isFinite(v)?v.toFixed(2)+'배':'—'):pct(v)}</dd>`).join('')}</dl></article>`).join('')}</div><p class="muted">재무 지표로 본업의 일부를 점검한 보조 의견입니다. 적정 가치·사업부 실적·수주·감사 의견을 확인한 매매 지시가 아닙니다.</p><a href="https://jaesung0804.github.io/st_dashboard/lgbm_warning_dashboard_macro_${row.market}_latest/stock.html?ticker=${encodeURIComponent(row.ticker)}" target="_blank" rel="noopener">기존 종목 기록 보기 ↗</a>`;
}
function validation() {
  const recent = ['kr','us'].map(m=>({market:m,fold:data.markets[m].folds.at(-1)}));
  $('evidence-status').innerHTML=`<h2>통합 점수의 운영 적용은 보류합니다</h2><p>수익률 전망의 정확도와 환율 실측 성과 검증이 먼저입니다. 아래 점수는 계산 구조를 검토하기 위한 연구치이며, 현재 국가 간 우열을 확정하는 근거로 쓰지 않습니다.</p><div>${recent.map(({market,fold})=>`<p><strong>${marketName(market)} · 최근 검증 ${esc(fold.boundary.slice(0,7))}</strong><br>수익률 중위 절대 오차 ${pp(fold.holdout.return_forecast.median_absolute_error)} / 단순 상수 기준 ${pp(fold.holdout.return_forecast.constant_median_absolute_error)}<br>하위 10% 전망보다 실제 수익률이 낮았던 비율 ${pct(fold.holdout.return_forecast.q10_observed_fraction)} (보정 목표 10%)</p>`).join('')}</div>`;
  $('validation-tables').innerHTML=['kr','us'].map(m=>`<article class="investment-market"><h3>${marketName(m)} · 독립 기간 검증</h3><div class="validation-scroll"><table class="validation-table"><thead><tr><th>시작 분기</th><th>하락 AUC</th><th>하락 적중 배수</th><th>상승 AUC</th><th>상승 적중 배수</th></tr></thead><tbody>${data.markets[m].folds.map(f=>`<tr><td>${esc(f.boundary.slice(0,7))}</td><td>${num(f.holdout.down.within_date.mean_date_auc)}</td><td>${num(f.holdout.down.within_date.mean_date_top_decile_lift)}</td><td>${num(f.holdout.up.within_date.mean_date_auc)}</td><td>${num(f.holdout.up.within_date.mean_date_top_decile_lift)}</td></tr>`).join('')}</tbody></table></div><details><summary>수익률 전망 오차·구간 검증</summary>${data.markets[m].folds.map(f=>`<p>${esc(f.boundary.slice(0,7))}: 중위 절대 오차 ${pp(f.holdout.return_forecast.median_absolute_error)} / 상수 기준 ${pp(f.holdout.return_forecast.constant_median_absolute_error)}<br>하위 10% 실제 포함률 ${pct(f.holdout.return_forecast.q10_observed_fraction)} · 상위 90% 실제 포함률 ${pct(f.holdout.return_forecast.q90_observed_fraction)}</p>`).join('')}</details></article>`).join('');
  $('contract').textContent=JSON.stringify({contract:data.contract,rules:data.rules,source_snapshot:data.source.snapshot_id,code_commit:data.code_commit,latest_models:Object.fromEntries(['kr','us'].map(m=>[m,data.markets[m].latest.training]))},null,2);
}
for(const id of ['market','fx','sort','opinion']) $(id).addEventListener('change',()=>{page=1;render();});
$('query').addEventListener('input',()=>{page=1;render();});
$('previous').addEventListener('click',()=>{page--;render();}); $('next').addEventListener('click',()=>{page++;render();});
$('rows').addEventListener('click',event=>{const button=event.target.closest('[data-ticker]');if(!button)return;const r=data.rows.find(r=>r.market===button.dataset.market&&r.ticker===button.dataset.ticker);if(r){detail(r);$('detail').scrollIntoView({behavior:'smooth',block:'start'});}});
$('themeToggle').addEventListener('click',()=>{const dark=document.documentElement.dataset.theme!=='dark';document.documentElement.dataset.theme=dark?'dark':'';$('themeToggle').textContent=dark?'라이트모드':'다크모드';$('themeToggle').setAttribute('aria-pressed',String(dark));});
renderMarkets();validation();render();
