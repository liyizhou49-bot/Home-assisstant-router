from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .client import QwenClient
from .const import DOMAIN

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass, entry):
    settings = {**entry.data, **entry.options}
    entry.runtime_data = QwenClient(async_get_clientsession(hass), settings['host'], settings['api_key'],settings.get('model','qwen3-tts-flash-realtime'))
    entry.async_on_unload(entry.add_update_listener(update_options))
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.TTS])
    return True


async def update_options(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, [Platform.TTS])
