from homeassistant.components.humidifier import HumidifierEntity,HumidifierDeviceClass
from .const import DOMAIN
from .entity import LabDevice
from .catalog import HUMIDIFIERS


async def async_setup_entry(hass,entry,async_add_entities):
    runtime=hass.data[DOMAIN][entry.entry_id]
    if runtime['settings'].get('virtual_devices',True):
        devices=[LabHumidifier(entry,key,name) for key,name,_room in HUMIDIFIERS]
        runtime['devices'].extend(devices)
        async_add_entities(devices)


class LabHumidifier(LabDevice,HumidifierEntity):
    _attr_device_class=HumidifierDeviceClass.HUMIDIFIER
    _attr_min_humidity=30
    _attr_max_humidity=80

    def __init__(self,entry,key,name):
        self.setup_device(entry,key,name,'humidifier')
        self._attr_is_on=False
        self._attr_target_humidity=50

    async def async_turn_on(self,**kwargs):
        self._attr_is_on=True
        self.async_write_ha_state()

    async def async_turn_off(self,**kwargs):
        self._attr_is_on=False
        self.async_write_ha_state()

    async def async_set_humidity(self,humidity):
        if not 30 <= humidity <= 80:
            raise ValueError('目标湿度范围为30至80')
        self._attr_target_humidity=humidity
        self.async_write_ha_state()

    def reset(self):
        self._attr_is_on=False
        self._attr_target_humidity=50
        self.async_write_ha_state()
