from homeassistant.components.cover import ATTR_POSITION, CoverDeviceClass, CoverEntity, CoverEntityFeature
from .const import DOMAIN
from .entity import LabDevice
from .catalog import COVERS


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = hass.data[DOMAIN][entry.entry_id]
    if runtime['settings'].get('virtual_devices', True):
        devices = [LabCover(entry,key,name,device_class) for key,name,_room,device_class in COVERS]
        runtime['devices'].extend(devices)
        async_add_entities(devices)


class LabCover(LabDevice, CoverEntity):
    _attr_device_class = CoverDeviceClass.CURTAIN
    _attr_supported_features = (CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE |
                                CoverEntityFeature.STOP | CoverEntityFeature.SET_POSITION)

    def __init__(self, entry, key='curtain', name='客厅窗帘', device_class='curtain'):
        self.setup_device(entry, key, name, 'cover')
        self._attr_device_class = CoverDeviceClass(device_class) if device_class else None
        self._default_position = 100 if device_class else 0
        self._attr_current_cover_position = self._default_position

    @property
    def is_closed(self):
        return self.current_cover_position == 0

    async def async_open_cover(self, **kwargs):
        await self.async_set_cover_position(position=100)

    async def async_close_cover(self, **kwargs):
        await self.async_set_cover_position(position=0)

    async def async_set_cover_position(self, **kwargs):
        value = int(kwargs[ATTR_POSITION])
        if not 0 <= value <= 100:
            raise ValueError('窗帘位置范围为0至100')
        self._attr_current_cover_position = value
        self.async_write_ha_state()

    async def async_stop_cover(self, **kwargs):
        self.async_write_ha_state()

    def reset(self):
        self._attr_current_cover_position = self._default_position
        self.async_write_ha_state()
