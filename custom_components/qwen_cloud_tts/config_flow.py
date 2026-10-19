import probatio as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .client import QwenClient, CloudTTSError, validate_host
from .const import DOMAIN, VOICES,MODELS


def schema(defaults, *, initial):
    fields = {vol.Required('host', default=defaults.get('host', '')): str}
    key = vol.Required('api_key') if initial else vol.Optional('api_key')
    fields[key] = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))
    fields[vol.Required('model',default=defaults.get('model','qwen3-tts-flash-realtime'))]=selector.SelectSelector(
        selector.SelectSelectorConfig(options=[{'value':key,'label':meta['name']} for key,meta in MODELS.items()]))
    fields[vol.Required('voice', default=defaults.get('voice', 'Cherry'))]=str
    fields[vol.Optional('fallback_engine', default=defaults.get('fallback_engine', 'tts.piper'))] = selector.EntitySelector(
        selector.EntitySelectorConfig(domain='tts'))
    return vol.Schema(fields)


async def validate(hass, data):
    data = dict(data, host=validate_host(data['host']), api_key=data['api_key'].strip())
    if not data['api_key']:
        raise ValueError('invalid_auth')
    model=data.get('model','qwen3-tts-flash-realtime')
    if model not in MODELS or data['voice'] not in MODELS[model]['voices']:raise ValueError('invalid_voice')
    if MODELS[model].get('legacy_only') and data['host'] not in ('dashscope.aliyuncs.com','dashscope-intl.aliyuncs.com'):
        raise ValueError('legacy_endpoint_required')
    data['model']=model
    client = QwenClient(async_get_clientsession(hass), data['host'], data['api_key'],model)
    await client.validate(data['voice'])
    return data


class QwenFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason='single_instance_allowed')
        errors = {}
        if user_input is not None:
            try:
                data = await validate(self.hass, user_input)
            except ValueError:
                errors['base'] = 'invalid_host'
            except CloudTTSError as error:
                errors['base'] = 'invalid_auth' if error.code == 'invalid_auth' else 'cannot_connect'
            else:
                return self.async_create_entry(title='千问云端朗读', data=data)
        return self.async_show_form(step_id='user', data_schema=schema(user_input or {}, initial=True), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return QwenOptions()


class QwenOptions(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        defaults = {**self.config_entry.data, **self.config_entry.options}
        errors = {}
        if user_input is not None:
            candidate = {**defaults, **user_input}
            if not user_input.get('api_key'):
                candidate['api_key'] = defaults['api_key']
            try:
                data = await validate(self.hass, candidate)
            except ValueError:
                errors['base'] = 'invalid_host'
            except CloudTTSError as error:
                errors['base'] = 'invalid_auth' if error.code == 'invalid_auth' else 'cannot_connect'
            else:
                return self.async_create_entry(title='', data=data)
        return self.async_show_form(step_id='init', data_schema=schema(defaults, initial=False), errors=errors)
