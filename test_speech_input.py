import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).parent/'custom_components/router_lab'
def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/(name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
speech=load('speech_input');rules=load('settings_rules')

class SpeechChecks(unittest.TestCase):
    def setUp(self):self.engines=[{'id':'stt.whisper','languages':['zh','en']}]
    def test_unrelated_save_preserves_stt(self):
        self.assertEqual(speech.pipeline_fields({'tts_threads':2},self.engines),{})
    def test_chinese_engine_selected_without_replacing_router(self):
        fields=speech.pipeline_fields({'stt_engine':'stt.whisper','stt_language':'zh'},self.engines)
        self.assertEqual(fields,{'stt_engine':'stt.whisper','stt_language':'zh'})
        self.assertNotIn('conversation_engine',fields)
    def test_unknown_engine_or_language_rejected(self):
        for data in [{'stt_engine':'stt.absent'},{'stt_engine':'stt.whisper','stt_language':'unsupported'}]:
            with self.assertRaises(ValueError):speech.pipeline_fields(data,self.engines)
    def test_disable_sets_both_pipeline_fields_null(self):
        self.assertEqual(speech.pipeline_fields({'stt_engine':''},self.engines),{'stt_engine':None,'stt_language':None})
    def test_input_schema_bounded(self):
        for data in [{'stt_engine':'tts.piper'},{'stt_engine':'../secret'},{'stt_language':'../secret'}]:
            with self.assertRaises(ValueError):rules.validate_settings(data,{})
        self.assertEqual(rules.validate_settings({'stt_engine':'stt.whisper','stt_language':'zh'},{}),{'stt_engine':'stt.whisper','stt_language':'zh'})

if __name__=='__main__':unittest.main()
