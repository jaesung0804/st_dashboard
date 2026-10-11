const assert = require('node:assert/strict');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const path = require('node:path');
const snippets = JSON.parse(execFileSync(process.env.PYTHON || 'python', ['-c', "import json,sys;sys.path.insert(0,'scripts');from dashboard_theme import theme_head_script,theme_toggle_script;print(json.dumps([theme_head_script(),theme_toggle_script()]))"], {cwd:path.resolve(__dirname,'..'),encoding:'utf8',env:{...process.env,PYTHONIOENCODING:'utf-8'}}));
for (const initial of [null,'light','dark','invalid','blocked']) {
  let saved=initial,writes=0;
  const events={},documentEvents={};
  const root={dataset:{},style:{}};
  const button={setAttribute(name,value){this[name]=value;},addEventListener(){}};
  const context=vm.createContext({document:{documentElement:root,body:{classList:{toggle(){}}},addEventListener(name,fn){documentEvents[name]=fn;},getElementById(){return button;}},window:{addEventListener(name,fn){events[name]=fn;}},localStorage:{getItem(){if(initial==='blocked')throw Error('disabled');return saved;},setItem(key,value){if(initial==='blocked')throw Error('disabled');saved=value;writes++;}}});
  for (const snippet of snippets) vm.runInContext(snippet.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1], context);
  const expected=initial==='light'?'light':'dark';
  assert.equal(root.dataset.theme,expected);
  assert.equal(root.style.colorScheme,expected);
  assert.equal(button['aria-pressed'],String(expected==='dark'));
  assert.equal(writes,0,'First visit is a default, not a recorded explicit choice');
  documentEvents.click({target:{closest(){return button;}},stopImmediatePropagation(){}});
  assert.equal(root.dataset.theme,expected==='dark'?'light':'dark');
  if(initial!=='blocked')assert.equal(saved,root.dataset.theme);
  events.storage({key:'dashboardTheme',newValue:'light'});
  assert.equal(root.dataset.theme,'light');
  events.storage({key:'dashboardTheme',newValue:null});
  assert.equal(root.dataset.theme,'dark');
}
console.log('PASS: dark default, explicit choices, unavailable storage, toggle persistence and cross-tab sync');
