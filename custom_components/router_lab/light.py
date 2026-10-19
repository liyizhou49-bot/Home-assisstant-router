from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from .const import DOMAIN
from .entity import LabDevice
from .catalog import LIGHTS


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = hass.data[DOMAIN][entry.entry_id]
    if runtime['settings'].get('virtual_devices', True):
        devices = [LabLight(entry, key, name) for key, name, _room in LIGHTS]
        runtime['devices'].extend(devices)
        async_add_entities(devices)


class LabLight(LabDevice, LightEntity):
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS

    def __init__(self, entry, key, name):
        self.setup_device(entry, key, name, 'light')
        self._attr_is_on = False
        self._attr_brightness = 128

    async def async_turn_on(self, **kwargs):
        self._attr_is_on = True
        self._attr_brightness = int(kwargs.get(ATTR_BRIGHTNESS, self._attr_brightness))
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._attr_is_on = False
        self.async_write_ha_state()

    def reset(self):
        self._attr_is_on = False
        self._attr_brightness = 128
        self.async_write_ha_state()
