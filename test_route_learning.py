"""Behavior checks for real Hassil parsing, bounded learning and idle priority."""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parent
PACKAGE='_route_learning_tests'
pkg=types.ModuleType(PACKAGE);pkg.__path__=[str(ROOT/'custom_components/router_lab')];sys.modules[PACKAGE]=pkg

def module(name,**values):
    result=types.ModuleType(name);result.__dict__.update(values);sys.modules[name]=result;return result

class Store:
    def __init__(self,*args):self.saved=None
    async def async_save(self,data):self.saved=copy.deepcopy(data)
    async def async_load(self):return self.saved

module('homeassistant');module('homeassistant.helpers');module('homeassistant.util')
module('homeassistant.auth');module('homeassistant.auth.permissions');module('homeassistant.auth.permissions.const',POLICY_CONTROL='control')
module('homeassistant.helpers.storage',Store=Store)
module('homeassistant.helpers.dispatcher',async_dispatcher_send=lambda *args:None)
dt=module('homeassistant.util.dt',now=lambda:datetime.now(timezone.utc))
sys.modules['homeassistant.util'].dt=dt
module(PACKAGE+'.diagnostics',configured_client=lambda *args:None)
local=__import__(PACKAGE+'.local_commands',fromlist=['x'])
learner_module=__import__(PACKAGE+'.route_learning',fromlist=['x'])
RouteLearning=learner_module.RouteLearning

def states():
    return [{'entity_id':'light.living','state':'on','attributes':{'friendly_name':'客厅灯','supported_color_modes':['brightness'],'brightness':77}},
        {'entity_id':'media_player.tv','state':'idle','attributes':{'friendly_name':'客厅电视','volume_level':.3}},
        {'entity_id':'climate.ac','state':'cool','attributes':{'friendly_name':'客厅空调','temperature':26,'min_temp':16,'max_temp':30,'target_temp_step':.5,'hvac_modes':['off','cool','heat']}},
        {'entity_id':'number.massage','state':'2','attributes':{'friendly_name':'按摩椅揉捏力度','min':1,'max':5,'step':1}},
        {'entity_id':'number.simulate_x','state':'1','attributes':{'friendly_name':'模拟','min':0,'max':100}}]

def case():
    return {'id':'case1','owner':'alice','text':'客厅灯亮度拧到30%','status':'pending','failure':[{'code':'no_intent_match'}],
            'evidence':[{'capability_id':'light.living|brightness','value':30.0}],'attempts':0}

def proposal():
    return {'decision':'learn','reason':'拧到这一表达未匹配现有句式。','capability_id':'light.living|brightness','sentences':['客厅灯亮度拧到{value}%']}

