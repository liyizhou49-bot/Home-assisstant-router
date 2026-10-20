// Execute the actual page script: labels, pagination, XLSX download and error recovery.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'custom_components/router_lab/frontend/route_modes.html'),'utf8').match(/<script>([\s\S]*)<\/script>/)[1];
class Element{constructor(tag='div'){this.tag=tag;this.children=[];this.disabled=false;this.textContent='';this.classList={add(){},remove(){}}}append(n){this.children.push(n)}replaceChildren(){this.children=[]}setAttribute(k,v){this[k]=v}click(){this.clicked=true}remove(){this.removed=true}}
const tick=()=>new Promise(r=>setImmediate(r));
async function main(){const nodes={},calls=[],links=[],blobs=[];let fail=false,subscription;const get=id=>nodes[id]??=new Element();
const rows=Array.from({length:31},(_,i)=>({id:'s'+i,text:i===0?'<img onerror=alert(1)>':'原话'+i,count:i+1,analysis_status:'review_only',reason:'需大模型理解',last_seen:'2026-10-20T12:00:00+08:00',label:''}));
const hass={async callWS(p){calls.push(p.service_data);const d=p.service_data;if(fail)throw Error('模拟导出失败');if(d.operation==='export')return {response:{filename:'router_samples.xlsx',mime:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',content_base64:'UEsDBA==',rows:31}};if(d.operation==='label')rows.find(r=>r.id===d.sample_id).label=d.label;const reviewed=rows.filter(r=>r.label).length;return {response:{mode:'normal',status:'等待空闲',usage:{},daily_limit:3,samples_offset:d.offset||0,samples:rows.slice(d.offset||0,(d.offset||0)+30),sample_summary:{unique:31,occurrences:496,duplicates:465,reviewed,labels:{llm:reviewed},review_target:50},rules:[],cases:[]}}},connection:{async subscribeEvents(fn){subscription=fn;return ()=>{}}}};
const context={document:{getElementById:get,createElement(tag){const n=new Element(tag);if(tag==='a')links.push(n);return n},querySelectorAll(){const list=[];function visit(n){if(n.tag==='button')list.push(n);for(const c of n.children)visit(c)};Object.values(nodes).forEach(visit);return [...list,...Object.values(nodes)]},body:new Element(),hidden:false},window:{parent:{document:{querySelector:()=>({hass})}},addEventListener(){}},URL:{createObjectURL(b){blobs.push(b);return 'blob:test'},revokeObjectURL(){}},Blob,Uint8Array,atob,setTimeout(fn){fn()}};
vm.runInNewContext(source,context);await tick();let checks=0;
assert.match(get('sampleSummary').textContent,/合并重复 465 次/);assert.equal(get('samples').children.length,30);assert.equal(get('previous').disabled,true);checks++;
assert.equal(get('samples').children[0].children[0].textContent,'<img onerror=alert(1)>');checks++;
assert.equal(get('exportReviewed').disabled,true);checks++;
const labelButton=get('samples').children[0].children.find(n=>n.className==='actions').children[0];await labelButton.onclick();assert.equal(rows[0].label,'llm');assert.equal(calls.findLast(c=>c.operation==='label').sample_id,'s0');assert.equal(get('exportReviewed').disabled,false);checks++;
await get('next').onclick();assert.equal(get('samples').children.length,1);assert.equal(calls.at(-1).offset,30);assert.equal(get('next').disabled,true);checks++;
await get('previous').onclick();assert.equal(get('samples').children.length,30);checks++;
await get('exportReviewed').onclick();assert.equal(calls.at(-1).reviewed_only,true);assert.equal(links[0].download,'router_samples.xlsx');assert.equal(links[0].clicked,true);assert.equal(links[0].removed,true);assert.equal(blobs[0].type,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');assert.equal(blobs[0].size,4);checks++;
await get('retry').onclick();assert.equal(calls.at(-1).operation,'retry');assert.match(get('status').textContent,/完成的记录不会重做/);checks++;
fail=true;await get('exportAll').onclick();assert.match(get('status').textContent,/模拟导出失败/);assert.equal(get('exportAll').disabled,false);checks++;
fail=false;subscription({data:{entity_id:'sensor.router_lab_learning'}});await tick();assert.equal(calls.at(-1).operation,undefined);checks++;
console.log(JSON.stringify({checks,passed:checks,scope:'Actual feedback page script; no cloud or device calls'}));}
main().catch(e=>{console.error(e);process.exitCode=1});
