from homeassistant.components.number import NumberEntity,NumberMode
from homeassistant.helpers.dispatcher import async_dispatcher_send
from .const import DOMAIN,SIGNAL_ENVIRONMENT
from .entity import LabDevice
from .catalog import NUMBERS,ENVIRONMENT_DEFAULTS


async def async_setup_entry(hass,entry,async_add_entities):
    runtime=hass.data[DOMAIN][entry.entry_id]
    if runtime['settings'].get('virtual_devices',True):
        devices=[LabNumber(entry,*item[:2],*item[3:]) for item in NUMBERS]
        runtime['devices'].extend(devices)
        async_add_entities(devices)


class LabNumber(LabDevice,NumberEntity):
    _attr_mode=NumberMode.SLIDER

    def __init__(self,entry,key,name,environment_key,minimum,maximum,step,unit):
        self.setup_device(entry,key,name,'number')
        self.entry_id=entry.entry_id
        self.environment_key=environment_key
        self._attr_native_min_value=minimum
        self._attr_native_max_value=maximum
        self._attr_native_step=step
        self._attr_native_unit_of_measurement=unit

    @property
    def native_value(self):
        return self.hass.data[DOMAIN][self.entry_id]['environment'][self.environment_key]

    def set_value(self,value):
        self.hass.data[DOMAIN][self.entry_id]['environment'][self.environment_key]=value
        self.async_write_ha_state()
        async_dispatcher_send(self.hass,SIGNAL_ENVIRONMENT)

    async def async_set_native_value(self,value):
        if not self.native_min_value <= value <= self.native_max_value:
            raise ValueError('模拟环境值超出范围')
        self.set_value(value)

    def reset(self):
        self.set_value(ENVIRONMENT_DEFAULTS[self.environment_key])
