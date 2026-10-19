// Exercise the page's actual asynchronous playback code with a synthetic HA host.
const fs=require('node:fs');const vm=require('node:vm');const assert=require('node:assert/strict');const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'custom_components/router_lab/frontend/voice.html'),'utf8').match(/<script>([\s\S]*)<\/script>/)[1];
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function page(mode='natural'){
 const audit={plays:0,fetches:[],subscriptions:[],revoked:[],savedMode:mode};const nodes={};let callback;
 class Element{
  constructor(id){this.id=id;this.value=id==='mode'?'natural':'';this.children=[];this.classList={toggle(){}};this.disabled=false;this.scrollHeight=10}
  append(...elements){this.children.push(...elements)}remove(){}replaceChildren(){this.children=[]}
  pause(){}load(){}removeAttribute(name){if(name==='src')this.src=''}
  async play(){audit.plays++;this.onplaying?.()}
 }
 const get=id=>nodes[id]||(nodes[id]=new Element(id));
 const hass={callWS:async request=>request.type==='assist_pipeline/pipeline/list'?{pipelines:[{id:'pipeline',conversation_engine:'conversation.router_lab',tts_engine:'tts.piper'}]}:
  {response:{mode:request.service_data.mode?(audit.savedMode=request.service_data.mode):audit.savedMode}},
  connection:{async subscribeEvents(cb){audit.traceEvent=cb;return ()=>{}},subscribeMessage(cb,options){callback=cb;audit.subscriptions.push(options);return Promise.resolve(()=>{})}}};
 const context={document:{getElementById:get,createElement:tag=>new Element(tag)},window:{parent:{document:{querySelector:()=>({hass})}}},
  performance,AbortController,URL:{createObjectURL:()=> 'blob:complete-audio',revokeObjectURL:url=>audit.revoked.push(url)},
  fetch(url,options){let complete;const pending=new Promise(resolve=>complete=resolve);audit.fetches.push({url,options,complete});return Promise.resolve({ok:true,blob:()=>pending})}};
 vm.runInNewContext(source,context);
 return {get,audit,event:(type,data={})=>callback({type,data}),async submit(text='测试朗读'){get('input').value=text;await get('form').onsubmit({preventDefault(){}})}};
}
async function main(){let checks=0;
 const natural=page();await tick();await natural.submit();
 natural.audit.traceEvent({data:{entity_id:'sensor.router_lab_trace',new_state:{attributes:{elapsed_ms:12.38,recognition:{source:'voice',mode:'正常 · SenseVoice 中文',recognition_ms:723}}}}});
 assert.match(natural.get('recognition-status').textContent,/0\.72 秒/);assert.match(natural.get('recognition-status').textContent,/12\.4 毫秒/);checks++;
 natural.audit.traceEvent({data:{entity_id:'sensor.router_lab_trace',new_state:{attributes:{recognition:{source:'text'}}}}});
 assert.match(natural.get('recognition-status').textContent,/本次为文字输入/);assert.doesNotMatch(natural.get('recognition-status').textContent,/0\.72/);checks++;
 natural.audit.traceEvent({data:{entity_id:'sensor.router_lab_trace',new_state:{attributes:{recognition:{source:'voice',capture_ms:5000}}}}});
 assert.match(natural.get('recognition-status').textContent,/无法精确计算/);assert.doesNotMatch(natural.get('recognition-status').textContent,/5\.00/);checks++;
 natural.event('run-start',{conversation_id:'a',tts_output:{url:'/api/tts_proxy/test.mp3'}});
 assert.equal(natural.audit.plays,0);assert.equal(natural.audit.fetches.length,1);checks++;
 natural.event('intent-progress',{chat_log_delta:{role:'assistant',content:'欢迎回来。'}});
 natural.event('intent-end',{intent_output:{response:{speech:{plain:{speech:'欢迎回来。'}}}}});
 natural.event('run-end');assert.equal(natural.audit.plays,0);checks++;
 natural.audit.fetches[0].complete({type:'audio/mpeg'});await tick();await tick();
 assert.equal(natural.audit.plays,1);assert.equal(natural.get('audio').src,'blob:complete-audio');checks++;
 natural.get('mode').value='fast';await natural.get('mode').onchange();assert.equal(natural.audit.savedMode,'fast');checks++;
 await natural.submit();natural.event('run-start',{conversation_id:'a',tts_output:{url:'/api/tts_proxy/fast.mp3'}});
 assert.equal(natural.audit.plays,2);assert.equal(natural.audit.fetches.length,1);checks++;
 assert.equal(natural.audit.subscriptions[1].conversation_id,'a');assert.equal(natural.audit.subscriptions[1].end_stage,'tts');checks++;
 const cancelled=page();await tick();await cancelled.submit();
 cancelled.event('run-start',{conversation_id:'old',tts_output:{url:'/api/tts_proxy/old.mp3'}});cancelled.event('run-end');
 cancelled.get('new').onclick();assert.equal(cancelled.audit.fetches[0].options.signal.aborted,true);checks++;
 cancelled.audit.fetches[0].complete({type:'audio/mpeg'});await tick();await tick();assert.equal(cancelled.audit.plays,0);checks++;
 const report={checks,passed:checks,scope:'actual frontend script with synthetic HA events; no real device calls'};
 console.log(JSON.stringify(report));
}
main().catch(error=>{console.error(error);process.exitCode=1});
