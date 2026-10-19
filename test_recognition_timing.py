"""Native timing must belong to the current request, never an earlier utterance."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as N
import sys
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('recognition_timing',Path(__file__).parent/'custom_components/router_lab/recognition_timing.py')
timing=importlib.util.module_from_spec(spec);spec.loader.exec_module(timing)


def events(vad=True):
    rows=[N(type='run-start',timestamp='2026-10-19T06:00:00+00:00',data={'tts_output':{'token':'never-export-me'}}),
          N(type='stt-start',timestamp='2026-10-19T06:00:00+00:00',data={'engine':'stt.faster_whisper'}),
          N(type='stt-end',timestamp='2026-10-19T06:00:04.720+00:00',data={'stt_output':{'text':'打开灯'}}),
          N(type='intent-start',timestamp='2026-10-19T06:00:04.721+00:00',data={'intent_input':'打开灯','conversation_id':'session'})]
    if vad:rows.append(N(type='stt-vad-end',timestamp='2026-10-19T06:00:04+00:00',data={}))
    return rows


class TimingTests(unittest.TestCase):
    def test_separates_recording_time_and_recognition_wait(self):
        result=timing.summarize(events(),'打开灯','session')
        self.assertEqual(result['recognition_ms'],720);self.assertEqual(result['capture_ms'],4720)
        self.assertNotIn('never-export-me',repr(result))
    def test_manual_stop_never_substitutes_full_recording_time(self):
        result=timing.summarize(events(False),'打开灯','session')
        self.assertNotIn('recognition_ms',result);self.assertEqual(result['capture_ms'],4720)
    def test_different_text_or_session_is_not_matched(self):
        self.assertIsNone(timing.summarize(events(),'关闭灯','session'))
        self.assertIsNone(timing.summarize(events(),'打开灯','another-session'))
    def test_text_turn_has_no_stale_speech_metrics(self):
        result=timing.summarize([e for e in events() if not e.type.startswith('stt')],'打开灯','session')
        self.assertEqual(result,{'source':'text'})
    def test_active_context_and_agent_are_required(self):
        context=N(id='request1',user_id='alice');run=N(context=context,pipeline=N(conversation_engine='conversation.router_lab'))
        data=N(pipeline_runs=N(_pipeline_runs={'pipeline':{'run':run}}),pipeline_debug={'pipeline':{'run':N(events=events())}})
        hass=N(data={'assist-key':data});user=N(context=context,conversation_id='session',text='打开灯')
        with patch.dict(sys.modules,{'homeassistant.components.assist_pipeline.pipeline':N(KEY_ASSIST_PIPELINE='assist-key')}):
            self.assertEqual(timing.current_run(hass,user,'conversation.router_lab')['recognition_ms'],720)
            user.context=N(id='another-request',user_id='alice')
            self.assertEqual(timing.current_run(hass,user,'conversation.router_lab'),{'source':'not_recorded'})
            user.context=N(id='request1',user_id='bob')
            self.assertEqual(timing.current_run(hass,user,'conversation.router_lab'),{'source':'not_recorded'})
            user.context=context
            self.assertEqual(timing.current_run(hass,user,'conversation.another'),{'source':'not_recorded'})
    def test_missing_or_changed_native_structure_does_not_break_routing(self):
        self.assertEqual(timing.current_run(N(data={}),N(),'agent'),{'source':'not_recorded'})
    def test_model_labels_separate_stt_from_routing_and_playback(self):
        self.assertIn('正常',timing.model_label({'stt_library':'funasr','model':'auto','language':'zh'}))
        self.assertIn('极速',timing.model_label({'stt_library':'faster-whisper','model':'base-int8','beam_size':1}))
        self.assertEqual(timing.model_label({'stt_library':'funasr','model':'another'}),'自定义识别配置')


if __name__=='__main__':unittest.main()
