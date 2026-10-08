'use strict';
const data=JSON.parse(document.getElementById('investment-data').textContent),M=InvestmentMetrics,$=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct=v=>M.finite(v)?`${(v*100).toFixed(1)}%`:'—',pp=v=>M.finite(v)?`${(v*100).toFixed(1)}%p`:'—',num=v=>M.finite(v)?v.toFixed(3):'—';
const signed=v=>M.finite(v)?`${v>0?'+':''}${pct(v)}`:'—',tone=v=>!M.finite(v)?'':v>0?'positive':v<0?'negative':'';
const marketName=m=>m==='kr'?'한국':'미국',rowsByMarket=Object.fromEntries(['kr','us'].map(m=>[m,data.rows.filter(r=>r.market===m)]));
const params=new URLSearchParams(location.search),pageSize=20;
let page=1,selected=null,view=['overview','performance','financial'].includes(params.get('view'))?params.get('view'):'overview',direction=params.get('direction')==='asc'?'asc':'desc';
const detailCache=new Map(),bundleCache=new Map();
for(const id of ['market','fx','sort','opinion','coverage','financial'])if([...$(id).options].some(o=>o.value===params.get(id)))$(id).value=params.get(id);
$('query').value=params.get('q')||'';
$('created').textContent=`기준 ${[...new Set(['kr','us'].map(m=>data.markets[m].latest.asof))].join(' / ')} · 고정 실험`;
$('universe-count').textContent=`한국 + 미국 ${data.rows.length.toLocaleString()}개 종목`;
const scenario=r=>M.scenario(r,$('fx').value),score=r=>M.finite(scenario(r)?.score)?scenario(r).score.toFixed(1):'—';
const formatFin=(k,v)=>M.definitions[k][1]==='times'?(M.finite(v)?v.toFixed(2)+'배':'—'):pct(v);
const price=r=>M.finite(r.price_metrics?.close)?new Intl.NumberFormat('ko-KR',{style:'currency',currency:r.market==='kr'?'KRW':'USD',maximumFractionDigits:r.market==='kr'?0:2}).format(r.price_metrics.close):'시세 없음';
function spark(r,large=false){
  const values=r.price_metrics?.chart||[];if(values.length<2)return '<div class="chart-empty">연속된 6개월 가격 자료 없음</div>';
  const width=large?640:160,height=large?160:44,pad=large?12:3,lo=Math.min(100,...values),hi=Math.max(100,...values),span=hi-lo||1;
  const points=values.map((v,i)=>`${(pad+i*(width-pad*2)/(values.length-1)).toFixed(2)},${(height-pad-(v-lo)/span*(height-pad*2)).toFixed(2)}`).join(' '),y=height-pad-(100-lo)/span*(height-pad*2);
  return `<svg class="spark ${tone(values.at(-1)-100)}" viewBox="0 0 ${width} ${height}" role="img" aria-label="최근 6개월 조정가격, 시작 100 기준 ${values.at(-1).toFixed(1)}"><line x1="0" y1="${y}" x2="${width}" y2="${y}" stroke="currentColor" stroke-opacity=".2" stroke-dasharray="4 4"/><polyline points="${points}" fill="none" stroke="currentColor" stroke-width="${large?2.5:2}" stroke-linejoin="round"/></svg>`;
}
function renderMarkets(){
  $('markets').innerHTML=['kr','us'].map(m=>{const d=data.markets[m].latest,rows=rowsByMarket[m].filter(r=>r.scored),trend=rows[0]?.market_trend_60,avg=rows.map(r=>scenario(r)?.score).filter(M.finite),average=avg.length?avg.reduce((a,b)=>a+b,0)/avg.length:null;
    return `<article class="market-pulse"><div><span class="country-badge ${m}">${m.toUpperCase()}</span><strong>${marketName(m)} 시장</strong><span class="pulse-count">${d.scored.toLocaleString()}개 평가</span></div><div class="pulse-numbers"><div><small>과거 60일 시장 추세</small><strong class="${tone(trend)}">${signed(trend)}</strong></div><div><small>통합 점수 평균 · 실험</small><strong>${M.finite(average)?average.toFixed(1):'—'}</strong></div><div><small>재무 자료</small><strong>${d.financial_covered}<span>개</span></strong></div></div></article>`;}).join('');
}
function identity(r){return `<button class="stock-button" data-market="${esc(r.market)}" data-ticker="${esc(r.ticker)}">${esc(r.name)}</button><span class="stock-meta"><span class="country-badge ${r.market}">${r.market.toUpperCase()}</span> ${esc(r.ticker)}${r.price_metrics?.sector?' · '+esc(r.price_metrics.sector):''}</span>`;}
const columnSets={overview:['통합 점수','6개월 흐름','3개월 수익률','상위 10% 확률','하위 30% 확률','재무 의견'],performance:['최근 종가','1일','1개월','3개월','6개월','20일 변동성¹','1년 고점 대비²'],financial:['매출 성장률','영업이익률','ROA','영업현금 / 자산','유동비율','부채 / 자본','재무 기준']};
function cells(r){const p=r.price_metrics,f=r.financials;
  if(view==='performance')return [price(r),...[1,21,63,126].map(n=>`<span class="${tone(p?.returns?.[n])}">${signed(p?.returns?.[n])}</span>`),pct(p?.volatility20),pct(p?.drawdown252)];
  if(view==='financial')return ['fin_revenue_growth','fin_operating_margin','fin_roa','fin_cfo_assets','fin_current_ratio','liabilities_equity'].map(k=>formatFin(k,M.financial(r,k))).concat(f?.latest?`${esc(f.latest.period_end)}${f.stale?'<small class="stale-label">오래된 재무</small>':''}`:'자료 없음');
  return [`<strong class="score-pill">${score(r)}</strong>`,spark(r),`<span class="${tone(p?.returns?.['63'])}">${signed(p?.returns?.['63'])}</span>`,pct(r.up10),`${pct(r.down30)}${r.warning?'<small class="risk-label">경보</small>':''}`,`<span class="opinion-${esc(r.opinion.status)}">${esc(r.opinion.label)}</span>`];
}
function card(r){const labels=columnSets[view],v=cells(r),f=r.financials,overview=view==='overview',fields=overview?[['3개월 수익률',v[2]],['상위 10% 확률',v[3]],['하위 30% 확률',v[4]],['재무 의견',v[5]]]:labels.slice(0,6).map((label,i)=>[label,v[i]]);
  return `<article class="stock-card"><div class="stock-card-head"><div class="stock-identity">${identity(r)}</div><div class="card-score"><small>통합 · 실험</small><strong>${score(r)}</strong></div></div>${overview?`<div class="card-price"><span>${price(r)} <small>${esc(r.price_metrics?.quote_date||'')}</small></span>${spark(r)}</div>`:''}<dl class="card-metrics">${fields.map(([label,value])=>`<div><dt>${label}</dt><dd>${value}</dd></div>`).join('')}</dl>${view==='performance'?`<p class="card-footnote">1년 고점 대비 ${v[6]}</p>`:''}${view==='financial'?`<p class="card-footnote">${f?.latest?'재무 '+esc(f.latest.period_end)+(f.stale?' · 240일 초과':''):'보관된 재무 자료 없음'}</p>`:''}<div class="card-bottom"><span>${r.scored?'시장 내 순위 '+r.market_score.toFixed(1):'모델 미평가'}</span><button class="text-button" data-market="${esc(r.market)}" data-ticker="${esc(r.ticker)}">지표·근거 보기 ↗</button></div></article>`;
}
function render(){
  const state=Object.fromEntries(['market','fx','sort','opinion','coverage','financial'].map(id=>[id,$(id).value]));Object.assign(state,{q:$('query').value,direction});const result=M.select(data.rows,state),rows=result.rows;$('sort').value=result.key;
  $('sort').querySelector('option[value="market_score"]').disabled=state.market==='all';
  document.querySelectorAll('[data-market-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.marketFilter===state.market)));document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===view)));
  $('direction').textContent=direction==='desc'?'↓':'↑';$('direction').setAttribute('aria-label',direction==='desc'?'낮은 값부터 정렬':'높은 값부터 정렬');const pages=Math.max(1,Math.ceil(rows.length/pageSize));page=Math.min(page,pages);
  $('counts').textContent=`${rows.length.toLocaleString()}개 · 평가 ${rows.filter(r=>r.scored).length.toLocaleString()}개`;
  $('filter-status').textContent=[state.coverage!=='all'?'평가 필터':'',state.financial!=='all'?'재무 필터':'',state.opinion!=='all'?'의견 필터':'',Number(state.fx)!==0?'환율 '+signed(Number(state.fx)):''].filter(Boolean).join(' · ');
  $('scenario').textContent=view==='financial'?'TTM = 최근 12개월. 종목마다 재무 기준일이 다릅니다. 오래된 지표는 표시하되 정렬에서 제외합니다.':view==='performance'?'조정가격 기준 · 1/3/6개월 = 21/63/126거래일 · ¹20일 로그수익률의 연율화 변동성 · ²최근 252개 종가 고점 대비 현재 낙폭':'통합 점수는 원화 기준 실험치 · 환율 가정 '+signed(Number(state.fx))+' · 국가별 순위 점수와 구분해서 읽어 주세요.';
  $('columns').innerHTML=`<tr><th scope="col">종목</th>${columnSets[view].map(x=>`<th scope="col">${x}</th>`).join('')}</tr>`;const slice=rows.slice((page-1)*pageSize,page*pageSize);
  $('rows').innerHTML=slice.map(r=>`<tr><td>${identity(r)}</td>${cells(r).map(v=>`<td>${v}</td>`).join('')}</tr>`).join('')||'<tr><td colspan="8" class="investment-empty">조건에 맞는 종목이 없습니다.</td></tr>';
  $('stock-cards').innerHTML=slice.map(card).join('')||'<p class="investment-empty">조건에 맞는 종목이 없습니다.</p>';
  $('page').textContent=`${page} / ${pages}`;$('previous').disabled=page===1;$('next').disabled=page===pages;
  try{history.replaceState(null,'',`${location.pathname}?${new URLSearchParams({...state,sort:result.key,view})}`);}catch(_){}
  if(selected&&!rows.includes(selected)){$('detail-panel').close();selected=null;}if(selected&&detailCache.has(selected.market+':'+selected.ticker))detail(detailCache.get(selected.market+':'+selected.ticker));renderMarkets();
}
function metricTile(label,value,note=''){return `<div class="metric-tile"><dt>${esc(label)}</dt><dd>${value}</dd>${note?`<small>${esc(note)}</small>`:''}</div>`;}
function detail(row){
  const s=scenario(row),o=row.opinion,p=row.price_metrics,f=row.financials,history=f?.history||[],latest=f?.latest,href=row.scored?`stock.html?ticker=${encodeURIComponent(row.ticker)}`:`legacy.html?mode=all&q=${encodeURIComponent(row.ticker)}`;
  const tiles=Object.entries(M.definitions).map(([k,[label]])=>{const v=M.financial(row,k),prev=history.at(-2)?.metrics?.[k],change=M.finite(v)&&M.finite(prev)?(M.definitions[k][1]==='times'?`${v-prev>=0?'+':''}${(v-prev).toFixed(2)}배`:`${v-prev>=0?'+':''}${pp(v-prev)}`):null;return metricTile(label,formatFin(k,v),change?'앞선 보고기간 대비 '+change:'자료 없으면 —');}).join('');
  $('detail').innerHTML=`<div class="detail-heading"><div><span class="stock-meta"><span class="country-badge ${row.market}">${row.market.toUpperCase()}</span> ${esc(row.ticker)} · ${esc(p?.exchange||marketName(row.market))}</span><h2 id="detail-title">${esc(row.name)}</h2><p>${price(row)} <small>종가 ${esc(p?.quote_date||'없음')}${p?.stale?' · 기준일 시세 아님':''}</small></p></div><div class="detail-score"><span>통합 매력도 · 실험</span><strong>${score(row)}</strong><small>원화 기준 · 환율 ${signed(Number($('fx').value))}</small></div></div>
  <section class="detail-section"><h3>가격의 흐름 <small>시작 = 100</small></h3><div class="detail-chart">${spark(row,true)}</div><div class="chart-dates"><span>${esc(p?.chart_start||'')}</span><span>${esc(p?.chart_end||'')}</span></div><dl class="detail-metrics">${[1,21,63,126].map((n,i)=>metricTile(['1일 수익률','1개월 수익률','3개월 수익률','6개월 수익률'][i],`<span class="${tone(p?.returns?.[n])}">${signed(p?.returns?.[n])}</span>`)).join('')}${metricTile('20일 변동성',pct(p?.volatility20),'로그수익률 표준편차 × √252')}${metricTile('1년 고점 대비',pct(p?.drawdown252),'최근 252개 조정 종가 기준')}${metricTile('1년 가격 범위 내 위치',pct(p?.range_position252),'0% = 최저 종가 · 100% = 최고 종가')}${metricTile('20일 평균 거래대금',M.finite(p?.turnover20)?new Intl.NumberFormat('ko-KR',{notation:'compact',maximumFractionDigits:1}).format(p.turnover20)+(row.market==='kr'?'원':' USD'):'—','종가 × 거래량의 평균 · 근사치')}</dl></section>
  <section class="detail-section"><h3>상대 기회와 위험</h3><dl class="detail-metrics">${metricTile('시장 내 순위 점수',M.finite(row.market_score)?row.market_score.toFixed(1):'미평가','국가 간 비교용 아님')}${metricTile('상위 5% 확률',pct(row.up5))}${metricTile('상위 10% 확률',pct(row.up10))}${metricTile('하위 30% 확률',pct(row.down30),row.warning?'하락 경보':'')}${metricTile('원화 중위 수익률 전망',pct(s?.median_krw),'63거래일 · 실험')}${metricTile('원화 하위 10% 전망',pct(s?.q10_krw),'통합 점수의 손실 조정에 사용')}</dl><p class="detail-note">${row.scored?'확률은 보정된 추정치입니다. 상위 5%·10% 모델을 독립 학습했고, 중간권 순위와 수익률 전망의 검증 성능에는 한계가 있습니다.':'미평가: '+esc(row.score_reason)} 하락 경보는 매도 지시가 아닙니다.</p></section>
  <section class="detail-section"><div class="section-heading"><h3>재무 한눈에</h3><span class="data-badge">${latest?esc(latest.period_end)+(f.stale?' · 오래된 자료':' 기준'):'자료 없음'}</span></div><p class="detail-note">${latest?`공시 이용 가능일 ${esc(latest.available_at)} · 보고기간 말로부터 ${f.age_days}일 · ${esc(latest.scope)}${f.financial_sector===1?' · 금융업은 일부 지표가 해당하지 않음':''}`:'이 종목의 공시일이 확인된 재무자료가 보관되어 있지 않습니다.'}</p><dl class="detail-metrics financial-tiles">${tiles}</dl><p class="detail-note">흐름 지표는 TTM(최근 12개월), 자산·부채·현금은 기말 기준입니다. 변화량은 직전 표시 보고기간과의 차이이며 전분기 성장률이 아닙니다. PER·PBR·ROE는 정확한 주당·평균자본 자료가 부족해 제공하지 않습니다.</p>
  ${history.length?`<details class="formula-panel"><summary>보고기간별 지표와 공시 근거</summary><div class="validation-scroll"><table class="validation-table"><thead><tr><th>재무 기간</th><th>매출 성장</th><th>영업이익률</th><th>ROA</th><th>영업현금 / 자산</th></tr></thead><tbody>${history.map(h=>`<tr><td>${esc(h.period_end)}</td>${['fin_revenue_growth','fin_operating_margin','fin_roa','fin_cfo_assets'].map(k=>`<td>${pct(h.metrics[k])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>${history.map(h=>`<p>${esc(h.period_end)} · 이용 가능 ${esc(h.available_at)} · 공시 ${esc(h.filing_id)}</p>`).join('')}</details>`:''}<details class="formula-panel"><summary>지표 계산 방법</summary><dl>${Object.values(M.definitions).map(([label,,formula])=>`<dt>${esc(label)}</dt><dd>${esc(formula)}</dd>`).join('')}</dl></details></section>
  <section class="opinion-box"><span class="opinion-${esc(o.status)}">${esc(o.label)}</span><h3>재무 룰의 보조 의견</h3><p>${o.reasons.map(esc).join('<br>')}</p><p class="detail-note">적정 주가·수주·경영진 설명·감사 의견을 확인한 증권사 리포트와 다릅니다. 가격 전망과 독립된 규칙이며, 자료 부족을 중립이나 매수로 처리하지 않습니다.</p></section><a class="button secondary" href="https://jaesung0804.github.io/st_dashboard/lgbm_warning_dashboard_macro_${row.market}_latest/${href}" target="_blank" rel="noopener">기존 종목 기록 보기 ↗</a>`;
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
  $('contract').textContent=JSON.stringify({presentationMetrics:data.presentation_metrics,rankingPolicy:data.asymmetric,returnModelContract:data.contract,rules:data.rules,source_snapshot:data.source.snapshot_id,base_experiment:data.base_snapshot,code_commit:data.code_commit,latest_models:Object.fromEntries(['kr','us'].map(m=>[m,data.markets[m].latest.training]))},null,2);
}

for(const id of ['market','fx','sort','opinion','coverage','financial'])$(id).addEventListener('change',()=>{page=1;render();});
$('query').addEventListener('input',()=>{page=1;render();});
$('market-tabs').addEventListener('click',e=>{const b=e.target.closest('[data-market-filter]');if(b){$('market').value=b.dataset.marketFilter;page=1;render();}});
$('view-tabs').addEventListener('click',e=>{const b=e.target.closest('[data-view]');if(b){view=b.dataset.view;$('sort').value={overview:'score',performance:'ret63',financial:'fin_operating_margin'}[view];direction='desc';page=1;render();}});
$('direction').addEventListener('click',()=>{direction=direction==='desc'?'asc':'desc';page=1;render();});
$('reset').addEventListener('click',()=>{for(const id of ['coverage','financial','opinion'])$(id).value='all';$('fx').value='0';$('query').value='';page=1;render();});
function pageMove(delta){page+=delta;render();$('comparison').scrollIntoView({block:'start'});}
$('previous').addEventListener('click',()=>pageMove(-1));$('next').addEventListener('click',()=>pageMove(1));
async function openDetail(r){
  selected=r;$('detail').innerHTML=`<h2 id="detail-title">${esc(r.name)}</h2><p role="status">차트와 공시 지표를 불러오고 있습니다.</p>`;
  if(!$('detail-panel').open)$('detail-panel').showModal();$('detail-panel').scrollTop=0;
  try{
    if(!/^details\/\d{3}\.json\?v=[a-f0-9]{12}$/.test(r.detail_bundle||''))throw new Error('Invalid detail reference');
    if(!bundleCache.has(r.detail_bundle))bundleCache.set(r.detail_bundle,fetch(r.detail_bundle).then(response=>{if(!response.ok)throw new Error('Missing detail data');return response.json();}).catch(error=>{bundleCache.delete(r.detail_bundle);throw error;}));
    const records=await bundleCache.get(r.detail_bundle);
    for(const item of records)detailCache.set(item.market+':'+item.ticker,item);
    const full=detailCache.get(r.market+':'+r.ticker);if(!full)throw new Error('Missing stock');
    if(selected===r&&$('detail-panel').open)detail(full);
  }catch(error){if(selected===r)$('detail').innerHTML=`<h2 id="detail-title">${esc(r.name)}</h2><p role="alert">상세 자료를 불러오지 못했습니다. 연결을 확인한 뒤 다시 열어 주세요.</p>`;}
}
for(const id of ['rows','stock-cards'])$(id).addEventListener('click',e=>{const b=e.target.closest('[data-ticker]');if(!b)return;const r=data.rows.find(r=>r.market===b.dataset.market&&r.ticker===b.dataset.ticker);if(r)openDetail(r);});
$('close-detail').addEventListener('click',()=>{$('detail-panel').close();});
$('detail-panel').addEventListener('close',()=>{selected=null;});
$('detail-panel').addEventListener('click',e=>{if(e.target===$('detail-panel')){const rect=e.target.getBoundingClientRect();if(e.clientX<rect.left||e.clientX>rect.right||e.clientY<rect.top||e.clientY>rect.bottom)e.target.close();}});
$('themeToggle').addEventListener('click',()=>{const dark=document.documentElement.dataset.theme!=='dark';document.documentElement.dataset.theme=dark?'dark':'';$('themeToggle').textContent=dark?'라이트모드':'다크모드';$('themeToggle').setAttribute('aria-pressed',String(dark));});
selectionTables();validation();render();
