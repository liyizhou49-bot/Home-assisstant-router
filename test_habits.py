"""Behavioral checks for priority, isolation, expiry, conditions and suggestions."""
import copy
from pathlib import Path
import runpy
import unittest

h=runpy.run_path(str(Path(__file__).parent/'custom_components/router_lab/habits.py'))
CURTAIN='cover.router_lab_curtain'
LIGHT='light.router_lab_living_room'


class HabitsTest(unittest.TestCase):
    def setUp(self):
        self.profile=h['new_profile']()
        self.env={'date':'2026-10-18','hour':15,'daylight':True,'occupied':False,
                  'illuminance':350,'temperature':27,'time':'2026-10-18T15:00:00+08:00','session_id':'a'}

    def rule(self,action,period='any',condition='always',entity=CURTAIN,**kwargs):
        return h['validate_rule'](dict(entity_id=entity,action=action,period=period,condition=condition,**kwargs),{CURTAIN:'窗帘',LIGHT:'灯'})

    def save(self,action,period='any',condition='always',**kwargs):
        h['upsert'](self.profile,self.rule(action,period,condition,**kwargs))

    def test_afternoon_and_night(self):
        self.save('open','afternoon');self.save('close','night')
        self.assertEqual(h['plan'](self.profile,self.env)[0]['action'],'open')
        self.env.update(hour=21,daylight=False)
        self.assertEqual(h['plan'](self.profile,self.env)[0]['action'],'close')

    def test_sun_overrides_wall_clock(self):
        self.save('close','night');self.env['daylight']=False
        self.assertEqual(h['plan'](self.profile,self.env)[0]['action'],'close')

    def test_specific_time_beats_generic(self):
        self.save('close');self.save('keep','day');self.save('open','afternoon')
        self.assertEqual(h['plan'](self.profile,self.env)[0]['action'],'open')

    def test_condition_beats_unconditional(self):
        self.save('close','day');self.save('open','day','empty')
        self.assertEqual(h['plan'](self.profile,self.env)[0]['action'],'open')
        self.env['occupied']=True
        self.assertEqual(h['plan'](self.profile,self.env)[0]['action'],'close')

    def test_unknown_presence_never_treated_as_empty(self):
        self.save('open',condition='empty');self.env['occupied']=None
        self.assertEqual(h['plan'](self.profile,self.env),[])

    def test_both_sides_of_environment_thresholds(self):
        for condition,field,yes,no in [('dark','illuminance',149,150),('hot','temperature',28,27.5)]:
            self.profile=h['new_profile']();self.save('on',condition=condition,entity=LIGHT)
            self.env[field]=yes;self.assertEqual(len(h['plan'](self.profile,self.env)),1)
            self.env[field]=no;self.assertEqual(h['plan'](self.profile,self.env),[])

    def test_temporary_wins_without_overwriting(self):
        self.save('open','afternoon');original=copy.deepcopy(self.profile['rules'])
        h['upsert'](self.profile,self.rule('close'),date=self.env['date'])
        self.assertEqual(h['plan'](self.profile,self.env)[0]['action'],'close')
        self.assertEqual(self.profile['rules'],original)
        self.env['date']='2026-10-19'
        self.assertEqual(h['plan'](self.profile,self.env)[0]['action'],'open')

    def test_once_isolated_and_expires(self):
        h['upsert'](self.profile,self.rule('close'),date=self.env['date'],session_id='a',expires_at='2026-10-18T15:20:00+08:00')
        self.assertEqual(len(h['plan'](self.profile,self.env)),1)
        self.env['session_id']='b';self.assertEqual(h['plan'](self.profile,self.env),[])
        self.env['session_id']='a';self.env['time']='2026-10-18T15:20:00+08:00'
        self.assertEqual(h['plan'](self.profile,self.env),[])

    def test_explicit_instruction_beats_today(self):
        h['upsert'](self.profile,self.rule('close'),date=self.env['date'])
        self.assertEqual(h['plan'](self.profile,self.env,[self.rule('open')])[0]['action'],'open')

    def test_once_and_today_coexist(self):
        h['upsert'](self.profile,self.rule('close'),date=self.env['date'])
        h['upsert'](self.profile,self.rule('open'),date=self.env['date'],session_id='a',expires_at='2026-10-18T15:20:00+08:00')
        self.assertEqual(len(self.profile['temporary']),2)
        self.assertEqual(h['plan'](self.profile,self.env)[0]['action'],'open')
        self.env['session_id']='b'
        self.assertEqual(h['plan'](self.profile,self.env)[0]['action'],'close')

    def test_modify_only_matching_rule(self):
        self.save('open','afternoon');self.save('close','night');self.save('keep','night')
        self.assertEqual(len(self.profile['rules']),2)
        self.assertEqual(self.profile['rules'][0]['action'],'open')
        self.assertEqual(self.profile['rules'][1]['action'],'keep')

    def test_no_rules_no_actions(self):
        self.assertEqual(h['plan'](self.profile,self.env),[])

    def test_untrusted_entities_and_ranges(self):
        for bad in [dict(entity_id='switch.real_heater',action='on'),dict(entity_id=LIGHT,action='open'),
                    dict(entity_id=LIGHT,action='brightness',value=101),dict(entity_id=LIGHT,action='brightness',value=True),
                    dict(entity_id=CURTAIN,action='position',value=float('nan')),dict(entity_id=CURTAIN,action='close',period='winter')]:
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                h['validate_rule'](bad,{CURTAIN:'窗帘',LIGHT:'灯'})

    def test_three_distinct_events_only_suggest(self):
        rule=self.rule('close','night')
        self.assertIsNone(h['observe'](self.profile,rule,'one'))
        self.assertIsNone(h['observe'](self.profile,rule,'one'))
        self.assertIsNone(h['observe'](self.profile,rule,'two'))
        self.assertIsNotNone(h['observe'](self.profile,rule,'three'))
        self.assertEqual(len(self.profile['suggestions']),1)
        self.assertEqual(self.profile['rules'],[])

    def test_context_detection(self):
        for text in ['客厅也开一下','以后晚上回来窗帘拉着','今天不开窗帘','你记了什么','忘掉下午的习惯']:
            self.assertRegex(text,h['CONTEXTUAL'])
        self.assertIsNone(h['CONTEXTUAL'].search('打开厨房灯'))
        self.assertIsNotNone(h['HOME'].fullmatch('我回来啦'))
        self.assertIsNone(h['HOME'].fullmatch('如果我回来啦会怎样'))

    def test_today_never_inherits_current_night(self):
        self.assertEqual(h['temporary_period']('今天窗帘就拉着吧','night'),'any')
        self.assertEqual(h['temporary_period']('这次回家别开客厅灯','night'),'any')
        self.assertEqual(h['temporary_period']('今晚窗帘拉着','any'),'night')
        self.assertEqual(h['temporary_period']('今天下午回家窗帘拉着','afternoon'),'afternoon')


if __name__=='__main__':
    unittest.main(verbosity=2)
