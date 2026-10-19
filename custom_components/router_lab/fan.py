from homeassistant.components.fan import FanEntity, FanEntityFeature
from .const import DOMAIN
from .entity import LabDevice
from .catalog import FANS


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = hass.data[DOMAIN][entry.entry_id]
    if runtime['settings'].get('virtual_devices',True):
        devices = [LabFan(entry,key,name) for key,name,_room in FANS]
        runtime['devices'].extend(devices)
        async_add_entities(devices)


class LabFan(LabDevice,FanEntity):
    _attr_supported_features = (FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF |
                                FanEntityFeature.SET_SPEED | FanEntityFeature.OSCILLATE)
    _attr_speed_count = 3

    def __init__(self,entry,key,name):
        self.setup_device(entry,key,name,'fan')
        self._attr_percentage = 0
        self._attr_oscillating = False

    @property
    def is_on(self):
        return self.percentage > 0

    async def async_turn_on(self,percentage=None,preset_mode=None,**kwargs):
        await self.async_set_percentage(percentage if percentage is not None else 66)

    async def async_turn_off(self,**kwargs):
        await self.async_set_percentage(0)

    async def async_set_percentage(self,percentage):
        if not 0 <= percentage <= 100:
            raise ValueError('风速范围为0至100')
        self._attr_percentage = percentage
        self.async_write_ha_state()

    async def async_oscillate(self,oscillating):
        self._attr_oscillating = bool(oscillating)
        self.async_write_ha_state()

    def reset(self):
        self._attr_percentage = 0
        self._attr_oscillating = False
        self.async_write_ha_state()
