"""Real Hassil preflight and real conversation method; synthetic HA, zero network calls."""
import ast
import asyncio
from collections import deque
from contextvars import ContextVar
from pathlib import Path
import re
from time import perf_counter
import types
import unittest

import test_route_learning as fixtures

compound = __import__(fixtures.PACKAGE + '.compound_commands', fromlist=['x'])
pipeline = __import__(fixtures.PACKAGE + '.pipeline', fromlist=['x'])
habits = __import__(fixtures.PACKAGE + '.habits', fromlist=['x'])
N = types.SimpleNamespace


def states():
    return fixtures.states() + [
        {'entity_id': 'light.bedroom', 'state': 'off', 'attributes': {'friendly_name': '卧室灯', 'supported_color_modes': ['brightness']}},
        {'entity_id': 'cover.curtain', 'state': 'closed', 'attributes': {'friendly_name': '客厅窗帘', 'current_position': 0}},
        {'entity_id': 'light.peace', 'state': 'off', 'attributes': {'friendly_name': '和平灯', 'supported_color_modes': ['onoff']}}]


class ParsingTests(unittest.TestCase):
    def setUp(self): self.caps = fixtures.local.capabilities(states())
    def parse(self, text): return compound.plan(text, self.caps, owner='alice')
    def test_single_explicit_command_accepts_asr_punctuation_but_preserves_questions(self):
        for text in ('关闭卧室灯', '关闭卧室灯。', '关闭卧室灯!', '关闭 卧室灯。'):
            with self.subTest(text=text):
                self.assertEqual(compound.single(text, self.caps)['capability_id'], 'light.bedroom|off')
        for text in ('关闭卧室灯？', '不要关闭卧室灯', '如果没人就关闭卧室灯',
                     '关闭卧室灯之后打开客厅灯', '我说过关闭卧室灯', '开启爱你模式',
                     '卧室灯有点暗', '把它调到28度'):
            with self.subTest(text=text): self.assertIsNone(compound.single(text, self.caps))
    def test_single_parameter_boundaries_and_ambiguous_names_still_reject(self):
        self.assertEqual(compound.single('把客厅空调调到26度。', self.caps)['value'], 26)
        self.assertIsNone(compound.single('把客厅空调调到60度。', self.caps))
        duplicate = states() + [{'entity_id': 'light.other', 'state': 'off', 'attributes': {'friendly_name': '卧室灯'}}]
        self.assertIsNone(compound.single('关闭卧室灯。', fixtures.local.capabilities(duplicate)))
    def test_target_followup_uses_only_explicit_previous_action_and_exact_device(self):
        previous={'user':'关灯。','assistant':'要关哪盏灯？客厅、卧室还是书房？'}
        got=compound.followup('卧室灯。',self.caps,previous)
        self.assertEqual(got['text'],'关闭卧室灯')
        self.assertEqual(got['plan']['capability_id'],'light.bedroom|off')
        for text in ('卧室', '不要卧室灯', '卧室灯？', '打开卧室灯', '它', '卧室灯和客厅灯'):
            self.assertIsNone(compound.followup(text,self.caps,previous))
        for request in ('不要关灯','如果没人就关灯','灯','查一下灯','关灯然后开电视'):
            self.assertIsNone(compound.followup('卧室灯',self.caps,dict(previous,user=request)))
        self.assertIsNone(compound.followup('卧室灯',self.caps,dict(previous,assistant='客厅灯已经关闭。')))
    def test_explicit_sequential_commands_have_ordered_plans(self):
        result = self.parse('关闭客厅灯，然后打开客厅空调')
        self.assertTrue(result['ok'])
        self.assertEqual([r['capability_id'] for r in result['plans']], ['light.living|off', 'climate.ac|on'])
    def test_shared_verb_applies_only_to_complete_device_names(self):
        result = self.parse('打开客厅灯和卧室灯')
        self.assertTrue(result['ok'])
        self.assertEqual(result['clauses'], ['打开客厅灯', '打开卧室灯'])
        self.assertFalse(self.parse('打开客厅灯，然后卧室灯')['ok'])
        self.assertFalse(self.parse('打开客厅灯和卧室')['ok'])
    def test_parameters_are_parsed_and_validated_by_each_device_capability(self):
        result = self.parse('把客厅灯亮度调到30%，再把客厅空调调到26.5度')
        self.assertTrue(result['ok'])
        self.assertEqual([p['value'] for p in result['plans']], [30, 26.5])
        self.assertFalse(self.parse('把客厅灯亮度调到30%，再把客厅空调调到60度')['ok'])
        self.assertFalse(self.parse('把客厅空调调到26.3度，然后打开客厅灯')['ok'])
    def test_mixed_domains_and_punctuation_work(self):
        for join in ('，', '；', '。', '并且', '同时', '顺便'):
            with self.subTest(join=join):
                self.assertTrue(self.parse('关掉客厅灯' + join + '把客厅窗帘开到50%')['ok'])
        self.assertTrue(self.parse('请先打开客厅灯，再关闭卧室灯吧。')['ok'])
    def test_connector_inside_a_device_name_is_not_split(self):
        self.assertTrue(self.parse('打开和平灯，然后关闭卧室灯')['ok'])
        self.assertIsNone(self.parse('打开和平灯'))
    def test_failure_of_any_clause_returns_no_executable_prefix(self):
        for text in ('打开客厅灯，然后打开不存在的灯', '打开客厅灯，然后做饭',
                     '打开客厅灯和和卧室灯', '打开客厅灯，然后', '打开客厅灯，然后调到26度'):
            with self.subTest(text=text):
                result = self.parse(text)
                self.assertFalse(result['ok']); self.assertEqual(result['plans'], [])
    def test_conditions_negation_queries_future_and_quoted_commands_never_execute(self):
        for text in ('不要打开客厅灯，然后打开客厅空调', '如果客厅热，打开客厅灯，然后打开客厅空调',
                     '当门锁打开，就打开客厅灯和卧室灯', '明天打开客厅灯，然后关闭卧室灯',
                     '等一会打开客厅灯，然后打开客厅空调', '打开客厅灯或者打开卧室灯',
                     '为什么打开客厅灯，然后打开客厅空调？', '“打开客厅灯，然后打开客厅空调”'):
            with self.subTest(text=text):
                result = self.parse(text)
                self.assertTrue(result is None or not result['ok'])
    def test_parameter_and_pronoun_inference_are_not_carried_across_actions(self):
        for text in ('把客厅空调调到26度和卧室灯', '打开客厅灯，然后把它调亮一点',
                     '打开客厅灯，然后再把空调也开一下'):
            result = self.parse(text); self.assertFalse(result['ok'])
    def test_more_than_eight_actions_and_duplicate_device_names_are_rejected(self):
        self.assertFalse(self.parse('然后'.join(['打开客厅灯'] * 9))['ok'])
        duplicate = states() + [{'entity_id': 'light.duplicate', 'state': 'off', 'attributes': {'friendly_name': '客厅灯'}}]
        self.assertFalse(compound.plan('打开客厅灯，然后打开客厅空调', fixtures.local.capabilities(duplicate))['ok'])
    def test_enabled_learned_phrases_can_participate_but_are_owner_scoped(self):
        proposal = fixtures.local.validate_proposal(fixtures.proposal(), fixtures.case(), self.caps)
        text = '客厅灯亮度拧到30%，然后打开客厅空调'
        self.assertTrue(compound.plan(text, self.caps, [proposal], 'alice')['ok'])
        self.assertFalse(compound.plan(text, self.caps, [proposal], 'bob')['ok'])


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    make = fixtures.IdleTests.make
    async def test_permissions_for_all_actions_are_checked_before_first_service(self):
        learning, calls = self.make()
        async def user(_): return N(permissions=N(check_entity=lambda entity, _: entity != 'climate.ac'))
        learning.hass.auth.async_get_user = user
        batch = learning.match_compound('打开客厅灯，然后打开客厅空调', 'alice')
        with self.assertRaises(ValueError):
            await learning.execute(batch['plans'], N(user_id='alice'), stop_on_failure=True)
        self.assertEqual(calls, [])
    async def test_runtime_failure_stops_remaining_actions_and_reports_partial_result(self):
        learning, calls = self.make()
        batch = learning.match_compound('打开客厅灯，然后打开客厅空调，然后打开客厅电视', 'alice')
        async def call(domain, service, data, **_):
            calls.append(data['entity_id'])
            if domain == 'climate': raise RuntimeError('offline')
        learning.hass.services.async_call = call
        result = await learning.execute(batch['plans'], N(user_id='alice'), stop_on_failure=True)
        self.assertEqual(calls, ['light.living', 'climate.ac'])
        self.assertEqual([r['status'] for r in result], ['done', 'failed', 'skipped'])
        self.assertIn('后续未执行', learning.response(result))


