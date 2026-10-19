"""Persistent idle research and compact LLM-to-Hassil command mode."""
import asyncio
import copy
from datetime import timedelta
import hashlib
import json
import logging
import time

from homeassistant.helpers.storage import Store
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from .const import SIGNAL_TRACE
from .diagnostics import configured_client
from .local_commands import capabilities,public,select_caps,parse_commands,evidence,validate_proposal,validate_value,actionable

_LOGGER=logging.getLogger(__name__)
MODE_NAMES={'normal':'正常','economy':'节流'}


class RouteLearning:
    def __init__(self,hass,entry,runtime):
        self.hass,self.entry,self.runtime=hass,entry,runtime
        self.store=Store(hass,1,'router_lab_learning.'+entry.entry_id)
        self.data={'rules':[],'cases':[],'usage':{},'revision':0}
        self.active=0;self.last_activity=time.monotonic();self.timer=None;self.task=None;self.closed=False
        self.status='等待空闲';self.lock=asyncio.Lock()

    async def load(self):
        self.data=await self.store.async_load() or self.data
        for case in self.data['cases']:
            if case['status']=='researching':case['status']='pending'

    async def save(self):
        async with self.lock:await self.store.async_save(copy.deepcopy(self.data))
        async_dispatcher_send(self.hass,SIGNAL_TRACE)

    def setting(self,key,default):return self.runtime['settings'].get(key,default)

    def catalog(self,context=None):
        from homeassistant.components.homeassistant.exposed_entities import async_should_expose
        states=[]
        for state in self.hass.states.async_all():
            if not async_should_expose(self.hass,'conversation',state.entity_id):continue
            states.append(state.as_dict())
        return capabilities(states)

    def _schedule(self):
        if self.timer:self.timer.cancel();self.timer=None
        if self.closed or self.active or not self.setting('idle_learning',True):return
        if self.task and not self.task.done():return
        if not any(c['status']=='pending' for c in self.data['cases']):return
        delay=max(1,self.setting('learning_idle_seconds',90)-(time.monotonic()-self.last_activity))
        self.timer=asyncio.get_running_loop().call_later(delay,self._start)

    def _start(self):
        self.timer=None
        if not self.closed and not self.active and not (self.task and not self.task.done()):
            self.task=self.hass.async_create_task(self._work())

    async def begin_turn(self):
        self.active+=1;self.last_activity=time.monotonic()
        if self.timer:self.timer.cancel();self.timer=None
        if self.task and not self.task.done():
            self.task.cancel()
            try:await self.task
            except asyncio.CancelledError:pass
        self.status='前台对话中，后台暂停'

    def end_turn(self):
        self.active=max(0,self.active-1);self.last_activity=time.monotonic()
        self.status='等待空闲';self._schedule()
        async_dispatcher_send(self.hass,SIGNAL_TRACE)

    async def close(self):
        self.closed=True
        if self.timer:self.timer.cancel()
        if self.task and not self.task.done():
            self.task.cancel()
            try:await self.task
            except asyncio.CancelledError:pass

    def usage(self):
        date=dt_util.now().date().isoformat()
        if self.data['usage'].get('date')!=date:self.data['usage']={'date':date,'research_calls':0,'research_tokens':0,'economy_calls':0,'economy_tokens':0}
        return self.data['usage']

    async def _json_call(self,messages,kind):
        from .deepseek_client import ensure_adapter
        ensure_adapter(self.hass,self.runtime['settings'].get('llm_agent',''))
        entry,subentry=configured_client(self.hass,self.runtime)
        if entry.domain!='llama_cpp':raise ValueError('此模式需要已配置的兼容对话接口。')
        client=entry.runtime_data.with_options(timeout=35,max_retries=0)
        usage=self.usage();usage[kind+'_calls']=usage.get(kind+'_calls',0)+1
        # Persist attempted research calls too; cancellation must not defeat the daily limit.
        if kind=='research':await self.save()
        result=await client.chat.completions.create(model=subentry.data['chat_model'],
            messages=messages,stream=False,max_tokens=1400,temperature=0,
            response_format={'type':'json_object'},extra_body={'thinking':{'type':'disabled'}})
        if result.usage:usage[kind+'_tokens']=usage.get(kind+'_tokens',0)+result.usage.total_tokens
        text=result.choices[0].message.content or ''
        if len(text)>18000:raise ValueError('模型返回过长。')
        parsed=json.loads(text)
        if not isinstance(parsed,dict):raise ValueError('模型必须返回JSON对象。')
        await self.save()
        return parsed

    async def enqueue(self,owner,trace,context):
        if trace.get('predicted_route')!='hassil' or trace.get('final_route')!='llm':return
        if trace.get('contextual_override'):return
        if not any(a.get('handler')=='hassil' for a in trace.get('attempts',[])):return
        text=trace.get('input','')
        if not actionable(text):return
        key=hashlib.sha256((owner+'|'+text).encode()).hexdigest()[:24]
        if any(case['id']==key for case in self.data['cases']):return
        caps=self.catalog(context)
        proof=evidence(trace.get('state_changes',{}),caps)
        # Research receives just this instruction, parser failure and observed action,
        # never the user's habit store, unrelated history, API credentials, or TTS.
        case={'id':key,'owner':owner,'text':text,'status':'pending','created_at':dt_util.now().isoformat(),
              'evidence':proof,'reason':'本地未匹配，已转交LLM；等待空闲分析。',
              'failure':trace.get('local_failures',[]),'attempts':0,
              'context_user_id':getattr(context,'user_id',None)}
        self.data['cases'].append(case)
        self.data['cases']=self.data['cases'][-100:]
        await self.save()

    def match(self,text,owner,canonical=False):
        return parse_commands(text,self.catalog(),self.data['rules'],owner,canonical)

    async def execute(self,plans,context):
        caps={c['id']:c for c in self.catalog(context)};prepared=[]
        if not 1<=len(plans)<=8:raise ValueError('每次支持1至8条本地动作。')
        user=await self.hass.auth.async_get_user(context.user_id) if getattr(context,'user_id',None) else None
        if not user:raise ValueError('需要已登录的HA用户才能执行扩展指令。')
        from homeassistant.auth.permissions.const import POLICY_CONTROL
        for plan in plans:
            cap=caps.get(plan['capability_id'])
            if not cap:raise ValueError('设备能力已变化或未开放给助手。')
            if not user.permissions.check_entity(cap['entity_id'],POLICY_CONTROL):raise ValueError('当前用户没有设备控制权限。')
            value=validate_value(cap,plan.get('value'));data={'entity_id':cap['entity_id'],**cap['fixed']}
            if cap['param']:
                data[cap['param']]=value/100 if cap['action']=='volume' else value
                if cap['action'] not in ('temperature','number','volume'):data[cap['param']]=round(value)
            prepared.append((cap,data,value))
        results=[]
        # All targets and parameters are validated before the first side effect.
        for cap,data,value in prepared:
            try:
                await self.hass.services.async_call(cap['domain'],cap['service'],data,blocking=True,context=context)
                status='done'
            except Exception:
                _LOGGER.warning('Local command failed for %s',cap['entity_id']);status='failed'
            label=cap['command'].replace('{value}',f'{value:g}' if value is not None else '')
            results.append({'capability_id':cap['id'],'description':label,'status':status})
        return results

    @staticmethod
    def response(results):
        done=[r['description'] for r in results if r['status']=='done']
        failed=[r['description'] for r in results if r['status']!='done']
        return ('已完成：'+'；'.join(done)+'。' if done else '')+('未完成：'+'；'.join(failed)+'，请检查设备。' if failed else '')

    async def economy(self,text,owner,session,context,stats):
        caps=self.catalog(context)
        selected=select_caps(caps,text,exchanges=session.get('exchanges',[]))
        notes=self.runtime['memory'].view(owner).get('notes',[])[:8]
        prompt=('你是中文家庭助手。只输出JSON。控制设备时返回'
            '{"kind":"commands","commands":["固定指令"]}，指令必须严格使用提供的command句式替换{value}，每条一个动作，最多8条。'
            '不得发明设备、能力或参数，不得声称已执行。缺少设备或参数、存在歧义时返回'
            '{"kind":"reply","reply":"简短追问"}；纯聊天也用reply回答。'
            '当前input可能是对recent_exchanges中上一轮追问的简短回答，应结合上下文解析设备和参数；'
            '仅列出筛选后的相关能力，不代表家中只有这些设备。无法确定时追问，不得断言家中没有某类设备。'
            '查询状态只能报告提供的数据；后台定时、自动化、记忆保存不在本工具能力内。'
            'temperature指令只设温度；用户同时要求开空调时还需生成打开指令。'
            '不得把否定、假设、未来计划当成立即控制。偏好只是参考，不能据此自行添加动作。')
        body={'input':text,'capabilities':[public(c) for c in selected],
              'recent_exchanges':[{k:str(v)[:240] for k,v in x.items()} for x in session['exchanges'][-3:]],
              'notes':[n['text'] for n in notes]}
        started=time.perf_counter()
        result=await self._json_call([{'role':'system','content':prompt},{'role':'user','content':json.dumps(body,ensure_ascii=False)}],'economy')
        stats['economy_api_ms']=round((time.perf_counter()-started)*1000,2)
        stats['economy_capabilities']=len(selected);stats['economy_prompt_chars']=len(prompt)+len(json.dumps(body,ensure_ascii=False))
        if result.get('kind')=='reply':
            reply=result.get('reply')
            if not isinstance(reply,str) or not 1<=len(reply)<=1500:raise ValueError('回复格式不正确。')
            return {'reply':reply,'results':[]}
        commands=result.get('commands')
        if result.get('kind')!='commands' or not isinstance(commands,list) or not 1<=len(commands)<=8:
            raise ValueError('固定指令列表不正确。')
        plans=[]
        for text in commands:
            plan=parse_commands(text,selected,self.data['rules'],owner,canonical=True)
            if not plan:raise ValueError('模型输出没有通过Hassil识别。')
            plans.append(plan)
        # Caller may fall back only before execute; never retry after side effects.
        results=await self.execute(plans,context)
        stats['economy_commands']=commands
        return {'reply':self.response(results),'results':results}

    async def _work(self):
        case=None
        try:
            if self.active or not self.setting('idle_learning',True):return
            quiet=self.setting('learning_idle_seconds',90)
            if time.monotonic()-self.last_activity<quiet:return
            if self.usage()['research_calls']>=self.setting('learning_daily_limit',3):
                self.status='今日后台研究次数已用完，明天继续';return
            case=next((c for c in self.data['cases'] if c['status']=='pending'),None)
            if not case:return
            case['status']='researching';case['attempts']+=1;self.status='大模型正在分析本地回退原因'
            caps=self.catalog();selected=select_caps(caps,case['text'])
            proof_ids={e['capability_id'] for e in case.get('evidence',[])}
            selected={c['id']:c for c in [*selected,*[c for c in caps if c['id'] in proof_ids]]}
            prompt=('你是Hassil句式维护助手，只输出JSON，不能执行设备。分析为什么此指令本地未识别却交给LLM。'
                '返回{"reason":"中文原因","decision":"learn或skip","capability_id":"已有能力id","sentences":["句式"]}。'
                '只有evidence中恰好一个明确动作，且原话明确要求该动作时才能learn；否则skip并解释。'
                '句式必须包含完整设备name，数字参数用且仅用{value}，无数值动作不含占位符。'
                '每条句式是一个完整固定句型，最多3条；不得含通配符、正则、可选词、条件、否定、查询或后台任务。'
                '不能从一次执行结果推断生活习惯，也不能发明服务、参数或设备功能。')
            payload={'text':case['text'],'failure':case['failure'],'evidence':case['evidence'],
                     'capabilities':[public(c) for c in selected.values()]}
            result=await self._json_call([{'role':'system','content':prompt},{'role':'user','content':json.dumps(payload,ensure_ascii=False)}],'research')
            if self.active:raise asyncio.CancelledError()
            reason=str(result.get('reason','未提供原因'))[:800];case['reason']=reason
            if result.get('decision')=='learn':
                try:
                    rule=validate_proposal(result,case,caps,self.data['rules'])
                    # Ambiguous compound/contextual phrases stay in LLM, even if a permissive proposal matched.
                    if any(w in case['text'] for w in ('同时','然后','顺便','并且','也','有点','稍微','一点','舒服')):
                        raise ValueError('多动作或主观表达仅保留分析，不固化为单条控制规则。')
                    if len(self.data['rules'])>=100:raise ValueError('本地扩展已达到100条。')
                    if not any(r['id']==rule['id'] for r in self.data['rules']):
                        rule['created_at']=dt_util.now().isoformat();rule['reason']=reason
                        self.data['rules'].append(rule);self.data['revision']+=1
                    case['status']='learned';case['rule_id']=rule['id']
                except ValueError as error:
                    case['status']='review_only';case['reason']=reason+' 本地检查：'+str(error)
            else:case['status']='review_only'
            case['completed_at']=dt_util.now().isoformat()
            await self.save();self.status='分析完成'
        except asyncio.CancelledError:
            if case and case['status']=='researching':case['status']='pending'
            await self.save();self.status='新指令到达，后台已暂停';raise
        except Exception as error:
            if case:
                case['status']='pending' if case['attempts']<2 else 'error'
                case['reason']='后台分析未完成，可重试；错误类型：'+type(error).__name__
            await self.save();self.status='后台分析暂未完成'
            self.last_activity=time.monotonic()
        finally:
            self.task=None
            # Daily budget exhaustion is revisited on the next day without polling or API calls.
            if not self.closed and not self.active and self.setting('idle_learning',True) and self.usage()['research_calls']>=self.setting('learning_daily_limit',3):
                now=dt_util.now();tomorrow=(now.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1))
                if self.timer:self.timer.cancel()
                self.timer=asyncio.get_running_loop().call_later(max(60,(tomorrow-now).total_seconds()),self._start)
            else:
                self.last_activity=time.monotonic();self._schedule()
            async_dispatcher_send(self.hass,SIGNAL_TRACE)

    def view(self,owner):
        signatures={cap['id']:cap['signature'] for cap in self.catalog()}
        return {'mode':self.setting('routing_mode','normal'),'mode_name':MODE_NAMES[self.setting('routing_mode','normal')],
                'idle_learning':self.setting('idle_learning',True),'idle_seconds':self.setting('learning_idle_seconds',90),
                'daily_limit':self.setting('learning_daily_limit',3),'status':self.status,'usage':dict(self.usage()),
                'rules':[dict(copy.deepcopy(r),available=signatures.get(r['capability_id'])==r['signature'])
                         for r in self.data['rules'] if r['owner']==owner],
                'cases':[{k:copy.deepcopy(v) for k,v in c.items() if k not in ('owner','context_user_id')}
                         for c in reversed(self.data['cases']) if c['owner']==owner]}

    async def manage(self,owner,operation,rule_id=None):
        rule=next((r for r in self.data['rules'] if r['id']==rule_id and r['owner']==owner),None)
        if operation in ('enable','disable'):
            if not rule:raise ValueError('没有找到当前账号的规则。')
            rule['enabled']=operation=='enable';self.data['revision']+=1;await self.save()
        elif operation=='retry':
            for c in self.data['cases']:
                if c['owner']==owner and c['status'] in ('error','review_only'):
                    c['status']='pending';c['attempts']=0
            await self.save();self._schedule()
        return self.view(owner)