class GrammarTests(unittest.TestCase):
    def setUp(self):self.caps=local.capabilities(states())
    def test_canonical_commands_use_real_hassil_and_device_metadata(self):
        self.assertEqual(local.parse_commands('设置客厅空调温度为26.5度',self.caps,canonical=True)['value'],26.5)
        self.assertEqual(local.parse_commands('打开客厅电视',self.caps,canonical=True)['capability_id'],'media_player.tv|on')
        self.assertFalse(any('simulate_' in c['id'] for c in self.caps))
    def test_temperature_followup_retrieves_ac_even_after_many_lights(self):
        many=[{'entity_id':f'light.extra{i}','state':'off','attributes':{'friendly_name':f'测试灯{i}','supported_color_modes':['brightness']}} for i in range(30)]
        caps=local.capabilities(many+states())
        selected=local.select_caps(caps,'调到28度吧',exchanges=[{'user':'我有点冷','assistant':'想把客厅空调温度调高一些吗？想调到多少度？'}])
        self.assertLessEqual(len(selected),18)
        self.assertIn('climate.ac|temperature',[c['id'] for c in selected])
    def test_explicit_new_device_takes_priority_over_previous_topic(self):
        selected=local.select_caps(self.caps,'把客厅电视音量调到30%',exchanges=[{'assistant':'客厅空调想调到多少度？'}])
        self.assertEqual(selected[0]['entity_id'],'media_player.tv')
    def test_followup_can_reuse_previous_brightness_topic(self):
        selected=local.select_caps(self.caps,'调到百分之30吧',exchanges=[{'assistant':'客厅灯亮度想调到多少？'}])
        self.assertIn('light.living|brightness',[c['id'] for c in selected])
    def test_new_number_parameter_reuses_metadata(self):
        self.assertEqual(local.parse_commands('设置按摩椅揉捏力度为3',self.caps,canonical=True)['value'],3)
        self.assertIsNone(local.parse_commands('设置按摩椅揉捏力度为6',self.caps,canonical=True))
        self.assertIsNone(local.parse_commands('设置按摩椅揉捏力度为2.4',self.caps,canonical=True))
    def test_offline_tv_capabilities_still_include_supported_volume_control(self):
        caps=local.capabilities([{'entity_id':'media_player.tv','state':'off','attributes':{'friendly_name':'客厅电视','supported_features':4}}])
        self.assertEqual(local.parse_commands('设置客厅电视音量为30%',caps,canonical=True)['value'],30)
    def test_negation_queries_conditions_and_unknown_capabilities_do_not_execute(self):
        for text in ['不要打开客厅电视','如果打开客厅电视','打开客厅电视了吗','设置客厅灯色温为3000K','打开不存在的灯']:
            self.assertIsNone(local.parse_commands(text,self.caps,canonical=True),text)
    def test_verified_template_generalizes_value_but_is_scoped_to_owner(self):
        rule=local.validate_proposal(proposal(),case(),self.caps)
        self.assertEqual(local.parse_commands('客厅灯亮度拧到70%',self.caps,[rule],'alice')['value'],70)
        self.assertIsNone(local.parse_commands('客厅灯亮度拧到70%',self.caps,[rule],'bob'))
        rule['enabled']=False
        self.assertIsNone(local.parse_commands(case()['text'],self.caps,[rule],'alice'))
    def test_wrong_action_arbitrary_grammar_and_insufficient_evidence_are_rejected(self):
        for patch in [{'capability_id':'media_player.tv|on'},{'sentences':['客厅灯亮度{value}%{anything}']},
                      {'sentences':['(客厅灯|卧室灯)亮度拧到{value}%']},{'sentences':['不要客厅灯亮度拧到{value}%']}]:
            with self.assertRaises(ValueError):local.validate_proposal(dict(proposal(),**patch),case(),self.caps)
        with self.assertRaises(ValueError):local.validate_proposal(proposal(),dict(case(),evidence=[]),self.caps)
    def test_capability_or_name_change_invalidates_learned_rule(self):
        rule=local.validate_proposal(proposal(),case(),self.caps)
        changed=states();changed[0]['attributes']['friendly_name']='大厅灯'
        self.assertIsNone(local.parse_commands(case()['text'],local.capabilities(changed),[rule],'alice'))
    def test_evidence_uses_observed_change_not_model_claim(self):
        before=states()[0];after=copy.deepcopy(before);after['attributes']['brightness']=128
        proof=local.evidence({'light.living':{'before':before,'after':after}},self.caps)
        self.assertEqual(proof,[{'capability_id':'light.living|brightness','value':50.0}])
        self.assertEqual(local.evidence({},self.caps),[])

