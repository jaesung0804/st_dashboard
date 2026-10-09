(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.InvestmentMetrics=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const definitions={
    fin_revenue_growth:['매출 성장률','pct','표시 보고기간 매출 ÷ 전년 같은 보고기간 매출 − 1'],
    fin_gross_margin:['매출총이익률','pct','표시 보고기간 매출총이익 ÷ 같은 기간 매출'],
    fin_operating_margin:['영업이익률','pct','표시 보고기간 영업이익 ÷ 같은 기간 매출'],
    fin_roa:['ROA','pct','확인된 연간 순이익 ÷ 기말·전년 동기 말 평균 자산'],
    fin_cfo_assets:['영업현금 / 자산','pct','확인된 연간 영업현금흐름 ÷ 평균 자산'],
    fin_cfo_after_ppe_assets:['설비투자 후 현금 / 자산','pct','(영업현금흐름 − 유형자산 취득액 절댓값) ÷ 평균 자산. 전체 잉여현금흐름과 다를 수 있습니다.'],
    fin_accruals_assets:['발생액 / 자산','pct','(최근 12개월 순이익 − 영업현금흐름) ÷ 평균 자산'],
    fin_cash_assets:['현금 / 자산','pct','기말 현금 및 현금성자산 ÷ 자산'],
    fin_liabilities_assets:['부채 / 자산','pct','기말 총부채 ÷ 총자산. 차입금만의 비율이 아닙니다.'],
    equity_assets:['자본 / 자산','pct','1 − 총부채 / 총자산. 자본 = 자산 − 부채로 계산합니다.'],
    liabilities_equity:['부채 / 자본','pct','총부채 ÷ (총자산 − 총부채). 자본이 0 이하이면 계산하지 않습니다.'],
    fin_current_ratio:['유동비율','times','유동자산 ÷ 유동부채'],
    fin_ppe_sales:['설비투자 / 매출','pct','최근 12개월 유형자산 취득액 절댓값 ÷ 매출'],
    fin_interest_coverage:['이자보상배율','times','최근 12개월 영업이익 ÷ 이자비용 또는 확보된 금융비용']
  };
  const finite=v=>typeof v==='number'&&Number.isFinite(v);
  const financial=(r,k)=>r.financials?.latest?.metrics?.[k]??null;
  function scenario(r,fx=0){return r.scenarios?.[String(Number(fx))]||r.scenarios?.[Number(fx).toFixed(1)]||null;}
  function value(r,key,fx=0){
    if(key==='score')return scenario(r,fx)?.score??null;
    if(key.startsWith('ret'))return r.price_metrics?.returns?.[key.slice(3)]??null;
    if(key in definitions)return r.financials?.stale?null:financial(r,key);
    if(['volatility20','drawdown252','turnover20'].includes(key))return r.price_metrics?.[key]??null;
    return r[key]??null;
  }
  function select(rows,state){
    const key=state.market==='all'&&state.sort==='market_score'?'score':state.sort;
    const q=(state.q||'').trim().toLocaleLowerCase();
    const result=rows.filter(r=>(state.market==='all'||r.market===state.market)
      && (!state.opinion||state.opinion==='all'||(r.financial_opinion||r.opinion).status===state.opinion)
      && (!state.coverage||state.coverage==='all'||state.coverage==='scored'&&r.scored||state.coverage==='unscored'&&!r.scored||state.coverage==='warning'&&r.warning)
      && (!state.financial||state.financial==='all'||state.financial==='available'&&r.financials?.latest||state.financial==='fresh'&&r.financials?.latest&&!r.financials.stale||state.financial==='growth'&&!r.financials?.stale&&financial(r,'fin_revenue_growth')>0&&financial(r,'fin_operating_margin')>0)
      && `${r.ticker} ${r.name} ${r.price_metrics?.sector||''}`.toLocaleLowerCase().includes(q));
    const direction=state.direction==='asc'?1:-1;
    result.sort((a,b)=>{
      const av=value(a,key,state.fx),bv=value(b,key,state.fx),af=finite(av),bf=finite(bv);
      if(af!==bf)return af?-1:1;
      return af&&av!==bv?direction*(av-bv):a.market.localeCompare(b.market)||a.ticker.localeCompare(b.ticker);
    });
    return {rows:result,key};
  }
  return {definitions,finite,financial,scenario,value,select};
});
