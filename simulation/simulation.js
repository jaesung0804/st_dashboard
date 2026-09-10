'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const names = {pulse:'펄스',compound:'컴파운드',adaptive:'어댑티브',spy_cash_matched:'SPY 90% + 현금 10%',baseline_cash_matched:'동일표본 90% + 현금 10%',spy:'SPY 100%'};
  const colors = {pulse:'#196b60',compound:'#4277af',adaptive:'#aa7842',spy_cash_matched:'#344148',baseline_cash_matched:'#9caaa5',spy:'#809096'};
  const roles = {pulse:'단기 · 모멘텀',compound:'장기 · 재무 품질',adaptive:'혼합 · 방어'};
  const firms = ['pulse','compound','adaptive'];
  const chartKeys = [...firms,'spy_cash_matched','baseline_cash_matched'];
  const visible = new Set([...firms,'spy_cash_matched']);
  let payload, current, mode='nav';
  const num = (v,n=2) => Number.isFinite(v) ? v.toLocaleString('ko-KR',{maximumFractionDigits:n,minimumFractionDigits:n}) : '—';
  const pct = v => Number.isFinite(v) ? `${v>0?'+':''}${num(v*100)}%` : '—';
  const pp = v => `${v>0?'+':''}${num(v*100)}%p`;
  const money = v => Number.isFinite(v) ? `$${num(v,0)}` : '—';
  const esc = x => String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const cell = v => `<td>${v}</td>`;
  function updateChoices() {
    const scope=$('scope').value,variant=$('variant').value;
    for(const opt of $('scenario').options) opt.disabled=!payload.runs[`${scope}-${variant}${opt.value}`];
    if($('scenario').selectedOptions[0].disabled) $('scenario').value='';
    current=payload.runs[`${scope}-${variant}${$('scenario').value}`];
    render();
  }
  function render() {
    if(!current) return;
    const base=payload.runs[`${$('scope').value}-baseline`];
    const long=$('scope').value==='long';
    $('scopeNotice').className=long?'notice warning':'notice';
    $('scopeNotice').textContent=long?'장기 스트레스 연구: 2022년에 선정한 현재 보존 종목을 과거로 재생합니다. 미래 표본선정·생존편향이 있어 당시 실현 가능한 성과나 시장초과수익의 증거로 사용할 수 없습니다.':'동일 조건 비교: 1차와 같은 128종목·가격 원장·기간을 사용합니다. 보존 상장군의 생존편향과 미확정 보유가격이 남아 있으며, 이번 규칙 개선은 과거 결과를 본 뒤의 연구입니다.';
    $('context').textContent=`${current.start} → ${current.end} · 편도 ${10*current.cost_multiplier}bp · 신호 후 ${current.fill_delay}거래일 시가 · 회사당 초기 $100,000 · 현금 이자 0%`;
    $('companies').innerHTML=firms.map(c=>{
      const v=current.summary[c],b=base.summary[c];
      return `<article class="company" style="--color:${colors[c]}"><div class="name">${names[c]}<span class="tag">${roles[c]}</span></div><div class="return ${v.net_return>=0?'positive':'negative'}">${pct(v.net_return)}</div><div class="minor"><span>연환산 ${pct(v.cagr)}</span><span>낙폭 ${pct(v.max_drawdown)}</span></div><div class="delta">기존 규칙 대비 ${pp(v.net_return-b.net_return)} · 최종 ${money(v.final_nav)}</div></article>`;
    }).join('');
    $('performance').innerHTML=[...firms,'spy_cash_matched','baseline_cash_matched','spy'].map(c=>{
      const v=current.summary[c];
      return `<tr><th scope="row">${names[c]}</th>${cell(pct(v.net_return))}${cell(pct(v.cagr))}${cell(pct(v.max_drawdown))}${cell(money(v.cost_paid))}${cell(num(v.trade_count,0))}${cell(pct(v.stale_reserved_return))}</tr>`;
    }).join('');
    $('sourceLink').href=`https://github.com/jaesung0804/st_dashboard/tree/main/docs/replays/2026-09-10-v2/${current.id}`;
    $('legend').innerHTML=chartKeys.map(k=>`<label style="--color:${colors[k]}"><input type="checkbox" value="${k}" ${visible.has(k)?'checked':''}><i></i>${names[k]}</label>`).join('');
    $('legend').querySelectorAll('input').forEach(input=>input.addEventListener('change',()=>{input.checked?visible.add(input.value):visible.delete(input.value);draw();}));
    const records=current.governance||[];
    $('governanceLog').innerHTML=records.length?`<p>분기별 회사 심사 ${current.governance_count}건 · 현금 이동 ${current.transfer_count}건. 아래는 마지막 심사입니다.</p>`+records.slice(-3).map(r=>`<p><b>${esc(r.date)} · ${names[r.company]}</b><br>${Object.entries(r.weights).map(([k,v])=>`${esc(k)} ${num(v*100,0)}%`).join(' / ')}<br>중단 팀: ${r.paused.length?r.paused.map(esc).join(', '):'없음'}</p>`).join(''):'<p>선택한 실험은 고정 배분이거나 요약 실행입니다. 기본 성과배분 실험에서 상세 심사 기록을 볼 수 있습니다.</p>';
    draw();
  }
  function draw() {
    if(!current) return;
    const svg=$('chart'),points=current.chart;
    if(!points?.length) return;
    const width=Math.max(320,svg.clientWidth),height=width<550?270:340,L=65,R=18,T=18,B=38;
    svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
    const keys=chartKeys.filter(k=>visible.has(k));
    const val=(p,k)=>mode==='nav'?p.nav[k]-1:p.dd[k];
    let min=0,max=0;
    for(const p of points)for(const k of keys){min=Math.min(min,val(p,k));max=Math.max(max,val(p,k));}
    if(min===max){min=-.05;max=.05;}
    const padding=(max-min)*.06;min-=padding;max+=padding;
    const x=i=>L+i/(points.length-1)*(width-L-R),y=v=>T+(max-v)/(max-min)*(height-T-B);
    let html='<title id="chartTitle">회사별 '+(mode==='nav'?'누적 순수익률':'최대점 대비 낙폭')+'</title><desc id="chartDesc">월말 표본. 전체 일별 원장의 최대낙폭은 성과 표에 표시됩니다.</desc>';
    for(let i=0;i<=4;i++){
      const v=min+(max-min)*i/4,yy=y(v);
      html+=`<line x1="${L}" x2="${width-R}" y1="${yy}" y2="${yy}" stroke="#e5ebe6"/><text x="${L-10}" y="${yy+4}" text-anchor="end" fill="#687e75" font-size="11">${num(v*100,0)}%</text>`;
    }
    if(min<0&&max>0)html+=`<line x1="${L}" x2="${width-R}" y1="${y(0)}" y2="${y(0)}" stroke="#b9cbc0" stroke-dasharray="3 4"/>`;
    const labels=width<550?3:5;
    for(let i=0;i<labels;i++){
      const idx=Math.round(i/(labels-1)*(points.length-1));
      html+=`<text x="${x(idx)}" y="${height-10}" text-anchor="${i===0?'start':i===labels-1?'end':'middle'}" fill="#687e75" font-size="11">${points[idx].date.slice(0,7)}</text>`;
    }
    for(const k of keys){const path=points.map((p,i)=>`${i?'L':'M'}${x(i).toFixed(2)},${y(val(p,k)).toFixed(2)}`).join(' ');html+=`<path d="${path}" fill="none" stroke="${colors[k]}" stroke-width="${k==='spy_cash_matched'?1.6:2.1}" ${k.includes('matched')?'stroke-dasharray="5 4"':''}/>`;}
    html+='<line id="cursor" visibility="hidden" stroke="#647a6f" stroke-dasharray="3 3"/>';
    svg.innerHTML=html;
    svg.onpointermove=e=>{
      const rect=svg.getBoundingClientRect(),px=(e.clientX-rect.left)/rect.width*width;
      const idx=Math.max(0,Math.min(points.length-1,Math.round((px-L)/(width-L-R)*(points.length-1))));
      const p=points[idx],cursor=$('cursor');
      cursor.setAttribute('x1',x(idx));cursor.setAttribute('x2',x(idx));cursor.setAttribute('y1',T);cursor.setAttribute('y2',height-B);cursor.setAttribute('visibility','visible');
      $('tooltip').innerHTML=`<b>${p.date}</b>`+keys.map(k=>`<div><span>${names[k]}</span><b>${pct(val(p,k))}</b></div>`).join('');$('tooltip').hidden=false;
    };
    svg.onpointerleave=()=>{$('tooltip').hidden=true;$('cursor')?.setAttribute('visibility','hidden');};
  }
  function setup() {
    $('runCount').textContent=payload.total_experiments;
    const a=payload.runs['matched-baseline'].summary,b=payload.runs['matched-efficient'].summary;
    const reduction=1-b.pulse.cost_paid/a.pulse.cost_paid;
    $('findings').innerHTML=[['펄스 · 비용을 줄이는 것이 먼저',`거래 효율 변경으로 발생 비용 ${num(reduction*100,1)}% 감소. 같은 원장에서 순수익률은 ${pct(a.pulse.net_return)} → ${pct(b.pulse.net_return)}로 변했습니다.`],['컴파운드 · 같은 처방을 일괄 적용하지 않기',`순수익률은 기존 ${pct(a.compound.net_return)}, 거래 효율 변경 ${pct(b.compound.net_return)}. 장기 팀은 순위 유지 규칙의 부작용을 따로 평가합니다.`],['자금배분 · 실력과 자금 유입을 구분',`성과배분의 성적을 고정 배분과 비교합니다. 과거 승자에게 더 맡기는 규칙도 다음 구간에서 뒤처질 수 있습니다.`]].map(([h,p])=>`<article class="finding"><h3>${h}</h3><p>${p}</p></article>`).join('');
    $('dataSource').textContent=`장기 원본은 2006년부터 수집한 미국 4,971종목·약 1,593만 행입니다. 이번 비교에는 기존 128종목 ${num(payload.data_sources.long_rows,0)}행을 연결했습니다. 신규 장기 스냅샷 ${payload.data_sources.origin_counts.long_2006_snapshot}종목, 기존 원장 전체 유지 ${payload.data_sources.origin_counts.retained_2021_snapshot_not_spliced}종목. 장기 결과의 재무 공시 커버리지는 초기 구간에서 제한됩니다.`;
    if(payload.market_validation){
      $('marketValidation').hidden=false;const m=payload.market_validation;
      $('marketContent').innerHTML=`<p class="context">${esc(m.description)}</p><div class="table-wrap"><table><thead><tr><th>규칙</th><th>연환산 수익</th><th>최대낙폭</th><th>2020년 이후 순수익</th></tr></thead><tbody>${m.rows.map(r=>`<tr><th>${esc(r.name)}</th><td>${pct(r.cagr)}</td><td>${pct(r.max_drawdown)}</td><td>${pct(r.later_return)}</td></tr>`).join('')}</tbody></table></div><p class="context">${esc(m.caveat)}</p>`;
    }
    ['scope','variant','scenario'].forEach(id=>$(id).addEventListener('change',updateChoices));
    $('teamCompany').addEventListener('change',()=>{teamChoices();renderTeams();});
    $('teamExperiment').addEventListener('change',renderTeams);
    teamChoices();renderTeams();renderOperations();setupRound();setupResearch();
    const evol=Object.values(payload.evolution);
    $('evolutionResults').innerHTML='<div class="table-wrap"><table><thead><tr><th>조직 운영안</th>'+firms.map(c=>`<th>${names[c]} 순수익</th>`).join('')+'<th>상위자 심사 / 거절</th></tr></thead><tbody>'+evol.map(v=>`<tr><th>${v.label}</th>${firms.map(c=>cell(pct(v.summary[c].net_return))).join('')}<td>${v.senior_reviews} / ${v.senior_vetoes}</td></tr>`).join('')+'</tbody></table></div>';
    $('supervisionVerdict').textContent='감사 결정: 직원 분화와 상위자 심사 기능은 확보했습니다. 이번 자동 배분·심사안의 수익 개선은 확인되지 않아 실전 채택을 보류합니다. 펄스의 거래비용 개선과 컴파운드 성장·수익성 팀을 우선 연구하되, 장기 표본·시작 시점에 따른 차이를 더 확인합니다.';
    [['navMode','nav'],['ddMode','dd']].forEach(([id,m])=>$(id).addEventListener('click',()=>{mode=m;for(const k of ['navMode','ddMode']){$(k).classList.toggle('selected',k===id);$(k).setAttribute('aria-pressed',String(k===id));}draw();}));
    new ResizeObserver(draw).observe($('chart').parentElement);
    updateChoices();
  }
  function teamChoices(){
    const company=$('teamCompany').value, reviews=payload.team_reviews[company];
    $('teamExperiment').innerHTML=Object.entries(reviews).map(([key,r])=>`<option value="${key}">${r.case.source==='matched'?'2023–2026':'장기 (편향 포함)'} · ${r.case.interval}일 · ${r.case.cost_multiplier===2?'비용 2배':r.case.phase?'시작 '+r.case.phase+'일 이동':'기본'}</option>`).join('');
  }
  function renderTeams(){
    const c=$('teamCompany').value,org=payload.organization.companies[c],r=payload.team_reviews[c][$('teamExperiment').value];
    const rooms=Object.fromEntries(payload.meeting_rooms.map(x=>[x.key,x.url]));
    $('teamContext').innerHTML=`운용책임자 ${esc(org.cio_name)} · <a href="${esc(rooms[c])}">회사 회의실 ↗</a> · ${r.case.source==='matched'?'같은 시작 자금과 후보 기준으로 매매 주기를 맞춘 비교입니다.':'장기 표본선정·생존편향을 포함한 연구입니다.'} 직원의 운용·연구 상태와 마지막 시험 성적은 2차 상위자 심사안의 최종 기록이며, r03의 비교안별 자금배정은 별도입니다.`;
    $('teamPerformance').innerHTML=Object.entries(org.teams).map(([t,v])=>{const s=r.summary[t];return `<tr><th scope="row">${esc(v.name)}</th><td>${pct(s.net_return)}</td><td>${pct(s.max_drawdown)}</td><td>${money(s.cost_paid)}</td><td>${pct(s.rolling_1y.worst)}</td><td>${num(s.rolling_1y.positive_fraction*100,1)}%</td></tr>`;}).join('');
    const status={funded:'운용 배정',shadow:'연구 중',paused:'보류',retired:'퇴사'};
    const genome={original:'기존 규칙',efficient:'거래 효율',guarded:'위험 통제',moderate:'중간 강도 후속안'};
    $('staffRoster').innerHTML=Object.entries(org.teams).map(([t,team])=>`<article class="team-card"><div class="team-title"><div><h3>${esc(team.name)}</h3><p>팀장 ${esc(team.team_lead_name)} · 운용 주기 ${team.baseline_interval_sessions}일 / 효율안 ${team.efficient_interval_sessions}일</p></div><a href="${esc(rooms[t])}">팀 대화방 ↗</a></div><p class="context">${esc(team.strategy)}</p><div class="table-wrap"><table><thead><tr><th>직원</th><th>전략 버전</th><th>상태</th><th>모의 생성일</th><th>마지막 시험 순수익</th></tr></thead><tbody>${team.employees.map(e=>{const last=e.evaluations.at(-1);return `<tr><th>${esc(e.display_name)}</th><td>${genome[e.genome]}</td><td><span class="staff-status ${e.status}">${status[e.status]}</span></td><td>${e.born}</td><td>${last?pct(last.net_return)+' ('+last.date+')':'관찰 중'}</td></tr>`;}).join('')}</tbody></table></div>${team.employees.map(e=>briefDetails(e)).join('')}${team.employees.filter(e=>e.status==='retired').map(e=>`<p class="context">${esc(e.display_name)} · ${e.retired_at}: ${esc(e.retirement_reason)}</p>`).join('')}</article>`).join('');
  }
  function briefDetails(employee){
    const b=employee.strategy_brief;if(!b)return '';
    const fields=[['thesis','투자 논리'],['entry','매수 조건'],['exit','축소·매도'],['horizon','검토 주기'],['risk','위험·비용'],['evidence_asof','근거 기준일'],['execution_note','배정 계좌 운용']];
    const events=(payload.team_conversations?.[employee.team]||[]).filter(e=>e.employee===employee.id);
    return `<details><summary>${esc(employee.display_name)} · 승인 요청 전략·심사 대화</summary>${fields.map(([k,label])=>`<p><strong>${label}</strong> · ${esc(b[k])}</p>`).join('')}${events.map(e=>`<div class="notice"><p><strong>${esc(employee.display_name)} → 팀장</strong> · ${e.date} 모의 심사<br>위 전략으로 증액 심사를 요청합니다. 관찰 ${e.evidence.observations}거래일, 비용 2배 순수익 ${pct(e.evidence.cost2_net_return)}, 최대낙폭 ${pct(e.evidence.max_drawdown)}.</p><p><strong>팀장 → 직원</strong> · ${e.approved?'심사 통과':'보완·거절'}: ${e.reasons.length?esc(e.reasons.join(', ')):'비용·위험·관찰 기준 통과'}. ${esc((e.advice||[]).join(' '))}</p></div>`).join('')}<p class="context">실제 모의 심사 이벤트를 대화 형식으로 설명한 사후 기록입니다. 과거 실제 LLM 대화가 아니며, 심사 통과가 자동 증액을 뜻하지 않습니다.</p></details>`;
  }
  function setupRound(){
    if(!payload.round_analysis)return;$('roundComparison').hidden=false;
    const base=payload.round_analysis.rows.find(r=>r.mode==='efficient_control');
    $('roundScenario').innerHTML=base.cases.map(r=>{const suffix=r.case.slice('efficient_control'.length);const phase=suffix.match(/p(\d+)/)[1];return `<option value="${suffix}">검토 시점 +${phase}일 · ${suffix.includes('-c2')?'비용 2배':'기본 비용'}${suffix.includes('delay2')?' · 추가 1일 지연':''}</option>`;}).join('');
    ['roundCompany','roundScenario'].forEach(id=>$(id).addEventListener('change',renderRound));renderRound();
  }
  function renderRound(){
    const rows=payload.round_analysis.rows.filter(r=>r.company===$('roundCompany').value),suffix=$('roundScenario').value;
    const control=rows.find(r=>r.mode==='efficient_control').cases.find(r=>r.case==='efficient_control'+suffix);
    $('roundPerformance').innerHTML='<div class="table-wrap"><table><thead><tr><th>운영 지시</th><th>순수익</th><th>최대낙폭</th><th>비용</th><th>효율안 대비 수익 차이</th><th>수익·낙폭 동시 개선 조건</th></tr></thead><tbody>'+rows.map(row=>{const r=row.cases.find(c=>c.case===row.mode+suffix);return `<tr><th>${esc(row.label)}</th><td>${pct(r.net_return)}</td><td>${pct(r.max_drawdown)}</td><td>${money(r.cost_paid)}</td><td>${num(r.return_difference*100,2)}%p</td><td>${row.jointly_better_count} / ${row.scenario_count}</td></tr>`;}).join('')+'</tbody></table></div>'+`<p class="context">선택한 비용·체결 조건에서 SPY 90% + 초기 현금 10%의 순수익은 ${pct(control.net_return-control.excess_spy)}입니다.</p>`;
  }
  function setupResearch(){
    const library=payload.research;if(!library)return;$('researchLibrary').hidden=false;
    const companies=library.companies.concat(library.pilot_companies||[]);
    $('researchCount').textContent=`${companies.length}개 기업 · ${library.imported_hypotheses+(library.pilot_hypotheses||0)}개 가설 · 직원 요청 ${library.requests.length}개`;
    $('researchCoverage').textContent=`기존 연구실: ${library.imported_company_count}개 기업·${library.imported_hypotheses}개 기간별 가설. 기존 가설과 128종목 표본의 교집합은 ${library.imported_cohort_overlap.length}개였습니다. 실제 목표 편입의 ${(library.pilot_cohort_overlap||[]).join(' · ')}에 ${library.pilot_hypotheses||0}개 가설을 추가했습니다. 기업 논리의 투자 성과 검증은 별도로 남아 있습니다.`;
    $('researchCompany').innerHTML=companies.map(c=>`<option value="${esc(c.ticker)}">${esc(c.name||c.company_name||c.ticker)} · ${esc(c.ticker)}</option>`).join('');
    ['researchCompany','researchHorizon'].forEach(id=>$(id).addEventListener('change',renderResearch));
    $('researchRequests').innerHTML=library.requests.map(r=>`<details><summary>${esc(r.requester_name)} → 통합 연구실 · ${esc(r.target_tickers.join(' · '))}</summary><p><strong>실적 근거</strong> · 팀 순수익 ${pct(r.evidence.net_return)}, 최대낙폭 ${pct(r.evidence.max_drawdown)}, 비용 ${money(r.evidence.cost_paid)}. 마지막 신호 ${r.evidence.last_signal}.</p><p><strong>본인 전략</strong> · ${esc(r.employee_strategy.thesis)}</p><p><strong>요청</strong> · ${esc(r.question)}</p>${r.follow_up_tickers?.length?'<p>최근 목표 편입에서 함께 조사: '+esc(r.follow_up_tickers.join(' · '))+'</p>':''}<p><strong>연구실 회신</strong> · ${esc(r.laboratory_response)}</p><p>상태: ${esc(r.status)} · 검토 담당 ${esc(r.reviewer_name)}</p></details>`).join('');renderResearch();
  }
  function renderResearch(){
    const companies=payload.research.companies.concat(payload.research.pilot_companies||[]);
    const c=companies.find(x=>x.ticker===$('researchCompany').value),h=c?.horizons?.[$('researchHorizon').value];
    if(!h){$('researchThesis').innerHTML='<p class="empty">해당 기간의 논리 명세를 확인하고 있습니다.</p>';return;}
    const list=v=>Array.isArray(v)?v:[v].filter(Boolean);
    const blocks=[['투자 논리',h.thesis],['기업의 현금 연결',h.stockLogic||c.coreThesis||c.core_thesis],['매수 검토 조건',h.entry],['철회·종료·예외 기준',h.invalidations||h.exit_or_invalidation||h.exit],['추가로 필요한 자료',h.dataNeeded||h.data_needed]];
    const links=c.source_links||c.sources||[];
    $('researchThesis').innerHTML=`<article class="team-card"><p class="eyebrow">${esc((c.taxonomy||[]).join(' / '))}</p><h3>${esc(c.name||c.ticker)} · ${esc(h.label||$('researchHorizon').selectedOptions[0].textContent)}</h3><p class="context">${esc(c.status==='research_hypothesis'?'연구 가설 · 기업 고유 KPI 검증 대기':c.status||'연구 가설 · 기업 고유 KPI 검증 대기')} · 실행 승인 없음</p>${blocks.map(([label,values])=>`<p><strong>${label}</strong></p><ul>${list(values).map(v=>`<li>${esc(typeof v==='string'?v:JSON.stringify(v))}</li>`).join('')}</ul>`).join('')}<p class="context">작성·연결 기준 ${esc(c.known_at||c.authored_at||payload.research.updated_at)}. 이 문장의 가설을 검증한 실험: ${(h.tested_by_experiments||[]).length}개.</p><details><summary>공시·기업 자료 출처</summary><ul>${links.filter(s=>s&&typeof s==='object'&&/^https?:\/\//.test(s.url||'')).map(s=>`<li><a href="${esc(s.url)}">${esc(s.title||s.id||'원문')}</a></li>`).join('')}</ul></details></article>`;
  }
  function renderOperations(){
    const ops=payload.operations;if(!ops)return;$('operations').hidden=false;
    const room=payload.meeting_rooms.find(r=>r.key==='operations');
    const people=Object.values(ops.actors||{}).filter(a=>a.type==='simulated_rule_employee'&&a.role);
    let html=`<p class="context">${people.map(a=>esc(a.name)+' · '+esc(a.role)).join(' / ')}${room?' · <a href="'+esc(room.url)+'">운영조정 회의실 ↗</a>':''}</p>`;
    const round=payload.rounds?.rounds?.at(-1);
    if(round){const time=s=>new Date(s).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',hour12:false});const label={active:'회차 진행 기록',completed:'회차 결과 검토 완료',ended_awaiting_review:'종료 후 결과 검토 대기',deferred:'시작 보류'};html+=`<p><strong>${esc(round.id)} · ${esc(label[round.status]||round.status)}</strong> · ${time(round.actual_started_at||round.requested_at)}–${time(round.scheduled_end_at||round.session_deadline)} (한국시간), 직원 ${round.strategy_employee_count||0}명 명단 고정</p><p class="context">운영 큐 관찰: ${time(ops.reviewed_as_of)} (한국시간)</p>`;}
    if(ops.queue?.tasks){const labels={completed:'완료',in_progress:'진행',blocked:'차단',approval:'승인 대기',ready:'준비'};
      html+='<div class="table-wrap"><table><thead><tr><th>작업</th><th>담당</th><th>상태</th><th>다음 단계 / 차단 사유</th></tr></thead><tbody>'+ops.queue.tasks.map(t=>`<tr><th>${esc(t.title||t.id)}</th><td>${esc(ops.actors[t.owner]?.name||t.owner||'배정 필요')}</td><td>${esc(labels[t.status]||t.status)}</td><td>${esc(t.next_action||t.blocker_reason||'운영 검토 기록 참조')}</td></tr>`).join('')+'</tbody></table></div>';
    }
    $('operationsContent').innerHTML=html;
  }
  fetch('results.json',{cache:'no-cache'}).then(r=>{if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json();}).then(data=>{payload=data;setup();}).catch(error=>{
    $('scopeNotice').className='notice warning';$('scopeNotice').textContent=`결과를 불러오지 못했습니다 (${error.message}). 페이지를 새로고침하거나 아래 전체 실험 원장을 열어 주세요.`;
    $('companies').innerHTML='<p class="empty">결과 파일 연결 확인이 필요합니다.</p>';
  });
})();
