const assert=require('node:assert/strict');
const M=require('../scripts/dashboard_web/investment-metrics.js');
const row=(ticker,market,score,growth,stale=false)=>({ticker,name:ticker,market,scored:true,market_score:99,
  scenarios:{'0':{score},'0.05':{score:score+(market==='us'?10:0)}},opinion:{status:'withheld'},
  financials:{latest:{metrics:{fin_revenue_growth:growth,fin_operating_margin:.1}},stale}});
const rows=[row('A','kr',30,.2),row('B','us',40,0),row('C','kr',null,null),row('D','us',50,.9,true)];
const base={market:'all',sort:'market_score',fx:0};
assert.equal(M.select(rows,base).key,'score');
assert.deepEqual(M.select(rows,base).rows.map(r=>r.ticker),['D','B','A','C']);
assert.deepEqual(M.select(rows,{...base,sort:'fin_revenue_growth'}).rows.map(r=>r.ticker),['A','B','C','D']);
assert.deepEqual(M.select(rows,{...base,sort:'fin_revenue_growth',direction:'asc'}).rows.map(r=>r.ticker),['B','A','C','D']);
assert.deepEqual(M.select(rows,{...base,financial:'growth'}).rows.map(r=>r.ticker),['A']);
assert.equal(M.value(rows[1],'fin_revenue_growth'),0);
assert.equal(M.value(rows[3],'fin_revenue_growth'),null);
assert.equal(M.value(rows[0],'score',.05),30);
assert.equal(M.value(rows[1],'score',.05),50);
assert.equal(M.select(rows,{...base,q:'missing'}).rows.length,0);
assert.equal(rows[0].ticker,'A');
console.log('PASS: global ordering, missing/stale metrics, zero values, FX and combined filters');
