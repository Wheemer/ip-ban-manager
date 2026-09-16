const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let Panel;
const timers = new Set();
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../custom_components/ip_ban_manager/panel.js'),'utf8'), {
  HTMLElement: class {},
  customElements:{get:()=>undefined,define:(_,value)=>{Panel=value;}},
  URLSearchParams,
  window:{
    setTimeout:(fn,ms)=>{const timer=setTimeout(fn,ms);timers.add(timer);return timer;},
    clearTimeout:timer=>{clearTimeout(timer);timers.delete(timer);},
    clearInterval,
  },
});
(async()=>{
  const panel=new Panel();
  panel._data={translations:{},banned_ips:[]};
  panel._isEditing=()=>true;
  panel._statusPath=()=> 'status';
  panel._api=async()=>({translations:{},banned_ips:[{count:1}]});
  let full=0,partial=0;
  panel._renderSafely=()=>{full++;};
  panel._renderIncremental=()=>{partial++;};
  await panel._load({silent:true});
  assert.equal(full,0);
  assert.equal(partial,1);
  assert.equal(panel._data.banned_ips[0].count,1);
  assert.equal(timers.size,0);
  let resolve;
  panel._api=()=>new Promise(r=>{resolve=r;});
  const old=panel._load({silent:true});
  panel._updateGeneration=1;
  panel._busy=true;
  panel._data={saved:true};
  resolve({stale:true});
  await old;
  assert.equal(panel._data.saved,true);
  assert.equal(panel._busy,true);
  assert.equal(partial,1);
  assert.equal(timers.size,0);
  console.log('Background updates: incremental only, editing allowed, stale responses ignored, timers cleared.');
})().catch(error=>{console.error(error);process.exitCode=1;});
