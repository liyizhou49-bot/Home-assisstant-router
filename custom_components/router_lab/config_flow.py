import probatio as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from .const import DEFAULT_URL, DOMAIN
from .prompts import DEFAULT_ASSISTANT_PROMPT,DEFAULT_MEMORY_INSTRUCTIONS


def schema(defaults):
    fields = {
        vol.Required('router_url', default=defaults.get('router_url', DEFAULT_URL)): str,
        vol.Required('model', default=defaults.get('model', 'hybrid_v4')):
            vol.In(['hybrid_v4', 'binary_v1']),
        vol.Required('virtual_devices', default=defaults.get('virtual_devices', True)): bool,
    }
    key = vol.Optional('llm_agent', **({'default': defaults['llm_agent']}
                                    if defaults.get('llm_agent') else {}))
    fields[key] = selector.EntitySelector(selector.EntitySelectorConfig(domain='conversation'))
    fields[vol.Optional('fast_response',default=defaults.get('fast_response',True))]=bool
    fields[vol.Optional('routing_mode',default=defaults.get('routing_mode','normal'))]=selector.SelectSelector(
        selector.SelectSelectorConfig(options=[{'value':'normal','label':'正常'}, {'value':'economy','label':'节流'}]))
    fields[vol.Optional('idle_learning',default=defaults.get('idle_learning',True))]=bool
    fields[vol.Optional('learning_idle_seconds',default=defaults.get('learning_idle_seconds',90))]=vol.All(int,vol.Range(min=30,max=3600))
    fields[vol.Optional('learning_daily_limit',default=defaults.get('learning_daily_limit',3))]=vol.All(int,vol.Range(min=1,max=20))
    fields[vol.Optional('speech_mode',default=defaults.get('speech_mode','fast'))]=selector.SelectSelector(
        selector.SelectSelectorConfig(options=[{'value':'natural','label':'整段 · 完整音频准备好后播放'},
                                               {'value':'fast','label':'极速 · 边生成边朗读'}]))
    fields[vol.Optional('assistant_prompt',default=defaults.get('assistant_prompt',DEFAULT_ASSISTANT_PROMPT))]=selector.TextSelector(selector.TextSelectorConfig(multiline=True))
    fields[vol.Optional('memory_instructions',default=defaults.get('memory_instructions',DEFAULT_MEMORY_INSTRUCTIONS))]=selector.TextSelector(selector.TextSelectorConfig(multiline=True))
    return vol.Schema(fields)


def validate(data):
    from urllib.parse import urlsplit
    url = urlsplit(data['router_url'])
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
        raise ValueError('invalid_url')
    return dict(data, router_url=data['router_url'].rstrip('/'))


class RouterLabFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason='single_instance_allowed')
        errors = {}
        if user_input is not None:
            try:
                data = validate(user_input)
            except (ValueError, KeyError):
                errors['base'] = 'invalid_url'
            else:
                return self.async_create_entry(title='路由测试台', data=data)
        return self.async_show_form(step_id='user', data_schema=schema(user_input or {}), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return RouterLabOptions()


class RouterLabOptions(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                data = validate(user_input)
            except (ValueError, KeyError):
                errors['base'] = 'invalid_url'
            else:
                return self.async_create_entry(title='', data=data)
        defaults = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id='init', data_schema=schema(defaults), errors=errors)
