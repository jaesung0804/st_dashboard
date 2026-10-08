const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assets = path.join(__dirname, '../scripts/dashboard_web');
const sandbox = {Intl, URLSearchParams};
vm.runInNewContext(fs.readFileSync(path.join(assets, 'common.js'), 'utf8') + '\nglobalThis.api=EWS;', sandbox);
const ui = sandbox.api;
const frozen = [{ticker:'005930', name:'삼성전자', modelVersion:'kr-2026-09-ews-price-v1', upScore:35.33, downRisk:59.55, riskEstimateRange:[32.31,59.55], isUpCandidate:true, isFinalCandidate:false}];
const before = JSON.stringify(frozen);
const rows = ui.mergedRows(frozen, {rows:[
  {ticker:'005930',trailingReturn6mPct:0,returnStatus:'available'},
  {ticker:'NEW',name:'미평가 종목',trailingReturn6mPct:null,coverageReasons:['가격 이력 부족']}
]});
assert.equal(ui.selectRows(rows,{mode:'final'}).length,0);
assert.equal(ui.selectRows(rows,{mode:'final',query:'삼성 전자'}).length, 0, 'Search must respect the visibly selected candidate filter');
assert.equal(ui.selectRows(rows,{mode:'all',query:'삼성 전자'}).map(r=>r.ticker).join(),'005930');
assert.equal(ui.selectRows(rows,{mode:'all',query:'NEW'})[0]._quoteOnly,true);
assert.equal(ui.candidate(rows[1]).label,'평가 없음');
assert.ok(ui.candidate(rows[0]).reasons.some(s=>s.includes('59.55%')));
for(const ascending of [false,true]) {
  assert.equal(ui.selectRows(rows,{sort:'upScore',ascending})[0].ticker,'005930');
  assert.equal(ui.selectRows(rows,{sort:'trailingReturn6mPct',ascending})[0].ticker,'005930');
}
assert.equal(ui.returnText(rows[0]._quote),'0.0%');
assert.equal(ui.percent(null),'자료 없음');
assert.equal(ui.percent(30),'30.0%');
assert.equal(ui.returnText({returnStatus:'unverified_price_continuity'}),'가격 연속성 확인 필요');
assert.ok(ui.score({_quoteOnly:true},'up').includes('평가 없음'));
assert.ok(ui.score({upScore:88},'up').includes('기존 순위 점수'));
assert.equal(JSON.stringify(frozen),before,'UI enrichment must not mutate forecasts');
assert.equal(ui.esc('<script>'),'&lt;script&gt;');
const view = ui.viewQuery({q:'삼성 & 전자',mode:'scored',sort:'name',ascending:true,size:50,page:2,date:'2026-09-17'});
const stockUrl = new URLSearchParams(ui.stockHref('005930','2026-09-17',view).split('?')[1]);
const back = ui.dashboardHref(stockUrl.get('view'),'2026-09-16');
const restored = ui.viewState(back.split('?')[1]);
assert.equal(restored.q,'삼성 & 전자'); assert.equal(restored.mode,'scored');
assert.equal(restored.date,'2026-09-16'); assert.equal(restored.page,2); assert.equal(restored.size,50); assert.equal(restored.ascending,true);
assert.ok(ui.dashboardHref('https://evil.example').startsWith('dashboard.html?'));
assert.equal(ui.viewState('mode=bad&sort=bad&page=-3&size=999').mode,'all');
assert.equal(ui.viewState('page=-3').page,1);
assert.equal(ui.freshness('2026-09-17',new Date('2026-10-08T00:00:00Z')).stale,true);
assert.equal(ui.freshness('2026-10-07',new Date('2026-10-08T00:00:00Z')).label,'1일 전 신호');
assert.equal(ui.freshness('invalid').stale,true);
for(const file of ['common.js','dashboard.js','stock.js','model.js','home.js'])new vm.Script(fs.readFileSync(path.join(assets,file),'utf8'),{filename:file});
const dashboard=fs.readFileSync(path.join(assets,'dashboard.html'),'utf8');
assert.match(dashboard, /data-mode="all" aria-pressed="true"/);
assert.ok(!dashboard.includes('@@GUIDE@@'));
assert.ok(fs.readFileSync(path.join(assets,'model.html'),'utf8').includes('@@GUIDE@@'));
console.log('PASS: combined filters, null/legacy semantics, immutable forecasts, safe return navigation, freshness, JavaScript syntax');
