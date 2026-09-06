'use strict';
(() => {
  const $ = id => document.getElementById(id), {esc, number, percent, numeric} = EWS;
  const decimal = value => Number.isFinite(numeric(value)) ? numeric(value).toFixed(3) : '자료 없음';
  const pct = value => Number.isFinite(numeric(value)) ? percent(numeric(value) * 100) : '자료 없음';
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  function activate(pane, focus=false) {
    for (const tab of tabs) {
      const selected = tab.dataset.pane === pane;
      tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1;
      $(`pane-${tab.dataset.pane}`).hidden = !selected;
      if (focus && selected) tab.focus();
    }
  }
  tabs.forEach((tab,index) => {
    tab.addEventListener('click', () => activate(tab.dataset.pane));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = tabs.length - 1;
      if (next !== undefined) { event.preventDefault(); activate(tabs[next].dataset.pane, true); }
    });
  });
  if (location.hash === '#comparison') activate('comparison');
  if (location.hash === '#shadow') activate('shadow');
  const table = (headers, rows) => `<table><thead><tr>${headers.map(h=>`<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(v=>`<td>${v}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  let report, stockNames=new Map(), page=0;
  function renderComparison() {
    if (!report) return;
    const c = report.comparison;
    if (!c) { $('comparisonBasis').textContent='별도 비교 배치의 검증 결과가 아직 없습니다.'; return; }
    const selected = $('compareFold').value, head = $('compareHead').value;
    const folds = selected === 'all' ? c.folds : c.folds.filter(f=>f.month === selected);
    const m = selected === 'all' ? c.aggregate : folds[0].metrics;
    $('comparisonBasis').textContent=`시험 월 ${folds.map(f=>f.month).join(', ')} · ${number(m.price[head].signal_dates)}개 신호일 · 관측 ${number(m.price[head].rows)}건 · 결과 제외 ${number(m.price[head].missing)}건. 과거 재현 시험이며 실전 투자 성과가 아닙니다.`;
    const fields=[['실제 사건 발생률','event_rate',pct],['AUC ↑','auc',decimal],['AR ↑','ar',decimal],['KS ↑','ks',decimal],['PR-AUC (AP) ↑','average_precision',decimal],['Brier ↓','brier',decimal],['Log loss ↓','log_loss',decimal]];
    $('comparisonMetrics').innerHTML=table(['지표','가격','가격 + 거시'],fields.map(([label,key,fmt])=>[esc(label),fmt(m.price[head][key]),fmt(m.macro[head][key])]));
    const warnings=[];
    for (const fold of folds) for (const arm of ['price','macro']) {
      if (fold.calibration_accepted?.[arm]?.[head] === false) warnings.push(`${fold.month} ${arm === 'price' ? '가격' : '거시 추가'}`);
    }
    $('calibrationWarning').hidden=!warnings.length;
    $('calibrationWarning').textContent=`${warnings.join(', ')}: 보정 구간에서 순위가 뒤집혀 확률 보정을 적용하지 않았습니다. 원래 추정치를 유지한 불안정 사례입니다.`;
    const reliability=[];
    for (const arm of ['price','macro']) for (const bin of m[arm][head].reliability || []) {
      reliability.push([arm === 'price' ? '가격' : '가격 + 거시',number(bin.count),pct(bin.predicted),pct(bin.observed)]);
    }
    $('reliability').innerHTML=table(['모델','표본','평균 예측 확률','실제 발생률'],reliability);
    const cohort=[['상승 목표 적중률','hit_rate',pct],['발생률 대비 Lift','lift',decimal],['5일 평균 가격 수익률','return_6m',pct],['전체 관측 종목 평균','benchmark_6m',pct],['전체 평균 대비 초과','excess_6m',v=>Number.isFinite(numeric(v)) ? `${(Number(v)*100).toFixed(2)}%p` : '자료 없음']];
    $('cohortReturns').innerHTML=table(['상승 확률 상위 10% · 날짜별 동일 비중','가격','가격 + 거시'],cohort.map(([label,key,fmt])=>[esc(label),fmt(m.price.top_decile[key]),fmt(m.macro.top_decile[key])]));
  }
  function renderShadow() {
    if (!report) return;
    const latest=report.latest_shadow;
    if (!latest) { $('shadowStatus').textContent='저장된 실험 추론이 아직 없습니다.'; return; }
    const meta=latest.meta;
    $('shadowStatus').textContent=`신호일 ${meta.signal_date} · ${meta.models.price.month} 고정 모델 · 거시 보관 월 ${meta.vintage_month} · ${meta.prediction_kind === 'delayed' ? '지연 생성: 당시 실시간 예측 아님' : '저장된 일별 실험 추론'}`;
    const q=$('shadowQuery').value.toLocaleLowerCase().replace(/\s+/g,''), key=$('shadowSort').value;
    const rows=latest.rows.filter(r=>`${r.ticker} ${stockNames.get(r.ticker)||''}`.toLocaleLowerCase().replace(/\s+/g,'').includes(q)).sort((a,b)=>b[key]-a[key] || a.ticker.localeCompare(b.ticker));
    const pages=Math.max(1,Math.ceil(rows.length/25)); page=Math.max(0,Math.min(page,pages-1));
    $('shadowRows').innerHTML=rows.length ? table(['종목','가격 상승','거시 추가 상승','가격 급락','거시 추가 급락'],rows.slice(page*25,(page+1)*25).map(r=>[
      `<a href="${EWS.stockHref(r.ticker,meta.signal_date)}">${esc(stockNames.get(r.ticker)||r.ticker)}</a><span class="subtext">${esc(r.ticker)}</span>`,pct(r.price_up),pct(r.macro_up),pct(r.price_down),pct(r.macro_down)
    ])) : '<p class="empty">해당 종목의 실험 점수가 없습니다. 운영 화면의 전체 검색에서 시세와 평가 조건을 확인할 수 있습니다.</p>';
    $('shadowPage').textContent=`${page+1} / ${pages} · ${number(rows.length)}개 종목`;
    $('shadowPrev').disabled=page===0; $('shadowNext').disabled=page>=pages-1;
  }
  function renderProduction() {
    const r=report.live_records || {};
    const rows=[];
    for (const [month,heads] of Object.entries(report.production_calibration_not_test || {})) for (const [head,details] of Object.entries(heads)) {
      const m=details.calibration_diagnostics_not_test;
      if (m) rows.push([esc(`${month} ${head === 'up' ? '상승' : '급락'}`),number(m.rows),decimal(m.auc),decimal(m.brier),pct(m.event_rate)]);
    }
    $('productionDiagnostics').innerHTML=`<p>저장된 실전 추론 ${number(r.live_dates)}일 · 지연 생성 ${number(r.delayed_dates)}일. 신호 생성 이후 126개 관측이 쌓이기 전에는 실전 6개월 성과를 알 수 없습니다.</p>${table(['보정 자료 진단','표본','AUC','Brier','발생률'],rows)}`;
    const outcomes=report.shadow_outcomes, prospective=[];
    for (const [kind,arms] of Object.entries(outcomes?.cohorts || {})) for (const arm of ['price','macro']) for (const head of ['up','down']) {
      const m=arms[arm][head];
      if (m.rows) prospective.push([kind === 'live' ? '실전 저장' : '지연 생성',arm === 'price' ? '가격' : '가격 + 거시',head === 'up' ? '상승' : '급락',number(m.signal_dates),number(m.rows),decimal(m.auc),decimal(m.brier)]);
    }
    $('prospectiveResults').innerHTML=prospective.length ? table(['기록 종류','모델','사건','신호일','관측','AUC','Brier'],prospective) : '<p class="notice">아직 결과 관측 기간을 채운 실험 예측이 없습니다. 3개월 급락·6개월 상승 결과가 성숙하면 여기에 표시됩니다.</p>';
  }
  for (const id of ['compareHead','compareFold']) $(id).addEventListener('change',renderComparison);
  $('shadowQuery').addEventListener('input',()=>{page=0;renderShadow();}); $('shadowSort').addEventListener('change',()=>{page=0;renderShadow();});
  $('shadowPrev').addEventListener('click',()=>{page--;renderShadow();}); $('shadowNext').addEventListener('click',()=>{page++;renderShadow();});
  async function init() {
    try {
      report=await EWS.json('model_report.json');
      try { const index=await EWS.json('stock_index.json');stockNames=new Map(index.map(r=>[r.ticker,r.name])); } catch (_) { /* Tickers remain searchable. */ }
      $('reportStatus').textContent=`자료 생성 ${report.generated_at} · 운영 모델과 별도 실험을 구분해 표시합니다.`;
      for (const fold of report.comparison?.folds || []) { const option=document.createElement('option');option.value=fold.month;option.textContent=fold.month;$('compareFold').append(option); }
      if (report.macro) $('macroProvenance').textContent=`${report.macro.vintages}개 월별 자료 보관 · 최신 보관 월 ${report.macro.latest_vintage} · 원본 해시와 적용 시점 보존`;
      renderComparison();renderShadow();renderProduction();
    } catch (error) {
      $('reportStatus').textContent='검증 자료를 불러오지 못했습니다. 잠시 후 다시 확인해 주세요. 사용법은 계속 볼 수 있습니다.';
      $('comparisonBasis').textContent='검증 결과를 확인할 수 없습니다.';$('shadowStatus').textContent='실험 결과를 확인할 수 없습니다.';
    }
  }
  init();
})();
