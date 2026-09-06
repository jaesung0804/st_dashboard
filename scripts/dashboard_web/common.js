'use strict';
const EWS = (() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const numeric = value => value === null || value === undefined || value === '' ? NaN : Number(String(value).replace(/[$,원%]/g, ''));
  const percent = (value, signed = false) => {
    const n = numeric(value);
    return Number.isFinite(n) ? `${signed && n > 0 ? '+' : ''}${n.toLocaleString('ko-KR', {minimumFractionDigits:1, maximumFractionDigits:2})}%` : '자료 없음';
  };
  const number = value => Number(value || 0).toLocaleString('ko-KR');
  function price(row) {
    const n = numeric(row.closeRaw ?? row.close);
    if (!Number.isFinite(n)) return '시세 없음';
    return new Intl.NumberFormat('ko-KR', {style:'currency', currency:row.currency === 'USD' ? 'USD' : 'KRW', maximumFractionDigits:row.currency === 'USD' ? 2 : 0}).format(n);
  }
  function riskUpper(row) {
    const values = (row.riskEstimateRange || []).map(numeric).filter(Number.isFinite);
    return values.length ? Math.max(...values) : NaN;
  }
  const isScored = row => !row._quoteOnly;
  function candidate(row) {
    if (row._quoteOnly) return {label:'평가 없음', style:'', reasons:row._quote?.coverageReasons?.length ? row._quote.coverageReasons : ['이 신호일에 저장된 모델 평가가 없습니다.']};
    if (!row.modelVersion) return {label:row.isFinalCandidate ? '기존 후보 기록' : '기존 순위 기록', style:'', reasons:['이전 모델의 순위 점수입니다. 새 확률과 직접 비교하지 않습니다.']};
    if (row.isFinalCandidate) return {label:'관심 후보', style:'opportunity', reasons:['상승 조건과 하락 위험 조건을 모두 통과했습니다.']};
    const reasons = [];
    if (!row.isUpCandidate) reasons.push('상승 기회 20% 조건 미충족');
    const upper = riskUpper(row);
    if (Number.isFinite(upper) && upper >= 15) reasons.push(`하락 추정 상단 ${percent(upper)} · 기준 15% 미만`);
    if (!reasons.length) reasons.push('저장된 후보 판정: 조건 미충족 (화면 수치는 반올림됨)');
    return {label:'조건 미충족', style:'', reasons};
  }
  function score(row, head) {
    if (row._quoteOnly) return '<span class="muted">평가 없음</span>';
    const value = head === 'up' ? row.upScore : row.downRisk;
    if (!row.modelVersion) return `<span>${esc(value ?? '—')}</span><span class="subtext">기존 순위 점수</span>`;
    return `<span class="metric-number ${head === 'up' ? 'opportunity' : 'risk'}">${percent(value)}</span>`;
  }
  function returnText(quote) {
    if (quote?.returnStatus === 'available') return percent(quote.trailingReturn6mPct, true);
    return ({stale_quote:'선택일 시세 없음', insufficient_history:'126개 관측 미만', missing_adjusted_price:'조정 가격 부족', unverified_price_continuity:'가격 연속성 확인 필요', no_prices:'시세 없음'})[quote?.returnStatus] || '자료 없음';
  }
  function mergedRows(rows, context) {
    const quotes = new Map((context?.rows || []).map(r => [String(r.ticker), r]));
    const result = rows.map(row => ({...row, _quote:quotes.get(String(row.ticker)), _quoteOnly:false}));
    const scored = new Set(rows.map(row => String(row.ticker)));
    for (const [ticker, quote] of quotes) {
      if (!scored.has(ticker)) result.push({...quote, _quote:quote, _quoteOnly:true});
    }
    return result;
  }
  function selectRows(rows, {mode='all', query='', sort='upScore', ascending=false} = {}) {
    const normalize = s => String(s || '').toLocaleLowerCase().replace(/\s+/g, '');
    const q = normalize(query);
    const result = rows.filter(row => {
      if (q) return normalize(row.ticker).includes(q) || normalize(row.name).includes(q) || normalize(row._quote?.name).includes(q);
      if (mode === 'scored') return isScored(row);
      if (mode === 'final') return isScored(row) && Boolean(row.isFinalCandidate);
      if (mode === 'up') return isScored(row) && Boolean(row.isUpCandidate);
      if (mode === 'down') return isScored(row) && (row.modelVersion ? numeric(row.downRisk) >= 35 : row.isDownRed || row.downGrade === 'RED');
      return true;
    });
    result.sort((a, b) => {
      const aValue = sort === 'trailingReturn6mPct' ? a._quote?.trailingReturn6mPct : a[sort];
      const bValue = sort === 'trailingReturn6mPct' ? b._quote?.trailingReturn6mPct : b[sort];
      if (sort === 'name' || sort === 'ticker') return (ascending ? 1 : -1) * String(aValue || '').localeCompare(String(bValue || ''), 'ko');
      const an = numeric(aValue), bn = numeric(bValue);
      // A missing estimate is never 0% and always sorts below known values.
      if (!Number.isFinite(an)) return Number.isFinite(bn) ? 1 : String(a.ticker).localeCompare(String(b.ticker));
      if (!Number.isFinite(bn)) return -1;
      return (ascending ? 1 : -1) * (an - bn) || String(a.ticker).localeCompare(String(b.ticker));
    });
    return result;
  }
  async function json(path) {
    const response = await fetch(path, {cache:'no-store'});
    if (!response.ok) throw new Error(`자료를 불러오지 못했습니다 (${response.status}). 잠시 후 다시 시도해 주세요.`);
    return response.json();
  }
  async function context(manifest, date) {
    if (!manifest.priceContext?.available) return {data:null, warning:'가격 설명 자료가 없어 저장된 평가 종목만 검색합니다. 과거 6개월 수익률은 표시하지 않습니다.'};
    try { return {data:await json(`price_context/${encodeURIComponent(date)}.json`), warning:''}; }
    catch (error) { return {data:null, warning:'가격 설명 자료를 불러오지 못했습니다. 저장된 평가는 표시하지만 전체 종목 검색과 과거 수익률은 일부 제한됩니다.'}; }
  }
  function naver(row) {
    const ticker = String(row.ticker || '');
    const suffix = {NASDAQ:'.O',NYSE:'.N',NYSEAMERICAN:'.A',NYSEARCA:'.P'};
    return row.currency === 'USD' ? `https://m.stock.naver.com/worldstock/stock/${encodeURIComponent(ticker + (suffix[String(row.exchange || '').toUpperCase()] || '.O'))}/total` : `https://m.stock.naver.com/domestic/stock/${encodeURIComponent(ticker.padStart(6, '0'))}/total`;
  }
  function stockHref(ticker, date) { return `stock.html?ticker=${encodeURIComponent(ticker)}&date=${encodeURIComponent(date)}`; }
  function record(row) {
    if (row._quoteOnly) return '선택일의 모델 평가가 없습니다. 시세 자료는 보관된 최신 목록에서 조회합니다.';
    if (!row.modelVersion) return '기존 모델의 순위 기록입니다. 새 사건 확률과 직접 비교할 수 없습니다.';
    const kind = {live:'저장된 일별 추론', delayed:'지연 생성 · 당시 실시간 예측 아님', research:'연구용 재현', reconstructed:'사후 복원 · 당시 실시간 예측 아님'}[row.predictionKind] || '생성 유형 미확인';
    return `${esc(row.modelMonth)} 모델 · ${kind}<br>학습 자료 기준일 ${esc(row.trainingCutoff)} · 생성 ${esc(row.generatedAt)}<br>모델 ${esc(row.modelVersion)}`;
  }
  return {esc, numeric, percent, number, price, riskUpper, isScored, candidate, score, returnText, mergedRows, selectRows, json, context, naver, stockHref, record};
})();
