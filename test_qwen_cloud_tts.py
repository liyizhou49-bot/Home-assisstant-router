"""Failure/cancellation checks for streaming TTS without a running HA server."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest
import json
import base64

SOURCE = Path(__file__).parent / 'custom_components/qwen_cloud_tts'
package = types.ModuleType('qwen_test')
package.__path__ = [str(SOURCE)]
sys.modules['qwen_test'] = package


def module(name):
    spec = importlib.util.spec_from_file_location('qwen_test.' + name, SOURCE / (name + '.py'))
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


client = module('client')
module('const')
for name in ['homeassistant', 'homeassistant.components', 'homeassistant.components.tts',
             'homeassistant.components.tts.models', 'homeassistant.components.tts.media_source',
             'homeassistant.core', 'homeassistant.exceptions']:
    sys.modules[name] = types.ModuleType(name)
ha_tts = sys.modules['homeassistant.components.tts']
class Entity:
    def async_write_ha_state(self):
        pass
class Request:
    def __init__(self, language, options, message_gen):
        self.language, self.options, self.message_gen = language, options, message_gen
class Response:
    def __init__(self, extension, data_gen):
        self.extension, self.data_gen = extension, data_gen
class HAError(Exception):
    pass
ha_tts.TextToSpeechEntity = Entity
ha_tts.TTSAudioRequest = Request
ha_tts.TTSAudioResponse = Response
sys.modules['homeassistant.components.tts.models'].Voice = lambda *x: x
sys.modules['homeassistant.core'].callback = lambda f: f
sys.modules['homeassistant.exceptions'].HomeAssistantError = HAError
fallback_calls = []
def media_id(hass, **kwargs):
    fallback_calls.append(kwargs)
    return 'fallback_id'
sys.modules['homeassistant.components.tts.media_source'].generate_media_source_id = media_id
async def local_audio(hass, identifier):
    return 'mp3', b'local-audio'
ha_tts.async_get_media_source_audio = local_audio
tts = module('tts')


class Checks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fallback_calls.clear()

    def entity(self, runtime):
        if not hasattr(runtime,'protocol'):runtime.protocol='realtime'
        entry = types.SimpleNamespace(entry_id='test', data={'voice': 'Cherry', 'fallback_engine': 'tts.piper'},
                                      options={}, runtime_data=runtime)
        entity = tts.QwenSpeech(entry)
        entity.hass = object()
        return entity

    async def test_failure_before_audio_keeps_later_text_for_fallback(self):
        async def source():
            yield '前半句，'
            await asyncio.sleep(.01)
            yield '后半句。'
        class Failed:
            async def stream(self, source, voice, metrics):
                await anext(source)
                raise client.CloudTTSError('connect_failed')
                yield b''
        entity = self.entity(Failed())
        response = await entity.async_stream_tts_audio(Request('zh-CN', {}, source()))
        data = b''.join([c async for c in response.data_gen])
        self.assertEqual(data, b'local-audio')
        self.assertEqual(fallback_calls[0]['message'], '前半句，后半句。')
        self.assertTrue(fallback_calls[0]['cache'])
        self.assertEqual(entity.fallbacks, 1)

    async def test_failure_after_audio_does_not_repeat_whole_reply(self):
        async def source():
            yield '完整回复。'
        class Partial:
            async def stream(self, source, voice, metrics):
                yield b'first-cloud-chunk'
                raise client.CloudTTSError('connection_closed')
        entity = self.entity(Partial())
        response = await entity.async_stream_tts_audio(Request('zh-CN', {}, source()))
        self.assertEqual(await anext(response.data_gen), b'first-cloud-chunk')
        with self.assertRaises(HAError):
            await anext(response.data_gen)
        self.assertEqual(fallback_calls, [])

    async def test_stopping_audio_closes_text_producer(self):
        closed = asyncio.Event()
        async def source():
            try:
                yield '第一句。'
                await asyncio.Event().wait()
            finally:
                closed.set()
        class Running:
            async def stream(self, source, voice, metrics):
                await anext(source)
                yield b'audio'
                await asyncio.Event().wait()
        entity = self.entity(Running())
        response = await entity.async_stream_tts_audio(Request('zh-CN', {}, source()))
        await anext(response.data_gen)
        await response.data_gen.aclose()
        self.assertTrue(closed.is_set())

    async def test_replay_rejects_excessive_input(self):
        async def source():
            yield '字' * 10001
        replay = client.ReplayText(source())
        try:
            with self.assertRaises(client.CloudTTSError):
                await replay.complete()
        finally:
            await replay.close()

    async def test_host_validation_limits_credentials_to_official_workspace(self):
        for host in ['example.com', 'ws-test.cn-beijing.maas.aliyuncs.com.evil.test',
                     'ws-test.cn-beijing.maas.aliyuncs.com/path']:
            with self.assertRaises(ValueError):
                client.validate_host(host)

    async def test_sse_handles_split_utf8_and_crlf(self):
        frame=('data: '+json.dumps({'text':'你好'},ensure_ascii=False)+'\r\n\r\n').encode()
        class Content:
            async def iter_chunked(self,size):
                for i in range(0,len(frame),3):yield frame[i:i+3]
        values=[event async for event in client.sse_events(Content())]
        self.assertEqual(values,[{'text':'你好'}])

    async def test_audio_flash_uses_http_mp3_protocol(self):
        audio=b'ID3-test-audio';frame=('data: '+json.dumps({'output':{'audio':{'data':base64.b64encode(audio).decode()},'finish_reason':'stop'},'usage':{'characters':4}})+'\n\n').encode()
        class Content:
            async def iter_chunked(self,size):yield frame
        class Response:
            status=200;content=Content()
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
        class Session:
            def post(self,url,**kwargs):self.url=url;self.kwargs=kwargs;return Response()
        session=Session();speech=client.QwenClient(session,'ws-test.cn-beijing.maas.aliyuncs.com','test-only','qwen-audio-3.0-tts-flash')
        async def text():yield '你好';yield '世界'
        metrics={};result=b''.join([chunk async for chunk in speech.stream(text(),'longanfengyue',metrics)])
        self.assertEqual(result,audio);self.assertTrue(session.url.endswith('/audio/tts/SpeechSynthesizer'))
        self.assertEqual(session.kwargs['json']['input'],{'text':'你好世界','voice':'longanfengyue','format':'mp3','sample_rate':24000})
        self.assertEqual(metrics['characters'],4)

    async def test_wav_download_never_forwards_api_auth(self):
        calls=[]
        class Content:
            async def iter_chunked(self,size):yield b'RIFF-test-wav'
        class Response:
            status=200;content=Content()
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def json(self):return {'output':{'audio':{'url':'https://audio.oss-cn-beijing.aliyuncs.com/signed.wav?signature=test-only'}}}
        class Session:
            def post(self,*args,**kwargs):return Response()
            def get(self,url,**kwargs):calls.append(kwargs);return Response()
        speech=client.QwenClient(Session(),'ws-test.cn-beijing.maas.aliyuncs.com','private-test-api','qwen3-tts-flash')
        result=await speech.file('你好。','Cherry',{})
        self.assertTrue(result.startswith(b'RIFF'));self.assertNotIn('headers',calls[0]);self.assertFalse(calls[0]['allow_redirects'])

    async def test_full_reply_failure_returns_actual_fallback_format(self):
        class Failed:
            protocol='qwen_http';model='qwen3-tts-flash'
            async def file(self,*args):raise client.CloudTTSError('synthesis_failed')
        entity=self.entity(Failed());entity.settings['model']='qwen3-tts-flash'
        async def text():yield '前半句，';yield '后半句。'
        response=await entity.async_stream_tts_audio(Request('zh-CN',{},text()))
        self.assertEqual(response.extension,'mp3');self.assertEqual(b''.join([c async for c in response.data_gen]),b'local-audio')
        self.assertEqual(fallback_calls[0]['message'],'前半句，后半句。')

    async def test_models_cannot_mix_voice_ids(self):
        speech=client.QwenClient(None,'ws-test.cn-beijing.maas.aliyuncs.com','test-only','qwen-audio-3.0-tts-plus')
        with self.assertRaises(ValueError):await speech.validate('Cherry')

    async def test_realtime_waits_for_first_text_before_connecting_and_preserves_all_chunks(self):
        ready=asyncio.Event();waiting=asyncio.Event();connections=[];sent=[]
        class Socket:
            def __init__(self):
                self.events=asyncio.Queue();self.events.put_nowait({'type':'session.created'})
            async def receive(self,timeout=None):
                event=await asyncio.wait_for(self.events.get(),timeout)
                return types.SimpleNamespace(type=client.aiohttp.WSMsgType.TEXT,data=json.dumps(event))
            async def send_json(self,event):
                sent.append(event)
                if event['type']=='session.update':self.events.put_nowait({'type':'session.updated'})
                elif event['type']=='input_text_buffer.append':
                    self.events.put_nowait({'type':'response.audio.delta','delta':base64.b64encode(b'audio').decode()})
                elif event['type']=='session.finish':
                    self.events.put_nowait({'type':'response.done','response':{'usage':{'characters':8}}})
                    self.events.put_nowait({'type':'session.finished'})
            async def close(self):pass
        class Session:
            async def ws_connect(self,*args,**kwargs):
                connections.append(1);self.assert_ready=ready.is_set();return Socket()
        session=Session();speech=client.QwenClient(session,'ws-test.cn-beijing.maas.aliyuncs.com','test-only')
        async def text():
            waiting.set();await ready.wait();yield '前半句，';yield '后半句。'
        async def collect():return b''.join([part async for part in speech.stream(text(),'Cherry',metrics)])
        metrics={};task=asyncio.create_task(collect());await waiting.wait();await asyncio.sleep(0)
        self.assertEqual(connections,[]);self.assertEqual(metrics['status'],'waiting_text')
        ready.set();audio=await task
        self.assertTrue(session.assert_ready);self.assertEqual(connections,[1]);self.assertEqual(audio,b'audioaudio')
        self.assertEqual([e['text'] for e in sent if e['type']=='input_text_buffer.append'],['前半句，','后半句。'])
        self.assertEqual(metrics['status'],'complete');self.assertIn('text_wait_ms',metrics)

    async def test_cancel_while_waiting_for_reply_never_opens_cloud_session(self):
        waiting=asyncio.Event();closed=asyncio.Event();connections=[]
        class Session:
            async def ws_connect(self,*args,**kwargs):connections.append(1);raise AssertionError('premature connection')
        speech=client.QwenClient(Session(),'ws-test.cn-beijing.maas.aliyuncs.com','test-only')
        async def text():
            try:waiting.set();await asyncio.Future();yield '不会生成'
            finally:closed.set()
        async def collect():return [part async for part in speech.stream(text(),'Cherry',{})]
        task=asyncio.create_task(collect());await waiting.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(connections,[]);self.assertTrue(closed.is_set())

    async def test_connection_failure_after_first_text_still_replays_entire_reply_to_fallback(self):
        failed=asyncio.Event();continue_text=asyncio.Event()
        class Session:
            async def ws_connect(self,*args,**kwargs):failed.set();raise client.aiohttp.ClientError('simulated offline')
        speech=client.QwenClient(Session(),'ws-test.cn-beijing.maas.aliyuncs.com','test-only')
        entity=self.entity(speech)
        async def text():yield '前半句，';await continue_text.wait();yield '后半句。'
        response=await entity.async_stream_tts_audio(Request('zh-CN',{},text()))
        async def collect():return b''.join([part async for part in response.data_gen])
        task=asyncio.create_task(collect());await failed.wait();continue_text.set()
        self.assertEqual(await task,b'local-audio')
        self.assertEqual(fallback_calls[0]['message'],'前半句，后半句。')
        self.assertEqual(entity.last_metrics['error'],'connect_failed')

    async def test_empty_text_does_not_connect_or_trigger_local_fallback(self):
        class Session:
            async def ws_connect(self,*args,**kwargs):raise AssertionError('empty cloud request')
        speech=client.QwenClient(Session(),'ws-test.cn-beijing.maas.aliyuncs.com','test-only')
        async def text():yield '';yield '  '
        metrics={};self.assertEqual([part async for part in speech.stream(text(),'Cherry',metrics)],[])
        self.assertEqual(metrics['status'],'empty_text')

    async def test_one_pre_audio_timeout_retries_cloud_with_complete_text(self):
        class Transient:
            def __init__(self):self.calls=[]
            async def stream(self,source,voice,metrics):
                text=''.join([part async for part in source]);self.calls.append(text)
                if len(self.calls)==1:raise client.CloudTTSError('audio_timeout')
                metrics['status']='complete';yield b'cloud-audio'
        runtime=Transient();entity=self.entity(runtime)
        async def text():yield '前半句，';yield '后半句。'
        response=await entity.async_stream_tts_audio(Request('zh-CN',{},text()))
        self.assertEqual(b''.join([c async for c in response.data_gen]),b'cloud-audio')
        self.assertEqual(runtime.calls,['前半句，后半句。']*2)
        self.assertEqual(entity.requests,2);self.assertEqual(entity.fallbacks,0)
        self.assertEqual(entity.last_metrics['retry_count'],1);self.assertEqual(fallback_calls,[])

    async def test_invalid_credentials_do_not_cause_paid_cloud_retry(self):
        class Invalid:
            def __init__(self):self.calls=0
            async def stream(self,*args):
                self.calls+=1;raise client.CloudTTSError('invalid_auth');yield b''
        runtime=Invalid();entity=self.entity(runtime)
        async def text():yield '测试。'
        response=await entity.async_stream_tts_audio(Request('zh-CN',{},text()))
        self.assertEqual(b''.join([c async for c in response.data_gen]),b'local-audio')
        self.assertEqual(runtime.calls,1);self.assertEqual(entity.fallbacks,1)


if __name__ == '__main__':
    unittest.main()
