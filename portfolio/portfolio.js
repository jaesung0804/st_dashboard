(function (root) {
  'use strict';
  const names = {buy_hold_80:'초기 종목 그대로 보유',equal_fixed:'유동성 상위 동일비중',equal_vol:'단순 변동성 조절',equal_regime:'시장 상태 조절',legacy_rank_equal:'기존 순위 모델',forecast_equal:'새 전망 동일비중',v01_full:'v0.1 전체 · 비용 문턱',v01_no_relation:'관계·충격 특징 제외',v01_raw_mean:'평균 보정 제외',v01_return_only:'수익 최적화만',v01_daily:'v0.1 매일 재조정',v01_threshold:'v0.1 비중 차이 문턱',v01_no_regime:'시장 상태 조절 제외'};
  const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number = (x, d=0) => Number.isFinite(x) ? x.toLocaleString('ko-KR',{minimumFractionDigits:d,maximumFractionDigits:d}) : '—';
  const pct = (x, d=2) => Number.isFinite(x) ? number(x*100,d)+'%' : '자료 없음';
  const money = x => Number.isFinite(x) ? number(x)+'원' : '자료 없음';
  function positions(snapshot, market='all', query='') {
    const q = query.replace(/\s/g,'').toLowerCase();
    // Keep real holdings and material requests, not near-zero solver candidates.
    return snapshot.positions.filter(p => (p.current_weight>1e-8||p.filled_weight>1e-8||p.requested_weight*snapshot.before.nav_krw>=50000||p.accepted_weight*snapshot.before.nav_krw>=50000)
      && (market==='all'||p.market===market) && (!q||[p.name,p.ticker,p.asset].join(' ').replace(/\s/g,'').toLowerCase().includes(q)))
      .slice().sort((a,b)=>b.filled_weight-a.filled_weight||a.asset.localeCompare(b.asset));
  }
  function allocation(snapshot, when='after') {
    const a=snapshot[when];
    return [{name:'한국 주식',value:a.kr_stock_krw,color:'#127d80'}, {name:'미국 주식',value:a.us_stock_krw,color:'#4a73b8'},
      {name:'원화 현금·대기금',value:a.krw_total_krw,color:'#93bfc3'}, {name:'달러 현금·대기금',value:a.usd_total_krw,color:'#b5c4d8'}]
      .map(x=>({...x,weight:x.value/a.nav_krw}));
  }
  function briefText(data, now=new Date()) {
    const today = new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'}).format(now);
    return `[한미 포트폴리오] ${today} · 매매안 보류\n과거 가상 계좌의 연구 결과입니다. 오늘 실행할 매매안이 아닙니다.\n최근 계획 기준: ${data.today_plan.decision_at}\n현재 유효한 매수·매도 지시를 생성하지 않았습니다.`;
  }
  function extensionContext(ext) {
    return `${ext.period.join(' — ')} · ${ext.days}개 판단일 · 각 전략은 같은 1억 원으로 새로 시작했습니다. 위 개발 계좌의 잔액을 이어 붙인 결과가 아닙니다.`;
  }
  function extensionMetrics(ext) {
    const order=[ext.primary_strategy,'legacy_rank_equal','equal_fixed','buy_hold_80','v01_daily','v01_threshold'];
    const rank=key=>order.includes(key)?order.indexOf(key):order.length;
    return ext.metrics.slice().sort((a,b)=>rank(a.strategy)-rank(b.strategy));
  }
  function researchRows(research,period,scenario) {
    const order=['reference','execution_only','bias_only','combined'];
    return research.metrics.filter(m=>m.period===period&&m.scenario===scenario).slice()
      .sort((a,b)=>order.indexOf(a.variant)-order.indexOf(b.variant));
  }
  function guardedRows(research,period,scenario) {
    const order=['guarded:combined','guarded:execution_only','frozen_v02:reference','frozen_v02:execution_only','frozen_v02:bias_only','frozen_v02:combined','benchmark:legacy_rank_equal','benchmark:equal_fixed','benchmark:buy_hold_80'];
    return research.metrics.filter(m=>m.period===period&&m.scenario===scenario).slice()
      .sort((a,b)=>order.indexOf(a.family+':'+a.variant)-order.indexOf(b.family+':'+b.variant));
  }
  function guardedUncertaintyRows(research,period,scenario) {
    const range=(bounds,scale,digits,suffix)=>Array.isArray(bounds)&&bounds.length===2&&bounds.every(Number.isFinite)
      ?bounds.map(x=>number(x*scale,digits)+suffix).join(' ~ '):'산출 불가';
    return research.paired_comparisons.filter(c=>c.period===period&&c.scenario===scenario)
      .flatMap(c=>c.intervals.slice().sort((a,b)=>a.block_days-b.block_days).map(v=>({
        variant:c.variant,reference_variant:c.reference_variant,block_days:v.block_days,
        return_range:range(v.interval?.annual_geometric_excess,100,2,'%p'),
        volatility_range:range(v.interval?.volatility_ratio,1,3,''),
        status:v.status==='insufficient_span_for_block'?'기간 부족':v.status==='descriptive_few_blocks'?'묶음 수 적음':'과거 자료 재표집'})));
  }
  const diagnosticDays=value=>Number.isFinite(value)?number(value)+'일':'미측정';
  function fxInstruction(value) {
    if(!Number.isFinite(value))return '자료 없음';
    if(value===0)return '환전 지시 없음';
    if(Math.abs(value)<.005)return '환전 지시 $0.01 미만';
    return `달러 ${value>0?'매수':'매도'} $${number(Math.abs(value),2)}`;
  }
  function accountSets(data) {
    return [{id:'v01:development:base',label:'v0.1 · 개발 기간 · 기본 체결',period:data.period,
      snapshots:data.snapshots,reconstruction:data.evidence.reconstruction},...(data.guarded_holdings?.sets||[]).map(s=>({...s,
        label:`${s.label} · ${s.period_key==='development'?'개발 기간':'추가 기간'} · ${s.scenario==='base'?'기본 체결':'하루 지연'}`}))];
  }
  function forecastRows(report,period,country) {
    return report.metrics.filter(m=>m.period===period&&m.country===country).slice()
      .sort((a,b)=>['reference','bias_only'].indexOf(a.variant)-['reference','bias_only'].indexOf(b.variant));
  }
  function forecastNotes(report,period,country) {
    const [a,b]=forecastRows(report,period,country);
    const p=report.paired.find(x=>x.period===period&&x.country===country);
    return [
      `평균 편향의 크기는 ${Math.abs(b.mean_bias)<Math.abs(a.mean_bias)?'줄었습니다':'줄지 않았습니다'}. 평균제곱오차는 ${b.mean_mse<a.mean_mse?'작아졌습니다':'커졌거나 같습니다'}.`,
      `하방 예측 손실은 ${b.q05_pinball<a.q05_pinball?'줄었습니다':'늘었거나 같습니다'}. 하단을 벗어난 비율은 5%를 기준으로 함께 확인합니다.`,
      `시장 내 종목 순위는 ${p.maximum_within_country_rank_change<1e-10?'같습니다':'변했습니다'}. 예측 구간 폭은 ${p.maximum_interval_width_change<1e-10?'그대로입니다':'변했습니다'}.`
    ];
  }
  const api={names,esc,number,pct,money,positions,allocation,briefText,extensionContext,extensionMetrics,researchRows,guardedRows,guardedUncertaintyRows,diagnosticDays,accountSets,fxInstruction,forecastRows,forecastNotes};
  root.PortfolioUI=api;
  if (typeof module !== 'undefined' && module.exports) module.exports=api;
  if (typeof document === 'undefined') return;
  const element=document.getElementById('portfolio-data');
  if(!element) return;
  const data=JSON.parse(element.textContent);
  const $=id=>document.getElementById(id);
  const by=Object.fromEntries(data.metrics.map(m=>[m.strategy,m]));
  const full=by.v01_full;
  const accounts=accountSets(data);
  let accountId=accounts[0].id,date=data.snapshots[data.snapshots.length-1].date, market='all', accountWhen='after';
  const activeAccount=()=>accounts.find(s=>s.id===accountId);
  const snapshot=()=>activeAccount().snapshots.find(s=>s.date===date);
  $('period').textContent=data.period.join(' — ')+' · 과거 개발 기간';
  $('metrics').innerHTML=[['누적 순수익',pct(full.total_return),'최종 가상 자산 '+money(full.final_nav_krw)],['연 변동성',pct(full.volatility),'일별 수익의 흔들림을 연 단위로 표시'],['최대 낙폭',pct(full.max_drawdown),'이전 최고 자산에서 가장 크게 하락한 폭'],['평균 주식 비중',pct(full.equity_mean,1),'현금 대기가 방어력에 미친 영향도 확인']]
    .map(([a,b,c])=>`<article class="metric"><span>${esc(a)}</span><strong>${esc(b)}</strong><small>${esc(c)}</small></article>`).join('');
  $('benchmark').innerHTML=['equal_fixed','buy_hold_80','legacy_rank_equal','forecast_equal','v01_daily','v01_threshold'].map(k=>`<option value="${k}">${esc(names[k])}</option>`).join('');
  function chart() {
    const chosen=$('benchmark').value, series=[['v01_full','#127d80'],[chosen,'#4a73b8']], values=series.flatMap(([key])=>data.curves.map(c=>c.values[key]/1e6));
    const lo=Math.floor(Math.min(100,...values)/10)*10, hi=Math.ceil(Math.max(100,...values)/10)*10;
    const w=710,h=235,l=42,r=10,t=12,b=28,plotW=w-l-r,plotH=h-t-b;
    const x=i=>l+i/(data.curves.length-1)*plotW, y=v=>t+(hi-v)/(hi-lo)*plotH;
    let svg=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="v0.1과 ${esc(names[chosen])}의 원화 자산 변화. 시작 자산 100.">`;
    for(let v=lo;v<=hi;v+=10) svg+=`<line class="grid" x1="${l}" x2="${w-r}" y1="${y(v)}" y2="${y(v)}"/><text x="${l-8}" y="${y(v)+4}" text-anchor="end">${v}</text>`;
    series.forEach(([key,color])=>{svg+=`<polyline fill="none" stroke="${color}" stroke-width="${key==='v01_full'?2.8:1.8}" stroke-linejoin="round" points="${data.curves.map((c,i)=>`${x(i).toFixed(2)},${y(c.values[key]/1e6).toFixed(2)}`).join(' ')}"/>`;});
    [0,Math.floor(data.curves.length/3),Math.floor(data.curves.length*2/3),data.curves.length-1].forEach(i=>{svg+=`<text x="${x(i)}" y="${h-5}" text-anchor="${i===0?'start':i===data.curves.length-1?'end':'middle'}">${esc(data.curves[i].date.slice(0,7))}</text>`;});
    $('chart').innerHTML=svg+'</svg>';
    $('legend').innerHTML=series.map(([key,color])=>`<span><i style="background:${color}"></i>${esc(names[key])}</span>`).join('');
  }
  $('benchmark').addEventListener('change',chart);chart();
  $('account-model').innerHTML=accounts.map(s=>`<option value="${esc(s.id)}">${esc(s.label)}</option>`).join('');
  $('account-model').disabled=accounts.length===1;
  function selectDates() {
    const a=activeAccount();
    if(!a.snapshots.some(s=>s.date===date))date=a.snapshots[a.snapshots.length-1].date;
    $('snapshot-date').innerHTML=a.snapshots.slice().reverse().map(s=>`<option value="${esc(s.date)}">${esc(s.date)}</option>`).join('');
    $('snapshot-date').value=date;
    $('account-context').textContent=`${a.label} · ${a.period.join(' — ')}의 별도 가상 계좌 · 전체 ${a.reconstruction.days}개 판단일 장부 대조 완료, 최근 ${a.snapshots.length}일 표시. 위 수익 곡선은 v0.1 개발 계좌입니다.`;
  }
  selectDates();
  function holdings() {
    const items=positions(snapshot(),market,$('search').value);
    $('position-count').textContent=`보유·조정 ${items.length}개 종목 · 과거 가상 계좌`;
    $('holdings').innerHTML=items.length?items.map(p=>{
      const change=p.filled_change>1e-7?'buy':p.filled_change< -1e-7?'sell':'hold';
      return `<tr><td><button class="stock-name" type="button" data-asset="${esc(p.asset)}">${esc(p.name)}<small>${esc(p.market)} · ${esc(p.ticker)}</small></button></td><td>${pct(p.current_weight,1)}</td><td>${pct(p.requested_weight,1)}</td><td>${Number.isFinite(p.accepted_weight)?pct(p.accepted_weight,1):'검사 없음'}</td><td><b>${pct(p.filled_weight,1)}</b></td><td>${pct(p.mean_5d)}</td><td><span class="change ${change}">${{buy:'매수 재현',sell:'매도 재현',hold:'유지'}[change]}</span></td></tr>`;
    }).join(''):'<tr><td colspan="7" class="empty">이 날짜·시장에 검색과 일치하는 종목이 없습니다.</td></tr>';
  }
  function account() {
    const s=snapshot(),a=s[accountWhen];
    const action={rebalance:'비중 조정',risk_repair:'위험 축소',cost_hold:'거래비용을 고려한 유지',infeasible_unresolved:'위험 계산 미해결'}[s.action]||'유지';
    $('date-context').textContent=`${s.date} 08:30 KST 결정 · ${action} · 체결 후 평가는 ${s.valuation_at.slice(0,10)} 08:30 KST · 연구용 수정 수량`;
    $('nav').textContent=money(a.nav_krw);
    const items=allocation(s,accountWhen);
    $('allocation-bar').innerHTML=items.map(x=>`<span style="width:${x.weight*100}%;background:${x.color}" title="${esc(x.name)} ${pct(x.weight,1)}"></span>`).join('');
    $('allocation').innerHTML=items.map(x=>`<div class="allocation-item"><i style="background:${x.color}"></i><span>${esc(x.name)}</span><b>${pct(x.weight,1)}</b><small>${money(x.value)}</small></div>`).join('');
    $('cash-risk').innerHTML=[['결제 완료 원화',money(a.settled_krw),'매수 재원으로 사용 가능'],['결제 완료 달러','$'+number(a.settled_usd,2),'원화 환산 '+money(a.settled_usd*a.fx)],['결제 대기금',money(a.receivable_krw+a.receivable_usd*a.fx),'원화·달러 합계 · 아직 매수에 사용하지 않음'],['이 날 거래·환전 비용',money(s.trade_cost_krw+s.fx_cost_krw),'가상 체결에서 실제로 차감한 금액'],['체결 후 예상 연 변동성',pct(s.postfill_volatility),'한도 '+pct(s.volatility_budget,0)],['체결 후 5일 CVaR95',pct(s.postfill_cvar_5d),'가장 나쁜 5% 상황의 평균 손실 전망']]
      .map(([a,b,c])=>`<div><span>${esc(a)}</span><strong>${esc(b)}</strong><small>${esc(c)}</small></div>`).join('');
    if(s.postfill_risk_breach)$('cash-risk').insertAdjacentHTML('beforeend','<div class="breach"><strong>위험 한도 초과 기록</strong><small>다음 거래 가능 시점의 축소 여부를 확인해야 합니다.</small></div>');
    if(typeof s.plan_check_passed==='boolean'){
      const labels={cluster:'종목군 집중',country:'국가 집중',cvar:'꼬리 손실',equity:'주식 총비중',name:'종목 비중',stress:'스트레스 손실',usd:'달러 노출',volatility:'변동성'};
      const breached=Object.entries(s.risk_violations).filter(([,v])=>v>2e-7).map(([k])=>labels[k]||k);
      $('cash-risk').insertAdjacentHTML('beforeend',`<div class="${s.plan_unresolved?'breach':''}"><span>최종 주문 검사</span><strong>${s.plan_check_passed?'설정 조건 통과':'한도 회복 미해결'}</strong><small>${s.executed?'승인 수량으로 체결 재현':'주문 없이 보유 유지'}</small></div><div class="${s.postfill_all_constraint_breach?'breach':''}"><span>체결 후 전체 위험 항목</span><strong>${s.postfill_all_constraint_breach?'초과 기록 있음':'초과 기록 없음'}</strong><small>${esc(breached.length?breached.join(' · '):'당시 모형의 8개 항목 기준')}</small></div><div><span>승인과 체결 수량 대조</span><strong>${s.partial_or_missing_fill?'일부 수량 차이':'수량 일치'}</strong><small>${esc(fxInstruction(s.accepted_fx_delta_usd))}</small></div>`);
    }
    holdings();
  }
  $('account-model').addEventListener('change',()=>{accountId=$('account-model').value;selectDates();account();});
  $('snapshot-date').addEventListener('change',()=>{date=$('snapshot-date').value;account();});
  document.querySelectorAll('[data-account]').forEach(b=>b.addEventListener('click',()=>{accountWhen=b.dataset.account;document.querySelectorAll('[data-account]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));account();}));
  document.querySelectorAll('[data-market]').forEach(b=>b.addEventListener('click',()=>{market=b.dataset.market;document.querySelectorAll('[data-market]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));holdings();}));
  $('search').addEventListener('input',holdings);account();
  $('holdings').addEventListener('click',event=>{
    const b=event.target.closest('[data-asset]');if(!b)return;
    const s=snapshot(),p=s.positions.find(p=>p.asset===b.dataset.asset);
    $('detail-title').textContent=p.name+' · '+p.ticker;
    const history=p.history_count===null?'위험 이력 확인 필요':p.history_count<60?'60개 미만: 신규 매수 금지':p.history_count<120?'120개 미만: 신규 비중 2% 제한':'종목별 위험 이력 '+p.history_count+'개';
    const orderRows=[['결정 전 비중',pct(p.current_weight)],['요청 목표 비중',pct(p.requested_weight)],
      ['승인 목표 비중',Number.isFinite(p.accepted_weight)?pct(p.accepted_weight):'최종 주문 검사 없음'],
      ['체결 후 비중',pct(p.filled_weight)],['요청한 수정 수량 변화',number(p.requested_change,4)],
      ['승인한 수정 수량 변화',number(p.accepted_change,4)],['재현된 수정 수량 변화',number(p.filled_change,4)]];
    $('detail-body').innerHTML=`<span class="pill neutral">${esc(s.date)} · 과거 연구</span><p>전망에 사용한 마지막 종목 가격 날짜: ${esc(p.observed_date||'새 전망 없음')}</p><div class="detail-grid">${[['하위 5% 전망',p.q05_5d],['평균 전망',p.mean_5d],['95% 분위수',p.q95_5d]].map(([a,b])=>`<div><span>${esc(a)}</span><strong>${pct(b)}</strong></div>`).join('')}</div><p>모두 같은 미래 5일의 원화 수익률입니다. 분위수는 가능한 손실의 절대 한도가 아니며, 평균과 중앙값도 다릅니다.</p><p><b>${esc(history)}</b></p><table><tbody>${orderRows.map(([a,b])=>`<tr><td>${esc(a)}</td><td>${esc(b)}</td></tr>`).join('')}</tbody></table><p>승인 목표는 최종 주문 검사와 최소 주문금액 조정을 거친 수량입니다. 하루 지연 조건에서는 대기 주문을 해당일 잔액으로 다시 검사한 결과입니다.</p><p>위 소수 수량은 수정가격에 맞춘 연구 수량입니다. 실제 주문에 사용할 주식 수가 아닙니다.</p>`;
    $('detail-dialog').showModal();
  });
  $('comparison').innerHTML=data.metrics.map(m=>`<tr class="${m.strategy==='v01_full'?'highlight':''}"><td>${esc(names[m.strategy]||m.strategy)}</td><td>${pct(m.total_return)}</td><td>${pct(m.volatility)}</td><td>${pct(m.max_drawdown)}</td><td>${pct(m.equity_mean,1)}</td><td>${money(m.trade_cost_krw+m.fx_cost_krw)}</td></tr>`).join('');
  $('gates').innerHTML=[['새 기간의 독립 검증','아직 통과하지 않음'],['체결 후 위험 관리',`${full.postfill_risk_breach_days}일 한도 초과 · 개선 필요`],['모의·소액 실전','각 126거래일 이상 관찰 필요'],['일일 매매안 연결','최신 자료·계좌 대조 미연결']].map(([a,b])=>`<div class="gate"><b>${esc(a)}</b><span>${esc(b)}</span></div>`).join('');
  const ci=data.paired_intervals.legacy_rank_equal[0];
  $('uncertainty').textContent=`기존 순위 대비 연산술 초과수익의 95% 구간: ${pct(ci.lower_95)} ~ ${pct(ci.upper_95)}. 0을 포함하므로 수익 우위가 확정되지 않았습니다. 여러 대안을 비교한 개발 결과입니다.`;
  $('reconstruction').textContent=`재현 확인: ${data.evidence.reconstruction.days}일의 가상 자산을 기존 보관 결과와 대조했습니다. 최대 차이 ${number(data.evidence.reconstruction.max_nav_difference_krw,4)}원. 공개 지표와 원본 아카이브의 해시를 검증해 연결했습니다.`;
  if(data.temporal_extension){
    const ext=data.temporal_extension;
    $('extension').hidden=false;
    $('extension-context').textContent=extensionContext(ext);
    $('extension-comparison').innerHTML=extensionMetrics(ext).map(m=>`<tr class="${m.strategy==='v01_full'?'highlight':''}"><td>${esc(names[m.strategy]||m.strategy)}</td><td>${pct(m.total_return)}</td><td>${pct(m.volatility)}</td><td>${pct(m.max_drawdown)}</td><td>${pct(m.equity_mean,1)}</td><td>${number(m.postfill_risk_breach_days)}일</td></tr>`).join('');
    $('extension-gate').textContent=`평가 일수 ${ext.days}일 / 최소 ${ext.inference.minimum_decision_days}일. 앞으로 쌓은 모의 운용: ${ext.inference.forward_paper_days}일. 비용 차감 수익과 변동성을 함께 검증하며, 이번 결과로 운영 승격하지 않습니다.`;
    $('extension-context').insertAdjacentHTML('afterend','<p class="footnote">위험 초과일: 단순 보유·동일비중 비교군에는 v0.1 한도를 적용한 진단입니다. 해당 비교군이 같은 위험 제약을 지키도록 설계되었다는 뜻은 아닙니다.</p>');
    const primary=ext.metrics.find(m=>m.strategy===ext.primary_strategy), legacy=ext.metrics.find(m=>m.strategy==='legacy_rank_equal');
    const card=document.querySelector('.decision-card');
    card.querySelector('h3').textContent='추가 기간까지 확인한 결과';
    card.querySelector('p').textContent=`${ext.period.join(' — ')} 비용 차감 수익: v0.1 ${pct(primary.total_return)}, 기존 순위 ${pct(legacy.total_return)}. 체결 후 위험 한도 초과 ${number(primary.postfill_risk_breach_days)}일. 앞선 개발 성과만으로 채택하지 않습니다.`;
    card.querySelector('a').href='#extension';
    card.querySelector('a').textContent='추가 기간 비교 보기 →';
    const riskGate=$('gates').querySelectorAll('.gate')[1];
    riskGate.querySelector('span').textContent=`개발 ${full.postfill_risk_breach_days}일 · 추가 ${primary.postfill_risk_breach_days}일 초과`;
  }
  if(data.forecast_quality){
    const report=data.forecast_quality,section=document.createElement('section');section.id='forecast-quality';
    section.innerHTML='<div class="section-title"><div><span class="eyebrow">FORECAST QUALITY</span><h2>수익 전망은 실제와 얼마나 맞았나</h2></div><span class="pill">과거 예측 진단</span></div><p class="context">향후 5일 원화 수익 전망의 정확도입니다. 계좌 수익률과 구분해서 보세요. 평균 오차가 줄어도 하락 위험 예측까지 좋아지는 것은 아닙니다.</p><div class="panel-heading forecast-controls"><label>비교 기간<select id="forecast-period"><option value="extension">2026.04–06 · 추가 기간</option><option value="development">2024.10–2026.03 · 개발 기간</option></select></label><label>시장<select id="forecast-country"><option value="1">미국</option><option value="0">한국</option></select></label></div><div id="forecast-metrics" class="forecast-metrics"></div><div class="chart-layout"><article class="panel"><h3>예측한 비율과 실제 비율</h3><div id="forecast-chart" class="chart"></div><div class="legend"><span><i style="background:#64748b"></i>기존 전망</span><span><i style="background:#2563eb"></i>편향 보정</span><span>점선: 일치 기준</span></div><p class="footnote">예를 들어 하단 5% 예측값보다 실제 수익이 낮거나 같았던 비율이 5%에 가까운지 봅니다. 세 지점을 연결한 것으로, 완전한 수익 분포를 뜻하지 않습니다.</p></article><article class="panel decision-card"><h3>무엇이 달라졌나</h3><div id="forecast-notes"></div><p>보정은 시장 공통 수준을 옮깁니다. 순위·구간 폭·실제 비용 차감 성과는 함께 확인해야 합니다.</p></article></div><details class="panel method"><summary>예측 손실·관측 수와 평가 기준</summary><div class="table-wrap"><table><caption class="sr-only">선택한 시장과 기간의 모든 전망 비교</caption><thead><tr><th>전망</th><th>평균제곱오차</th><th>하단 분위수 손실</th><th>구간 점수</th><th>순위 상관</th></tr></thead><tbody id="forecast-score-rows"></tbody></table></div><p id="forecast-observations" class="footnote"></p><p class="footnote">평균제곱오차·분위수 손실·구간 점수는 낮을수록 좋습니다. 구간 점수는 폭과 구간 밖 오차를 함께 평가합니다. 순위 상관은 −1~1이며 클수록 예측 순위와 실현 순위가 잘 맞습니다. 같은 날짜·종목으로 비교하고 날짜를 동일 비중으로 평균했습니다. 겹치는 5일 수익을 독립 표본으로 세지 않습니다.</p><p class="footnote">이미 살펴본 기간의 사후 진단입니다. 유의성 검정·독립 검증·모의 실전 성과가 아니며 모델 승격이나 주문을 허용하지 않습니다.</p></details>';
    document.querySelector('main footer').before(section);
    function showForecast(){
      const period=$('forecast-period').value,country=Number($('forecast-country').value),[a,b]=forecastRows(report,period,country);
      const pp=v=>number(v*100,2)+'%p';
      const cards=[['평균 예측 − 실제 수익',pp(a.mean_bias),pp(b.mean_bias),'0에 가까울수록 평균 편향이 작음'],
        ['예측 하한을 벗어난 비율',pct(a.q05_coverage),pct(b.q05_coverage),'하단 5% 예측 · 기준 5%'],
        ['90% 구간에 포함된 비율',pct(a.coverage90),pct(b.coverage90),'예측 구간 안의 실제 수익 · 기준 90%']];
      $('forecast-metrics').innerHTML=cards.map(([title,before,after,note])=>`<article class="metric"><span>${esc(title)}</span><div class="forecast-values"><span>기존 <b>${esc(before)}</b></span><span>보정 <b>${esc(after)}</b></span></div><small>${esc(note)}</small></article>`).join('');
      $('forecast-notes').innerHTML=forecastNotes(report,period,country).map(t=>`<p>${esc(t)}</p>`).join('');
      const w=Math.max(280,$('forecast-chart').clientWidth||660),h=245,l=43,r=14,t=12,bottom=35,x=v=>l+v*(w-l-r),y=v=>t+(1-v)*(h-t-bottom);
      let svg=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${country===0?'한국':'미국'} ${period==='development'?'개발':'추가'} 기간의 5·50·95% 분위수 도달률">`;
      [0,.25,.5,.75,1].forEach(v=>{svg+=`<line class="grid" x1="${l}" x2="${w-r}" y1="${y(v)}" y2="${y(v)}"/><text x="${l-7}" y="${y(v)+4}" text-anchor="end">${number(v*100)}%</text>`;});
      svg+=`<path d="M${x(.05)},${y(.05)} L${x(.95)},${y(.95)}" fill="none" stroke="#a4afbd" stroke-dasharray="6 5" stroke-width="2"/>`;
      [[a,'#64748b'],[b,'#2563eb']].forEach(([m,color])=>{const points=[m.q05_coverage,m.q50_coverage,m.q95_coverage];svg+=`<polyline fill="none" stroke="${color}" stroke-width="2.6" points="${points.map((v,i)=>`${x([.05,.5,.95][i])},${y(v)}`).join(' ')}"/>`;points.forEach((v,i)=>{svg+=`<circle cx="${x([.05,.5,.95][i])}" cy="${y(v)}" r="4" fill="${color}"><title>${m.variant==='reference'?'기존':'보정'} ${number([5,50,95][i])}% 예측: 실제 ${pct(v)}</title></circle>`;});});
      [.05,.5,.95].forEach(v=>{svg+=`<text x="${x(v)}" y="${h-9}" text-anchor="middle">${number(v*100)}% 예측</text>`;});
      $('forecast-chart').innerHTML=svg+'</svg>';
      $('forecast-score-rows').innerHTML=[a,b].map(m=>`<tr><td>${m.variant==='reference'?'기존':'편향 보정'}</td><td>${number(m.mean_mse,6)}</td><td>${number(m.q05_pinball,6)}</td><td>${m.interval_score90===null?'구간 순서 오류':number(m.interval_score90,5)}</td><td>${number(m.rank_ic,4)}</td></tr>`).join('');
      $('forecast-observations').textContent=`${report.period_ranges[period].join(' — ')} · 관측 결과가 있는 ${a.labelled_days}일 · 종목·날짜 ${number(a.labelled_rows)}개 · 실현값 누락 ${number(a.missing_outcomes)}개. 누락을 수익 0으로 채우지 않았습니다. 순위 상관 산출 ${a.rank_defined_days}일 · 구간 순서 오류 ${a.interval_invalid_days}일. 예측 또는 실현 수익에 종목 간 차이가 없는 날은 순위 상관을 계산하지 않습니다.`;
    }
    $('forecast-period').addEventListener('change',showForecast);$('forecast-country').addEventListener('change',showForecast);root.addEventListener('resize',showForecast);showForecast();
  }
  if(data.v02_research&&!data.guarded_comparison){
    const research=data.v02_research,section=document.createElement('section');section.id='v02-research';
    section.innerHTML='<div class="section-title"><div><span class="eyebrow">V0.2 · CONTROLLED DEVELOPMENT</span><h2>체결과 전망, 무엇을 고쳐야 할까</h2></div><span class="pill">개발 재실험 · 채택 전</span></div><p class="context">이미 본 기간에서 개선 원인을 분리하는 비교입니다. 독립 검증·모의 실전 성과가 아닙니다. 네 후보는 같은 초기 자산·위험 한도·비용을 사용합니다.</p><div class="panel-heading"><label>비교 기간<select id="research-period"><option value="extension">2026.04–06 · 추가 기간</option><option value="development">2024.10–2026.03 · 개발 기간</option></select></label><label>체결 조건<select id="research-scenario"><option value="base">기본 체결</option><option value="one_day_delay">하루 지연</option></select></label></div><div class="table-wrap panel"><table><caption class="sr-only">v0.2 체결·보정 분리 비교</caption><thead><tr><th>후보</th><th>누적 수익</th><th>연 변동성</th><th>최대 낙폭</th><th>평균 주식</th><th>총 비용</th><th>위험 초과일</th><th>미해결 계획</th><th>계산·주문 검사 실패</th></tr></thead><tbody id="research-comparison"></tbody></table></div><p class="footnote">미해결 계획은 거래 제한 때문에 요청한 위험 한도를 회복하지 못한 날입니다. 정상·안전 상태로 처리하지 않습니다. 체결 수정 후보는 지연 주문을 현재 잔액으로 다시 검사하므로, 단순히 어제 주문을 그대로 실행하는 비교군과 실행 정책이 다릅니다.</p><p class="context">실전 승격 보류 · 앞으로 쌓은 모의 관찰 0일 · 실제 주문 미연결</p>';
    document.querySelector('main').insertBefore(section,document.querySelector('main > footer'));
    if(research.known_defects?.items?.length){
      const warning=document.createElement('p');warning.className='context breach';
      warning.textContent='체결 후보의 결함 확인: 동시에 계산한 매수·매도 비용이 실제 순주문 비용과 달라질 수 있습니다. 아래 수익은 재현 장부의 결과이며, 계획의 위험 한도 준수는 검증되지 않았습니다. 수정안은 별도 실험으로 확인합니다.';
      section.querySelector('.panel-heading').before(warning);
    }
    const scope=document.createElement('p');scope.className='footnote';
    scope.textContent='위험 초과일은 체결 후 변동성·CVaR에 기존 허용 오차를 적용한 지표입니다. 모든 종목·통화·집중 한도를 통과했다는 뜻은 아닙니다.';
    section.querySelector('.table-wrap').after(scope);
    if(data.return_risk_appendix){
      const appendix=document.createElement('p');appendix.className='context';
      appendix.innerHTML='<a href="return-risk.html">단순 매매 규칙을 포함한 기존 44개 조건 비교 보기 →</a><br><span class="footnote">기본 체결 16개·하루 지연 6개 후보를 두 기간에서 비교한 기존 결과입니다. 최종 주문 재검사 수정안의 8개 조건은 별도 비교에 포함됩니다. 평균 주식 비중·낙폭·비용·계산 실패도 함께 확인하세요.</span>';
      scope.after(appendix);
    }
    const labels={reference:'v0.1 그대로',execution_only:'체결 구조만 수정',bias_only:'수익 보정만 수정',combined:'체결 + 수익 보정'};
    const show=()=>{
      const period=$('research-period').value,scenario=$('research-scenario').value;
      $('research-comparison').innerHTML=researchRows(research,period,scenario).map(m=>{
        const audit=research.execution_audit.find(a=>a.period===period&&a.variant===m.variant&&a.scenario===scenario);
        return `<tr class="${m.variant==='combined'?'highlight':''}"><td>${esc(labels[m.variant])}</td><td>${pct(m.total_return)}</td><td>${pct(m.volatility)}</td><td>${pct(m.max_drawdown)}</td><td>${pct(m.equity_mean,1)}</td><td>${money(m.trade_cost_krw+m.fx_cost_krw)}</td><td>${number(m.postfill_risk_breach_days)}일</td><td>${audit&&Number.isFinite(audit.unresolved_plan_days)?number(audit.unresolved_plan_days)+'일':'계획 단계 검사 없음'}</td><td>${number(m.solver_failures)}</td></tr>`;
      }).join('');
    };
    $('research-period').addEventListener('change',show);$('research-scenario').addEventListener('change',show);show();
  }
  if(data.guarded_comparison){
    const research=data.guarded_comparison,section=document.createElement('section');section.id='guarded-comparison';
    section.innerHTML='<div class="section-title"><div><span class="eyebrow">FINAL ORDER CHECKS · RESEARCH</span><h2>최종 주문을 검사하면 결과가 달라질까</h2></div><span class="pill">개발 비교 · 채택 전</span></div><p class="context">각 기간마다 같은 1억 원에서 새로 시작했습니다. 수익과 함께 변동성·주식 비중을 확인하세요. 위 계좌 상세는 v0.1의 과거 재현이고, 아래는 수정안까지 포함한 성과 비교입니다.</p><div class="panel-heading"><label>비교 기간<select id="guarded-period"><option value="extension">2026.04–06 · 추가 기간</option><option value="development">2024.10–2026.03 · 개발 기간</option></select></label><label>체결 조건<select id="guarded-scenario"><option value="base">기본 체결</option><option value="one_day_delay">하루 지연</option></select></label></div><div class="table-wrap panel"><table class="guarded-table"><caption class="sr-only">모든 후보와 비교군의 비용 차감 성과</caption><thead><tr><th>후보</th><th>순수익</th><th>연 변동성</th><th>최대 낙폭</th><th>평균 주식</th><th>거래·환전 비용</th><th>변동성·CVaR 초과</th><th>전체 위험 항목 초과</th><th>주문 계획 미해결</th><th>계산·주문 검사 실패</th></tr></thead><tbody id="guarded-rows"></tbody></table></div><p class="footnote">전체 위험 항목을 검사하지 않았던 비교군은 미측정으로 표시합니다. 기존 변동성·CVaR 초과일과 새 전체 항목 진단은 정의가 다릅니다. 비교군의 위험 정책 차이도 함께 고려해야 합니다. 주문 계획 미해결은 수정안의 최종 검사 기준이며, 계산·주문 검사 실패는 최적화 계산 중단과 산출 주문의 최종 검사 거절을 포함한 사건 수입니다. 실패 날짜 수나 실제 위험 초과와 구분합니다.</p><p class="context breach">기존 v0.2 체결 후보에는 순주문 비용과 계획 비용이 달라질 수 있는 결함이 확인됐습니다. 그 장부는 비교용으로 보존하며, 계획의 위험 준수를 입증하는 자료로 사용하지 않습니다.</p><h3>같은 전망에서 실행 구조를 바꾼 차이</h3><div class="table-wrap panel"><table><thead><tr><th>수정 후보</th><th>비교 후보</th><th>누적 수익 차이</th><th>변동성 비율</th><th>판단일</th></tr></thead><tbody id="guarded-effects"></tbody></table></div><p class="footnote">수익 차이는 해당 기간의 %p 차이입니다. 변동성 비율이 1보다 작으면 수정안의 흔들림이 작습니다. 이미 본 자료의 비교이며, 통계 구간도 독립 검증이나 수익 보장이 아닙니다.</p><h3>수익과 변동성의 95% 구간</h3><div class="table-wrap panel"><table><caption class="sr-only">같은 날짜를 묶어 재표집한 수익 차이와 변동성 비율</caption><thead><tr><th>수정 후보</th><th>비교 후보</th><th>묶음</th><th>연 수익 차이 구간</th><th>변동성 비율 구간</th><th>해석</th></tr></thead><tbody id="guarded-uncertainty"></tbody></table></div><p class="footnote">10·20·60일 묶음을 각각 2,000회 재표집했습니다. 수익 구간은 누적 차이가 아닌 연환산 차이입니다. 변동성 비율 구간이 1을 포함하면 감소를 단정할 수 없습니다. 기간이 부족하거나 재표집된 비교군의 변동성이 0이면 산출 불가로 남깁니다. 이미 본 기간의 설명용 구간이며 미사용 자료의 검증을 대신하지 않습니다.</p><p class="context">모델 채택 보류 · 앞으로 쌓은 모의·실전 관찰 각 0일 · 최종 주문 검사 후에도 가격 급변·부분 체결에 따른 위험을 다시 확인해야 합니다.</p>';
    document.querySelector('main').insertBefore(section,document.querySelector('main > footer'));
    if(data.guarded_holdings)section.querySelector('.context').textContent='각 기간마다 같은 1억 원에서 새로 시작했습니다. 수익과 함께 변동성·주식 비중을 확인하세요. 위 계좌 선택에서 각 수정안의 요청·승인·체결 후 비중을 확인할 수 있습니다.';
    const show=()=>{
      const period=$('guarded-period').value,scenario=$('guarded-scenario').value;
      const rows=guardedRows(research,period,scenario);
      $('guarded-rows').innerHTML=rows.map(m=>`<tr class="${m.family==='guarded'?'highlight':''}"><td>${esc(m.label)}</td><td>${pct(m.total_return)}</td><td>${pct(m.volatility)}</td><td>${pct(m.max_drawdown)}</td><td>${pct(m.equity_mean,1)}</td><td>${money(m.trade_cost_krw+m.fx_cost_krw)}</td><td>${diagnosticDays(m.postfill_risk_breach_days)}</td><td>${diagnosticDays(m.postfill_all_constraint_breach_days)}</td><td>${diagnosticDays(m.guard_unresolved_days)}</td><td>${number(m.solver_failures)}건</td></tr>`).join('');
      const label=(family,variant)=>rows.find(m=>m.family===family&&m.variant===variant)?.label||variant;
      $('guarded-effects').innerHTML=research.paired_comparisons.filter(c=>c.period===period&&c.scenario===scenario).map(c=>`<tr><td>${esc(label('guarded',c.variant))}</td><td>${esc(label('frozen_v02',c.reference_variant))}</td><td>${number(c.cumulative_return_difference*100,2)}%p</td><td>${Number.isFinite(c.volatility_ratio)?number(c.volatility_ratio,3):'산출 불가'}</td><td>${number(c.decision_days)}일</td></tr>`).join('');
      $('guarded-uncertainty').innerHTML=guardedUncertaintyRows(research,period,scenario).map(c=>`<tr><td>${esc(label('guarded',c.variant))}</td><td>${esc(label('frozen_v02',c.reference_variant))}</td><td>${c.block_days}일</td><td>${esc(c.return_range)}</td><td>${esc(c.volatility_range)}</td><td>${esc(c.status)}</td></tr>`).join('');
    };
    $('guarded-period').addEventListener('change',show);$('guarded-scenario').addEventListener('change',show);show();
    $('period').textContent=data.period.join(' — ')+' · v0.1 과거 계좌 재현';
    const matched=research.metrics.filter(m=>m.family==='guarded').map(m=>({candidate:m,reference:research.metrics.find(r=>r.family==='frozen_v02'&&r.variant==='reference'&&r.period===m.period&&r.scenario===m.scenario)})).filter(m=>m.reference);
    const higher=matched.filter(m=>m.candidate.total_return>m.reference.total_return).length;
    const lowerVol=matched.filter(m=>m.candidate.volatility<m.reference.volatility).length;
    const card=document.querySelector('.decision-card');card.querySelector('h3').textContent='최종 주문 검사까지 추가한 결과';
    card.querySelector('p').textContent=`같은 기간·체결 조건의 v0.1 대비 ${matched.length}개 조건 중 수익이 높았던 조건은 ${higher}개, 변동성이 낮았던 조건은 ${lowerVol}개입니다. 수익 우위는 일관되지 않았으며, 최종 주문 검사 후에도 위험 항목 초과가 남았습니다. 전체 비교를 바탕으로 채택을 보류합니다.`;
    card.querySelector('a').href='#guarded-comparison';card.querySelector('a').textContent='모든 후보 비교 보기 →';
  }
  if(data.v02_research?.known_defects?.items?.some(item=>item.id==='candidate-boundary-ties-follow-ticker-order')){
    const section=$('guarded-comparison')||$('v02-research');
    if(section){
      const note=document.createElement('p');note.className='footnote';
      note.textContent='공통 후보 선별의 한계: 상위 20개 경계에서 기대수익이 같으면 종목 코드 순서에 따라 일부만 남습니다. 주문 재검사도 이 후보군을 유지합니다. 동점 종목을 함께 비교하는 수정안의 수익 효과는 별도 검증이 필요합니다.';
      section.appendChild(note);
    }
  }
  $('brief-open').addEventListener('click',()=>{$('brief-text').textContent=briefText(data);$('brief-dialog').showModal();});
  document.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',()=>$(b.dataset.close).close()));
  $('theme').addEventListener('click',()=>{const active=document.body.classList.toggle('dark');$('theme').setAttribute('aria-pressed',String(active));$('theme').textContent=active?'라이트모드':'다크모드';});
})(typeof globalThis!=='undefined'?globalThis:this);