class Response:
    def __init__(self, **_): self.response_type = 'success'; self.error_code = None; self.success_results = []; self.speech = ''
    def async_set_speech(self, text): self.speech = text
    def async_set_error(self, code, text): self.response_type = 'error'; self.error_code = code; self.speech = text
    def as_dict(self): return {'speech': {'plain': {'speech': self.speech}}}


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    make = fixtures.IdleTests.make
    async def setup_process(self, *, fail_domain=None, replies=None):
        learning, device_calls = self.make()
        learning.catalog = lambda *args: fixtures.local.capabilities(states())
        # Execute the actual _process method, with only external HA/network adapters substituted.
        path = fixtures.ROOT / 'custom_components/router_lab/conversation.py'
        cls = next(n for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n, ast.ClassDef))
        method = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_process')
        remote_calls = []; events = []
        async def empty(*_): return []
        sessions={}
        memory = N(owner=lambda _: 'alice', session=lambda conversation,owner: sessions.setdefault((conversation,owner),{'exchanges': []}),
                   profile=lambda _: {'rules': [], 'temporary': []}, note_corrections=empty,
                   prompt=lambda *_: 'synthetic prompt')
        runtime = {'settings': {'router_url': 'http://fake.invalid', 'llm_agent': 'conversation.llm'},
                   'memory': memory, 'learning': learning, 'history': deque(maxlen=20)}
        learning.hass.data = {'router_lab': {'entry': runtime}}
        learning.hass.states = N(async_all=lambda: [N(entity_id=s['entity_id'], attributes=s['attributes'], as_dict=lambda s=s: s) for s in states()])
        learning.hass.bus = N(async_fire=lambda *args: events.append(args))
        original_call = learning.hass.services.async_call
        async def call(domain, service, data, **kw):
            await original_call(domain, service, data, **kw)
            if domain == fail_domain: raise RuntimeError('offline')
        learning.hass.services.async_call = call
        class HttpResponse:
            async def __aenter__(self): return self
            async def __aexit__(self, *_): pass
            def raise_for_status(self): pass
            async def json(self): return {'route': 'hassil', 'scores': {'hassil': .8, 'llm': .2}}
        class Http:
            def post(self, path, **kw): remote_calls.append(('http', path)); return HttpResponse()
        async def converse(_hass, text, _session, _context, **kw):
            remote_calls.append((kw['agent_id'], text))
            response = Response(); response.async_set_speech((replies or {}).get(text,'已收到完整原话'))
            return N(response=response)
        async def describe(*_): return {'source': 'text'}
        env = {'DOMAIN': 'router_lab', 'SIGNAL_TRACE': 'trace', 'perf_counter': perf_counter,
               'async_get_clientsession': lambda _: Http(), 'ulid_now': lambda: 'session',
               'intent': N(IntentResponse=Response, IntentResponseType=N(ERROR='error'),
                           IntentResponseErrorCode=N(NO_INTENT_MATCH='no_intent_match', FAILED_TO_HANDLE='failed')),
               'conversation': N(ConversationResult=lambda **kw: N(**kw), async_converse=converse),
               'HOME_ASSISTANT_AGENT': 'homeassistant', 'CONTEXTUAL': habits.CONTEXTUAL,
               'trigger_scene': habits.trigger_scene, 'scene_label': habits.scene_label,
               'ACTIVE_TURN': ContextVar('active'), 'API_REQUEST_MODE': ContextVar('request'),
               'ensure_adapter': lambda *_: False, 'dispatch': pipeline.dispatch,
               'describe_speech': describe, 'async_dispatcher_send': lambda *_: None}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), env)
        self_obj = N(hass=learning.hass, entry=N(entry_id='entry', entity_id='conversation.router_lab'))
        self_obj.entity_id = 'conversation.router_lab'
        async def process(text, conversation_id='session'):
            user = N(text=text, context=N(user_id='alice'), conversation_id=conversation_id, language='zh-CN',
                     extra_system_prompt='', device_id=None, satellite_id=None)
            return await env['_process'](self_obj, user, {})
        return process, runtime, device_calls, remote_calls
    async def test_supported_combination_bypasses_classifier_llm_and_native_agent(self):
        process, runtime, calls, remote = await self.setup_process()
        result = await process('关闭客厅灯，然后打开客厅空调')
        self.assertEqual(len(calls), 2); self.assertEqual(remote, [])
        self.assertEqual(runtime['history'][-1]['status'], 'compound_local_success')
        self.assertTrue(runtime['history'][-1]['router_bypassed'])
        self.assertIn('已完成', result.response.speech)
    async def test_rejected_combination_forwards_original_without_partial_execution_or_correction(self):
        process, runtime, calls, remote = await self.setup_process()
        text = '打开客厅灯，然后打开不存在的设备'
        await process(text)
        self.assertEqual(calls, [])
        self.assertEqual(remote, [('http', 'http://fake.invalid/route'), ('conversation.llm', text)])
        self.assertEqual(runtime['history'][-1]['final_route'], 'llm')
        self.assertEqual(runtime['history'][-1]['correction']['reason'], 'compound_requires_full_match')
    async def test_partial_device_failure_is_never_retried_by_llm(self):
        process, runtime, calls, remote = await self.setup_process(fail_domain='climate')
        await process('打开客厅灯，然后打开客厅空调，然后打开客厅电视')
        self.assertEqual(len(calls), 2); self.assertEqual(remote, [])
        self.assertEqual(runtime['history'][-1]['status'], 'compound_local_partial')
        self.assertEqual([r['status'] for r in runtime['history'][-1]['compound_results']], ['done', 'failed', 'skipped'])
    async def test_verified_single_commands_bypass_classifier_and_llm(self):
        process, runtime, calls, remote = await self.setup_process()
        for text in ('关闭卧室灯', '关闭卧室灯。', '关闭 卧室灯。'):
            await process(text)
            trace = runtime['history'][-1]
            self.assertEqual(trace['status'], 'direct_local_success')
            self.assertEqual(trace['routing_source'], 'local_verified')
            self.assertIsNone(trace['predicted_route'])
            self.assertTrue(trace['router_bypassed'])
        self.assertEqual(len(calls), 3); self.assertEqual(remote, [])
    async def test_unmatched_single_command_still_uses_existing_route_and_native_agent(self):
        process, runtime, calls, remote = await self.setup_process()
        await process('关闭所有灯')
        self.assertEqual(calls, [])
        self.assertEqual(remote, [('http', 'http://fake.invalid/route'), ('homeassistant', '关闭所有灯')])
        self.assertEqual(runtime['history'][-1]['status'], 'local_success')
    async def test_single_device_failure_does_not_retry_through_llm(self):
        process, runtime, calls, remote = await self.setup_process(fail_domain='light')
        result = await process('关闭卧室灯。')
        self.assertEqual(len(calls), 1); self.assertEqual(remote, [])
        self.assertEqual(runtime['history'][-1]['status'], 'direct_local_error')
        self.assertIn('未完成', result.response.speech)
    async def test_clarified_target_closes_light_in_same_session_without_second_llm(self):
        process,runtime,calls,remote=await self.setup_process(replies={'关灯':'要关哪盏灯？客厅还是卧室？'})
        await process('关灯');remote.clear()
        await process('卧室灯。')
        trace=runtime['history'][-1]
        self.assertEqual(trace['context_resolution']['resolved'],'关闭卧室灯')
        self.assertEqual(trace['input'],'卧室灯。')
        self.assertEqual(trace['timings']['context_turns'],1)
        self.assertEqual(len(calls),1);self.assertEqual(remote,[])
    async def test_other_or_expired_session_does_not_inherit_an_action(self):
        process,runtime,calls,remote=await self.setup_process(replies={'关灯':'要关哪盏灯？'})
        await process('关灯');remote.clear()
        await process('卧室灯',conversation_id='new-session')
        self.assertNotIn('context_resolution',runtime['history'][-1])
        self.assertEqual(calls,[]);self.assertTrue(remote)
        session=runtime['memory'].session('session','alice');session['last_turn_at']-=121
        remote.clear();await process('卧室灯')
        self.assertNotIn('context_resolution',runtime['history'][-1])
        self.assertEqual(calls,[]);self.assertTrue(remote)


if __name__ == '__main__': unittest.main()
