const assert = require('node:assert/strict');
const ensure = require('../scripts/ensure_pages_build.cjs');
const build = (id, status, commit='target') => ({url:`https://api.github.com/builds/${id}`,status,commit,created_at:`2026-10-08T08:46:${String(id).padStart(2,'0')}Z`});
async function run(sequence, expectedRequests, fails=false) {
  let index=0,requests=0;
  const github={rest:{repos:{
    listPagesBuilds:async()=>({data:sequence[Math.min(index++,sequence.length-1)]}),
    requestPagesBuild:async()=>{requests++;}
  }}};
  const result=ensure(github,{},'target',()=>{},async()=>{});
  if(fails) await assert.rejects(result,/did not finish/); else await result;
  assert.equal(requests,expectedRequests);
}
(async()=>{
  await run([[build(1,'built')]],0);
  await run([[build(1,'building')],[build(2,'building'),build(1,'errored')],[build(2,'built'),build(1,'errored')]],0);
  await run([[build(1,'errored')],[build(1,'errored')],[build(2,'queued'),build(1,'errored')],[build(2,'built')]],1);
  await run([[build(1,'built','different')]],1,true);
  await run([[build(1,'errored')]],1,true);
  console.log('PASS: reuse active/completed builds, ignore cancelled predecessor, reject wrong commit and failed builds');
})().catch(e=>{console.error(e);process.exitCode=1;});
