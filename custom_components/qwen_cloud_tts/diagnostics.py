"""Do not expose credentials or input text through downloadable diagnostics."""
async def async_get_config_entry_diagnostics(hass, entry):
    settings = {**entry.data, **entry.options}
    return {'model': 'qwen3-tts-flash-realtime', 'voice': settings.get('voice', 'Cherry'),
            'fallback_engine': settings.get('fallback_engine'), 'credentials': '**REDACTED**'}
