'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let manifest, rows = [], page = 1, ascending = false, request = 0;
  const labels = ['종목', '종가', '과거 6개월', '6개월 상승 기회', '3개월 급락 위험', '후보 판정'];
  function render() {
    const query = $('query').value.trim();
    $('mode').disabled = Boolean(query);
    const filtered = EWS.selectRows(rows, {query, mode:$('mode').value, sort:$('sort').value, ascending});
    const size = Number($('pageSize').value);
    const pages = Math.max(1, Math.ceil(filtered.length / size));
    page = Math.max(1, Math.min(page, pages));
    const start = (page - 1) * size, shown = filtered.slice(start, start + size), date = $('date').value;
    $('meta').textContent = `${date} · ${EWS.number(filtered.length)}종목${query ? ' · 전체 종목에서 검색 중' : ''}${shown.length ? ` · ${EWS.number(start + 1)}–${EWS.number(start + shown.length)}번째 표시` : ''}`;
    $('pageInfo').textContent = `${page} / ${pages}`;
    $('prev').disabled = page <= 1;
    $('next').disabled = page >= pages;
    $('dir').textContent = ascending ? '낮은 순 ↑' : '높은 순 ↓';
    if (!shown.length) {
      $('table').innerHTML = `<div class="empty"><h3>${query ? '검색한 종목을 찾지 못했습니다' : '이 조건을 통과한 종목이 없습니다'}</h3><p>${query ? '종목명이나 티커를 확인해 주세요. 보관 목록에 없는 종목에는 임의의 점수를 만들지 않습니다.' : '전체 종목에서 가격·확률·미충족 조건을 확인할 수 있습니다.'}</p><button id="showAll" type="button">전체 종목 보기</button></div>`;
      $('showAll').onclick = () => {$('query').value=''; $('mode').value='all'; page=1; render();};
      return;
    }
    $('table').innerHTML = '<table><thead><tr>' + labels.map(t => `<th scope="col">${t}</th>`).join('') + '</tr></thead><tbody>' + shown.map(row => {
      const condition = EWS.candidate(row), quote = row._quote;
      const pastClass = EWS.numeric(quote?.trailingReturn6mPct) < 0 ? 'neg' : 'pos';
      const cells = [
        `<div><a class="stock-name" href="${EWS.stockHref(row.ticker,date)}">${EWS.esc(row.name || row.ticker)}</a><span class="subtext">${EWS.esc(row.ticker)}${row.exchange ? ' · ' + EWS.esc(row.exchange) : ''}</span></div>`,
        `<div>${EWS.price(row)}${row._quoteOnly && quote?.quoteDate !== date ? `<span class="subtext">${EWS.esc(quote?.quoteDate || '시세 없음')}${quote?.quoteDate ? ' 보관 종가' : ''}</span>` : ''}</div>`,
        `<div><span class="${quote?.returnStatus === 'available' ? 'metric-number ' + pastClass : 'muted'}">${EWS.returnText(quote)}</span></div>`,
        `<div>${EWS.score(row, 'up')}</div>`, `<div>${EWS.score(row, 'down')}</div>`,
        `<div><span class="chip ${condition.style}">${condition.label}</span><div class="condition-note">${EWS.esc(condition.reasons[0])}</div></div>`
      ];
      return '<tr>' + cells.map((cell,i) => `<td data-label="${labels[i]}"${i === 5 ? ' class="condition-cell"' : ''}>${cell}</td>`).join('') + '</tr>';
    }).join('') + '</tbody></table>';
  }
  async function loadDate() {
    const id = ++request, date = $('date').value;
    rows = []; page = 1;
    $('table').innerHTML = '<div class="empty">선택일 자료를 불러오는 중입니다.</div>';
    try {
      const [forecasts, lookup] = await Promise.all([EWS.json(`walkforward_scores_by_date/${encodeURIComponent(date)}.json`), EWS.context(manifest,date)]);
      if (id !== request) return;
      rows = EWS.mergedRows(forecasts, lookup.data);
      $('dataWarning').hidden = !lookup.warning;
      $('dataWarning').textContent = lookup.warning;
      const up = forecasts.filter(r=>r.isUpCandidate).length, final = forecasts.filter(r=>r.isFinalCandidate).length;
      $('rowCount').textContent=EWS.number(forecasts.length); $('upCount').textContent=EWS.number(up);
      $('finalCount').textContent=EWS.number(final); $('lookupCount').textContent=EWS.number(rows.length);
      const legacy = !forecasts.some(r => r.modelVersion);
      labels[3] = legacy ? '기존 상승 순위' : '6개월 상승 기회';
      labels[4] = legacy ? '기존 하락 순위' : '3개월 급락 위험';
      $('candidateNotice').textContent = legacy ? '이 날짜는 기존 모델의 순위 기록입니다. 새 모델의 확률과 직접 비교하지 마세요.' : `${final ? '관심 후보 ' + EWS.number(final) + '개' : '관심 후보 0개'} · 상승 조건 통과 ${EWS.number(up)}개 중 하락 추정 상단 15% 미만까지 통과한 결과입니다. 종목 검색은 후보 여부와 관계없이 작동합니다.`;
      if (manifest.predictionKindsByDate?.[date] === 'reconstructed') $('candidateNotice').textContent = '사후 복원 자료입니다. 당시 실시간으로 생성된 예측이나 실전 성과가 아닙니다. ' + $('candidateNotice').textContent;
      if (manifest.marketName === '한국' && date.startsWith('2026-09')) $('candidateNotice').textContent += ' 이 월 모델은 상승 확률 보정 구간의 구분력이 낮습니다. 설명·검증 페이지의 확률 진단을 함께 확인하세요.';
      render();
    } catch (error) {
      if (id !== request) return;
      $('table').innerHTML = `<div class="empty">${EWS.esc(error.message)} <button id="retry" type="button">다시 불러오기</button></div>`;
      $('meta').textContent='선택일 결과를 불러오지 못했습니다.';
      $('retry').onclick=loadDate;
    }
  }
  async function init() {
    manifest = await EWS.json('manifest.json');
    for (const date of manifest.dates || []) $('date').add(new Option(date + (manifest.predictionKindsByDate?.[date] === 'reconstructed' ? ' · 사후 복원' : ''),date));
    const params = new URLSearchParams(location.search);
    if ((manifest.dates || []).includes(params.get('date'))) $('date').value=params.get('date');
    $('query').value=params.get('q') || '';
    $('date').onchange=loadDate;
    for (const id of ['query','mode','sort','pageSize']) $(id).addEventListener(id==='query'?'input':'change',()=>{page=1;render();});
    $('clearQuery').onclick=()=>{$('query').value='';page=1;render();$('query').focus();};
    $('dir').onclick=()=>{ascending=!ascending;render();};
    $('prev').onclick=()=>{page--;render();}; $('next').onclick=()=>{page++;render();};
    await loadDate();
  }
  init().catch(error=>{$('table').innerHTML=`<div class="empty">${EWS.esc(error.message)}</div>`;});
})();
