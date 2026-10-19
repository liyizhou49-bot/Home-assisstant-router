"""Kokoro synthesis runs exclusively in the VM's isolated compute application."""
import aiohttp
from homeassistant.components.tts import TextToSpeechEntity
from homeassistant.components.tts.models import Voice
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import DOMAIN

VOICES=['zf_001','zf_003','zf_017','zf_094','zm_010','zm_031','zm_069','zm_089']


async def async_setup_entry(hass,entry,async_add_entities):async_add_entities([LocalSpeech(entry)])


class LocalSpeech(TextToSpeechEntity):
    _attr_name='虚拟机本地中文朗读'
    _attr_icon='mdi:server'
    _attr_should_poll=False
    _attr_default_language='zh-CN'
    _attr_supported_languages=['zh-CN']
    _attr_supported_options=['voice','model','threads']

    def __init__(self,entry):
        self.entry=entry;self.entity_id='tts.router_lab_local';self._attr_unique_id=entry.entry_id+'_local_tts'

    @property
    def default_options(self):
        settings=self.hass.data[DOMAIN][self.entry.entry_id]['settings']
        return {'voice':settings.get('local_tts_voice','zf_001'),
                'model':settings.get('local_tts_model','kokoro_fp32'),
                'threads':settings.get('tts_threads',2)}

    @callback
    def async_get_supported_voices(self,language):return [Voice(v,('女声 ' if v.startswith('zf') else '男声 ')+v[3:]) for v in VOICES]

    async def async_get_tts_audio(self,message,language,options):
        runtime=self.hass.data[DOMAIN][self.entry.entry_id];settings=runtime['settings']
        voice=(options or {}).get('voice',settings.get('local_tts_voice','zf_001'))
        model=(options or {}).get('model',settings.get('local_tts_model','kokoro_fp32'))
        threads=(options or {}).get('threads',settings.get('tts_threads',2))
        if voice not in VOICES or model not in ('kokoro_fp32','kokoro_fp16'):raise HomeAssistantError('本地音色或模型设置不正确。')
        if len(message)>1500:raise HomeAssistantError('朗读内容过长，请缩短后重试。')
        try:
            session=async_get_clientsession(self.hass)
            async with session.post(settings['router_url']+'/tts',json={'text':message,'model':model,'voice':voice,'threads':threads},timeout=aiohttp.ClientTimeout(total=180)) as response:
                if response.status!=200:raise ValueError('tts_failed')
                audio=await response.read()
                if len(audio)>20*1024**2 or not audio.startswith(b'RIFF'):raise ValueError('invalid_audio')
                return 'wav',audio
        except Exception:raise HomeAssistantError('本地语音生成失败，请检查计算应用与模型下载状态。') from None
