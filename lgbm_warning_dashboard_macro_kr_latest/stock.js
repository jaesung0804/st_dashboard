'use strict';
(() => {
  const $ = id => document.getElementById(id), params = new URLSearchParams(location.search), ticker = params.get('ticker') || '';
  let manifest, history = [], historyWarning = '', request = 0;
  function tickerFile(value) {
    let name=encodeURIComponent(value).replace(/[!'()*]/g,c=>'%'+c.charCodeAt(0).toString(16).toUpperCase());
    const stem=(name.includes('.') ? name.slice(0,name.lastIndexOf('.')) : name).toUpperCase();
    if (/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$/.test(stem)) name+='_';
    return name+'.json';
  }
  function show(row, date) {
    const quote=row._quote, condition=EWS.candidate(row);
    $('title').textContent=`${row.name || ticker} · ${ticker}`;
    document.title=`${row.name || ticker} · 조기경보 상세`;
    $('stockMeta').textContent=`${date} 신호일 · ${EWS.price(row)}${row._quoteOnly && quote?.quoteDate && quote.quoteDate !== date ? ' ('+quote.quoteDate+' 보관 종가)' : ''}${row.exchange ? ' · '+row.exchange : ''}${row.sector ? ' · '+row.sector : ''}${row.detailSector ? ' / '+row.detailSector : ''}`;
    $('naver').href=EWS.naver(row); $('naver').hidden=false;
    const returnClass=EWS.numeric(quote?.trailingReturn6mPct)<0?'neg':'pos';
    const legacy=!row.modelVersion && !row._quoteOnly;
    const range=quote?.returnStatus==='available' ? `${EWS.esc(quote.returnStartDate)} → ${EWS.esc(quote.returnEndDate)}<br>126개 가격 관측 구간 · 보관 조정 종가 기준` : '해당일의 조정 가격과 126개 관측 구간이 모두 있어야 계산합니다.';
    const upTitle=legacy?'기존 상승 순위':'6개월 상승 기회', downTitle=legacy?'기존 하락 순위':'3개월 급락 위험';
    const legacyNote='이전 모델의 순위 점수입니다. 새 사건 확률과 비교하지 않습니다.';
    const upNote=legacy?legacyNote:'126개 관측 뒤 +20% 이상<br>그리고 비교 종목 중간값보다 +10%p 이상';
    const downNote=legacy?legacyNote:'63개 관측 안에 종가가 한 번이라도<br>신호 종가보다 −20% 이하';
    const cards=`<div class="metric-cards"><article class="metric-card opportunity-card"><h2>${upTitle}</h2><div class="big opportunity">${EWS.score(row,'up')}</div><p>${upNote}</p></article><article class="metric-card risk-card"><h2>${downTitle}</h2><div class="big risk">${EWS.score(row,'down')}</div><p>${downNote}</p></article><article class="metric-card"><h2>과거 6개월 수익률 · 실제 가격 변화</h2><div class="big ${quote?.returnStatus==='available'?returnClass:'muted'}">${EWS.returnText(quote)}</div><p>${range}</p></article></div>`;
    const status=`<section class="panel"><h2><span class="chip ${condition.style}">${condition.label}</span></h2><ul class="status-list">${condition.reasons.map(text=>`<li>${EWS.esc(text)}</li>`).join('')}</ul><p class="muted">후보 조건은 확인할 종목을 추리는 기준입니다. 예상 수익률이나 매매 지시를 뜻하지 않습니다.</p></section>`;
    const e=row.evidence || {};
    const evidence=row.modelVersion ? `<section class="panel"><h2>관측 지표와 의미</h2><div class="evidence-grid">${[
      ['1개월 수익률', EWS.percent(e.return20,true), '20개 관측 전보다 조정 종가가 얼마나 변했는지 보여줍니다.'],
      ['3개월 상대강도', EWS.percent(e.relative60,true).replace('%','%p'), '60개 관측 수익률과 시장 바스켓 수익률의 차이입니다. 플러스면 상대적으로 더 강했다는 뜻입니다.'],
      ['연율 변동성', EWS.percent(e.volatility20), '최근 20개 일간 수익률의 흔들림을 연간으로 환산했습니다. 상승·하락의 방향은 알려주지 않습니다.'],
      ['200일선 위 종목 비율', EWS.percent(e.breadth200), '이 종목만의 점수가 아닙니다. 가격 이력이 충분하고 거래가 있는 관측 종목들의 시장 폭입니다.']
    ].map(([label,value,explain])=>`<article><h3>${label}</h3><b>${value}</b><p>${explain}</p></article>`).join('')}</div><details><summary>하락 추정 범위와 위험 기여 보기</summary><p>보정 전후 추정 범위: <b>${(row.riskEstimateRange || []).map(v=>EWS.percent(v)).join(' – ') || '자료 없음'}</b>. 두 추정의 차이이며 신뢰구간이 아닙니다.</p><ul>${(row.riskFactors || []).map(f=>`<li>${EWS.esc(f.label)} · ${EWS.esc(f.direction)}</li>`).join('')}</ul><p class="muted">트리 모델 부분에서 기여가 큰 두 지표입니다. 인과관계나 혼합 모델 전체의 설명이 아닙니다.</p></details></section>` : '';
    $('content').innerHTML=cards+status+'<p class="notice">상승 기회는 사건의 추정 확률, 과거 수익률은 이미 관측한 가격 변화입니다. 앞으로 6개월의 평균 예상 수익률은 현재 모델이 계산하지 않습니다.</p>'+evidence+`<details><summary>모델·예측 생성 기록</summary><p class="record-meta">${EWS.record(row)}</p><p class="footnote">표시용 가격 변화는 보관 가격의 현재 정정 상태로 계산하며, 배당·수수료·실제 체결을 모두 반영한 투자 수익률이 아닙니다. 예측 원본의 확률은 그대로 유지합니다.</p></details>`;
    $('history').innerHTML=historyWarning ? `<p class="notice warning">${EWS.esc(historyWarning)}</p>` : !history.length ? '<p class="muted">공개된 기간에 이 종목의 저장된 평가가 없습니다.</p>' : '<div class="history-table"><table><thead><tr><th scope="col">신호일</th><th scope="col">상승 기회 / 기존 순위</th><th scope="col">급락 위험 / 기존 순위</th><th scope="col">모델·기록 유형</th></tr></thead><tbody>'+history.slice().reverse().map(r=>`<tr><td><a href="${EWS.stockHref(ticker,r.date)}">${EWS.esc(r.date)}</a></td><td>${EWS.score(r,'up')}</td><td>${EWS.score(r,'down')}</td><td>${r.modelVersion?EWS.esc(r.modelMonth)+(r.predictionKind==='delayed'?' · 지연 생성':r.predictionKind==='research'?' · 연구용':' · 일별 추론'):'기존 순위 기록'}</td></tr>`).join('')+'</tbody></table></div>';
  }
  async function loadDate() {
    const id=++request, date=$('date').value;
    $('content').textContent='선택일 자료를 불러오는 중입니다.';
    try {
      const [forecasts,lookup]=await Promise.all([EWS.json(`walkforward_scores_by_date/${encodeURIComponent(date)}.json`),EWS.context(manifest,date)]);
      if(id!==request)return;
      $('dataWarning').hidden=!lookup.warning; $('dataWarning').textContent=lookup.warning;
      const row=EWS.mergedRows(forecasts,lookup.data).find(r=>String(r.ticker)===ticker);
      if(!row)throw new Error('이 날짜의 보관 자료에서 종목을 찾지 못했습니다. 전체 종목 검색에서 확인해 주세요.');
      show(row,date);
    }catch(error){if(id===request)$('content').innerHTML=`<div class="empty">${EWS.esc(error.message)} <a href="dashboard.html">전체 종목 검색</a></div>`;}
  }
  async function init() {
    if(!ticker)throw new Error('종목이 지정되지 않았습니다. 전체 종목 검색에서 선택해 주세요.');
    manifest=await EWS.json('manifest.json');
    for(const date of manifest.dates || [])$('date').add(new Option(date,date));
    if((manifest.dates || []).includes(params.get('date')))$('date').value=params.get('date');
    try {
      const response=await fetch(`stock_history/${tickerFile(ticker)}`,{cache:'no-store'});
      if(response.ok)history=(await response.json()).rows || [];
      else if(response.status!==404)historyWarning='이전 예측 기록을 불러오지 못했습니다.';
    }catch(error){historyWarning='이전 예측 기록을 불러오지 못했습니다.';}
    $('date').onchange=loadDate;
    await loadDate();
  }
  init().catch(error=>{$('content').textContent=error.message;});
})();
