// Exercise the actual mode-switch page against a synthetic Supervisor.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'custom_components/router_lab/frontend/stt_modes.html'),'utf8').match(/<script>([\s\S]*)<\/script>/)[1];
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function page({engine='stt.faster_whisper',failRestart=false,concurrent=false,restoreFails=false}={}){
 const nodes={},calls=[];let restarts=0,options={model:'base-int8',beam_size:1,stt_library:'faster-whisper',language:'zh',debug_logging:false,custom_model:'keep-my-custom-source'};
 const get=id=>nodes[id]??={disabled:true,textContent:'',classList:{toggle(){}},setAttribute(k,v){this[k]=v}};
 const hass={async callWS(p){calls.push(p);if(p.type==='assist_pipeline/pipeline/list')return {pipelines:[{conversation_engine:'conversation.router_lab',stt_engine:engine}]};
  assert.equal(p.type,'supervisor/api');assert.match(p.endpoint,/^\/addons\/core_whisper\/(info|options|restart)$/);
  if(p.endpoint.endsWith('/info'))return {state:'started',options:{...options}};
  if(p.endpoint.endsWith('/options')){options={...p.data.options};return {}};
  restarts++;if(restoreFails||(failRestart&&restarts===1)){if(concurrent)options={...options,model:'medium-int8'};throw Error('restart failed')}return {}}};
 const context={document:{getElementById:get},window:{parent:{document:{querySelector:()=>({hass})}},addEventListener(){}}};
 vm.runInNewContext(source,context);return {get,calls,options:()=>options};
}
(async()=>{
 let checks=0;
 const p=page();await tick();assert.equal(p.get('fast')['aria-pressed'],'true');checks++;
 await p.get('normal').onclick();assert.equal(p.options().model,'auto');assert.equal(p.options().stt_library,'funasr');assert.equal(p.options().beam_size,5);assert.equal(p.options().custom_model,'keep-my-custom-source');assert.equal(p.options().debug_logging,false);assert.ok(!p.options().initial_prompt.includes('激光'));checks++;
 assert.equal(p.get('normal')['aria-pressed'],'true');assert.equal(p.get('normal').disabled,false);checks++;
 await p.get('fast').onclick();assert.equal(p.options().model,'base-int8');assert.equal(p.options().beam_size,1);checks++;
 const bad=page({engine:'stt.other'});await tick();await bad.get('normal').onclick();assert.equal(bad.calls.filter(p=>p.method==='post').length,0);assert.match(bad.get('status').textContent,/没有使用官方 Whisper/);checks++;
 const failed=page({failRestart:true});await tick();await failed.get('normal').onclick();assert.equal(failed.options().model,'base-int8');assert.equal(failed.options().custom_model,'keep-my-custom-source');assert.match(failed.get('status').textContent,/已恢复/);checks++;
 const raced=page({failRestart:true,concurrent:true});await tick();await raced.get('normal').onclick();assert.equal(raced.options().model,'medium-int8');assert.match(raced.get('status').textContent,/未覆盖/);checks++;
 const broken=page({restoreFails:true});await tick();await broken.get('normal').onclick();assert.match(broken.get('status').textContent,/自动恢复未完成/);checks++;
 console.log(JSON.stringify({checks,passed:checks,scope:'Actual page; preserves unrelated settings, blocks other engines, rolls back restart failure, avoids overriding a concurrent change'}));
})().catch(e=>{console.error(e);process.exitCode=1});
