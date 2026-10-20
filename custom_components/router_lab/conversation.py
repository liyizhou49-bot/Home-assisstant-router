import asyncio
from dataclasses import replace
from time import perf_counter
from homeassistant.components import conversation
from homeassistant.components.conversation.const import ConversationEntityFeature, HOME_ASSISTANT_AGENT
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from .const import DOMAIN, SIGNAL_TRACE
from .pipeline import dispatch
from .memory import ACTIVE_TURN
from .habits import HOME, CONTEXTUAL,trigger_scene,scene_label
from homeassistant.util.ulid import ulid_now
from homeassistant.helpers.chat_session import async_get_chat_session
from .deepseek_client import API_REQUEST_MODE,ensure_adapter
from .recognition_timing import current_run as speech_run,describe as describe_speech


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([RouterConversation(entry)])


class RouterConversation(conversation.ConversationEntity):
    _attr_name = '路由测试助手'
    _attr_supported_features = ConversationEntityFeature.CONTROL
    _attr_supports_streaming = True

    def __init__(self, entry):
        self.entry = entry
        self._attr_unique_id = f'{entry.entry_id}_conversation'
        self.entity_id = 'conversation.router_lab'
        self._request_lock = asyncio.Lock()

    @property
    def supported_languages(self):
        return '*'

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)
        self.hass.data[DOMAIN][self.entry.entry_id]['agent_id'] = self.entity_id

    async def async_will_remove_from_hass(self):
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def async_process(self, user_input):
        submitted=perf_counter()
        learning=self.hass.data[DOMAIN][self.entry.entry_id]['learning']
        await learning.begin_turn()
        try:
            return await self._process_with_session(user_input,submitted)
        finally:
            learning.end_turn()

    async def _process_with_session(self,user_input,submitted):
        async with self._request_lock:
            # Register the session before forwarding. Otherwise HA replaces our
            # newly minted ULID and single-use preferences keep the abandoned ID.
            with async_get_chat_session(self.hass,user_input.conversation_id) as chat_session:
                actual=replace(user_input,conversation_id=chat_session.conversation_id)
                stats={'queue_ms':round((perf_counter()-submitted)*1000,2),'text_chunks':0,'tool_calls':0}
                speech=speech_run(self.hass,actual,self.entity_id)
                with conversation.async_get_chat_log(self.hass,chat_session,actual) as chat_log:
                    previous_listener=chat_log.delta_listener
                    def listener(log,delta):
                        if delta.get('content'):
                            stats.setdefault('first_text_ms',round((perf_counter()-submitted)*1000,2))
                            stats['text_chunks']+=1
                        if delta.get('tool_calls'):
                            stats.setdefault('first_tool_ms',round((perf_counter()-submitted)*1000,2))
                            stats['tool_calls']+=len(delta['tool_calls'])
                        if previous_listener:previous_listener(log,delta)
                    chat_log.delta_listener=listener
                    try:
                        return await self._process(actual,stats,speech)
                    finally:
                        chat_log.delta_listener=previous_listener

    async def _process(self, user_input,stats,speech=None):
        runtime = self.hass.data[DOMAIN][self.entry.entry_id]
        settings = runtime['settings']
        session = async_get_clientsession(self.hass)
        memory=runtime['memory']
        learning=runtime['learning']
        owner=memory.owner(user_input.context)
        conversation_id=user_input.conversation_id or ulid_now()
        memory_session=memory.session(conversation_id,owner)
        memory_session['id']=conversation_id
        stats['context_turns']=len(memory_session['exchanges'])
        turn={'owner':owner,'text':user_input.text,'session':memory_session,'tool_calls':[]}

        def spoken(text):
            response=intent.IntentResponse(language=user_input.language)
            response.async_set_speech(text)
            return conversation.ConversationResult(response=response,conversation_id=conversation_id)

        async def request(path, text):
            import aiohttp
            request_started=perf_counter()
            async with session.post(settings['router_url'] + path,
                json={'text': text, 'model': settings.get('model', 'hybrid_v4')},
                timeout=aiohttp.ClientTimeout(total=8)) as reply:
                reply.raise_for_status()
                result=await reply.json()
                stats['router_ms' if path=='/route' else 'correction_ms']=round((perf_counter()-request_started)*1000,2)
                return result

        def snapshot():
            return {state.entity_id: state.as_dict() for state in self.hass.states.async_all()
                    if state.attributes.get('router_lab_virtual') is True}

        async def forward(text, agent_id, *, memory_prompt=False):
            extra=user_input.extra_system_prompt
            if memory_prompt:
                extra=(extra+'\n' if extra else '')+memory.prompt(owner,memory_session,text,
                    settings.get('assistant_prompt'),settings.get('memory_instructions'))
                stats['extra_prompt_chars']=len(extra)
            forward_started=perf_counter()
            result = await conversation.async_converse(
                self.hass, text, conversation_id, user_input.context,
                language=user_input.language, agent_id=agent_id,
                device_id=user_input.device_id, satellite_id=user_input.satellite_id,
                extra_system_prompt=extra)
            stats['llm_ms' if memory_prompt else 'local_ms']=round((perf_counter()-forward_started)*1000,2)
            success = result.response.response_type != intent.IntentResponseType.ERROR
            code = result.response.error_code
            code = getattr(code, 'value', code)
            retryable = (not success and code in ('no_intent_match', 'no_valid_targets')
                         and not result.response.success_results)
            if not memory_prompt and not success:
                stats.setdefault('local_failures',[]).append({'code':code,'retryable':retryable})
            return result, success, retryable

        compound_cache={}
        def compound_plan(text):
            if text not in compound_cache:
                batch=learning.match_compound(text,owner)
                if batch and batch['ok'] and CONTEXTUAL.search(text):
                    batch={**batch,'ok':False,'plans':[],'reason':'这组表达需要习惯或对话上下文。'}
                compound_cache[text]=batch
            return compound_cache[text]

        async def execute_compound(batch):
            local_started=perf_counter()
            results=await learning.execute(batch['plans'],user_input.context,stop_on_failure=True)
            stats.update(local_ms=round((perf_counter()-local_started)*1000,2),
                         compound_commands=batch['clauses'],compound_results=results,compound_count=len(results))
            return spoken(learning.response(results))

        async def correct(text):
            # Whole-sentence ASR correction must never drop an unrecognized second action.
            if compound_plan(text) is not None:
                return {'accepted':False,'reason':'compound_requires_full_match'}
            return await request('/correct',text)

        async def local(text):
            batch=compound_plan(text)
            if batch is not None:
                if batch['ok']:
                    # Even a device failure ends this path: there may already be side effects.
                    return await execute_compound(batch),True,False
                stats['compound_rejection']=batch['reason']
                stats.setdefault('local_failures',[]).append({'code':'compound_not_fully_matched','retryable':True})
                result=spoken('组合指令没有全部通过本地识别，尚未执行。')
                result.response.async_set_error(intent.IntentResponseErrorCode.NO_INTENT_MATCH,batch['reason'])
                return result,False,True
            result,success,retryable=await forward(text, HOME_ASSISTANT_AGENT)
            if not success and retryable:
                extension=learning.match(text,owner,canonical=True)
                if extension:
                    results=await learning.execute([extension],user_input.context)
                    stats['local_extension']=True
                    return spoken(learning.response(results)),True,False
            return result,success,retryable

        llm_id = settings.get('llm_agent')
        if llm_id in (self.entity_id, self.entry.entry_id, HOME_ASSISTANT_AGENT):
            llm_id = None

        async def llm(text):
            if settings.get('routing_mode','normal')=='economy' and not CONTEXTUAL.search(text):
                try:
                    outcome=await learning.economy(text,owner,memory_session,user_input.context,stats)
                    stats['routing_mode']='economy'
                    return spoken(outcome['reply']),True,False
                except Exception as error:
                    stats['economy_fallback']=type(error).__name__
            token=ACTIVE_TURN.set(turn)
            enabled=ensure_adapter(self.hass,llm_id)
            fast=enabled and settings.get('fast_response',True)
            stats['fast_response']=fast
            api_token=API_REQUEST_MODE.set({'fast':fast,'stats':stats})
            try:
                return await forward(text, llm_id,memory_prompt=True)
            finally:
                API_REQUEST_MODE.reset(api_token)
                ACTIVE_TURN.reset(token)

        before = snapshot()
        started = perf_counter()
        try:
            known_scenes={r.get('scene','home') for field in ('rules','temporary') for r in memory.profile(owner)[field]}
            scene=trigger_scene(user_input.text,known_scenes)
            if scene:
                outcome=await memory.run_scene(owner,memory_session,user_input.context,scene)
                completed=[item['description'].split('，',1)[-1] for item in outcome['results'] if item['status'] in ('done','kept')]
                failed=[memory.names[item['entity_id']] for item in outcome['results'] if item['status'] not in ('done','kept')]
                opening={'home':'欢迎回来，','away':'出门安排：','sleep':'睡前安排：','wake':'起床安排：'}.get(scene,scene_label(scene)+'安排：')
                message=opening+('；'.join(completed)+'。' if completed else '这次没有匹配的'+scene_label(scene)+'动作。')
                if not outcome['has_habits']:
                    message='还没设置你的'+scene_label(scene)+'习惯。可以说“记住，以后'+scene_label(scene)+'时关闭客厅灯”等明确要求。'
                if failed:
                    message+='这些设备没能完成，请检查：'+'、'.join(failed)+'。'
                result=spoken(message)
                trace={'input':user_input.text,'predicted_route':None,'final_route':'home_memory','status':'home_routine' if scene=='home' else 'scene_routine',
                       'scores':{},'attempts':[],'routine':outcome,'elapsed_ms':round((perf_counter()-started)*1000,2)}
            elif user_input.text.strip('？?。！! ') in ('你记住了什么','你记了什么','查看我的习惯','查看回家习惯','查看离家习惯','查看睡觉习惯','查看起床习惯','查看生活习惯','记得什么','有什么习惯'):
                view=memory.view(owner)
                requested={'查看回家习惯':'home','查看离家习惯':'away','查看睡觉习惯':'sleep','查看起床习惯':'wake'}.get(user_input.text.strip('？?。！! '))
                lines=[item['description'] for item in view['rules'] if requested is None or item['scene']==requested]
                lines+=['临时安排：'+item['description'] for item in view['temporary'] if requested is None or item['scene']==requested]
                if requested is None:lines+=[item['text'] for item in view['notes']]
                result=spoken('已记住：\n'+'\n'.join(lines) if lines else '目前还没有保存你的习惯。')
                trace={'input':user_input.text,'predicted_route':None,'final_route':'home_memory','status':'memory_list',
                       'scores':{},'attempts':[],'elapsed_ms':round((perf_counter()-started)*1000,2)}
            else:
                compound_started=perf_counter()
                batch=compound_plan(user_input.text)
                stats['compound_parse_ms']=round((perf_counter()-compound_started)*1000,2)
                direct=learning.match_direct(user_input.text,owner) if batch is None and not CONTEXTUAL.search(user_input.text) else None
                resolved=None
                context_age=perf_counter()-memory_session.get('last_turn_at',float('-inf'))
                if direct is None and batch is None and memory_session['exchanges'] and 0<=context_age<=120:
                    resolved=learning.match_followup(user_input.text,memory_session['exchanges'][-1])
                    if resolved:direct=resolved['plan']
                if batch and batch['ok']:
                    result=await execute_compound(batch)
                    complete=all(item['status']=='done' for item in stats['compound_results'])
                    trace={'input':user_input.text,'predicted_route':'hassil','final_route':'hassil',
                           'status':'compound_local_success' if complete else 'compound_local_partial',
                           'routing_source':'local_compound','router_bypassed':True,
                           'attempts':[{'handler':'hassil_compound','count':len(batch['plans'])}],
                           'elapsed_ms':round((perf_counter()-started)*1000,2)}
                elif direct:
                    local_started=perf_counter()
                    results=await learning.execute([direct],user_input.context)
                    stats['local_ms']=round((perf_counter()-local_started)*1000,2)
                    result=spoken(learning.response(results))
                    complete=all(item['status']=='done' for item in results)
                    learned=direct['rule_id']!='compound_builtin'
                    trace={'input':user_input.text,'predicted_route':None,'final_route':'hassil',
                           'routing_source':'local_verified','router_bypassed':True,
                           'status':('learned_local_success' if learned else 'direct_local_success') if complete else 'direct_local_error',
                           'attempts':[{'handler':'hassil_verified','rule_id':direct['rule_id']}],
                           'elapsed_ms':round((perf_counter()-started)*1000,2)}
                    if resolved:
                        trace.update(context_resolution={'original':user_input.text,'resolved':resolved['text'],
                                                        'source':'previous_target_question','age_seconds':round(context_age,2)})
                else:
                    result, trace = await dispatch(user_input.text,
                        lambda text: request('/route', text), local,
                        correct, llm if llm_id else None,
                        contextual_llm=bool(CONTEXTUAL.search(user_input.text)))
        except Exception:
            result = None
            trace = {'input': user_input.text, 'predicted_route': None, 'final_route': 'error',
                     'status': 'handler_error', 'message': '处理过程中出错。请查看设备当前状态后再试。',
                     'elapsed_ms': round((perf_counter()-started)*1000, 2), 'attempts': []}
        after = snapshot()
        changed = {}
        for entity_id, state in after.items():
            previous = before.get(entity_id, {})
            if (state.get('state'), state.get('attributes')) != (previous.get('state'), previous.get('attributes')):
                changed[entity_id] = {'before': {'state': previous.get('state'), 'attributes': previous.get('attributes')},
                                      'after': {'state': state.get('state'), 'attributes': state.get('attributes')}}
        trace['state_changes'] = changed
        trace['timings']=stats
        trace['recognition']=await describe_speech(self.hass,speech or {'source':'not_recorded'})
        trace['routing_mode']=settings.get('routing_mode','normal')
        trace['local_failures']=stats.get('local_failures',[])
        if stats.get('compound_commands'):
            trace['compound_commands']=stats['compound_commands']
            trace['compound_results']=stats['compound_results']
        if stats.get('economy_commands'):trace['fixed_commands']=stats['economy_commands']
        if turn['tool_calls']:
            trace['memory_tools']=turn['tool_calls']
        if result is not None:
            speech=result.response.as_dict().get('speech',{}).get('plain',{}).get('speech','')
            suggestions=await memory.note_corrections(owner,memory_session,changed,user_input.text)
            if suggestions:
                speech+=' 我注意到你多次这样调整：'+'；'.join(suggestions)+'。要记成长期习惯吗？'
                result.response.async_set_speech(speech)
            memory_session['exchanges']=(memory_session['exchanges']+[{'user':user_input.text,'assistant':speech}])[-8:]
            memory_session['last_turn_at']=perf_counter()
            trace['habit_suggestions']=suggestions
        try:await learning.enqueue(owner,trace,user_input.context)
        except Exception:
            trace['learning_queue_error']=True
        runtime['history'].append(trace)
        self.hass.bus.async_fire('router_lab_trace', trace)
        async_dispatcher_send(self.hass, SIGNAL_TRACE)
        if result is not None:
            return result
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_error(intent.IntentResponseErrorCode.FAILED_TO_HANDLE, trace['message'])
        return conversation.ConversationResult(response=response, conversation_id=conversation_id)
