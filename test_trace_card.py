import copy
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('trace_card',Path(__file__).parent/'tools/trace_card.py')
card=importlib.util.module_from_spec(spec);spec.loader.exec_module(card)


class DashboardTests(unittest.TestCase):
    def test_local_preflight_prediction_and_status_labels_are_idempotent(self):
        config={'type':'markdown','title':'最近一次测试','content':'**预测分流：** {{ state_attr("sensor.router_lab_trace", "predicted_route") or "—" }}\n{% set labels = {"local_success":"本地处理成功",} %}'}
        self.assertEqual(card.upgrade(config),1)
        self.assertIn('router_bypassed',config['content'])
        self.assertIn('direct_local_success',config['content'])
        self.assertIn('跳过分类（本地完整匹配）',config['content'])
        self.assertEqual(card.upgrade(config),0)
        self.assertEqual(config['content'].count('跳过分类（本地完整匹配）'),1)
    def test_upgrade_preserves_other_views_and_existing_result_fields(self):
        config={'views':[{'path':'test','cards':[{'type':'markdown','title':'最近一次测试','content':'**原话：** example\n\n**耗时：** {{ state_attr("sensor.router_lab_trace", "elapsed_ms") }} 毫秒\n\n**状态改变：** keep'}]},{'path':'bedroom','cards':[{'type':'tile','entity':'climate.bedroom'}]}]}
        bedroom=copy.deepcopy(config['views'][1]);self.assertEqual(card.upgrade(config),1)
        text=config['views'][0]['cards'][0]['content'];self.assertIn('**语音识别：**',text);self.assertIn('**文字处理：**',text);self.assertIn('**状态改变：** keep',text)
        self.assertEqual(config['views'][1],bedroom)
        self.assertEqual(card.upgrade(config),0)
        self.assertEqual(text,config['views'][0]['cards'][0]['content'])


if __name__=='__main__':unittest.main()
