"""HA-native LLM tools; credentials remain in the existing DeepSeek integration."""
import probatio as vol
from homeassistant.helpers import llm
from .habits import PERIODS, CONDITIONS, DURABLE, TEMPORARY, temporary_period,normalize_scene,mentioned_scenes
from .memory import ACTIVE_TURN

RULE_FIELDS={
    vol.Required('scene'):str,
    vol.Required('entity_id'):str,
    vol.Required('action'):vol.In(['on','off','keep','open','close','brightness','position','percentage','temperature','volume','humidity']),
    vol.Optional('period',default='any'):vol.In(PERIODS),
    vol.Optional('condition',default='always'):vol.In(CONDITIONS),
    vol.Optional('value'):vol.Coerce(float),
}


class MemoryTool(llm.Tool):
    integration='router_lab'
    annotations=llm.ToolAnnotations(destructive=False,open_world=False)

    def __init__(self,memory,turn,name,description,parameters):
        self.memory,self.turn=memory,turn
        self.name,self.description,self.parameters=name,description,vol.Schema(parameters)

    async def async_call(self,hass,tool_input,llm_context):
        if not self.turn:
            return llm.ToolResult(data={'error':'请通过路由测试助手使用记忆。'},error=True)
        args=tool_input.tool_args
        owner,text=self.turn['owner'],self.turn['text']
        try:
            if self.name in ('SaveSceneRule','SaveTemporarySceneRule','RunSceneRoutine'):
                requested=normalize_scene(args['scene'])
                known={r.get('scene','home') for r in self.memory.profile(owner)['rules']}
                known.add(requested)
                hints=mentioned_scenes(text,known)
                inherited=self.turn['session'].get('memory_scene')
                if hints and requested not in hints:
                    raise ValueError('场景与用户原话不一致：应使用'+ '、'.join(sorted(hints))+'，保留完整名称，不能改名或以回家代存。')
                if not hints and inherited!=requested:
                    raise ValueError('本句没有明确触发场景，也没有可沿用的场景上下文；请先追问。')
                args=dict(args,scene=requested)
            if self.name=='SaveSceneRule':
                if not DURABLE.search(text) or any(word in text for word in ('别记','不要记','不用记')):
                    raise ValueError('本句没有长期记忆要求；今天或这次的安排应使用临时工具。')
                result=await self.memory.save_rule(owner,args,source_text=text)
                self.turn['session']['memory_scene']=requested
            elif self.name=='SaveTemporarySceneRule':
                if not TEMPORARY.search(text):
                    raise ValueError('请先明确这是今天或这次的要求。')
                once=not any(word in text for word in ('今天','今晚'))
                args=dict(args,period=temporary_period(text,args.get('period','any')))
                result=await self.memory.save_rule(owner,args,True,self.turn['session']['id'] if once else None,source_text=text)
                self.turn['session']['memory_scene']=requested
            elif self.name=='ManageHomeMemory':
                operation=args['operation']
                if operation=='save_note' and not DURABLE.search(text):
                    raise ValueError('只有用户明确要求记忆长期偏好时才能保存。')
                if operation in ('delete_rule','delete_note') and not any(word in text for word in ('忘','删','取消','移除','不要记','别记')):
                    raise ValueError('用户本句没有删除记忆的要求。')
                if operation=='delete_rule' and any(word in text for word in ('今天','今晚','临时','这次')):
                    args['temporary']=True
                if operation=='accept_suggestion' and not any(word in text for word in ('同意','可以','好','记住','保存','就这样','行')):
                    raise ValueError('用户尚未确认这条建议。')
                if operation=='move_rules':
                    if not any(word in text for word in ('改','移','挪','更正','放到','放在','放进')):raise ValueError('用户本句没有更正场景的要求。')
                    hints=mentioned_scenes(text)
                    if hints and normalize_scene(args['scene']) not in hints:raise ValueError('目标场景与用户原话不一致。')
                result=await self.memory.manage(owner,**args)
            else:
                preview=args.get('preview',False)
                if any(word in text for word in ('假如','如果','会怎样','预览','测试计划','看看计划')):
                    preview=True
                environment=self.memory.environment()
                if 'preview_hour' in args or 'preview_daylight' in args:
                    preview=True
                    if 'preview_hour' in args:
                        environment['hour']=args['preview_hour']
                    if 'preview_daylight' in args:
                        environment['daylight']=args['preview_daylight']
                if args.get('occupied') is not None:
                    environment['occupied']=args['occupied']
                result=await self.memory.run_scene(owner,self.turn['session'],llm_context.context,args['scene'],
                    args.get('overrides',[]),preview=preview,environment=environment)
        except (ValueError,KeyError) as error:
            return llm.ToolResult(data={'error':str(error),'saved':False,'executed':False},error=True)
        self.turn['tool_calls'].append({'tool':self.name,'result':result})
        return llm.ToolResult(data=result)


class MemoryAPI(llm.API):
    def __init__(self,hass,memory):
        super().__init__(hass=hass,id='router_lab_memory',name='Router memory')
        self.memory=memory

    async def async_get_api_instance(self,llm_context):
        turn=ACTIVE_TURN.get()
        specs=[
            ('SaveSceneRule','保存或修改用户明确要求的长期生活习惯。scene必填：home回家、away离家、sleep睡觉、wake起床，也支持明确命名的场景如观影。只保存不执行；同场景同时间同条件同设备才替换。不同场景互不影响。value是百分比或摄氏度。',RULE_FIELDS),
            ('SaveTemporarySceneRule','保存指定scene下今天或本次的例外，不修改长期习惯。scene必填，不可把离家存成回家。只保存不执行当前动作；要求现在操作时还需设备工具。',RULE_FIELDS),
            ('ManageHomeMemory','查看生活习惯、保存文字偏好、删除规则/偏好、确认建议、更正规则场景。修改或删除前先list取得准确id。move_rules提供ids和目标scene，只改场景不执行、不改其他规则；临时规则删除时temporary=true。',{
                vol.Required('operation'):vol.In(['list','save_note','delete_rule','delete_note','accept_suggestion','reject_suggestion','move_rules']),
                vol.Optional('id'):str,vol.Optional('ids'):[str],vol.Optional('scene'):str,
                vol.Optional('text'):str,vol.Optional('temporary',default=False):bool}),
            ('RunSceneRoutine','执行指定scene的已记住习惯，或preview=true只预览。home回家、away离家、sleep睡觉、wake起床，其他使用明确的场景名称。不会跨场景执行或自行添加动作。假设下午用preview_hour=15、preview_daylight=true；填写preview_参数强制只预览。overrides中scene须与本次一致。',{
                vol.Required('scene'):str,
                vol.Optional('preview',default=False):bool,
                vol.Optional('preview_hour'):vol.All(int,vol.Range(min=0,max=23)),
                vol.Optional('preview_daylight'):bool,
                vol.Optional('occupied'):bool,
                vol.Optional('overrides',default=[]):[vol.Schema(RULE_FIELDS)]}),
        ]
        return llm.APIInstance(api=self,llm_context=llm_context,
            api_prompt='Use tools to read/write actual persistent life preferences. SaveSceneRule requires the correct explicit scene. home=returning home, away=leaving home, sleep=bedtime, wake=waking up; custom named scenes are supported. Never substitute home for an unsupported or missing scene. Never claim saved/deleted/executed without successful tool results. Use RunSceneRoutine for scene actions.',
            tools=[MemoryTool(self.memory,turn,*spec) for spec in specs])
