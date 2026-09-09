'use strict';
(() => {
  const $=id=>document.getElementById(id), {esc}=EWS;
  const num=(v,n=3)=>v===null||v===undefined||!Number.isFinite(Number(v))?'자료 없음':Number(v).toFixed(n);
  const pct=v=>v===null||v===undefined?'자료 없음':`${num(v*100,2)}%`;
  const table=(heads,rows)=>`<table><thead><tr>${heads.map(h=>`<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(v=>`<td>${v}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  const arms={price:'가격',price_macro:'가격 + 거시',price_accounting:'가격 + 재무',price_macro_accounting:'가격 + 거시 + 재무'};
  let report;
  function metrics() {
    const c=report.accounting;if(!c)return;
    const head=$('accountingHead').value,fold=$('accountingFold').value;
    const data=fold==='all'?c.aggregate:c.folds.find(f=>f.month===fold)?.arms;
    $('accountingMetrics').innerHTML=table(['모델','AUC ↑','Brier ↓','실제 발생률','상위 10% 6개월 수익률'],Object.entries(arms).map(([k,label])=>{
      const m=fold==='all'?data?.[k]?.[head]:data?.[k]?.[head]?.metrics;
      return [esc(label),num(m?.auc),num(m?.brier,4),pct(m?.event_rate),head==='up'?pct(m?.top_mean_return):'상승 탭에서 확인'];
    }));
  }
  function stocks() {
    const query=$('accountingQuery').value.trim().toLowerCase();
    const rows=(report.accounting_scores||[]).filter(r=>`${r.ticker} ${r.name}`.toLowerCase().includes(query)).slice(0,20);
    $('accountingStocks').innerHTML=table(['기업 / 사용 공시','영업이익률 / 매출 성장','현금흐름 / 발생액 ÷ 자산','가격 / 재무 상승 확률','거시 / 거시·재무 상승 확률'],rows.map(r=>[
      `<a href="stock.html?ticker=${encodeURIComponent(r.ticker)}">${esc(r.name||r.ticker)}</a><br>${esc(r.ticker)} · ${esc(String(r.date).slice(0,10))}<br><small>접수 ${esc(String(r.filed).slice(0,10))}<br>${esc(r.filing_id)}</small>`,
      `${pct(r.fin_operating_margin)} / ${pct(r.fin_revenue_growth)}`,
      `${pct(r.fin_cfo_assets)} / ${pct(r.fin_accruals_assets)}`,
      `${pct(r.price_up)} / ${pct(r.price_accounting_up)}`,
      `${pct(r.price_macro_up)} / ${pct(r.price_macro_accounting_up)}`]));
  }
  EWS.json('research_report.json').then(r=>{
    report=r;
    if(!r.available){$('researchStatus').textContent='재무·확률 진단 자료가 아직 없습니다.';return;}
    const study=r.market_study,head=study.diagnostics.latest_model['2026-09'].up,comp=head.components;
    $('researchStatus').textContent=`2026-09-04 기준 분석 · 8월~9월 사후 복원 ${study.diagnostics.daily.length}개 거래일. 과거 기록과 사후 복원은 날짜 선택창에서 구분합니다.`;
    $('compressionMetrics').innerHTML=table(['상승 확률 구성','평균','표준편차 (%p)','10~90 분위 범위'],['up_raw','up_cal_only','up'].map((k,i)=>[ ['보정 전','보정 함수만 적용','화면 표시 (두 값 50:50)'][i],pct(comp[k].mean),num(comp[k].std*100,2),`${pct(comp[k].p10)} ~ ${pct(comp[k].p90)}`]));
    $('compressionNotice').textContent=`보정 기울기 ${num(head.calibration.coef,5)}, 보정 구간 AUC ${num(head.calibration_diagnostics_not_test.auc)}. 이 구간은 보정에 사용했으므로 독립 시험이 아닙니다. 범위를 넓히는 것 자체가 예측력 개선은 아닙니다.`;
    const change=study.model_transition.up;
    $('modelChange').textContent=`8/31→9/1 같은 ${study.model_transition.common_tickers}종목의 평균 상승 확률 변화 ${num(change.observed_change_pp,2)}%p = 가격 특징 변화 ${num(change.new_price_features_effect_pp,2)}%p + 월 모델 교체 ${num(change.monthly_model_effect_pp,2)}%p. 모델이 바뀐 날의 점수 변화를 시장 변화로만 읽지 마세요.`;
    $('correlationMetrics').innerHTML=table(['9/4 종목 간 Spearman 상관','상승 확률'],Object.entries(head.feature_spearman).filter(([,v])=>v!==null).sort((a,b)=>Math.abs(b[1])-Math.abs(a[1])).slice(0,8).map(([k,v])=>[esc(k),num(v)]));
    const lag=r.cross_market.prior_us_session_to_kr;
    $('crossMarket').textContent=`미국 전 거래일→한국 당일의 보관 종목 동일가중 일 수익률 상관: Pearson ${num(lag.pearson)}, ${lag.n}개 관측. 짧은 한 구간의 동행성으로, 인과관계나 검증된 선행 예측력이 아닙니다.`;
    if(r.accounting){
      const c=r.accounting,cov=c.coverage;
      $('accountingStatus').textContent=`사전 선정 ${cov.cohort_issuers}기업 파일럿 · 공시일 확인 ${r.accounting_audit.issuers}기업 · 현재 비교 가능 ${c.latest_rows}기업. 동일 종목·시점·목표로 네 모델을 비교했습니다. 생존 편향과 표본 크기의 한계가 남습니다.`;
      for(const f of c.folds.filter(f=>f.status==='evaluated'))$('accountingFold').add(new Option(f.month,f.month));
      $('accountingHead').onchange=metrics;$('accountingFold').onchange=metrics;$('accountingQuery').oninput=stocks;metrics();stocks();
    }else $('accountingStatus').textContent='공시 시점을 확인한 재무 비교 결과를 준비 중입니다.';
  }).catch(e=>{$('researchStatus').textContent=`진단 자료를 불러오지 못했습니다: ${e.message}`;});
})();
