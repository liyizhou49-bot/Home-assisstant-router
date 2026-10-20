"""Native HA streaming TTS, native cache, and pre-audio Piper fallback."""
from contextlib import aclosing
from homeassistant.components import tts
from homeassistant.components.tts import TextToSpeechEntity, TTSAudioRequest, TTSAudioResponse
from homeassistant.components.tts.models import Voice
from homeassistant.components.tts.media_source import generate_media_source_id
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from .client import CloudTTSError, ReplayText
from .const import DOMAIN, VOICES,MODELS


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([QwenSpeech(entry)])


class QwenSpeech(TextToSpeechEntity):
    _attr_name = '千问云端朗读'
    _attr_icon = 'mdi:account-voice'
    _attr_default_language = 'zh-CN'
    _attr_supported_languages = ['zh-CN']
    _attr_supported_options = ['voice']
    _attr_should_poll = False

    def __init__(self, entry):
        self.entry = entry
        self._attr_unique_id = entry.entry_id + '_tts'
        self.entity_id = 'tts.qwen_cloud_tts'
        self.settings = {**entry.data, **entry.options}
        self._attr_default_options = {'voice': self.settings.get('voice', 'Cherry'),'model':self.settings.get('model','qwen3-tts-flash-realtime')}
        self._attr_supported_options=['voice','model']
        self.requests = 0
        self.fallbacks = 0
        self.last_metrics = {}

    @callback
    def async_get_supported_voices(self, language):
        return [Voice(voice, name) for voice, name in MODELS[self.settings.get('model','qwen3-tts-flash-realtime')]['voices'].items()]

    @property
    def extra_state_attributes(self):
        return {'cloud_requests': self.requests, 'fallback_count': self.fallbacks,
                'default_voice': self.settings.get('voice', 'Cherry'),
                'default_model':self.settings.get('model','qwen3-tts-flash-realtime'),
                'last_synthesis': dict(self.last_metrics), 'cache': 'Home Assistant原生语音缓存'}

    async def async_stream_tts_audio(self, request: TTSAudioRequest):
        voice = request.options.get('voice', self.settings.get('voice', 'Cherry'))
        if request.options.get('model',self.settings.get('model','qwen3-tts-flash-realtime'))!=self.settings.get('model','qwen3-tts-flash-realtime'):
            raise HomeAssistantError('请在统一设置页切换语音模型。')
        if voice not in MODELS[self.settings.get('model','qwen3-tts-flash-realtime')]['voices']:
            raise HomeAssistantError('请选择此集成支持的音色。')

        if self.entry.runtime_data.protocol=='qwen_http':
            message=''.join([part async for part in request.message_gen])
            if len(message)>10000:raise HomeAssistantError('朗读文本过长。')
            metrics={'voice':voice,'engine':'cloud','model':self.entry.runtime_data.model}
            self.requests+=1
            try:
                data=await self.entry.runtime_data.file(message,voice,metrics);extension='wav'
            except CloudTTSError:
                fallback=self.settings.get('fallback_engine')
                if not fallback:raise HomeAssistantError('云端朗读暂时不可用。') from None
                media_id=generate_media_source_id(self.hass,message=message,engine=fallback,language='zh_CN',options=None,cache=True)
                extension,data=await tts.async_get_media_source_audio(self.hass,media_id)
                self.fallbacks+=1;metrics.update(status='local_fallback',engine=fallback)
            self.last_metrics=metrics;self.async_write_ha_state()
            async def complete():yield data
            return TTSAudioResponse(extension,complete())

        async def audio():
            replay = ReplayText(request.message_gen)
            emitted = False
            metrics = {'voice': voice, 'engine': 'cloud','model':self.settings.get('model','qwen3-tts-flash-realtime')}
            try:
                for attempt in range(2):
                    self.requests += 1;metrics['cloud_attempts']=attempt+1
                    try:
                        async with aclosing(self.entry.runtime_data.stream(replay.stream(), voice, metrics)) as stream:
                            async for data in stream:
                                emitted = True
                                yield data
                        break
                    except CloudTTSError as error:
                        # Retry one transient failure only before the first audible byte.
                        # A fresh reader replays every text chunk; the producer is not restarted.
                        if not emitted and attempt==0 and error.code in ('audio_timeout','connect_failed','connection_closed','stream_failed','invalid_response'):
                            metrics.update(retry_count=1,retry_reason=error.code)
                            continue
                        metrics.update(status='failed', error=error.code)
                        fallback = self.settings.get('fallback_engine')
                        if emitted or not fallback or fallback == self.entity_id:
                            raise HomeAssistantError('千问朗读暂时无法完成，请稍后重试。') from None
                        message = await replay.complete()
                        if not message:
                            return
                        media_id = generate_media_source_id(self.hass, message=message, engine=fallback,
                                                            language='zh_CN', options=None, cache=True)
                        extension, data = await tts.async_get_media_source_audio(self.hass, media_id)
                        if extension != 'mp3' or not data:
                            raise HomeAssistantError('本地备用朗读暂时不可用。')
                        self.fallbacks += 1
                        metrics.update(status='local_fallback', engine=fallback)
                        yield data
                        break
            finally:
                await replay.close()
                self.last_metrics = dict(metrics)
                self.async_write_ha_state()

        return TTSAudioResponse('mp3', audio())

    async def async_get_tts_audio(self, message, language, options):
        async def text():
            yield message
        response = await self.async_stream_tts_audio(TTSAudioRequest(language, options, text()))
        return response.extension, b''.join([chunk async for chunk in response.data_gen])