class IdleTests(unittest.IsolatedAsyncioTestCase):
    def make(self):
        calls=[]
        async def async_call(domain,service,data,**kwargs):calls.append((domain,service,data))
        async def user(_id):return types.SimpleNamespace(permissions=types.SimpleNamespace(check_entity=lambda *args:True))
        hass=types.SimpleNamespace(async_create_task=asyncio.create_task,services=types.SimpleNamespace(async_call=async_call),auth=types.SimpleNamespace(async_get_user=user))
        runtime={'settings':{'learning_idle_seconds':90,'learning_daily_limit':3},'memory':types.SimpleNamespace(view=lambda _: {'notes':[]})}
        learning=RouteLearning(hass,types.SimpleNamespace(entry_id='entry'),runtime)
        learning.catalog=lambda *args:local.capabilities(states());learning.last_activity=learner_module.time.monotonic()-91
        self.addAsyncCleanup(learning.close)
        return learning,calls
    async def test_idle_research_activates_verified_rule_without_controlling_devices(self):
        learning,calls=self.make();learning.data['cases']=[case()]
        async def reply(*args):return proposal()
        learning._json_call=reply
        await learning._work()
        self.assertEqual(learning.data['cases'][0]['status'],'learned')
        self.assertTrue(learning.match('客厅灯亮度拧到80%','alice'))
        self.assertEqual(calls,[])
        self.assertEqual(learning.store.saved['rules'],learning.data['rules'])
    async def test_new_user_request_cancels_background_and_preserves_pending_case(self):
        learning,calls=self.make();learning.data['cases']=[case()];started=asyncio.Event()
        async def wait(*args):started.set();await asyncio.Future()
        learning._json_call=wait;learning.task=asyncio.create_task(learning._work());await started.wait()
        await learning.begin_turn()
        self.assertEqual(learning.data['cases'][0]['status'],'pending');self.assertEqual(learning.active,1);self.assertEqual(calls,[])
        learning.end_turn()
    async def test_daily_budget_and_foreground_activity_prevent_calls(self):
        learning,calls=self.make();learning.data['cases']=[case()];research=[]
        async def reply(*args):research.append(1);return proposal()
        learning._json_call=reply;learning.active=1;await learning._work();self.assertEqual(research,[])
        learning.active=0;learning.usage()['research_calls']=3;await learning._work();self.assertEqual(research,[])
    async def test_settings_refresh_cannot_start_a_second_idle_worker(self):
        learning,_=self.make();learning.data['cases']=[case()]
        learning.task=asyncio.create_task(asyncio.sleep(30))
        original=learning.task;learning._schedule();self.assertIsNone(learning.timer)
        learning._start();self.assertIs(learning.task,original)
    async def test_execution_validates_entire_batch_before_any_action(self):
        learning,calls=self.make();context=types.SimpleNamespace(user_id='alice')
        with self.assertRaises(ValueError):await learning.execute([{'capability_id':'media_player.tv|on'}, {'capability_id':'number.massage|number','value':9}],context)
        self.assertEqual(calls,[])
        results=await learning.execute([{'capability_id':'media_player.tv|volume','value':35}],context)
        self.assertEqual(calls[0][2]['volume_level'],.35);self.assertEqual(results[0]['status'],'done')
    async def test_partial_execution_failure_is_reported_without_retry(self):
        learning,calls=self.make()
        async def fail(domain,service,data,**kwargs):calls.append(data);raise RuntimeError('device offline')
        learning.hass.services.async_call=fail
        results=await learning.execute([{'capability_id':'media_player.tv|on'}],types.SimpleNamespace(user_id='alice'))
        self.assertEqual(len(calls),1);self.assertEqual(results[0]['status'],'failed')
        self.assertIn('未完成',learning.response(results))
    async def test_economy_rejects_unparseable_batch_without_side_effects(self):
        learning,calls=self.make()
        async def reply(*args):return {'kind':'commands','commands':['打开客厅电视','设置客厅灯色温为3000K']}
        learning._json_call=reply
        with self.assertRaises(ValueError):await learning.economy('打开电视并调整色温','alice',{'exchanges':[]},types.SimpleNamespace(user_id='alice'),{})
        self.assertEqual(calls,[])
    async def test_restart_recovers_interrupted_research_and_scopes_records(self):
        learning,_=self.make();pending=case();pending['status']='researching';learning.store.saved={'rules':[],'cases':[pending],'usage':{},'revision':0}
        await learning.load();self.assertEqual(learning.data['cases'][0]['status'],'pending')
        self.assertEqual(learning.view('bob')['cases'],[]);self.assertEqual(len(learning.view('alice')['cases']),1)

if __name__=='__main__':unittest.main()
