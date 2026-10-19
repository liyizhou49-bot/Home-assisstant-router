"""Validate secret handling and reject invalid settings before any mutation."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import hashlib
import ast
from types import SimpleNamespace

ROOT=Path(__file__).parent
spec=importlib.util.spec_from_file_location('settings_rules',ROOT/'custom_components/router_lab/settings_rules.py')
rules=importlib.util.module_from_spec(spec);spec.loader.exec_module(rules)
sys.path.insert(0,str(ROOT/'router_runtime'))
import downloads


class SettingsTests(unittest.TestCase):
    def test_blank_credentials_are_omitted(self):
        result=rules.validate_settings({'deepseek_api_key':'  ','cloud_api_key':'','llm_api_key':' '},{})
        self.assertNotIn('deepseek_api_key',result);self.assertNotIn('cloud_api_key',result);self.assertNotIn('llm_api_key',result)

    def test_passwords_never_in_public_defaults(self):
        self.assertFalse(any('api_key' in k for k in rules.PUBLIC_DEFAULTS))

    def test_validate_bounds_and_types(self):
        for data in [{'tts_threads':True},{'tts_threads':5},{'learning_daily_limit':0},{'idle_learning':'false'},{'tts_source':'other'},{'cloud_voice':123},{'unexpected':1}]:
            with self.subTest(data=data),self.assertRaises(ValueError):rules.validate_settings(data,{})

    def test_endpoint_cannot_embed_credentials(self):
        for url in ['file:///etc/passwd','http://user:secret@localhost','http://localhost/?token=secret','http://localhost/#key']:
            with self.subTest(url=url),self.assertRaises(ValueError):rules.validate_settings({'router_url':url},{})

    def test_url_normalization_and_new_key(self):
        result=rules.validate_settings({'router_url':'http://local-router-runtime:17861/','deepseek_api_key':' new-test-key '},{})
        self.assertEqual(result['router_url'],'http://local-router-runtime:17861');self.assertEqual(result['deepseek_api_key'],'new-test-key')


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.root=Path(self.dir.name)
        self.old_assets=downloads.ASSETS;self.old_models=downloads.MODELS
        digest=hashlib.sha256(b'good').hexdigest()
        downloads.ASSETS={'asset':('model.bin','https://invalid.test/model',digest,4)}
        downloads.MODELS={'test':{'name':'Test','assets':['asset'],'kind':'tts'}}
        (self.root/'seed').mkdir();self.manager=downloads.Downloads(self.root/'data',self.root/'seed')
    def tearDown(self):
        downloads.ASSETS=self.old_assets;downloads.MODELS=self.old_models;self.dir.cleanup()
    def finish(self):
        for _ in range(300):
            if self.manager.jobs['test']['state']!='downloading':return
            time.sleep(.005)
        self.fail('Download did not finish')
    def test_seed_installs_only_after_checksum(self):
        (self.root/'seed/model.bin').write_bytes(b'good');self.manager.start('test');self.finish()
        self.assertTrue(self.manager.ready('test'));self.assertEqual(self.manager.jobs['test']['state'],'ready')
    def test_bad_download_not_activated_and_retry_works(self):
        (self.root/'seed/model.bin').write_bytes(b'bad!');self.manager.start('test');self.finish()
        self.assertFalse(self.manager.ready('test'));self.assertFalse((self.root/'data/model.bin').exists())
        self.assertFalse(list((self.root/'data').glob('*.partial')))
        self.assertEqual(self.manager.jobs['test']['error'],'checksum_mismatch')
        (self.root/'seed/model.bin').write_bytes(b'good');self.manager.start('test');self.finish();self.assertTrue(self.manager.ready('test'))
    def test_unknown_model_and_parallel_writer_rejected(self):
        with self.assertRaises(ValueError):self.manager.start('../../secrets')
        self.manager.jobs['other']={'state':'downloading'}
        with self.assertRaises(ValueError):self.manager.start('test')
    def test_modified_installed_file_reverified(self):
        (self.root/'seed/model.bin').write_bytes(b'good');self.manager.start('test');self.finish();self.assertTrue(self.manager.ready('test'))
        (self.root/'data/model.bin').write_bytes(b'evil');self.assertFalse(self.manager.ready('test'))

    def test_delete_preserves_other_model_shared_assets(self):
        digest=hashlib.sha256(b'good').hexdigest()
        downloads.ASSETS={k:(k+'.bin','https://invalid.test/x',digest,4) for k in ('fp32','fp16','voices')}
        downloads.MODELS={name:{'assets':[precision,'voices']} for name,precision in [('kokoro_fp32','fp32'),('kokoro_fp16','fp16')]}
        for key in downloads.ASSETS:(self.root/'data'/downloads.ASSETS[key][0]).write_bytes(b'good')
        self.manager.delete('kokoro_fp16')
        self.assertTrue(self.manager.ready('kokoro_fp32'));self.assertTrue((self.root/'data/voices.bin').exists())
        self.manager.delete('kokoro_fp32');self.assertFalse((self.root/'data/voices.bin').exists())
        with self.assertRaises(ValueError):self.manager.delete('router')

    def test_head_archive_rejects_unexpected_paths(self):
        import zipfile
        downloads.ASSETS={'heads':('heads.zip','','',0)}
        with zipfile.ZipFile(self.root/'data/heads.zip','w') as z:z.writestr('../outside.bin',b'bad')
        with self.assertRaises(ValueError):self.manager.install_heads()
        self.assertFalse((self.root/'outside.bin').exists())

    def test_head_source_cannot_contain_keys_or_arbitrary_hosts(self):
        for url in ['http://github.com/owner/repo/releases/download/v1/router-heads-v4.zip',
                    'https://evil.invalid/owner/repo/releases/download/v1/router-heads-v4.zip',
                    'https://github.com/owner/repo/releases/download/v1/router-heads-v4.zip?token=private']:
            with self.subTest(url=url),self.assertRaises(ValueError):self.manager.set_source(url)


class TransactionTests(unittest.IsolatedAsyncioTestCase):
    def functions(self,**extra):
        # Load the production transaction functions without booting a HA server.
        tree=ast.parse((ROOT/'custom_components/router_lab/unified_settings.py').read_text('utf-8'))
        tree.body=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in ('remember','save','status')]
        namespace={'PUBLIC_DEFAULTS':rules.PUBLIC_DEFAULTS,'DEFAULT_URL':'http://local-router-runtime:17861',
                   'DEFAULT_ASSISTANT_PROMPT':'assistant','DEFAULT_MEMORY_INSTRUCTIONS':'memory', 'providers':SimpleNamespace(identify=lambda url:'deepseek',PROVIDERS={}), 'CLOUD_MODELS':{},**extra}
        exec(compile(tree,'unified_settings.py','exec'),namespace)
        return namespace

    async def test_failed_validation_does_not_revert_concurrent_changes(self):
        removed=[];entry=SimpleNamespace(entry_id='original',data={'api_key':'original'},options={},subentries={})
        runtime={'settings':{'routing_mode':'normal'}}
        async def fail(hass,runtime,entry,data,transaction):
            entry.data={'api_key':'changed-elsewhere'}
            raise ValueError('validation_failed')
        async def remove(entry_id):removed.append(entry_id)
        f=self.functions(_save=fail,pipelines=SimpleNamespace(async_get_pipelines=lambda h:[]))
        hass=SimpleNamespace(config_entries=SimpleNamespace(async_remove=remove))
        with self.assertRaises(ValueError):await f['save'](hass,runtime,entry,{})
        self.assertEqual(entry.data['api_key'],'changed-elsewhere');self.assertEqual(removed,[])

    async def test_partial_commit_restores_only_owned_changes(self):
        removed=[];entry=SimpleNamespace(entry_id='original',data={'api_key':'keep'},options={'x':1},subentries={})
        runtime={'settings':{'routing_mode':'normal'}}
        namespace=self.functions(pipelines=SimpleNamespace(async_get_pipelines=lambda h:[]))
        async def fail(hass,runtime,entry,data,transaction):
            namespace['remember'](transaction,entry)
            transaction['runtime']=dict(runtime['settings']);transaction['created'].append('created-by-this-save')
            entry.data={'api_key':'changed'};entry.options={'x':9};runtime['settings']={'routing_mode':'economy'}
            raise ValueError('pipeline_failed')
        async def remove(entry_id):removed.append(entry_id)
        def update(e,**kw):e.__dict__.update(kw)
        namespace['_save']=fail
        hass=SimpleNamespace(config_entries=SimpleNamespace(async_remove=remove,async_update_entry=update))
        with self.assertRaises(ValueError):await namespace['save'](hass,runtime,entry,{})
        self.assertEqual(entry.data,{'api_key':'keep'});self.assertEqual(entry.options,{'x':1})
        self.assertEqual(runtime['settings'],{'routing_mode':'normal'});self.assertEqual(removed,['created-by-this-save'])

    async def test_status_never_serializes_credentials(self):
        async def compute(*a,**k):return {'compute':'ha_vm'}
        f=self.functions(compute=compute,llm_config=lambda *a:(SimpleNamespace(data={'api_key':'private-llm-test-value'}),SimpleNamespace(data={'chat_model':'model'})),
            cloud_config=lambda *a:(object(),{'api_key':'private-cloud-test-value','host':'host','voice':'Cherry'}),
            pipelines=SimpleNamespace(async_get_pipelines=lambda h:[]))
        result=await f['status'](None,{'settings':{},'agent_id':'test'})
        self.assertTrue(result['llm_configured'] and result['cloud_configured'])
        self.assertNotIn('private-llm-test-value',repr(result));self.assertNotIn('private-cloud-test-value',repr(result))

    async def test_failed_save_restores_stt_as_well_as_tts(self):
        old=SimpleNamespace(id='pipeline',tts_engine='tts.old',tts_language='zh-CN',tts_voice='voice',stt_engine='stt.old',stt_language='zh')
        live=SimpleNamespace(**vars(old));live.stt_engine='stt.new'
        changes=[]
        async def update(hass,pipeline,**values):changes.append(values);pipeline.__dict__.update(values)
        async def fail(hass,runtime,entry,data,transaction):
            transaction['pipelines']=[old];raise ValueError('late_failure')
        f=self.functions(_save=fail,pipelines=SimpleNamespace(async_get_pipelines=lambda h:[live],async_update_pipeline=update))
        with self.assertRaises(ValueError):await f['save'](None,{},None,{})
        self.assertEqual(live.stt_engine,'stt.old');self.assertEqual(live.stt_language,'zh')
        self.assertEqual(changes[0]['tts_engine'],'tts.old')


if __name__=='__main__':unittest.main()
