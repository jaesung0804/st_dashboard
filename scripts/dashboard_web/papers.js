'use strict';
const PaperLibrary = (() => {
  const tracks = {mechanical:'기계적 매매',long_term:'장기투자 판단'};
  const reviews = {metadata:'서지 확인',abstract:'초록 검토',partial_text:'본문 일부 검토',full_text:'원문 심층 검토'};
  const byline = p => p.authors.length ? p.authors.join(' · ') : '저자 정보 확인 중';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const paragraphs = value => (Array.isArray(value) ? value : [value]).map(v => `<p>${esc(v)}</p>`).join('');
  const plain = value => (Array.isArray(value) ? value : [value]).join(' ');
  const normalize = value => String(value || '').normalize('NFKC').toLocaleLowerCase().replace(/\s+/g, '');
  const source = value => {try {const url = new URL(value);return url.protocol === 'https:' && !url.username && !url.password ? url.href : '';} catch {return '';}};
  function select(papers, filters = {}) {
    const query = normalize(filters.q);
    return papers.filter(p => (!filters.year || String(p.year) === filters.year) && (!filters.field || p.field === filters.field) &&
      (!filters.track || p.tracks.includes(filters.track)) && (!filters.review || p.review_status === filters.review) &&
      (!query || normalize([p.title,...p.authors,p.field,plain(p.research_question)].join(' ')).includes(query)))
      .slice().sort((a,b) => b.year - a.year || a.title.localeCompare(b.title, 'ko'));
  }
  const tags = p => `<div class="paper-tags"><span class="paper-tag">${p.year}</span><span class="paper-tag">${esc(p.field)}</span>${p.tracks.map(t => `<span class="paper-tag">${tracks[t]}</span>`).join('')}<span class="paper-tag ${p.review_status === 'full_text' ? 'review' : 'partial'}">${reviews[p.review_status]}</span></div>`;
  function card(p) {
    return `<article class="paper-card">${tags(p)}<h3><a href="#paper=${encodeURIComponent(p.id)}" data-paper="${esc(p.id)}">${esc(p.title)}</a></h3><p class="paper-byline">${esc(byline(p))}${p.publication ? ' · '+esc(p.publication) : ''}</p><p class="paper-question">${esc(plain(p.research_question))}</p><div class="paper-card-bottom"><a href="#paper=${encodeURIComponent(p.id)}" data-paper="${esc(p.id)}">연구 노트 읽기 →</a><a href="${esc(source(p.source_url))}" target="_blank" rel="noopener noreferrer">원문 ↗</a></div></article>`;
  }
  function detail(p) {
    const sections = [['research_question','01','연구 질문'],['empirical_results','03','실증 결과'],['limitations','04','한계'],['application_hypothesis','05','적용 가설'],['validation_method','06','검증 방법']];
    const block = ([key,n,label]) => `<section><h3>${n} / ${label}</h3>${paragraphs(p[key])}</section>`;
    const math = `<section><h3>02 / 핵심 수식과 직관</h3>${p.core_math.map(e => `<div class="paper-equation"><pre>${esc(e.formula)}</pre>${paragraphs(e.intuition)}</div>`).join('')}</section>`;
    return `${tags(p)}<h2 id="paper-title" tabindex="-1">${esc(p.title)}</h2><p class="paper-byline">${esc(byline(p))} · ${esc(p.publication || p.year)}</p>${p.review_status !== 'full_text' ? `<p class="paper-review-note">${reviews[p.review_status]} 단계입니다. 원문 전체 검토 및 결과 재현 여부를 구분해 확인하세요.</p>` : ''}${p.review_note ? `<p class="paper-review-note">검토 범위: ${esc(p.review_note)}</p>` : ''}${block(sections[0])}${math}${sections.slice(1).map(block).join('')}<section><h3>07 / 원문 링크</h3><a class="paper-source" href="${esc(source(p.source_url))}" target="_blank" rel="noopener noreferrer">원문 사이트에서 읽기 ↗</a></section>`;
  }
  function mount() {
    const $ = id => document.getElementById(id);
    const papers = JSON.parse($('paper-data').textContent).papers;
    const controls = {q:$('paper-query'),year:$('paper-year'),field:$('paper-field'),track:$('paper-track'),review:$('paper-review')};
    for (const [key,values] of [['year',[...new Set(papers.map(p=>p.year))].sort((a,b)=>b-a)],['field',[...new Set(papers.map(p=>p.field))].sort((a,b)=>a.localeCompare(b,'ko'))]]) {
      for (const value of values) controls[key].add(new Option(String(value),String(value)));
    }
    $('paper-total').textContent = String(papers.length);
    const dialog = $('paper-dialog');
    let opened = '', returnFocus = null;
    function readFilters() {
      const params = new URLSearchParams(location.search);
      for (const [key,control] of Object.entries(controls)) control.value = (params.get(key) || '').slice(0,200);
    }
    function render() {
      const result = select(papers,Object.fromEntries(Object.entries(controls).map(([key,control]) => [key,control.value])));
      $('paper-count').textContent = `${result.length} / ${papers.length}편`;
      $('paper-list').innerHTML = result.map(card).join('');
      $('paper-empty').hidden = result.length > 0;
      if (papers.length && !result.length) $('paper-empty').innerHTML = '<h3>조건에 맞는 논문이 없습니다.</h3><p>검색어나 필터를 바꿔 다시 찾아보세요.</p>';
    }
    function updateUrl() {
      const url = new URL(location.href);
      for (const [key,control] of Object.entries(controls)) {if(control.value)url.searchParams.set(key,control.value);else url.searchParams.delete(key);}
      history.replaceState(null,'',url);
      render();
    }
    function syncDetail() {
      const params = new URLSearchParams(location.hash.slice(1));
      const paper = papers.find(p => p.id === params.get('paper'));
      if (!paper) {if(dialog.open)dialog.close();opened='';return;}
      if (opened === paper.id && dialog.open) return;
      opened = paper.id;
      $('paper-detail').innerHTML = detail(paper);
      if (!dialog.open) dialog.showModal();
      dialog.scrollTop = 0;
      $('paper-title').focus({preventScroll:true});
    }
    function closeDetail() {
      const url = new URL(location.href);url.hash='';history.replaceState(null,'',url);
      opened='';dialog.close();
      if(returnFocus?.isConnected)returnFocus.focus();else controls.q.focus();
    }
    $('paper-list').addEventListener('click',event => {const link=event.target.closest('[data-paper]');if(!link)return;event.preventDefault();returnFocus=link;history.pushState(null,'',link.href);syncDetail();});
    $('paper-close').addEventListener('click',closeDetail);
    dialog.addEventListener('cancel',event => {event.preventDefault();closeDetail();});
    dialog.addEventListener('click',event => {if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)closeDetail();}});
    $('paper-filters').addEventListener('submit',event=>event.preventDefault());
    $('paper-filters').addEventListener('input',updateUrl);
    $('paper-filters').addEventListener('reset',()=>{requestAnimationFrame(updateUrl);});
    window.addEventListener('popstate',()=>{readFilters();render();syncDetail();});
    window.addEventListener('hashchange',syncDetail);
    readFilters();render();syncDetail();
  }
  return {select,card,detail,source,mount};
})();
if(typeof module !== 'undefined')module.exports=PaperLibrary;
if(typeof document !== 'undefined')PaperLibrary.mount();
