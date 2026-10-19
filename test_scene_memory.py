"""Scene isolation, migration, ambiguous-language guard and one-time lifecycle."""
import asyncio
import copy
from datetime import datetime,timezone
from pathlib import Path
import runpy
import sys
import types
import unittest
from unittest.mock import patch
import uuid

SOURCE=Path(__file__).parent/'custom_components/router_lab'
h=runpy.run_path(str(SOURCE/'habits.py'))
LIGHT='light.router_lab_living_room'
CURTAIN='cover.router_lab_curtain'


class ScenePlanningTests(unittest.TestCase):
    def setUp(self):
        self.profile=h['new_profile']();self.env={'scene':'home','date':'2026-10-18','hour':22,'daylight':False,'occupied':False,'illuminance':10,'temperature':27,'time':'2026-10-18T22:00:00+08:00','session_id':'session'}
    def rule(self,scene,action='off',**args):
        return h['validate_rule'](dict(entity_id=LIGHT,action=action,scene=scene,**args),{LIGHT:'客厅灯'})
    def test_same_device_in_different_scenes_does_not_overwrite(self):
        home=h['upsert'](self.profile,self.rule('home','brightness',value=25))
        away=h['upsert'](self.profile,self.rule('away'))
        self.assertNotEqual(home['id'],away['id']);self.assertEqual(len(self.profile['rules']),2)
        self.assertEqual(h['plan'](self.profile,self.env)[0]['action'],'brightness')
        self.assertEqual(h['plan'](self.profile,dict(self.env,scene='away'))[0]['action'],'off')
    def test_legacy_rule_defaults_to_home_and_keeps_id(self):
        old={'entity_id':LIGHT,'action':'off','period':'any','condition':'always'}
        h['upsert'](self.profile,old)
        self.assertEqual(h['rule_key'](old),'any:always:'+LIGHT)
        self.assertEqual(len(h['plan'](self.profile,self.env)),1)
        self.assertEqual(h['plan'](self.profile,dict(self.env,scene='away')),[])
    def test_sleep_wake_and_custom_are_independent(self):
        for scene in ('sleep','wake','观影'):h['upsert'](self.profile,self.rule(scene))
        self.assertEqual(h['plan'](self.profile,self.env),[])
        for scene in ('sleep','wake','观影'):
            plan=h['plan'](self.profile,dict(self.env,scene=scene));self.assertEqual(len(plan),1);self.assertEqual(plan[0]['scene'],scene)
    def test_temporary_exception_is_scoped_to_scene(self):
        h['upsert'](self.profile,self.rule('home','on'))
        h['upsert'](self.profile,self.rule('away'))
        h['upsert'](self.profile,self.rule('away','keep'),date=self.env['date'])
        self.assertEqual(h['plan'](self.profile,self.env)[0]['action'],'on')
        self.assertEqual(h['plan'](self.profile,dict(self.env,scene='away'))[0]['action'],'keep')
    def test_disabled_rules_and_cross_scene_overrides_do_not_apply(self):
        h['upsert'](self.profile,self.rule('home','on',enabled=False))
        self.assertEqual(h['plan'](self.profile,self.env,[self.rule('away')]),[])
    def test_explicit_triggers_and_hypothetical_negation(self):
        for text,scene in [('我回来啦','home'),('我走了','away'),('我出门了','away'),('我要睡觉了','sleep'),('我起床了','wake'),('开启观影模式','观影')]:
            self.assertEqual(h['trigger_scene'](text,{'观影'}),scene)
        for text in ['如果我走了会怎样','我不出门了','我走的时候关灯','开启未知模式','我回来啦但不要操作']:
            self.assertIsNone(h['trigger_scene'](text,{'观影'}))
    def test_source_scene_detection_and_correction(self):
        self.assertEqual(h['mentioned_scenes']('记住我走的时候关灯'),{'away'})
        self.assertEqual(h['mentioned_scenes']('是离家，不是回家'),{'away'})
        self.assertEqual(h['mentioned_scenes']('以后起床开灯，睡觉关灯'),{'wake','sleep'})
        self.assertEqual(h['mentioned_scenes']('记住以后开启观影模式时调暗灯光'),{'观影'})
        self.assertEqual(h['mentioned_scenes']('记住以后开启验证观影模式时调暗灯光'),{'验证观影'})
    def test_scene_names_are_normalized_and_bounded(self):
        self.assertEqual(h['normalize_scene']('出门'),'away')
        self.assertEqual(h['normalize_scene']('home'),'home')
        for invalid in ['',None,'x'*21,'home:away','观影\n命令']:
            with self.assertRaises(ValueError):h['normalize_scene'](invalid)


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        def module(name,**attrs):
            m=sys.modules.get(name) or types.ModuleType(name);m.__dict__.update(attrs);sys.modules[name]=m;return m
        module('homeassistant');module('homeassistant.helpers');module('homeassistant.util')
        class Store:
            def __init__(self,*args):self.data=None
            async def async_save(self,data):self.data=copy.deepcopy(data)
            async def async_load(self):return self.data
        module('homeassistant.helpers.storage',Store=Store)
        module('homeassistant.helpers.dispatcher',async_dispatcher_send=lambda *args:None)
        dt=module('homeassistant.util.dt',now=lambda:datetime.now(timezone.utc),utcnow=lambda:datetime.now(timezone.utc))
        sys.modules['homeassistant.util'].dt=dt
        module('homeassistant.util.ulid',ulid_now=lambda:uuid.uuid4().hex)
        module('probatio',Required=lambda key,**kwargs:key,Optional=lambda key,**kwargs:key,In=lambda x:x,Coerce=lambda x:x,Schema=lambda x:x,All=lambda *x:x,Range=lambda **x:x)
        llm=module('homeassistant.helpers.llm',Tool=object,API=object,ToolAnnotations=lambda **kwargs:kwargs,
            ToolResult=lambda data,error=False:types.SimpleNamespace(data=data,error=error))
        sys.modules['homeassistant.helpers'].llm=llm
        package=module('_scene_memory_tests');package.__path__=[str(SOURCE)]
        cls.memory_module=__import__('_scene_memory_tests.memory',fromlist=['x'])
        cls.api_module=__import__('_scene_memory_tests.memory_api',fromlist=['x'])
    def setUp(self):
        self.calls=[]
        async def call(domain,service,data,**kwargs):self.calls.append((domain,service,data))
        states=types.SimpleNamespace(get=lambda eid:types.SimpleNamespace(state='on',attributes={'router_lab_virtual':True}))
        hass=types.SimpleNamespace(states=states,services=types.SimpleNamespace(async_call=call))
        self.memory=self.memory_module.HomeMemory(hass,types.SimpleNamespace(entry_id='entry'))
        self.memory.environment=lambda:{'date':'2026-10-18','hour':22,'daylight':False,'occupied':False,'illuminance':10,'temperature':27,'time':'2026-10-18T22:00:00+08:00'}
    async def save(self,scene,action='off',**kwargs):
        return await self.memory.save_rule('alice',dict(entity_id=LIGHT,scene=scene,action=action,**kwargs))
    async def tool(self,name,text,args,scene=None):
        session={'id':'s','exchanges':[]}
        if scene:session['memory_scene']=scene
        turn={'owner':'alice','text':text,'session':session,'tool_calls':[]}
        tool=self.api_module.MemoryTool(self.memory,turn,name,'description',{})
        return await tool.async_call(self.memory.hass,types.SimpleNamespace(tool_args=args),types.SimpleNamespace(context=None))
    async def test_wrong_model_scene_is_rejected_before_storage(self):
        result=await self.tool('SaveSceneRule','记住以后我走的时候关客厅灯',{'entity_id':LIGHT,'action':'off','scene':'home'})
        self.assertTrue(result.error);self.assertEqual(self.memory.profile('alice')['rules'],[])
        result=await self.tool('SaveSceneRule','记住以后我走的时候关客厅灯',{'entity_id':LIGHT,'action':'off','scene':'away'})
        self.assertFalse(result.error);self.assertEqual(self.memory.view('alice')['rules'][0]['scene'],'away')
        self.assertFalse(self.calls)
    async def test_missing_scene_context_asks_instead_of_defaulting_home(self):
        result=await self.tool('SaveSceneRule','记住以后客厅灯关掉',{'entity_id':LIGHT,'action':'off','scene':'home'})
        self.assertTrue(result.error);self.assertFalse(self.memory.profile('alice')['rules'])
    async def test_explicit_custom_scene_can_be_saved(self):
        result=await self.tool('SaveSceneRule','记住以后开启观影模式时客厅灯用20%',{'entity_id':LIGHT,'action':'brightness','value':20,'scene':'观影'})
        self.assertFalse(result.error);self.assertEqual(result.data['scene'],'观影');self.assertFalse(self.calls)
    async def test_custom_scene_name_cannot_be_silently_shortened(self):
        args={'entity_id':LIGHT,'action':'brightness','value':20,'scene':'观影'}
        result=await self.tool('SaveSceneRule','记住以后开启验证观影模式时客厅灯用20%',args)
        self.assertTrue(result.error);self.assertFalse(self.memory.profile('alice')['rules'])
        result=await self.tool('SaveSceneRule','记住以后开启验证观影模式时客厅灯用20%',dict(args,scene='验证观影'))
        self.assertFalse(result.error);self.assertEqual(result.data['scene'],'验证观影')
    async def test_move_rules_is_atomic_and_preserves_notes_and_other_scenes(self):
        a=await self.save('home');b=await self.save('sleep');self.memory.profile('alice')['notes']=[{'id':'n','text':'喜欢安静'}]
        baseline=copy.deepcopy(self.memory.data)
        with self.assertRaises(ValueError):await self.memory.manage('alice','move_rules',ids=[a['id'],'missing'],scene='away')
        self.assertEqual(self.memory.data,baseline)
        result=await self.memory.manage('alice','move_rules',ids=[a['id']],scene='away')
        self.assertEqual(result['moved'],1);view=self.memory.view('alice')
        self.assertEqual(len(view['rules']),2);self.assertEqual(view['notes'],baseline['profiles']['alice']['notes'])
        self.assertTrue(any(r['id']==b['id'] for r in view['rules']));self.assertFalse(self.calls)
    async def test_target_scene_collision_does_not_overwrite(self):
        a=await self.save('home');await self.save('away','on');baseline=copy.deepcopy(self.memory.data)
        with self.assertRaises(ValueError):await self.memory.manage('alice','move_rules',ids=[a['id']],scene='away')
        self.assertEqual(self.memory.data,baseline)
    async def test_execution_and_once_consumption_do_not_cross_scenes(self):
        with patch.object(self.memory_module.dt_util,'now',return_value=datetime.fromisoformat('2026-10-18T22:00:00+08:00')):
            for scene in ('home','away'):
                await self.memory.save_rule('alice',dict(entity_id=LIGHT,action='off',scene=scene),True,'session')
        result=await self.memory.run_scene('alice',{'id':'session'},None,'away')
        self.assertEqual(len(result['results']),1);self.assertEqual(len(self.calls),1)
        self.assertTrue(result['has_habits'])
        self.assertEqual([r['scene'] for r in self.memory.profile('alice')['temporary']],['home'])
    async def test_preview_and_mismatched_overrides_never_execute(self):
        await self.save('away');result=await self.memory.run_scene('alice',{},None,'away',preview=True)
        self.assertEqual(len(result['plan']),1);self.assertFalse(result['executed']);self.assertFalse(self.calls)
        with self.assertRaises(ValueError):await self.memory.run_scene('alice',{},None,'away',[dict(entity_id=LIGHT,action='off',scene='home')])
        self.assertFalse(self.calls)


if __name__=='__main__':unittest.main()
