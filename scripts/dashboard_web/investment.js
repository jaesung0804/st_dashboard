'use strict';
const data = JSON.parse(document.getElementById('investment-data').textContent);
const $ = id => document.getElementById(id);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct = v => Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : '—';
const pp = v => Number.isFinite(v) ? `${(v * 100).toFixed(1)}%p` : '—';
const num = v => Number.isFinite(v) ? v.toFixed(3) : '—';
const marketName = m => m === 'kr' ? '한국' : '미국';
const rowsByMarket = Object.fromEntries(['kr','us'].map(m=>[m,data.rows.filter(r=>r.market===m)]));
let page = 1, selected = null;
const pageSize = 40;
const params = new URLSearchParams(location.search);
for (const id of ['market','fx','sort','opinion','coverage']) if ([...$(id).options].some(o => o.value === params.get(id))) $(id).value = params.get(id);
$('query').value = params.get('q') || '';
$('created').textContent = `실험 생성 ${data.created_at.slice(0,16).replace('T',' ')} UTC`;
function scenario(row) { return row.scenarios?.[String(Number($('fx').value))] || row.scenarios?.[Number($('fx').value).toFixed(1)]; }
function renderMarkets() {
  $('markets').innerHTML = ['kr','us'].map(m => {
    const d = data.markets[m].latest, rows = rowsByMarket[m].filter(r=>r.scored);
    return `<article class="investment-market"><span class="eyebrow">${m.toUpperCase()} MARKET</span><h2>${marketName(m)} 시장</h2><p>가격·신호 기준 ${esc(d.asof)} · 이후 생성한 고정 실험 결과 · 일일 갱신 아님</p><dl><div><dt>최근 60거래일 시장 추세</dt><dd>${pct(rows[0]?.market_trend_60)}</dd></div><div><dt>하위 30% 경보 비중</dt><dd>${pct(d.warning_count/d.scored)}</dd></div><div><dt>모델 평가 / 보관 목록</dt><dd>${d.scored.toLocaleString()} / ${d.listed.toLocaleString()}</dd></div></dl><p>경보 문턱 ${pct(d.training.down_warning_threshold)}: 보정 기간의 하위 30% 사건을 80% 이상 포착하도록 정했습니다. 실제 검증 포착률은 아래 표에 있습니다.<br>재무 근거 ${d.financial_covered}개 · 시장 추세는 활성 종목 동일가중 가격지수의 과거 변화입니다.</p></article>`;
  }).join('');
}
function render() {
  const allMarkets=$('market').value==='all';
  $('sort').querySelector('option[value="market_score"]').disabled=allMarkets;
  if(allMarkets && $('sort').value==='market_score') $('sort').value='score';
  const q = $('query').value.trim().toLocaleLowerCase(), coverage=$('coverage').value;
  let rows = data.rows.filter(r=>($('market').value==='all'||r.market===$('market').value)
    && ($('opinion').value==='all'||r.opinion.status===$('opinion').value)
    && (coverage==='all'||coverage==='scored'&&r.scored||coverage==='unscored'&&!r.scored||coverage==='warning'&&r.warning)
    && `${r.ticker} ${r.name}`.toLocaleLowerCase().includes(q));
  const key=$('sort').value;
  const value = r => key === 'score' ? scenario(r)?.score : r[key];
  rows.sort((a,b)=>(value(b) ?? -Infinity)-(value(a) ?? -Infinity)||a.market.localeCompare(b.market)||a.ticker.localeCompare(b.ticker));
  const pages = Math.max(1,Math.ceil(rows.length/pageSize)); page=Math.min(page,pages);
  $('counts').textContent=`${rows.length.toLocaleString()}개 · 평가 ${rows.filter(r=>r.scored).length.toLocaleString()}개`;
  $('scenario').textContent=`시장 점수는 해당 시장 안의 백분위입니다. 국가 간 비교에는 실험 통합 점수를 선택하세요. 환율 가정 USD/KRW ${Number($('fx').value)>0?'+':''}${pct(Number($('fx').value))} · 비용 0.3%p 가정 · 세금 미반영.`;
  $('rows').innerHTML=rows.slice((page-1)*pageSize,page*pageSize).map(r=>{
    const s=scenario(r);
    return `<tr><td><button class="stock-button" data-market="${esc(r.market)}" data-ticker="${esc(r.ticker)}">${esc(r.name)}</button><span class="stock-meta">${r.market.toUpperCase()} · ${esc(r.ticker)} · ${esc(r.asof)}${r.scored?'':`<br>${esc(r.score_reason)}`}</span></td><td><span class="score-pill">${Number.isFinite(r.market_score)?r.market_score.toFixed(1):'미평가'}</span></td><td>${pct(r.up5)}</td><td>${pct(r.up10)}</td><td>${pct(r.down30)}${r.warning?'<span class="stock-meta opinion-sell_review">경보</span>':''}</td><td>${Number.isFinite(s?.score)?s.score.toFixed(1):'—'}</td><td class="opinion-${esc(r.opinion.status)}">${esc(r.opinion.label)}</td></tr>`;
  }).join('') || '<tr><td colspan="7" class="investment-empty">조건에 맞는 종목이 없습니다.</td></tr>';
  $('page').textContent=`${page} / ${pages}`; $('previous').disabled=page===1; $('next').disabled=page===pages;
  const state=new URLSearchParams({market:$('market').value,fx:$('fx').value,sort:$('sort').value,opinion:$('opinion').value,coverage,q:$('query').value});
  try { history.replaceState(null,'',`${location.pathname}?${state}`); } catch (_) {}
  if(selected && !rows.includes(selected)) { selected=null; $('detail').hidden=true; }
  if(selected) detail(selected);
}
const labels={fin_revenue_growth:'매출 증가율 (TTM 전년 대비)',fin_operating_margin:'영업이익률 (TTM)',fin_cfo_assets:'영업현금흐름 / 자산',fin_cfo_after_ppe_assets:'설비투자 후 영업현금 / 자산',fin_liabilities_assets:'부채 / 자산',fin_interest_coverage:'이자보상배율'};
function detail(row) {
  selected=row; const s=scenario(row), o=row.opinion;
  $('detail').hidden=false;
  const href=row.scored?`stock.html?ticker=${encodeURIComponent(row.ticker)}`:`dashboard.html?mode=all&q=${encodeURIComponent(row.ticker)}`;
  $('detail').innerHTML=`<h3>${esc(row.name)} <span class="muted">${esc(row.ticker)} · ${marketName(row.market)}</span></h3><p>${row.scored?`시장 점수 ${row.market_score.toFixed(1)} · 시장 내 상대 순위이며 수익 확률이 아닙니다.<br>상위 5% 확률 ${pct(row.up5)} · 상위 10% 확률 ${pct(row.up10)} · 하위 30% 확률 ${pct(row.down30)}<br>전체 순위 모델이 추정한 미래 순위 위치 ${pct(row.expected_percentile)} · 중간권의 순위는 불확실합니다.`:`미평가: ${esc(row.score_reason)}`}<br>현지 통화 중위 전망 ${pct(row.q50)} → 원화 중위 전망 ${pct(s?.median_krw)} · 원화 하위 10% 전망 ${pct(s?.q10_krw)}<br>과거 60거래일 시장 대비 수익률 차이 ${pp(row.relative_trend_60)} · 미래 상대수익 예측치가 아닙니다.</p><p class="muted">상위 5%·10% 모델을 독립 학습해 확률이 일관된 순서를 보장하지는 않습니다. 통합 점수의 수익률 모형은 앞선 실험을 재사용하며, 최근 검증에서 오차가 커 실험치로 제공합니다.</p><h4>${esc(o.label)} · 모델과 분리된 재무 의견</h4><p>${o.reasons.map(esc).join('<br>')}</p><div class="evidence-grid">${o.evidence.map(e=>`<article><h4>${esc(e.period_end)} 기준 재무</h4><p>이용 가능일 ${esc(e.available_at)}<br>공시 식별자 ${esc(e.filing_id)}</p><dl>${Object.entries(e.metrics).map(([k,v])=>`<dt>${esc(labels[k]||k)}</dt><dd>${k==='fin_interest_coverage'?(Number.isFinite(v)?v.toFixed(2)+'배':'—'):pct(v)}</dd>`).join('')}</dl></article>`).join('')}</div><p class="muted">재무 지표로 본업의 일부를 점검한 보조 의견입니다. 적정 가치·사업부 실적·수주·감사 의견을 확인한 매매 지시가 아닙니다.</p><a href="https://jaesung0804.github.io/st_dashboard/lgbm_warning_dashboard_macro_${row.market}_latest/${href}" target="_blank" rel="noopener">기존 종목 기록 보기 ↗</a>`;
}
function selectionTables() {
  const models={up5:'상위 5% 모델',up10:'상위 10% 모델',rank:'전체 순위 모델',old_up20:'기존 상위 20% 모델'};
  $('selection-tables').innerHTML=['kr','us'].map(m=>{
    const folds=data.markets[m].folds;
    const mean=(key,field)=>{const vals=folds.map(f=>f.holdout.selection[key]?.means[field]).filter(Number.isFinite);return vals.length?vals.reduce((a,b)=>a+b,0)/vals.length:null;};
    return `<article class="investment-market"><h3>${marketName(m)} · 네 검증 분기 평균</h3><p>상위 5% 모델 후보 중 실제 하위 30% 도달 ${pct(mean('up5-top5-all','down30_fraction'))}. 경보를 모두 제외하면 예산의 ${pct(mean('up5-top5-filtered','invested_fraction'))}만 투자됩니다. 급등 후보와 급락 위험이 크게 겹치므로 경보를 자동 제외 규칙으로 쓰지 않습니다.</p><div class="validation-scroll"><table class="validation-table"><thead><tr><th>선별 모델</th><th>경보 제외</th><th>투입 비중</th><th>수익률</th><th>시장 평균 대비</th><th>손익비*</th></tr></thead><tbody>${Object.entries(models).flatMap(([model,label])=>(model==='old_up20'?[false]:[false,true]).map(filtered=>{const key=`${model}-top5-${filtered?'filtered':'all'}`;return `<tr><td>${label}</td><td>${filtered?'적용':'미적용'}</td><td>${pct(mean(key,'invested_fraction'))}</td><td>${pct(mean(key,'net_return_with_cash'))}</td><td>${pp(mean(key,'excess_vs_pool_mean'))}</td><td>${num(mean(key,'payoff_ratio'))}</td></tr>`;})).join('')}</tbody></table></div><p>각 분기를 같은 비중으로 평균했습니다. *손익비는 각 날짜의 평균 이익 ÷ 평균 손실 절댓값이며, 이익·손실 양쪽이 있는 날짜에서만 계산됩니다. 적은 잔여 종목과 작은 손실 때문에 큰 값이 나올 수 있어 수익률·투입 비중을 함께 보세요. 빈 예산은 수익 0인 현금으로 둡니다.</p><details><summary>분기별 시장 평균 대비 성과 · 경보 제외 전</summary><div class="validation-scroll"><table class="validation-table"><thead><tr><th>분기</th><th>상위5 모델</th><th>상위10 모델</th><th>전체 순위</th></tr></thead><tbody>${folds.map(f=>`<tr><td>${esc(f.boundary.slice(0,7))}</td>${['up5','up10','rank'].map(k=>`<td>${pp(f.holdout.selection[`${k}-top5-all`].means.excess_vs_pool_mean)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details></article>`;
  }).join('');
}
function validation() {
  const recent = ['kr','us'].map(m=>({market:m,fold:data.markets[m].folds.at(-1)}));
  $('evidence-status').innerHTML=`<h2>통합 점수와 재무 의견을 실험 기능으로 제공합니다</h2><p>점수의 국가 간 우열과 매매 성과는 아직 검증되지 않았습니다. 특히 최근 수익률 전망은 단순 상수 기준보다 오차가 컸습니다. 환율은 ±5% 가정이며 실측 환율 예측 모델이 아닙니다.</p><div>${recent.map(({market,fold})=>`<p><strong>${marketName(market)} · 최근 검증 ${esc(fold.boundary.slice(0,7))}</strong><br>수익률 중위 절대 오차 ${pp(fold.holdout.return_forecast.median_absolute_error)} / 상수 기준 ${pp(fold.holdout.return_forecast.constant_median_absolute_error)}<br>하위 10% 전망 아래 실제 결과 ${pct(fold.holdout.return_forecast.q10_observed_fraction)} (보정 목표 10%)</p>`).join('')}</div>`;
  $('validation-tables').innerHTML=['kr','us'].map(m=>`<article class="investment-market"><h3>${marketName(m)} · 상대 경보와 전체 정렬</h3><div class="validation-scroll"><table class="validation-table"><thead><tr><th>분기</th><th>상위5 AUC</th><th>상위10 AUC</th><th>하위30 AUC</th><th>경보 포착률</th><th>경보 비중</th></tr></thead><tbody>${data.markets[m].folds.map(f=>`<tr><td>${esc(f.boundary.slice(0,7))}</td><td>${num(f.holdout.up5.within_date.mean_date_auc)}</td><td>${num(f.holdout.up10.within_date.mean_date_auc)}</td><td>${num(f.holdout.down30.within_date.mean_date_auc)}</td><td>${pct(f.holdout.down_warning.recall)}</td><td>${pct(f.holdout.down_warning.alert_fraction)}</td></tr>`).join('')}</tbody></table></div><details><summary>순위 구분력·경보 정확도·확률의 일관성</summary>${data.markets[m].folds.map(f=>`<p>${esc(f.boundary.slice(0,7))}: 전체 순위 상관 ${num(f.holdout.rank.mean_date_rank_ic)} / 예측 중간권 60% 순위 상관 ${num(f.holdout.rank.mean_date_middle_rank_ic)}<br>경보 적중률 ${pct(f.holdout.down_warning.precision)} · 비사건 오경보율 ${pct(f.holdout.down_warning.false_positive_rate)}<br>독립 모델의 상위5 확률이 상위10 확률을 넘는 비율 ${pct(f.holdout.nesting_violation_fraction)}</p>`).join('')}</details></article>`).join('');
  $('contract').textContent=JSON.stringify({rankingPolicy:data.asymmetric,returnModelContract:data.contract,rules:data.rules,source_snapshot:data.source.snapshot_id,base_experiment:data.base_snapshot,code_commit:data.code_commit,latest_models:Object.fromEntries(['kr','us'].map(m=>[m,data.markets[m].latest.training]))},null,2);
}
for(const id of ['market','fx','sort','opinion','coverage']) $(id).addEventListener('change',()=>{page=1;render();});
$('query').addEventListener('input',()=>{page=1;render();});
$('previous').addEventListener('click',()=>{page--;render();}); $('next').addEventListener('click',()=>{page++;render();});
$('rows').addEventListener('click',event=>{const button=event.target.closest('[data-ticker]');if(!button)return;const r=data.rows.find(r=>r.market===button.dataset.market&&r.ticker===button.dataset.ticker);if(r){detail(r);$('detail').scrollIntoView({behavior:'smooth',block:'start'});}});
$('themeToggle').addEventListener('click',()=>{const dark=document.documentElement.dataset.theme!=='dark';document.documentElement.dataset.theme=dark?'dark':'';$('themeToggle').textContent=dark?'라이트모드':'다크모드';$('themeToggle').setAttribute('aria-pressed',String(dark));});
renderMarkets();selectionTables();validation();render();
